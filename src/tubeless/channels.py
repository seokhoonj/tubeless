"""The set of channels the user follows, read from ``~/.config/tubeless/channels.toml``.

Each entry names where to look (a handle, URL, id, or playlist), how deeply to
summarize, and optionally a title filter to keep only some of the source's
uploads. This module only reads config; resolving a source and listing its
recent videos is ``discover.py``'s job.

Example ``channels.toml``::

    [[channel]]
    source = "@examplechannel"   # a handle, channel URL, 'UC...' id, or playlist
    detail = "deep"

    [[channel]]
    # a playlist narrows a channel to one series; title_includes narrows it
    # further to uploads whose title contains every listed word (e.g. one host).
    # title_excludes drops uploads carrying any listed word -- e.g. a channel that
    # posts a "LIVE" broadcast and an edited replay of the same episode.
    source         = "PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    detail         = "deep"
    title_includes = ["Some Host"]
    title_excludes = ["LIVE"]

The legacy keys ``includes`` / ``excludes`` are still accepted as aliases.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from tubeless.config import config_dir
from tubeless.errors import ConfigError
from tubeless.summary import DETAIL_LEVELS, DetailLevel

__all__ = ["Channel", "channels_path", "load_channels"]


def channels_path() -> Path:
    """The followed-channels file, ``channels.toml`` in ``config_dir()``. A function,
    not an import-time constant, so the base dir resolves when a command needs it (under
    the CLI's error surface), not as a side effect of import.

    Raises:
        ConfigError: no config directory can be resolved (propagated from ``config_dir``).
    """
    return config_dir() / "channels.toml"


@dataclass(frozen=True, slots=True)
class Channel:
    """One followed channel and how to summarize it. ``source`` (config key
    ``source``, or legacy ``handle`` / ``channel_id``) is whatever the user wrote
    (handle / URL / id / playlist); ``discover`` resolves it at digest time.
    ``includes`` (config key ``title_includes``, or legacy ``includes``) keeps
    only uploads whose title contains every listed word (case-insensitive) -- empty
    means keep all. ``excludes`` (config key ``title_excludes``, or legacy
    ``excludes``) then drops any upload whose title contains any listed word (e.g.
    ``"LIVE"`` to skip a live broadcast kept alongside its edited replay) -- empty
    means drop none."""

    source:   str
    detail:   DetailLevel = "deep"
    includes: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()


def load_channels(path: Path | None = None) -> tuple[Channel, ...]:
    """Read the followed-channels list from ``path`` (default ``channels_path()``).

    Raises:
        ConfigError: the file is missing, unreadable, or has no valid entries.
    """
    path = path or channels_path()
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
    # Read the canonical title_* keys, falling back to the legacy names. The key
    # that actually supplied the value is passed to _keywords, so a malformed value
    # is reported against the key the user really wrote, not the canonical spelling.
    includes_key = "title_includes" if "title_includes" in entry else "includes"
    excludes_key = "title_excludes" if "title_excludes" in entry else "excludes"
    return Channel(
        source   = source,
        detail   = detail,
        includes = _keywords(entry.get(includes_key, ()), includes_key, source),
        excludes = _keywords(entry.get(excludes_key, ()), excludes_key, source),
    )


def _keywords(raw: object, field: str, source: object) -> tuple[str, ...]:
    """Normalise a title-filter field to a tuple: a bare string is a single
    filter phrase, a list/tuple is taken as-is; anything else is a config error."""
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, (list, tuple)):
        raise ConfigError(
            f"channel {source!r}: {field} must be a string or a list, "
            f"got {type(raw).__name__}"
        )
    return tuple(str(word) for word in raw)
