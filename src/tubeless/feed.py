"""Resolve a channel to its recent uploads via YouTube's public RSS feed.

No API key or quota: every channel publishes an Atom feed of its latest uploads
at ``feeds/videos.xml``. This module turns a channel handle or id into a list of
``Upload`` rows; it does not fetch transcripts or summarize -- that is the
single-video engine's job, called later by the digest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree import ElementTree

import requests

from tubeless.errors import FeedError

__all__ = ["Upload", "fetch_channel_uploads", "resolve_channel_id"]

_FEED_URL = "https://www.youtube.com/feeds/videos.xml"
_TIMEOUT_SECONDS = 15.0

# A channel id is 'UC' + 22 chars of the base64url alphabet -- a stable observed
# form of every channel feed URL, the one place to change if YouTube ever alters
# it (same reasoning as source.py's video-id pattern).
_CHANNEL_ID_PATTERN = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

# Atom + YouTube feed namespaces, as declared on the feed root.
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt":   "http://www.youtube.com/xml/schemas/2015",
}


@dataclass(frozen=True, slots=True)
class Upload:
    """One video as listed in a channel feed (identity + when it was posted)."""

    video_id:      str
    title:         str
    published:     str   # ISO 8601, exactly as the feed gives it
    channel_id:    str
    channel_title: str


def fetch_channel_uploads(channel_id: str, *, limit: int = 15) -> tuple[Upload, ...]:
    """Return up to ``limit`` most-recent uploads for a channel, newest first.

    Raises:
        FeedError: the id is malformed, or the feed could not be fetched/parsed.
    """
    if not _CHANNEL_ID_PATTERN.match(channel_id):
        raise FeedError(
            f"not a channel id: {channel_id!r} (expected 'UC...'); resolve a "
            "handle with resolve_channel_id() first"
        )
    try:
        response = requests.get(
            _FEED_URL, params={"channel_id": channel_id}, timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as err:
        raise FeedError(f"could not fetch feed for channel {channel_id!r}: {err}") from err
    return _parse_feed(response.text, limit=limit)


def _parse_feed(xml_text: str, *, limit: int) -> tuple[Upload, ...]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as err:
        raise FeedError(f"could not parse channel feed: {err}") from err

    channel_id    = _text(root.find("yt:channelId", _NS)) or ""
    channel_title = _text(root.find("atom:title", _NS)) or ""

    uploads: list[Upload] = []
    for entry in root.findall("atom:entry", _NS)[:limit]:
        video_id = _text(entry.find("yt:videoId", _NS))
        if not video_id:
            continue
        uploads.append(Upload(
            video_id      = video_id,
            title         = _text(entry.find("atom:title", _NS)) or video_id,
            published     = _text(entry.find("atom:published", _NS)) or "",
            channel_id    = channel_id,
            channel_title = channel_title,
        ))
    return tuple(uploads)


def _text(element: ElementTree.Element | None) -> str | None:
    return element.text.strip() if element is not None and element.text else None


def resolve_channel_id(handle_or_url: str) -> str:
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

    match = re.search(r'"(?:channelId|externalId)":"(UC[A-Za-z0-9_-]{22})"', response.text)
    if not match:
        raise FeedError(f"could not find a channel id on {url!r}")
    return match.group(1)


def _channel_page_url(handle_or_url: str) -> str:
    if handle_or_url.startswith("http"):
        return handle_or_url
    handle = handle_or_url if handle_or_url.startswith("@") else f"@{handle_or_url}"
    return f"https://www.youtube.com/{handle}"
