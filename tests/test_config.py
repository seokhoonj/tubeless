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
