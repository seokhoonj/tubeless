"""The set of channels the user follows, read from ``~/.tubeless/channels.toml``.

Each entry names where to look (a handle, URL, id, or playlist), how deeply to
summarize, and optionally a title filter to keep only some of the source's
uploads. This module only reads config; resolving a source and listing its
recent videos is ``discover.py``'s job.

Example ``channels.toml``::

    [[channel]]
    source = "@examplechannel"   # a handle, channel URL, 'UC...' id, or playlist
    detail = "deep"

    [[channel]]
    # a playlist narrows a channel to one series; includes narrows it further to
    # uploads whose title contains every listed word (e.g. one host). excludes
    # drops uploads carrying any listed word -- e.g. a channel that posts a "LIVE"
    # broadcast and an edited replay of the same episode.
    source   = "PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    detail   = "deep"
    includes = ["Some Host"]
    excludes = ["LIVE"]
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
    user wrote (handle / URL / id / playlist); ``discover`` resolves it at digest
    time. ``includes`` keeps only uploads whose title contains every listed word
    (case-insensitive) -- empty means keep all. ``excludes`` then drops any upload
    whose title contains any listed word (e.g. ``"LIVE"`` to skip a live broadcast
    kept alongside its edited replay) -- empty means drop none."""

    source:   str
    detail:   DetailLevel = "deep"
    includes: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()


def load_channels(path: Path | None = None) -> tuple[Channel, ...]:
    """Read the followed-channels list from ``path`` (default CHANNELS_PATH).

    Raises:
        ConfigError: the file is missing, unreadable, or has no valid entries.
    """
    path = path or CHANNELS_PATH
    if not path.exists():
        raise ConfigError(
            f"no channels file at {path}; create it with [[channel]] entries "
            "(each needs a source)"
        )
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as err:
        raise ConfigError(f"could not read {path}: {err}") from err

    entries = parsed.get("channel")
    if not entries:
        raise ConfigError(f"{path} has no [[channel]] entries")
    return tuple(_channel_from(entry, path) for entry in entries)


def _channel_from(entry: dict[str, object], path: Path) -> Channel:
    source = entry.get("source") or entry.get("handle") or entry.get("channel_id")
    if not source:
        raise ConfigError(f"a [[channel]] in {path} is missing 'source' (a handle, URL, or id)")
    detail = entry.get("detail", "deep")
    if detail not in DETAIL_LEVELS:
        raise ConfigError(
            f"channel {source!r}: detail must be one of {DETAIL_LEVELS}, got {detail!r}"
        )
    return Channel(
        source   = source,
        detail   = detail,
        includes = _keywords(entry.get("includes", ()), "includes", source),
        excludes = _keywords(entry.get("excludes", ()), "excludes", source),
    )


def _keywords(raw: object, field: str, source: object) -> tuple[str, ...]:
    """Normalise a title-filter field to a tuple: a bare string is a one-word
    filter, a list/tuple is taken as-is; anything else is a config error."""
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, (list, tuple)):
        raise ConfigError(
            f"channel {source!r}: {field} must be a string or a list, "
            f"got {type(raw).__name__}"
        )
    return tuple(str(word) for word in raw)
