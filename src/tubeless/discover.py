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

import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import requests

from tubeless.errors import FeedError
from tubeless.source import Video

__all__ = ["DEFAULT_SCAN", "fetch_recent_videos"]

_FEED_URL        = "https://www.youtube.com/feeds/videos.xml"
_TIMEOUT_SECONDS = 15.0

# A channel's RSS feed carries about 15 recent uploads. Scanning the whole window
# is the default so a sparse title filter (one host's episodes among many) does
# not miss matches further down. This is the former hidden fetch cap, now an
# explicit argument the caller can narrow.
DEFAULT_SCAN = 15

# A channel id is 'UC' + 22 chars of the base64url alphabet; a user playlist id
# is 'PL' + a longer run of the same alphabet. Both are stable observed forms of
# the feed URLs -- the one place to change if YouTube ever alters them (same
# reasoning as source.py's video-id pattern).
_CHANNEL_ID_PATTERN  = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_PLAYLIST_ID_PATTERN = re.compile(r"^PL[A-Za-z0-9_-]{10,}$")

# The channel id embedded in a channel page, in order of trust: the canonical
# link is always THIS channel, while a bare externalId/channelId scan can match a
# recommended channel earlier in the HTML.
_CANONICAL_CHANNEL_ID = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{22})"'
)
_EXTERNAL_ID        = re.compile(r'"externalId":"(UC[A-Za-z0-9_-]{22})"')
_CHANNEL_ID_IN_PAGE = re.compile(r'"channelId":"(UC[A-Za-z0-9_-]{22})"')

# Atom + YouTube feed namespaces, as declared on the feed root.
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt":   "http://www.youtube.com/xml/schemas/2015",
}


def fetch_recent_videos(
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
        params = {"channel_id": _resolve_channel_id(source)}

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


def _text(element: ElementTree.Element | None) -> str | None:
    return element.text.strip() if element is not None and element.text else None


def _normalise_published(raw: str | None) -> str | None:
    """Normalise a feed timestamp to ISO-8601 UTC (``YYYY-MM-DDTHH:MM:SSZ``).

    A single canonical format makes lexicographic order match chronological order,
    so date-range queries can compare ``published`` strings directly. A value that
    cannot be parsed -- or that carries no timezone, so its instant is unknown --
    degrades to ``None`` rather than raising or being falsely stamped 'Z': a
    missing or ambiguous date must not sink an otherwise-good video, nor corrupt
    the ordering by pretending an unknown-offset time is UTC."""
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _playlist_id_of(source: str) -> str | None:
    """Return the 'PL...' id if ``source`` is a playlist (bare id or a URL with
    a ``list=`` parameter); ``None`` if it names a channel instead."""
    text = source.strip()
    if _PLAYLIST_ID_PATTERN.match(text):
        return text
    if "list=" in text:
        query = urlparse(text if "://" in text else "https://" + text).query
        for value in parse_qs(query).get("list", []):
            if _PLAYLIST_ID_PATTERN.match(value):
                return value
    return None


def _resolve_channel_id(handle_or_url: str) -> str:
    """Resolve an ``@handle``, channel URL, or bare id to a 'UC...' channel id.

    A bare 'UC...' id is returned unchanged. Anything else is looked up by
    fetching the channel page and reading the canonical channel id embedded in
    it (YouTube offers no keyless id-lookup API).

    Raises:
        FeedError: the channel id could not be determined.
    """
    text = handle_or_url.strip()
    if _CHANNEL_ID_PATTERN.match(text):
        return text

    url = _channel_page_url(text)
    try:
        response = requests.get(
            url, timeout=_TIMEOUT_SECONDS, headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
    except requests.RequestException as err:
        raise FeedError(f"could not open channel page {url!r}: {err}") from err

    # Prefer the page's canonical channel link -- it is always THIS channel. A
    # bare "channelId"/"externalId" scan returns the FIRST match, which can be a
    # recommended/related channel appearing earlier in the HTML, silently
    # resolving a handle to the wrong channel (seen with some non-ASCII handles).
    channel_id_match = (
        _CANONICAL_CHANNEL_ID.search(response.text)
        or _EXTERNAL_ID.search(response.text)
        or _CHANNEL_ID_IN_PAGE.search(response.text)
    )
    if not channel_id_match:
        raise FeedError(f"could not find a channel id on {url!r}")
    return channel_id_match.group(1)


def _channel_page_url(handle_or_url: str) -> str:
    if handle_or_url.startswith("http"):
        return handle_or_url
    handle = handle_or_url if handle_or_url.startswith("@") else f"@{handle_or_url}"
    return f"https://www.youtube.com/{handle}"
