"""The set of channels the user follows, read from ``~/.tubeless/channels.toml``.

Each entry names where to look (a handle, URL, id, or playlist), a label for the
digest, how deeply to summarize, and optionally a title filter to keep only some
of the source's uploads. This module only reads config; resolving a handle to an
id and fetching uploads is ``feed.py``'s job.

Example ``channels.toml``::

    [[channel]]
    source = "@examplechannel"   # a handle, channel URL, 'UC...' id, or playlist
    label  = "Example Channel"
    detail = "deep"

    [[channel]]
    # a playlist narrows a channel to one series; title_includes narrows it
    # further to uploads whose title contains every listed word (e.g. one host).
    source         = "PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    label          = "A Daily Show"
    detail         = "deep"
    title_includes = ["Some Host"]
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from tubeless.errors import ConfigError
from tubeless.summary import DETAIL_LEVELS, DetailLevel

__all__ = ["CHANNELS_PATH", "Channel", "load_channels"]

CHANNELS_PATH = Path.home() / ".tubeless" / "channels.toml"


@dataclass(frozen=True, slots=True)
class Channel:
    """One followed channel and how to summarize it. ``source`` is whatever the
    user wrote (handle / URL / id / playlist); ``feed.fetch_uploads`` resolves it
    at digest time. ``title_includes`` keeps only uploads whose title contains
    every listed word (case-insensitive) -- empty means keep all. ``preset`` is
    reserved for a future domain profile and is unused by the neutral core."""

    source:         str
    label:          str
    detail:         DetailLevel = "deep"
    preset:         str | None = None
    title_includes: tuple[str, ...] = ()


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
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as err:
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
    if detail not in DETAIL_LEVELS:
        raise ConfigError(
            f"channel {source!r}: detail must be one of {DETAIL_LEVELS}, got {detail!r}"
        )
    raw_filter = entry.get("title_includes", ())
    if isinstance(raw_filter, str):   # a bare string is a one-word filter
        raw_filter = [raw_filter]
    elif not isinstance(raw_filter, (list, tuple)):
        raise ConfigError(
            f"channel {source!r}: title_includes must be a string or a list, "
            f"got {type(raw_filter).__name__}"
        )
    return Channel(
        source         = source,
        label          = entry.get("label") or source,
        detail         = detail,
        preset         = entry.get("preset"),
        title_includes = tuple(str(word) for word in raw_filter),
    )
