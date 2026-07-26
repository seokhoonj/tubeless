"""Where tubeless keeps its secrets.

The LLM API keys -- and the transcript proxy's credentials -- are the values that
let a run speak to a paid service. They live in a file only their owner can read,
under the XDG config directory, well outside any checkout that gets synced to a
cloud drive or committed by accident. This is the shape ``.netrc`` and the cloud
CLIs' credential files take, chosen for the reason they chose it: it never
prompts, so a cron job or an agent session works the same as a terminal.

JSON, not the TOML the settings use: it is the same store the sibling packages
keep their secrets in, and a flat ``name -> value`` map with no comments or types
is all a secret file needs. The keys are the same names the environment uses
(``OPENAI_API_KEY`` ...), so one workflow overrides the other.

The file is not encrypted -- the ``0600`` mode guards against other users on the
machine, not against anything running as you. What limits the damage is the
secret itself: an API key is revocable at the vendor without touching anything
else. Store nothing else here.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Literal

from tubeless.config import config_dir
from tubeless.errors import CredentialsError, InsecureCredentialsError

__all__ = ["Vendor", "api_key", "credentials_path", "legacy_config_note", "secret"]

# The vendors tubeless resolves a key for. Closed set: a typo is a static error,
# not a runtime KeyError against the map below.
Vendor = Literal["claude", "openai", "gemini"]

# The secret name each vendor's key is stored under -- the backend name plus the
# shared ``_API_KEY`` suffix, so the name always matches ``--backend`` and reads
# the same whether it comes from the file or the environment.
_KEY_NAME: dict[Vendor, str] = {
    "claude": "CLAUDE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def credentials_path() -> Path:
    """Where tubeless looks for stored secrets: ``credentials.json`` beside the
    settings, in ``config_dir()``."""
    return config_dir() / "credentials.json"


def legacy_config_note() -> str:
    """A migration hint if the pre-0.2 ``~/.tubeless/config.env`` still exists, else ``""``.

    The 0.2 redesign moved keys and settings out of that single file into
    ``credentials.json`` and ``config.toml`` under the XDG config dir. An
    upgrading user whose keys are still in the old file finds no key at all, so
    the missing-key error appends this to point them at the move rather than leave
    them to guess why the upgrade dropped their config.

    Returns ``""`` (no hint) if the home directory cannot be resolved -- a defensive
    branch, reached only if ``Path.home()`` fails while this hint is being built for the
    missing-key error; the hint must not raise and replace that error.
    """
    try:
        home = Path.home()
    except RuntimeError:
        return ""   # a hint builder must not raise -- no home just means no hint to add
    legacy = home / ".tubeless" / "config.env"
    if not legacy.exists():
        return ""
    return (
        f"; a pre-0.2 {legacy} still exists -- its API keys move to "
        f"{credentials_path()} and its TUBELESS_* settings to config.toml"
    )


def api_key(vendor: Vendor) -> str | None:
    """Return the API key for ``vendor`` ('openai' / 'claude' / 'gemini'), or
    ``None`` when neither the environment nor the credentials file has it -- so a
    backend can phrase its own "no key" error."""
    return secret(_KEY_NAME[vendor])


def secret(name: str) -> str | None:
    """Return the named secret from the environment (which wins) or the
    credentials file, or ``None`` when neither has it.

    The environment is checked first so a one-off or a container can supply a
    secret without a file. Reading a credentials file that other users can reach
    raises rather than trusting it -- a secret behind loose permissions is the
    failure this exists to catch -- but an absent file is simply "no secret".

    Raises:
        InsecureCredentialsError: the file is readable beyond its owner.
        CredentialsError: the file exists but is not the JSON name-to-secret map.
    """
    from_env = os.environ.get(name)
    if from_env:
        return from_env
    path = credentials_path()
    if not path.exists():
        return None
    _require_owner_only_readable(path)
    return _load(path).get(name) or None


def _load(path: Path) -> dict[str, str]:
    """Read ``credentials.json`` as a name-to-secret map.

    Raises CredentialsError when the file cannot be read or is not a JSON object
    whose values are all strings -- so a malformed store surfaces one clear error
    rather than a stray secret going missing silently.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        # UnicodeDecodeError is a ValueError, not an OSError, so it must be named
        # explicitly or a non-UTF-8 file would escape this boundary.
        raise CredentialsError(f"cannot read {path}: {err}") from err
    try:
        stored = json.loads(text)
    except json.JSONDecodeError as err:
        raise CredentialsError(f"{path} is not valid JSON: {err}") from err
    if not isinstance(stored, dict) or not all(
        isinstance(value, str) for value in stored.values()
    ):
        raise CredentialsError(f"{path} should map each secret name to its value")
    return stored


def _require_owner_only_readable(path: Path) -> None:
    """Refuse a credentials file that other users can read.

    POSIX only, because the mode is only real there: Windows synthesises
    ``st_mode`` from the read-only attribute alone, so this test would match every
    file and send the reader off to run a ``chmod`` that Windows does not have.
    What guards the file there is the ACL on the user's profile directory.
    """
    if os.name != "posix":
        return
    try:
        mode = path.stat().st_mode
    except OSError as err:
        raise CredentialsError(f"cannot check permissions on {path}: {err}") from err
    if not (mode & (stat.S_IRWXG | stat.S_IRWXO)):
        return
    raise InsecureCredentialsError(
        f"{path} is readable by more than its owner; secrets must not be. "
        f"Fix it with: chmod 600 {path}"
    )
