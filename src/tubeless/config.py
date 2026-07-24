"""tubeless's non-secret settings, and the directory they share with secrets.

Settings -- which backend, which model, the digest defaults -- are not secrets,
so they live in a plain, hand-editable TOML file, ``~/.config/tubeless/config.toml``.
The API keys and the proxy credentials live apart, in ``credentials`` (a 0600 JSON
file), so a settings file that is safe to read is never where a key can leak.

``config_dir()`` is the one place that resolves the shared XDG directory: every
other module (state, corpus, digests, channels, logs, credentials) hangs its own
file off the same root rather than re-deriving the path.

A setting is read from the environment first (``TUBELESS_BACKEND`` ...), then the
file, so a one-off ``TUBELESS_BACKEND=gemini tubeless ...`` overrides it without
editing anything.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from tubeless.errors import ConfigError

__all__ = ["config_dir", "config_path", "load_settings", "setting"]


def config_dir() -> Path:
    """tubeless's machine-local directory, following the XDG base spec.

    ``$XDG_CONFIG_HOME/tubeless`` when that variable is set, else
    ``~/.config/tubeless``. One directory holds everything tubeless owns on the
    machine -- ``config.toml``, ``credentials.json``, ``channels.toml``, saved
    state, the transcript corpus, the digest output, and logs -- and git never
    tracks it.
    """
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "tubeless"


def config_path() -> Path:
    """Where the non-secret settings live: ``config.toml`` in ``config_dir()``."""
    return config_dir() / "config.toml"


def load_settings(path: Path | None = None) -> dict[str, object]:
    """Parse ``config.toml`` into a dict; empty when the file is absent.

    Raises:
        ConfigError: the file exists but is not readable TOML -- surfaced as a
            one-line CLI error, not a traceback.
    """
    path = path or config_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as err:
        # tomllib decodes UTF-8 internally, so a non-UTF-8 file raises
        # UnicodeDecodeError (a ValueError, not an OSError) -- name it explicitly
        # or it escapes this boundary as a bare traceback (as credentials._load does).
        raise ConfigError(f"could not read config file {path}: {err}") from err


def setting(name: str) -> str | None:
    """Return a setting from the environment (which wins) or ``config.toml``.

    ``name`` is the environment-variable spelling (``TUBELESS_LANG``); in the file
    it is the same key without the ``TUBELESS_`` prefix, lower-cased (``lang``),
    since the file already namespaces it. An absent key or an empty string reads as
    absent (``None``) -- the same on both sides, so an empty field falls back to
    the default instead of overriding it. Any other TOML scalar is stringified with
    ``str()`` (a number including ``0``, a bool as ``"True"``/``"False"``): matching
    the environment, where a caller that needs the int (``max_points``) parses the
    returned digits and an out-of-range ``0`` still reaches its "must be positive" error.
    """
    from_env = os.environ.get(name)
    if from_env:
        return from_env
    value = load_settings().get(name.removeprefix("TUBELESS_").lower())
    return None if value is None or value == "" else str(value)
