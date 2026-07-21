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

__all__ = ["CONFIG_PATH", "Vendor", "api_key", "read_config"]

CONFIG_PATH = Path.home() / ".tubeless" / "config.env"

# The vendors tubeless resolves a key for. Closed set: a typo is a static error,
# not a runtime KeyError against the maps below.
Vendor = Literal["openai", "anthropic", "gemini"]

# The env-var name that holds each vendor's key. "SECRET_KEY" mirrors the wording
# on OpenAI's own key page ("secret key") and gives every vendor the same shape,
# <VENDOR>_SECRET_KEY, so a new backend's name is predictable.
_KEY_NAME = {
    "openai":    "OPENAI_SECRET_KEY",
    "anthropic": "ANTHROPIC_SECRET_KEY",
    "gemini":    "GEMINI_SECRET_KEY",
}
# The SDK-standard name each vendor's own client reads. Tried after the tubeless
# name so a machine that already exports OPENAI_API_KEY keeps working untouched.
_SDK_NAME = {
    "openai":    "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini":    "GEMINI_API_KEY",
}


def read_config(path: Path | None = None) -> dict[str, str]:
    """Parse ``~/.tubeless/config.env`` into a dict; empty if the file is absent.

    Each line is ``KEY=VALUE``; blank lines and ``#`` comments are skipped and
    surrounding quotes on the value are stripped. This is a hand-written key file,
    not a full dotenv document, so the parser stays deliberately small.
    """
    path = path or CONFIG_PATH
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def api_key(vendor: Vendor, *, config: dict[str, str] | None = None) -> str | None:
    """Return the API key for ``vendor`` ('openai' / 'anthropic' / 'gemini'), or ``None``.

    Each name is looked up in the environment first, then in the config file, so a
    single run can override the file. The tubeless name (``<VENDOR>_SECRET_KEY``)
    wins over the SDK-standard name (``<VENDOR>_API_KEY``); the latter is a
    fallback for a machine that already has the standard variable set.
    """
    values = read_config() if config is None else config
    for name in (_KEY_NAME[vendor], _SDK_NAME[vendor]):
        found = os.environ.get(name) or values.get(name)
        if found:
            return found
    return None
