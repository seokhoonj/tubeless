"""The set of channels the user follows, read from ``~/.tubeless/channels.toml``.

Each entry names where to look (a handle, URL, or id), a label for the digest,
and how deeply to summarize that channel. This module only reads config;
resolving a handle to an id and fetching uploads is ``feed.py``'s job.

Example ``channels.toml``::

    [[channel]]
    source = "@superstocktv"   # a handle, a channel URL, or a 'UC...' id
    label  = "수페TV"
    detail = "deep"

    [[channel]]
    source = "@somelecture"
    label  = "강의채널"
    detail = "normal"
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tomllib

from tubeless.errors import ConfigError

__all__ = ["CHANNELS_PATH", "Channel", "load_channels"]

CHANNELS_PATH = Path.home() / ".tubeless" / "channels.toml"
_VALID_DETAIL = ("brief", "normal", "deep")


@dataclass(frozen=True, slots=True)
class Channel:
    """One followed channel and how to summarize it. ``source`` is whatever the
    user wrote (handle / URL / id); ``feed.resolve_channel_id`` turns it into an
    id at digest time. ``preset`` is reserved for a future domain profile and is
    unused by the neutral core."""

    source: str
    label:  str
    detail: str = "deep"
    preset: str | None = None


def load_channels(path: Path | None = None) -> tuple[Channel, ...]:
    """Read the followed-channels list from ``path`` (default CHANNELS_PATH).

    Raises:
        ConfigError: the file is missing, unreadable, or has no valid entries.
    """
    path = path or CHANNELS_PATH
    if not path.exists():
        raise ConfigError(
            f"no channels file at {path}; create it with [[channel]] entries "
            "(each needs a source and a label)"
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as err:
        raise ConfigError(f"could not read {path}: {err}") from err

    entries = data.get("channel")
    if not entries:
        raise ConfigError(f"{path} has no [[channel]] entries")
    return tuple(_channel_from(entry, path) for entry in entries)


def _channel_from(entry: dict, path: Path) -> Channel:
    source = entry.get("source") or entry.get("handle") or entry.get("channel_id")
    if not source:
        raise ConfigError(f"a [[channel]] in {path} is missing 'source' (a handle, URL, or id)")
    detail = entry.get("detail", "deep")
    if detail not in _VALID_DETAIL:
        raise ConfigError(
            f"channel {source!r}: detail must be one of {_VALID_DETAIL}, got {detail!r}"
        )
    return Channel(
        source = source,
        label  = entry.get("label") or source,
        detail = detail,
        preset = entry.get("preset"),
    )
