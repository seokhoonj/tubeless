"""Where tubeless keeps its secrets.

The LLM API keys -- and the transcript proxy's credentials -- are the values that
let a run speak to a paid service. They live in a file only their owner should
read, under the XDG config directory, well outside any checkout that gets synced
to a cloud drive or committed by accident. This is the shape ``.netrc`` and the
cloud CLIs' credential files take, chosen for the reason they chose it: it never
prompts, so a cron job or an agent session works the same as a terminal.

The store is delegated to credbox: a flat ``name -> value`` JSON map at
``$XDG_CONFIG_HOME/tubeless/credentials.json`` -- the same file, byte for byte,
tubeless read before, and the same shape the sibling packages keep their secrets
in. The keys are the same names the environment uses (``OPENAI_API_KEY`` ...), so
one workflow overrides the other, and credbox checks the environment first.

The file is not encrypted -- the ``0600`` mode guards against other users on the
machine, not against anything running as you. A group/other-readable file is
warned about (a ``chmod 600`` nudge) and still read, the fleet-standard posture;
what limits the damage is the secret itself, an API key revocable at the vendor
without touching anything else. Store nothing else here.

The store binding is not hardcoded: ``Credentials.for_app("tubeless")`` lets a
host embedding tubeless redirect it via ``TUBELESS_STORE_APP`` /
``TUBELESS_NAMESPACE``; standalone it is exactly the flat file above.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from credbox import CredBoxError, Credentials

from tubeless.config import config_dir
from tubeless.errors import CredentialsError

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

_STORE_APP = "tubeless"


def credentials_path() -> Path:
    """Where tubeless's secrets live standalone: ``credentials.json`` beside the
    settings, in ``config_dir()``. Not redirect-aware -- under a ``TUBELESS_STORE_APP``
    redirect the real store differs; this is the path the missing-key hint points at."""
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

    The environment is checked first (by credbox) so a one-off or a container can
    supply a secret without a file; a blank value is treated as absent.

    Raises:
        CredentialsError: the credential store cannot be read -- an unreadable or
            invalid file (not valid UTF-8, not JSON, or not the name-to-secret map
            it must be) or an invalid ``TUBELESS_STORE_APP`` / ``TUBELESS_NAMESPACE``
            binding.
    """
    try:
        found = _get_credentials().secret(name)
    except CredBoxError as err:
        # credbox's message names the store path + fault; it detaches secret-bearing
        # context, so chaining `from err` keeps other secrets out of any traceback.
        raise CredentialsError(f"could not read the credential store: {err}") from err
    return found.reveal() if found is not None else None


@lru_cache(maxsize=1)
def _get_credentials() -> Credentials:
    """tubeless's credbox credential store, built on first use and cached.

    Built via ``for_app`` (not the bare ``Credentials(...)``) so a host embedding
    tubeless can redirect the binding with ``TUBELESS_STORE_APP`` /
    ``TUBELESS_NAMESPACE`` before the first lookup. credbox re-resolves the store
    *path* per call (honouring a later ``XDG_CONFIG_HOME``); the binding is read from
    the environment once, when this facade is built.
    """
    return Credentials.for_app(_STORE_APP)
