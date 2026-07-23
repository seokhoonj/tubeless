"""Discover a source's recent videos via YouTube's public RSS feed.

Discovery is separate from fetching a known video (``source.py``): given a
channel or playlist the user follows, this lists what it has posted lately, as
``Video`` rows ready for the transcript/summary pipeline. No API key or quota --
every channel publishes an Atom feed of its latest uploads at ``feeds/videos.xml``.

A title filter (``includes``/``excludes``) is applied here, at the discovery
boundary, so a caller receives only the videos it wants and never re-scans the
feed to filter them itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from xml.etree import ElementTree

import requests

from tubeless.errors import FeedError
# Channel resolution and the feed URL/namespaces live in feed.py, which still
# serves the legacy Upload path; discover reuses them and adds a Video-producing
# parse plus the title filter.
from tubeless.feed import (
    _FEED_URL,
    _NS,
    _TIMEOUT_SECONDS,
    _playlist_id_of,
    _text,
    resolve_channel_id,
)
from tubeless.source import Video

__all__ = ["DEFAULT_SCAN", "discover"]

# A channel's RSS feed carries about 15 recent uploads. Scanning the whole window
# is the default so a sparse title filter (one host's episodes among many) does
# not miss matches further down. This is the former hidden fetch cap, now an
# explicit argument the caller can narrow.
DEFAULT_SCAN = 15


def discover(
    source: str,
    *,
    limit:    int = DEFAULT_SCAN,
    includes: tuple[str, ...] = (),
    excludes: tuple[str, ...] = (),
) -> tuple[Video, ...]:
    """Return recent videos of a channel or playlist ``source``, newest first.

    ``source`` may be a channel (an ``@handle``, a channel URL, or a 'UC...' id)
    or a playlist (a 'PL...' id, or any URL carrying a ``list=PL...`` parameter).
    A playlist narrows a channel down to a single series -- e.g. one daily show
    among the many a channel posts.

    Up to ``limit`` feed entries are scanned; ``includes``/``excludes`` then keep
    only those whose title contains every ``includes`` word and none of the
    ``excludes`` words (case-insensitive). Empty ``includes`` keeps all, empty
    ``excludes`` drops none.

    Each result is a ``Video`` with ``published`` set to the feed's upload time,
    normalised to ISO-8601 UTC -- the same type ``fetch_video`` yields, so both
    ways of finding a video feed the transcript/summary pipeline unchanged.

    Raises:
        FeedError: the source could not be resolved, or its feed could not be
            fetched or parsed.
    """
    playlist_id = _playlist_id_of(source)
    if playlist_id is not None:
        params = {"playlist_id": playlist_id}
    else:
        params = {"channel_id": resolve_channel_id(source)}

    videos = _scan_feed(params, limit=limit)
    return _matching_title(videos, includes, excludes)


def _scan_feed(params: dict[str, str], *, limit: int) -> tuple[Video, ...]:
    try:
        response = requests.get(_FEED_URL, params=params, timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as err:
        raise FeedError(f"could not fetch feed {params}: {err}") from err
    return _parse_feed(response.text, limit=limit)


def _parse_feed(xml_text: str, *, limit: int) -> tuple[Video, ...]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as err:
        raise FeedError(f"could not parse channel feed: {err}") from err

    channel = _text(root.find("atom:title", _NS)) or None

    videos: list[Video] = []
    for entry in root.findall("atom:entry", _NS)[:limit]:
        video_id = _text(entry.find("yt:videoId", _NS))
        if not video_id:
            continue
        videos.append(Video(
            video_id  = video_id,
            title     = _text(entry.find("atom:title", _NS)) or video_id,
            url       = f"https://www.youtube.com/watch?v={video_id}",
            channel   = channel,
            published = _normalise_published(_text(entry.find("atom:published", _NS))),
        ))
    return tuple(videos)


def _normalise_published(raw: str | None) -> str | None:
    """Normalise a feed timestamp to ISO-8601 UTC (``YYYY-MM-DDTHH:MM:SSZ``).

    A single canonical format makes lexicographic order match chronological order,
    so date-range queries can compare ``published`` strings directly. A value that
    cannot be parsed degrades to ``None`` rather than raising -- a missing date
    must not sink an otherwise-good video (partial-failure preservation)."""
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _matching_title(
    videos:   tuple[Video, ...],
    includes: tuple[str, ...],
    excludes: tuple[str, ...],
) -> tuple[Video, ...]:
    """Keep videos whose title contains every ``includes`` word and none of the
    ``excludes`` words (case-insensitive). Empty ``includes`` keeps all; empty
    ``excludes`` drops none."""
    if not includes and not excludes:
        return videos
    wanted   = [word.lower() for word in includes]
    unwanted = [word.lower() for word in excludes]

    def matches(video: Video) -> bool:
        title = video.title.lower()   # lower once per video, not once per keyword
        return (all(word in title for word in wanted)
                and not any(word in title for word in unwanted))

    return tuple(video for video in videos if matches(video))
