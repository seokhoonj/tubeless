"""Key resolution: file parsing and the env-over-file, tubeless-over-SDK order.

No network and no real ~/.tubeless/config.env -- every lookup is pointed at a
temp file or an injected dict so the tests are hermetic.
"""

from tubeless import config


def _write(tmp_path, text):
    path = tmp_path / "config.env"
    path.write_text(text, encoding="utf-8")
    return path


def test_read_config_skips_blanks_comments_and_strips_quotes(tmp_path):
    path = _write(
        tmp_path,
        '\n'
        '# a comment\n'
        'OPENAI_SECRET_KEY = "sk-quoted"  \n'
        "ANTHROPIC_SECRET_KEY='sk-ant'\n"
        "not a key line\n",
    )
    values = config.read_config(path)
    assert values == {"OPENAI_SECRET_KEY": "sk-quoted", "ANTHROPIC_SECRET_KEY": "sk-ant"}


def test_read_config_missing_file_is_empty(tmp_path):
    assert config.read_config(tmp_path / "nope.env") == {}


def test_api_key_reads_the_tubeless_name_from_the_config_dict(monkeypatch):
    for name in ("OPENAI_SECRET_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert config.api_key("openai", config={"OPENAI_SECRET_KEY": "sk-file"}) == "sk-file"


def test_api_key_env_overrides_the_config_file(monkeypatch):
    monkeypatch.setenv("OPENAI_SECRET_KEY", "sk-env")
    assert config.api_key("openai", config={"OPENAI_SECRET_KEY": "sk-file"}) == "sk-env"


def test_api_key_falls_back_to_the_sdk_standard_name(monkeypatch):
    monkeypatch.delenv("OPENAI_SECRET_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-sdk")
    assert config.api_key("openai", config={}) == "sk-sdk"


def test_api_key_prefers_the_tubeless_name_over_the_sdk_name(monkeypatch):
    monkeypatch.delenv("OPENAI_SECRET_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    values = {"OPENAI_SECRET_KEY": "sk-tubeless", "OPENAI_API_KEY": "sk-sdk"}
    assert config.api_key("openai", config=values) == "sk-tubeless"


def test_api_key_returns_none_when_absent(monkeypatch):
    for name in ("ANTHROPIC_SECRET_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert config.api_key("anthropic", config={}) is None


def test_api_key_resolves_gemini(monkeypatch):
    for name in ("GEMINI_SECRET_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert config.api_key("gemini", config={"GEMINI_SECRET_KEY": "sk-gem"}) == "sk-gem"
