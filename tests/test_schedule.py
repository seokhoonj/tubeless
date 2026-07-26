import datetime
import subprocess

import pytest

from tubeless import schedule as schedule_module
from tubeless.cli import main
from tubeless.errors import ScheduleError
from tubeless.schedule import (
    TASK_LABEL,
    CronScheduler,
    DigestSchedule,
    find_executable,
    log_path,
    parse_daily_time,
    resolve_digest_command,
    scheduler_for_platform,
)

SAMPLE = DigestSchedule(command=("/opt/bin/tubeless", "digest"), daily_time=datetime.time(7, 0))


# --- parse_daily_time -------------------------------------------------------

def test_parse_daily_time_reads_hh_mm():
    assert parse_daily_time("22:30") == datetime.time(22, 30)


def test_parse_daily_time_strips_surrounding_whitespace():
    assert parse_daily_time("  07:00 ") == datetime.time(7, 0)


@pytest.mark.parametrize("bad", ["7am", "25:00", "07:60", "0700", ""])
def test_parse_daily_time_rejects_bad_input(bad):
    with pytest.raises(ScheduleError):
        parse_daily_time(bad)


# --- find_executable / resolve_digest_command -------------------------------

def test_find_executable_prefers_the_console_script(monkeypatch):
    monkeypatch.setattr(schedule_module.shutil, "which", lambda name: "/usr/local/bin/tubeless")
    assert find_executable() == ("/usr/local/bin/tubeless",)


def test_find_executable_falls_back_to_python_module(monkeypatch):
    monkeypatch.setattr(schedule_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(schedule_module.sys, "executable", "/usr/bin/python3")
    assert find_executable() == ("/usr/bin/python3", "-m", "tubeless")


def test_resolve_digest_command_appends_digest(monkeypatch):
    monkeypatch.setattr(schedule_module.shutil, "which", lambda name: "/opt/bin/tubeless")
    assert resolve_digest_command() == ("/opt/bin/tubeless", "digest")


# --- crontab rendering (pure) -----------------------------------------------

def test_crontab_line_carries_time_command_and_marker():
    line = schedule_module._crontab_line(SAMPLE)
    assert line.startswith("0 7 * * * /opt/bin/tubeless digest")
    assert str(log_path()) in line
    assert line.rstrip().endswith(f"# {TASK_LABEL}")


def test_render_crontab_keeps_unrelated_entries_and_writes_one_block():
    rendered = schedule_module._render_crontab("0 0 * * * /usr/bin/backup\n", SAMPLE)
    assert "0 0 * * * /usr/bin/backup" in rendered
    assert rendered.count(f"# {TASK_LABEL}") == 2   # header comment + entry marker
    assert rendered.endswith("\n")


def test_render_crontab_is_idempotent():
    once  = schedule_module._render_crontab("", SAMPLE)
    twice = schedule_module._render_crontab(once, SAMPLE)
    assert once == twice                             # re-install replaces, never stacks


def test_strip_crontab_block_removes_only_managed_lines():
    existing = schedule_module._render_crontab("0 0 * * * /usr/bin/backup\n", SAMPLE)
    stripped = schedule_module._strip_crontab_block(existing)
    assert "/usr/bin/backup" in stripped
    assert TASK_LABEL not in stripped


def test_find_crontab_line_returns_none_when_absent():
    assert schedule_module._find_crontab_line("0 0 * * * /usr/bin/backup\n") is None


# --- scheduler_for_platform -------------------------------------------------

def test_scheduler_for_platform_is_cron_on_linux(monkeypatch):
    monkeypatch.setattr(schedule_module.sys, "platform", "linux")
    assert isinstance(scheduler_for_platform(), CronScheduler)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_scheduler_for_platform_rejects_unsupported(monkeypatch, platform):
    monkeypatch.setattr(schedule_module.sys, "platform", platform)
    with pytest.raises(ScheduleError):
        scheduler_for_platform()


# --- CronScheduler against an in-memory crontab -----------------------------

class _FakeCrontab:
    """Stand-in for the ``crontab`` binary: ``crontab -l`` reads, ``crontab -``
    writes, and ``crontab -l`` exits non-zero while empty (as the real tool does
    with no table yet)."""

    def __init__(self, text: str = ""):
        self.text = text

    def __call__(self, argv, *, stdin=None, check=True):
        if argv == ("crontab", "-l"):
            code = 0 if self.text else 1
            return subprocess.CompletedProcess(argv, code, stdout=self.text, stderr="")
        if argv == ("crontab", "-"):
            self.text = stdin or ""
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected argv: {argv}")


def test_cron_install_status_uninstall_roundtrip(monkeypatch):
    fake = _FakeCrontab()
    monkeypatch.setattr(schedule_module, "_run", fake)
    cron = CronScheduler()

    assert cron.install(SAMPLE).installed
    assert cron.status().installed
    assert fake.text.count(f"# {TASK_LABEL}") == 2   # exactly one managed block

    cron.install(SAMPLE)                             # re-install stays single
    assert fake.text.count(f"# {TASK_LABEL}") == 2

    assert cron.uninstall() is True
    assert cron.status().installed is False
    assert cron.uninstall() is False                 # nothing left to remove


def test_cron_leaves_unrelated_entries_untouched(monkeypatch):
    fake = _FakeCrontab("0 0 * * * /usr/bin/backup\n")
    monkeypatch.setattr(schedule_module, "_run", fake)
    cron = CronScheduler()
    cron.install(SAMPLE)
    assert "/usr/bin/backup" in fake.text
    cron.uninstall()
    assert "/usr/bin/backup" in fake.text


def test_run_raises_when_binary_missing(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(schedule_module.subprocess, "run", boom)
    with pytest.raises(ScheduleError):
        schedule_module._run(("crontab", "-l"))


# --- CLI wiring -------------------------------------------------------------

def test_cli_schedule_status_reports_not_scheduled(monkeypatch, capsys):
    fake = _FakeCrontab()
    monkeypatch.setattr(schedule_module, "_run", fake)
    monkeypatch.setattr(schedule_module.sys, "platform", "linux")
    assert main(["schedule", "status"]) == 0
    assert "not scheduled" in capsys.readouterr().out


def test_cli_schedule_install_then_status(monkeypatch, capsys):
    fake = _FakeCrontab()
    monkeypatch.setattr(schedule_module, "_run", fake)
    monkeypatch.setattr(schedule_module.sys, "platform", "linux")
    monkeypatch.setattr(schedule_module.shutil, "which", lambda name: "/opt/bin/tubeless")

    assert main(["schedule", "install", "--at", "22:30"]) == 0
    out = capsys.readouterr().out
    assert "daily at 22:30" in out

    assert main(["schedule", "status"]) == 0
    assert "scheduled:" in capsys.readouterr().out


def test_cli_schedule_install_rejects_bad_time(monkeypatch):
    monkeypatch.setattr(schedule_module.sys, "platform", "linux")
    with pytest.raises(SystemExit):   # argparse type= failure exits 2
        main(["schedule", "install", "--at", "9pm"])
