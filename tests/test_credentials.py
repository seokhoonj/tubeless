"""Secret resolution: credentials.json, env-over-file order, embedding, and the
loose-permission warn-and-read posture. No real ~/.config/tubeless/credentials.json
is ever touched -- XDG_CONFIG_HOME points the store at a temp file.
"""

import json
import os

import pytest

from tubeless import credentials
from tubeless.errors import CredentialsError


@pytest.fixture(autouse=True)
def isolated_credentials_path(tmp_path, monkeypatch):
    """Point credbox's store at a temp XDG config home and clear the vendor env vars,
    so no real secret file is read and a dev's shell key cannot leak in."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for name in ("OPENAI_API_KEY", "CLAUDE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "tubeless" / "credentials.json"


def _write_credentials(path, secrets: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(secrets), encoding="utf-8")
    path.chmod(mode)


def test_secret_reads_from_the_file(isolated_credentials_path):
    _write_credentials(isolated_credentials_path, {"OPENAI_API_KEY": "sk-file"})
    assert credentials.secret("OPENAI_API_KEY") == "sk-file"


def test_secret_env_overrides_the_file(isolated_credentials_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    _write_credentials(isolated_credentials_path, {"OPENAI_API_KEY": "sk-file"})
    assert credentials.secret("OPENAI_API_KEY") == "sk-env"


def test_secret_is_none_when_the_file_is_absent(isolated_credentials_path):
    assert credentials.secret("OPENAI_API_KEY") is None


def test_secret_is_none_when_the_key_is_absent(isolated_credentials_path):
    _write_credentials(isolated_credentials_path, {"OPENAI_API_KEY": "sk-file"})
    assert credentials.secret("GEMINI_API_KEY") is None


@pytest.mark.parametrize(
    ("vendor", "key_name"),
    [("claude", "CLAUDE_API_KEY"), ("openai", "OPENAI_API_KEY"), ("gemini", "GEMINI_API_KEY")],
)
def test_api_key_maps_each_vendor_to_its_secret_name(isolated_credentials_path, vendor, key_name):
    # Pins every row of _KEY_NAME, so a wrong entry for one vendor cannot ship green.
    _write_credentials(isolated_credentials_path, {key_name: "sk"})
    assert credentials.api_key(vendor) == "sk"


def test_secret_empty_env_does_not_override_the_file(isolated_credentials_path, monkeypatch):
    # An exported-but-empty var must fall through to the file, not shadow it with "".
    monkeypatch.setenv("OPENAI_API_KEY", "")
    _write_credentials(isolated_credentials_path, {"OPENAI_API_KEY": "sk-file"})
    assert credentials.secret("OPENAI_API_KEY") == "sk-file"


def test_secret_empty_stored_value_is_absent(isolated_credentials_path):
    # A blank stored value reads as absent (credbox treats blank as absent), so a caller
    # falls back rather than authenticating with "".
    _write_credentials(isolated_credentials_path, {"OPENAI_API_KEY": ""})
    assert credentials.secret("OPENAI_API_KEY") is None


def test_secret_is_trimmed(isolated_credentials_path):
    # credbox strips surrounding whitespace, so a pasted trailing newline no longer breaks
    # auth; pinned so a future change cannot silently return padding.
    _write_credentials(isolated_credentials_path, {"OPENAI_API_KEY": "  sk-file  "})
    assert credentials.secret("OPENAI_API_KEY") == "sk-file"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits required")
def test_loose_permissions_warn_and_still_read(isolated_credentials_path, capsys):
    # A group/other-readable file is warned about (chmod 600 nudge), not refused --
    # the fleet-standard credbox posture (the old hard refusal was dropped).
    _write_credentials(isolated_credentials_path, {"OPENAI_API_KEY": "sk"}, mode=0o644)
    assert credentials.secret("OPENAI_API_KEY") == "sk"
    assert "chmod 600" in capsys.readouterr().err


@pytest.mark.parametrize("contents", ["{not json", '["not", "a", "map"]'])
def test_malformed_credentials_file_raises(isolated_credentials_path, contents):
    isolated_credentials_path.parent.mkdir(parents=True, exist_ok=True)
    isolated_credentials_path.write_text(contents, encoding="utf-8")
    with pytest.raises(CredentialsError, match="could not read"):
        credentials.secret("OPENAI_API_KEY")


def test_non_string_value_raises(isolated_credentials_path):
    # credbox validates the whole store, so a non-string value is rejected, not skipped.
    _write_credentials(isolated_credentials_path, {"OPENAI_API_KEY": "sk"})
    isolated_credentials_path.write_text(json.dumps({"OPENAI_API_KEY": 123}), encoding="utf-8")
    with pytest.raises(CredentialsError, match="could not read"):
        credentials.secret("OPENAI_API_KEY")


def test_non_utf8_file_raises(isolated_credentials_path):
    isolated_credentials_path.parent.mkdir(parents=True, exist_ok=True)
    isolated_credentials_path.write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(CredentialsError, match="could not read"):
        credentials.secret("OPENAI_API_KEY")


def test_malformed_store_never_leaks_another_secret_it_holds(isolated_credentials_path):
    # A malformed store can hold a real secret; the CredentialsError (and its chain) must
    # not surface it -- credbox detaches secret-bearing content.
    secret = "sk-secret-value-abcdef"
    isolated_credentials_path.parent.mkdir(parents=True, exist_ok=True)
    # invalid UTF-8 carrying the secret
    isolated_credentials_path.write_bytes(secret.encode() + b"\xff")
    with pytest.raises(CredentialsError) as caught:
        credentials.secret("OPENAI_API_KEY")
    seen: set[int] = set()
    pending: list[BaseException | None] = [caught.value]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        assert secret not in str(current)
        assert secret not in repr(current)
        pending.extend([current.__cause__, current.__context__])


def test_store_binding_redirects_to_a_host_namespace(
    isolated_credentials_path, tmp_path, monkeypatch
):
    # A host embedding tubeless redirects the store via TUBELESS_STORE_APP +
    # TUBELESS_NAMESPACE, so tubeless's key lives in the host's store under a section.
    monkeypatch.setenv("TUBELESS_STORE_APP", "host")
    monkeypatch.setenv("TUBELESS_NAMESPACE", "tubeless")
    host = tmp_path / "host"
    host.mkdir(parents=True)
    (host / "credentials.json").write_text(
        json.dumps({"tubeless": {"OPENAI_API_KEY": "sk-host"}}), encoding="utf-8")
    assert credentials.secret("OPENAI_API_KEY") == "sk-host"


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


def test_legacy_config_note_survives_no_home_directory(monkeypatch):
    from pathlib import Path

    def no_home():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    assert credentials.legacy_config_note() == ""
