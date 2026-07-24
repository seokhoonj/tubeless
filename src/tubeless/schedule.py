"""Register the daily digest with the host operating system's scheduler.

``tubeless digest`` builds one Markdown file per day, but only when something runs
it every day. This module installs that recurring job so a user never hand-edits
their crontab. Today only Linux (cron) is implemented; the ``Scheduler`` protocol
and ``scheduler_for_platform()`` leave a seam for a launchd (macOS) or Task
Scheduler (Windows) backend to slot in behind the same three verbs without the
CLI changing.

One label, ``tubeless-digest``, tags the job so install / uninstall / status find
and replace exactly our crontab line and leave the rest of the crontab alone. The
OS-independent parts -- finding the ``tubeless`` executable, rendering the crontab
line -- are pure functions, testable without touching the real ``crontab``.
"""

from __future__ import annotations

import datetime
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tubeless.errors import ScheduleError

__all__ = [
    "TASK_LABEL",
    "LOG_PATH",
    "DEFAULT_DAILY_TIME",
    "DigestSchedule",
    "ScheduleStatus",
    "Scheduler",
    "CronScheduler",
    "scheduler_for_platform",
    "find_executable",
    "resolve_digest_command",
    "parse_daily_time",
]

# One label identifies the job across any backend: here, the trailing marker on
# the crontab line. A stable constant -- changing it orphans an installed job.
TASK_LABEL = "tubeless-digest"

# Where a scheduled run sends its stdout/stderr. A cron job has no terminal, so
# without this redirect its output (and any error) would be mailed away or lost.
LOG_PATH = Path.home() / ".tubeless" / "digest.log"

# The daily run time when --at is not given. A morning build has the prior day's
# uploads ready to read.
DEFAULT_DAILY_TIME = datetime.time(7, 0)

# Written above the managed crontab line so a human reading their crontab sees
# what put it there; and the trailing marker that tags our own entry line.
_CRON_HEADER = f"# {TASK_LABEL} (managed by `tubeless schedule`)"
_CRON_MARKER = f"# {TASK_LABEL}"


@dataclass(frozen=True)
class DigestSchedule:
    """What to schedule: a command to run once a day at a fixed local time.

    ``command`` is a full argv (the resolved ``tubeless`` executable plus
    ``digest``), not a shell string, so a path containing spaces needs no quoting.
    """
    command:    tuple[str, ...]
    daily_time: datetime.time = DEFAULT_DAILY_TIME


@dataclass(frozen=True)
class ScheduleStatus:
    """Whether the digest job is installed, plus a one-line description of it (the
    crontab line, or why none was found) to show the user."""
    installed:   bool
    description: str


class Scheduler(Protocol):
    """A host scheduler that can install, remove, and report the digest job.

    Selected by ``scheduler_for_platform()``. Each expected failure (the scheduler
    binary missing, a call returning non-zero) is raised as ``ScheduleError``, not
    a raw ``CalledProcessError``, so the CLI prints one clean line.
    """

    def install(self, schedule: DigestSchedule) -> ScheduleStatus:
        """Register (or replace) the daily digest job; return its status."""
        ...

    def uninstall(self) -> bool:
        """Remove the digest job if present; return whether one was removed."""
        ...

    def status(self) -> ScheduleStatus:
        """Report whether the digest job is installed."""
        ...


def scheduler_for_platform() -> Scheduler:
    """The scheduler implementation for the current operating system.

    Only Linux (cron) is implemented today. macOS and Windows raise a
    ``ScheduleError`` pointing at running ``tubeless digest`` from launchd or Task
    Scheduler by hand, until a native backend is added behind this same seam.

    Raises:
        ScheduleError: the platform has no ``tubeless schedule`` backend yet.
    """
    if sys.platform.startswith("linux"):
        return CronScheduler()
    raise ScheduleError(
        f"`tubeless schedule` is currently Linux-only (this platform is "
        f"{sys.platform}); run `tubeless digest` from your OS scheduler instead"
    )


def find_executable() -> tuple[str, ...]:
    """The argv prefix that runs tubeless from a scheduled job.

    Prefer the installed ``tubeless`` console script by absolute path, since cron
    runs with a minimal PATH. Fall back to ``python -m tubeless`` when the script
    is not found (an editable checkout, an unusual install), which still works as
    long as the interpreter can import the package.
    """
    found = shutil.which("tubeless")
    if found:
        return (found,)
    return (sys.executable, "-m", "tubeless")


def resolve_digest_command() -> tuple[str, ...]:
    """The full argv a scheduled run executes: ``<tubeless> digest``.

    Backend and language are deliberately not baked in -- the scheduled
    ``tubeless digest`` reads them from ``~/.tubeless/config.env`` like any other
    run, so they change in one place without reinstalling the job.
    """
    return (*find_executable(), "digest")


def parse_daily_time(text: str) -> datetime.time:
    """Parse a ``HH:MM`` 24-hour clock time (e.g. ``07:00``, ``22:30``).

    Raises:
        ScheduleError: the text is not a valid ``HH:MM`` time -- surfaced as a
            one-line CLI error, not a traceback.
    """
    try:
        parsed = datetime.datetime.strptime(text.strip(), "%H:%M")
    except ValueError:
        raise ScheduleError(f"--at must be a HH:MM 24-hour time, got {text!r}") from None
    return parsed.time()


class CronScheduler:
    """Linux scheduler backed by the user's crontab.

    The job is a single crontab line tagged with a trailing ``# tubeless-digest``
    marker (below a header comment), so install / uninstall find and replace only
    our line and leave every other crontab entry untouched. Plain cron has no
    catch-up: a run whose time passed while the machine was off is simply missed,
    which ``status`` states outright.
    """

    def install(self, schedule: DigestSchedule) -> ScheduleStatus:
        self._write_crontab(_render_crontab(self._read_crontab(), schedule))
        return self.status()

    def uninstall(self) -> bool:
        existing = self._read_crontab()
        stripped = _strip_crontab_block(existing)
        if stripped == existing:
            return False
        self._write_crontab(stripped)
        return True

    def status(self) -> ScheduleStatus:
        line = _find_crontab_line(self._read_crontab())
        if line is None:
            return ScheduleStatus(installed=False, description="no crontab entry")
        return ScheduleStatus(
            installed   = True,
            description = f"{line}  (cron does not run missed builds)",
        )

    def _read_crontab(self) -> str:
        # `crontab -l` exits non-zero when the user has no crontab yet; for us that
        # is not an error, just an empty starting point.
        result = _run(("crontab", "-l"), check=False)
        return result.stdout if result.returncode == 0 else ""

    def _write_crontab(self, text: str) -> None:
        # crontab refuses input without a trailing newline; an empty body clears
        # the table, which is exactly uninstall removing our only line.
        body = text if not text or text.endswith("\n") else text + "\n"
        _run(("crontab", "-"), stdin=body)


def _crontab_line(schedule: DigestSchedule) -> str:
    command = " ".join(schedule.command)
    return (f"{schedule.daily_time.minute} {schedule.daily_time.hour} * * * "
            f"{command} >> {LOG_PATH} 2>&1  {_CRON_MARKER}")


def _render_crontab(existing: str, schedule: DigestSchedule) -> str:
    """The crontab text after installing our job: strip any prior managed block,
    then append a fresh header + line. Re-installing is therefore idempotent -- it
    replaces our block rather than stacking a second one."""
    kept  = _strip_crontab_block(existing).rstrip("\n")
    block = f"{_CRON_HEADER}\n{_crontab_line(schedule)}"
    return f"{block}\n" if not kept else f"{kept}\n{block}\n"


def _strip_crontab_block(existing: str) -> str:
    """``existing`` with our managed lines removed (the header comment and the
    marked entry), leaving every other crontab line in place."""
    kept = [
        line for line in existing.splitlines()
        if line.strip() != _CRON_HEADER and not line.rstrip().endswith(_CRON_MARKER)
    ]
    return "\n".join(kept)


def _find_crontab_line(existing: str) -> str | None:
    """Our managed entry line from ``existing``, or None if it is not there."""
    for line in existing.splitlines():
        if line.rstrip().endswith(_CRON_MARKER):
            return line.strip()
    return None


def _run(argv: tuple[str, ...], *, stdin: str | None = None,
         check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a scheduler command, translating its failures into ``ScheduleError``.

    ``FileNotFoundError`` means the scheduler binary (here, ``crontab``) is not
    installed; a non-zero exit under ``check`` means the command itself failed.
    Both become a one-line ``ScheduleError`` so the CLI prints a clean message
    instead of a traceback.
    """
    try:
        result = subprocess.run(argv, input=stdin, capture_output=True, text=True)
    except FileNotFoundError:
        raise ScheduleError(f"required command not found: {argv[0]}") from None
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ScheduleError(f"{argv[0]} failed: {detail}" if detail else f"{argv[0]} failed")
    return result
