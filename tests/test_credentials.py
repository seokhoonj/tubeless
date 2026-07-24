"""Secret resolution: credentials.json, env-over-file order, and the owner-only
permission gate. No real ~/.config/tubeless/credentials.json is ever touched.
"""

import json
from pathlib import Path

import pytest

from tubeless import credentials
from tubeless.errors import CredentialsError, InsecureCredentialsError


@pytest.fixture(autouse=True)
def creds_path(tmp_path, monkeypatch):
    """Point credentials_path at a temp file so no real secrets file is read --
    autouse, so a test that calls secret() without requesting it still cannot
    fall through to the real ~/.config/tubeless/credentials.json."""
    path = tmp_path / "credentials.json"
    monkeypatch.setattr(credentials, "credentials_path", lambda: path)
    return path


def _write_secure(path: Path, secrets: dict) -> None:
    path.write_text(json.dumps(secrets), encoding="utf-8")
    path.chmod(0o600)


def test_secret_reads_from_the_file(creds_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _write_secure(creds_path, {"OPENAI_API_KEY": "sk-file"})
    assert credentials.secret("OPENAI_API_KEY") == "sk-file"


def test_secret_env_overrides_the_file(creds_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    _write_secure(creds_path, {"OPENAI_API_KEY": "sk-file"})
    assert credentials.secret("OPENAI_API_KEY") == "sk-env"


def test_secret_is_none_when_the_file_is_absent(creds_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert credentials.secret("OPENAI_API_KEY") is None


def test_secret_is_none_when_the_key_is_absent(creds_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _write_secure(creds_path, {"OPENAI_API_KEY": "sk-file"})
    assert credentials.secret("GEMINI_API_KEY") is None


@pytest.mark.parametrize(
    ("vendor", "key_name"),
    [("claude", "CLAUDE_API_KEY"), ("openai", "OPENAI_API_KEY"), ("gemini", "GEMINI_API_KEY")],
)
def test_api_key_maps_each_vendor_to_its_secret_name(creds_path, monkeypatch, vendor, key_name):
    # Pins every row of _KEY_NAME, so a wrong entry for one vendor cannot ship green.
    monkeypatch.delenv(key_name, raising=False)
    _write_secure(creds_path, {key_name: "sk"})
    assert credentials.api_key(vendor) == "sk"


def test_secret_empty_env_does_not_override_the_file(creds_path, monkeypatch):
    # An exported-but-empty var must fall through to the file, not shadow it with "".
    monkeypatch.setenv("OPENAI_API_KEY", "")
    _write_secure(creds_path, {"OPENAI_API_KEY": "sk-file"})
    assert credentials.secret("OPENAI_API_KEY") == "sk-file"


def test_secret_empty_stored_value_is_absent(creds_path, monkeypatch):
    # A stored empty string passes _load's all-strings check but reads as absent
    # (the `.get(name) or None` collapse), so a caller falls back rather than
    # authenticating with "".
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _write_secure(creds_path, {"OPENAI_API_KEY": ""})
    assert credentials.secret("OPENAI_API_KEY") is None


def test_loose_permissions_are_refused(creds_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    creds_path.write_text(json.dumps({"OPENAI_API_KEY": "sk"}), encoding="utf-8")
    creds_path.chmod(0o644)
    with pytest.raises(InsecureCredentialsError):
        credentials.secret("OPENAI_API_KEY")


def test_windows_skips_the_permission_gate(creds_path, monkeypatch):
    # A 0644 file is refused on POSIX; on Windows (os.stat has no real mode bits)
    # the gate must tolerate it -- proving the skip on a file that would otherwise
    # be rejected, not merely on a nonexistent path.
    creds_path.write_text("{}", encoding="utf-8")
    creds_path.chmod(0o644)
    monkeypatch.setattr(credentials.os, "name", "nt")
    credentials._require_owner_only_readable(creds_path)  # must not raise


def test_malformed_json_raises(creds_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    creds_path.write_text("{not json", encoding="utf-8")
    creds_path.chmod(0o600)
    with pytest.raises(CredentialsError):
        credentials.secret("OPENAI_API_KEY")


def test_non_utf8_file_raises_credentials_error(creds_path, monkeypatch):
    # The read arm catches UnicodeDecodeError (a ValueError, not an OSError)
    # explicitly; a non-UTF-8 file must surface as CredentialsError, not a raw
    # traceback. Without this test, dropping UnicodeDecodeError from the except
    # tuple would stay green.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    creds_path.write_bytes(b"\xff\xfe not utf-8")
    creds_path.chmod(0o600)
    with pytest.raises(CredentialsError):
        credentials.secret("OPENAI_API_KEY")


def test_non_object_json_raises(creds_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    creds_path.write_text(json.dumps(["not", "a", "map"]), encoding="utf-8")
    creds_path.chmod(0o600)
    with pytest.raises(CredentialsError):
        credentials.secret("OPENAI_API_KEY")


def test_legacy_config_note_is_empty_when_the_old_file_is_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert credentials.legacy_config_note() == ""


def test_legacy_config_note_points_at_the_move_when_the_old_file_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy_dir = tmp_path / ".tubeless"
    legacy_dir.mkdir()
    (legacy_dir / "config.env").write_text("OPENAI_API_KEY=x\n", encoding="utf-8")
    note = credentials.legacy_config_note()
    assert ".tubeless" in note and "credentials.json" in note
