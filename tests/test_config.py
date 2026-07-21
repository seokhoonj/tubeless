"""Key resolution: config-file parsing and the environment-over-file order.

No network and no real ~/.tubeless/config.env -- every lookup is pointed at a
temp file or an injected dict so the tests are hermetic.
"""

import pytest

from tubeless import config
from tubeless.errors import ConfigError


def _write(tmp_path, text):
    path = tmp_path / "config.env"
    path.write_text(text, encoding="utf-8")
    return path


def test_read_config_skips_blanks_comments_and_strips_quotes(tmp_path):
    path = _write(
        tmp_path,
        '\n'
        '# a comment\n'
        'OPENAI_API_KEY = "sk-quoted"  \n'
        "CLAUDE_API_KEY='sk-claude'\n"
        "not a key line\n",
    )
    values = config.read_config(path)
    assert values == {"OPENAI_API_KEY": "sk-quoted", "CLAUDE_API_KEY": "sk-claude"}


def test_read_config_missing_file_is_empty(tmp_path):
    assert config.read_config(tmp_path / "nope.env") == {}


def test_read_config_raises_on_an_unreadable_file(tmp_path):
    # a directory at the path: exists() is true, read_text raises OSError -> ConfigError,
    # not a bare traceback out of the CLI.
    unreadable = tmp_path / "config.env"
    unreadable.mkdir()
    with pytest.raises(ConfigError):
        config.read_config(unreadable)


def test_read_config_raises_on_a_non_utf8_file(tmp_path):
    path = tmp_path / "config.env"
    path.write_bytes(b"\xff\xfe\x00not utf-8")
    with pytest.raises(ConfigError):
        config.read_config(path)


def test_api_key_reads_the_key_from_the_config_dict(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert config.api_key("openai", config={"OPENAI_API_KEY": "sk-file"}) == "sk-file"


def test_api_key_env_overrides_the_config_file(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    assert config.api_key("openai", config={"OPENAI_API_KEY": "sk-file"}) == "sk-env"


def test_api_key_reads_the_claude_key(monkeypatch):
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    assert config.api_key("claude", config={"CLAUDE_API_KEY": "sk-claude"}) == "sk-claude"


def test_api_key_returns_none_when_absent(monkeypatch):
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    assert config.api_key("claude", config={}) is None


def test_api_key_resolves_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert config.api_key("gemini", config={"GEMINI_API_KEY": "sk-gem"}) == "sk-gem"


def test_setting_reads_from_the_config_file(monkeypatch):
    monkeypatch.delenv("TUBELESS_BACKEND", raising=False)
    assert config.setting("TUBELESS_BACKEND", config={"TUBELESS_BACKEND": "gemini"}) == "gemini"


def test_setting_env_overrides_the_config_file(monkeypatch):
    monkeypatch.setenv("TUBELESS_BACKEND", "ollama")
    assert config.setting("TUBELESS_BACKEND", config={"TUBELESS_BACKEND": "gemini"}) == "ollama"


def test_setting_returns_none_when_absent(monkeypatch):
    monkeypatch.delenv("TUBELESS_BACKEND", raising=False)
    assert config.setting("TUBELESS_BACKEND", config={}) is None
