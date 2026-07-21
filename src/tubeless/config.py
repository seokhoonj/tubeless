"""Where tubeless finds its API keys.

Keys are secrets, so they never live in the repo: they sit in
``~/.tubeless/config.env`` (``KEY=VALUE`` lines) or in the process environment.
This module is the single place that knows those names, which keeps the backends
free of any file or environment lookup -- they ask for ``api_key(vendor)`` and
get a string or ``None``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from tubeless.errors import ConfigError

__all__ = ["CONFIG_PATH", "Vendor", "api_key", "read_config", "setting"]

CONFIG_PATH = Path.home() / ".tubeless" / "config.env"

# The vendors tubeless resolves a key for. Closed set: a typo is a static error,
# not a runtime KeyError against the maps below.
Vendor = Literal["claude", "openai", "gemini"]

# The env-var (or config.env) name tubeless reads for each vendor's key: the
# backend name plus the shared `_API_KEY` suffix, so the key name always matches
# `--backend`. (A Claude key comes from the Claude console, platform.claude.com.)
_KEY_NAME = {
    "claude": "CLAUDE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def read_config(path: Path | None = None) -> dict[str, str]:
    """Parse ``~/.tubeless/config.env`` into a dict; empty if the file is absent.

    Each line is ``KEY=VALUE``; blank lines and ``#`` comments are skipped and
    surrounding quotes on the value are stripped. This is a hand-written key file,
    not a full dotenv document, so the parser stays deliberately small.

    Raises:
        ConfigError: the file exists but could not be read (I/O error or not
            UTF-8) -- surfaced as a one-line CLI error, not a traceback.
    """
    path = path or CONFIG_PATH
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        raise ConfigError(f"could not read config file {path}: {err}") from err
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def api_key(vendor: Vendor, *, config: dict[str, str] | None = None) -> str | None:
    """Return the API key for ``vendor`` ('openai' / 'claude' / 'gemini'), or ``None``.

    The name (``<BACKEND>_API_KEY``) is looked up in the environment first, then
    in the config file, so a single run can override the file.
    """
    values = read_config() if config is None else config
    name = _KEY_NAME[vendor]
    return os.environ.get(name) or values.get(name) or None


def setting(name: str, *, config: dict[str, str] | None = None) -> str | None:
    """Return an optional tubeless setting from the environment or config file
    (environment wins), or ``None``.

    Used for CLI defaults such as ``TUBELESS_BACKEND`` -- putting
    ``TUBELESS_BACKEND=gemini`` in ``~/.tubeless/config.env`` makes a bare
    ``tubeless <url>`` use Gemini, so a non-OpenAI user need not pass
    ``--backend`` every time (an explicit flag still overrides it).
    """
    values = read_config() if config is None else config
    return os.environ.get(name) or values.get(name)
