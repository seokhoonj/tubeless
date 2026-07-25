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


def test_data_dir_defaults_to_xdg_data_home(monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert config.data_dir() == Path.home() / ".local" / "share" / "tubeless"


def test_data_dir_respects_xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert config.data_dir() == tmp_path / "tubeless"


def test_state_dir_defaults_to_xdg_state_home(monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert config.state_dir() == Path.home() / ".local" / "state" / "tubeless"


def test_state_dir_respects_xdg_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert config.state_dir() == tmp_path / "tubeless"


def test_the_three_bases_are_distinct(monkeypatch):
    # config vs data vs state must not collapse to one directory: the whole point
    # of 0.3.0 is that a settings reset never touches the corpus.
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)
    bases = {config.config_dir(), config.data_dir(), config.state_dir()}
    assert len(bases) == 3


def _xdg_layout(tmp_path, monkeypatch) -> tuple[Path, Path, Path]:
    """Point the three bases at separate temp roots and return their tubeless dirs."""
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


def test_migrate_relocates_config_files_when_config_dir_itself_moved(tmp_path, monkeypatch):
    # The macOS/Windows upgrade: 0.2.0 hand-rolled the XDG path even there, so it
    # kept everything (including the config files) in ~/.config/tubeless, but 0.3.0's
    # config_dir() is a native location elsewhere. The legacy source must be the old
    # ~/.config formula, and the config files must move to the new config dir -- not
    # be orphaned. Simulated by pointing config_dir() somewhere other than XDG_CONFIG_HOME.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "dotconfig"))   # drives the legacy root
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    native_config = tmp_path / "native" / "tubeless"
    monkeypatch.setattr(config, "config_dir", lambda: native_config)

    legacy = tmp_path / "dotconfig" / "tubeless"
    legacy.mkdir(parents=True)
    (legacy / "config.toml").write_text('backend = "gemini"\n', encoding="utf-8")
    (legacy / "credentials.json").write_text("{}", encoding="utf-8")
    (legacy / "channels.toml").write_text("", encoding="utf-8")
    (legacy / "corpus").mkdir()
    (legacy / "corpus" / "s.json").write_text("{}", encoding="utf-8")

    config.migrate_legacy_layout()

    # config files land in the native config dir, not orphaned in ~/.config
    assert (native_config / "config.toml").read_text(encoding="utf-8") == 'backend = "gemini"\n'
    assert (native_config / "credentials.json").exists()
    assert (native_config / "channels.toml").exists()
    assert not (legacy / "config.toml").exists()
    # and data still goes to the data dir
    assert (tmp_path / "data" / "tubeless" / "corpus" / "s.json").exists()


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
