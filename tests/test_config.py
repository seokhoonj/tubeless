"""Settings resolution: TOML parsing, the XDG directory, and env-over-file order.

No real ~/.config/tubeless/config.toml -- every lookup is pointed at a temp file
or an injected dict so the tests are hermetic.
"""

from pathlib import Path

import pytest

from tubeless import config
from tubeless.errors import ConfigError


def _write_toml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_config_dir_defaults_to_xdg_config_home(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config.config_dir() == Path.home() / ".config" / "tubeless"


def test_config_dir_respects_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.config_dir() == tmp_path / "tubeless"
    assert config.config_path() == tmp_path / "tubeless" / "config.toml"


def _blind_overrides(monkeypatch):
    # data_dir()/state_dir() consult _dir_override -> the TUBELESS_*_DIR env vars and
    # config.toml; blind both so the dev machine's real config/env (which supports a
    # data_dir/state_dir key) can never flip a default-path assertion below.
    monkeypatch.setattr(config, "load_settings", dict)
    for var in ("TUBELESS_DATA_DIR", "TUBELESS_STATE_DIR"):
        monkeypatch.delenv(var, raising=False)


def test_data_dir_defaults_to_xdg_data_home(monkeypatch):
    _blind_overrides(monkeypatch)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert config.data_dir() == Path.home() / ".local" / "share" / "tubeless"


def test_data_dir_respects_xdg_data_home(tmp_path, monkeypatch):
    _blind_overrides(monkeypatch)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert config.data_dir() == tmp_path / "tubeless"


def test_state_dir_defaults_to_xdg_state_home(monkeypatch):
    _blind_overrides(monkeypatch)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert config.state_dir() == Path.home() / ".local" / "state" / "tubeless"


def test_state_dir_respects_xdg_state_home(tmp_path, monkeypatch):
    _blind_overrides(monkeypatch)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert config.state_dir() == tmp_path / "tubeless"


_DIR_OVERRIDES = [
    ("data_dir",  "TUBELESS_DATA_DIR",  "data_dir"),
    ("state_dir", "TUBELESS_STATE_DIR", "state_dir"),
]


@pytest.mark.parametrize("dir_fn, env_name, key", _DIR_OVERRIDES)
def test_dir_config_key_overrides_the_platform_default(dir_fn, env_name, key, monkeypatch):
    # a data_dir/state_dir in config.toml relocates the dir, read every run (so cron
    # sees it too) -- used as an explicit path, no app-name appended.
    monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(config, "load_settings", lambda: {key: "/mnt/big/tube"})
    assert getattr(config, dir_fn)() == Path("/mnt/big/tube")


@pytest.mark.parametrize("dir_fn, env_name, key", _DIR_OVERRIDES)
def test_dir_env_var_wins_over_config_and_default(dir_fn, env_name, key, monkeypatch):
    monkeypatch.setenv(env_name, "/env/wins")
    monkeypatch.setattr(config, "load_settings", lambda: {key: "/from/config"})
    assert getattr(config, dir_fn)() == Path("/env/wins")


@pytest.mark.parametrize("dir_fn, env_name, key", _DIR_OVERRIDES)
def test_dir_override_expands_a_leading_tilde(dir_fn, env_name, key, monkeypatch):
    monkeypatch.setenv(env_name, "~/mydata")
    assert getattr(config, dir_fn)() == Path.home() / "mydata"


@pytest.mark.parametrize("dir_fn, env_name, key", _DIR_OVERRIDES)
def test_dir_empty_override_falls_back_to_the_default(dir_fn, env_name, key, monkeypatch):
    # an exported-but-empty var and an empty config value both read as unset.
    monkeypatch.setenv(env_name, "")
    monkeypatch.setattr(config, "load_settings", lambda: {key: ""})
    assert getattr(config, dir_fn)().name == "tubeless"   # the platform default, not ""


@pytest.mark.parametrize("dir_fn, env_name, key", _DIR_OVERRIDES)
def test_dir_tolerates_a_malformed_config_without_raising(dir_fn, env_name, key, monkeypatch):
    # data_dir()/state_dir() resolve paths at import time, so a broken config must
    # not raise here -- it surfaces later through setting(). Falls back to the default.
    monkeypatch.delenv(env_name, raising=False)
    def boom():
        raise ConfigError("bad toml")
    monkeypatch.setattr(config, "load_settings", boom)
    assert getattr(config, dir_fn)().name == "tubeless"


def test_config_dir_has_no_config_key_override(monkeypatch):
    # config_dir cannot be named in config.toml (it is where config.toml lives): a
    # data_dir/state_dir key must not accidentally move it.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(config, "load_settings",
                        lambda: {"data_dir": "/x", "state_dir": "/y", "config_dir": "/z"})
    assert config.config_dir() == Path.home() / ".config" / "tubeless"


def test_the_three_bases_are_distinct(monkeypatch):
    # config vs data vs state must not collapse to one directory: the whole point
    # of 0.3.0 is that a settings reset never touches the corpus.
    _blind_overrides(monkeypatch)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)
    bases = {config.config_dir(), config.data_dir(), config.state_dir()}
    assert len(bases) == 3


def _xdg_layout(tmp_path, monkeypatch) -> tuple[Path, Path, Path]:
    """Point the three bases at separate temp roots and return their tubeless dirs."""
    _blind_overrides(monkeypatch)   # a data_dir/state_dir key in the real config must not shift the targets
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return config.config_dir(), config.data_dir(), config.state_dir()


def test_migrate_moves_legacy_data_and_state_out_of_config(tmp_path, monkeypatch):
    cfg, data, state = _xdg_layout(tmp_path, monkeypatch)
    # a <=0.2.0 install: corpus, digests, state.json, digest.log all under config.
    (cfg / "corpus").mkdir(parents=True)
    (cfg / "corpus" / "s.json").write_text("{}", encoding="utf-8")
    (cfg / "digests").mkdir()
    (cfg / "state.json").write_text('{"seen": ["a"]}', encoding="utf-8")
    (cfg / "digest.log").write_text("run\n", encoding="utf-8")

    config.migrate_legacy_layout()

    assert (data / "corpus" / "s.json").read_text(encoding="utf-8") == "{}"
    assert (data / "digests").is_dir()
    assert (state / "state.json").read_text(encoding="utf-8") == '{"seen": ["a"]}'
    assert (state / "digest.log").read_text(encoding="utf-8") == "run\n"
    # the legacy copies are gone (moved, not copied)
    assert not (cfg / "corpus").exists()
    assert not (cfg / "state.json").exists()


def test_migrate_leaves_config_files_in_place(tmp_path, monkeypatch):
    cfg, data, state = _xdg_layout(tmp_path, monkeypatch)
    (cfg).mkdir(parents=True)
    (cfg / "config.toml").write_text('backend = "gemini"\n', encoding="utf-8")
    (cfg / "credentials.json").write_text("{}", encoding="utf-8")

    config.migrate_legacy_layout()

    assert (cfg / "config.toml").exists()
    assert (cfg / "credentials.json").exists()


def test_migrate_is_idempotent_and_never_clobbers_new_data(tmp_path, monkeypatch):
    cfg, data, state = _xdg_layout(tmp_path, monkeypatch)
    # a stale legacy state file lingers, but the new location already has real state:
    # migration must not overwrite the new one.
    (cfg).mkdir(parents=True)
    (cfg / "state.json").write_text('{"seen": ["old"]}', encoding="utf-8")
    (state).mkdir(parents=True)
    (state / "state.json").write_text('{"seen": ["new"]}', encoding="utf-8")

    config.migrate_legacy_layout()
    config.migrate_legacy_layout()   # second call: still a no-op

    assert (state / "state.json").read_text(encoding="utf-8") == '{"seen": ["new"]}'


def test_migrate_is_a_noop_on_a_fresh_install(tmp_path, monkeypatch):
    cfg, data, state = _xdg_layout(tmp_path, monkeypatch)
    # nothing exists yet: migration must not create empty dirs or fail.
    config.migrate_legacy_layout()
    assert not data.exists()
    assert not state.exists()


def test_migrate_wraps_a_move_failure_as_config_error(tmp_path, monkeypatch):
    # A failed relocation must raise, not be swallowed: silently reading a
    # moved-but-missing state ledger as empty would re-process the whole backlog.
    import shutil
    cfg, _data, _state = _xdg_layout(tmp_path, monkeypatch)
    cfg.mkdir(parents=True)
    (cfg / "state.json").write_text('{"seen": ["a"]}', encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("cross-device move failed")
    monkeypatch.setattr(shutil, "move", boom)

    with pytest.raises(ConfigError):
        config.migrate_legacy_layout()


def test_dir_override_non_string_value_reads_as_absent(monkeypatch):
    # data_dir = 12345 must not become Path("12345") (a working-directory-relative
    # dir); a non-string value reads as unset and the platform default stands.
    monkeypatch.delenv("TUBELESS_DATA_DIR", raising=False)
    monkeypatch.setattr(config, "load_settings", lambda: {"data_dir": 12345})
    assert config.data_dir().name == "tubeless"   # the platform default, not "12345"


def test_load_settings_parses_toml(tmp_path):
    path = _write_toml(tmp_path, 'backend = "gemini"\nmax_points = 20\n')
    assert config.load_settings(path) == {"backend": "gemini", "max_points": 20}


def test_load_settings_missing_file_is_empty(tmp_path):
    assert config.load_settings(tmp_path / "nope.toml") == {}


def test_load_settings_raises_on_malformed_toml(tmp_path):
    path = _write_toml(tmp_path, "[unclosed section\n")
    with pytest.raises(ConfigError):
        config.load_settings(path)


def test_load_settings_raises_on_an_unreadable_file(tmp_path):
    # a directory at the path: exists() is true, open() raises OSError -> ConfigError,
    # not a bare traceback out of the CLI.
    unreadable = tmp_path / "config.toml"
    unreadable.mkdir()
    with pytest.raises(ConfigError):
        config.load_settings(unreadable)


def test_load_settings_raises_on_a_non_utf8_file(tmp_path):
    # tomllib decodes internally, so a non-UTF-8 file raises UnicodeDecodeError
    # (a ValueError, not an OSError); it must surface as ConfigError, not a raw
    # traceback. Without the UnicodeDecodeError guard this would escape.
    path = tmp_path / "config.toml"
    path.write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(ConfigError):
        config.load_settings(path)


def test_setting_reads_from_the_file(monkeypatch):
    monkeypatch.delenv("TUBELESS_BACKEND", raising=False)
    monkeypatch.setattr(config, "load_settings", lambda: {"backend": "gemini"})
    assert config.setting("TUBELESS_BACKEND") == "gemini"


def test_setting_env_overrides_the_file(monkeypatch):
    monkeypatch.setenv("TUBELESS_BACKEND", "ollama")
    monkeypatch.setattr(config, "load_settings", lambda: {"backend": "gemini"})
    assert config.setting("TUBELESS_BACKEND") == "ollama"


def test_setting_returns_none_when_absent(monkeypatch):
    monkeypatch.delenv("TUBELESS_BACKEND", raising=False)
    monkeypatch.setattr(config, "load_settings", dict)
    assert config.setting("TUBELESS_BACKEND") is None


def test_setting_coerces_a_numeric_toml_value_to_str(monkeypatch):
    # config.toml stores max_points as an int; setting() yields a str, as the
    # environment always would, so callers that int() it see no difference.
    monkeypatch.delenv("TUBELESS_MAX_POINTS", raising=False)
    monkeypatch.setattr(config, "load_settings", lambda: {"max_points": 20})
    assert config.setting("TUBELESS_MAX_POINTS") == "20"


def test_setting_empty_env_does_not_override_the_file(monkeypatch):
    # An exported-but-empty var must fall through to the file, not shadow it with "".
    monkeypatch.setenv("TUBELESS_BACKEND", "")
    monkeypatch.setattr(config, "load_settings", lambda: {"backend": "gemini"})
    assert config.setting("TUBELESS_BACKEND") == "gemini"


def test_setting_empty_file_value_is_absent(monkeypatch):
    # An empty setting in config.toml falls back to the default (None), not "" --
    # which would crash a choice-validated flag like --detail.
    monkeypatch.delenv("TUBELESS_DETAIL", raising=False)
    monkeypatch.setattr(config, "load_settings", lambda: {"detail": ""})
    assert config.setting("TUBELESS_DETAIL") is None


def test_setting_stringifies_a_zero_instead_of_dropping_it(monkeypatch):
    # 0 is not empty: it stringifies to "0" (matching an env "0"), so an
    # out-of-range max_points still reaches its "must be positive" error rather
    # than silently falling back to the default -- only "" reads as absent.
    monkeypatch.delenv("TUBELESS_MAX_POINTS", raising=False)
    monkeypatch.setattr(config, "load_settings", lambda: {"max_points": 0})
    assert config.setting("TUBELESS_MAX_POINTS") == "0"


def test_load_settings_reads_the_default_config_path(tmp_path, monkeypatch):
    # setting() calls load_settings() with no argument -> it must read config_path().
    path = _write_toml(tmp_path, 'backend = "gemini"\n')
    monkeypatch.setattr(config, "config_path", lambda: path)
    assert config.load_settings() == {"backend": "gemini"}
