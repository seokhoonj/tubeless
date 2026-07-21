"""Identify a video and resolve its public metadata.

This module owns the boundary between "whatever the user typed" and a
validated ``video_id`` (Ch 7.7 of the Python style constitution: validate at
the boundary so the rest of the package can assume a well-formed id).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import requests

from tubeless.errors import InvalidVideoURL

__all__ = ["Video", "parse_video_id", "fetch_video_meta"]

# A YouTube video id is exactly 11 characters of this alphabet. The length and
# alphabet are stable observed facts of every public YouTube URL form, not a
# documented API guarantee -- if YouTube ever changes them, this is the one
# place to update.
_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Path prefixes that carry the id as the next path segment, e.g.
# youtube.com/shorts/<id>, youtube.com/embed/<id>, youtube.com/live/<id>.
_PATH_PREFIXES = ("/shorts/", "/embed/", "/live/", "/v/")

_OEMBED_ENDPOINT = "https://www.youtube.com/oembed"
_OEMBED_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class Video:
    """Public identity of one video. ``channel`` is None when metadata could
    not be resolved (the summary path must not depend on it)."""

    video_id: str
    title:    str
    url:      str
    channel:  str | None


def parse_video_id(url_or_id: str) -> str:
    """Extract the 11-character video id from a YouTube URL or a bare id.

    Accepts ``watch?v=``, ``youtu.be/``, ``/shorts/``, ``/embed/``, ``/live/``
    URL forms (with or without scheme) and a bare id.

    Raises:
        InvalidVideoURL: the input matches none of the accepted forms.
    """
    candidate = url_or_id.strip()
    if not candidate:
        raise InvalidVideoURL("empty input; expected a YouTube URL or an 11-character video id")

    if _VIDEO_ID_PATTERN.match(candidate):
        return candidate

    # urlparse needs a scheme to populate netloc; users routinely paste
    # scheme-less URLs ("youtube.com/watch?v=...").
    if "://" not in candidate:
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    host   = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")

    if host in ("youtube.com", "youtube-nocookie.com"):
        if parsed.path == "/watch":
            for video_id in parse_qs(parsed.query).get("v", []):
                if _VIDEO_ID_PATTERN.match(video_id):
                    return video_id
        for prefix in _PATH_PREFIXES:
            if parsed.path.startswith(prefix):
                video_id = parsed.path.removeprefix(prefix).split("/")[0]
                if _VIDEO_ID_PATTERN.match(video_id):
                    return video_id
    elif host == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0]
        if _VIDEO_ID_PATTERN.match(video_id):
            return video_id

    raise InvalidVideoURL(
        f"cannot extract a video id from {url_or_id!r}; expected a YouTube URL "
        "(watch?v=, youtu.be/, /shorts/, /embed/) or a bare 11-character id"
    )


def fetch_video_meta(url_or_id: str) -> Video:
    """Resolve title and channel via YouTube's oembed endpoint (no API key).

    Metadata is decoration on the summary, not a prerequisite: on any network
    or payload failure this falls back to a ``Video`` whose title is the id,
    so the transcript-and-summarize path keeps working offline from oembed.

    Raises:
        InvalidVideoURL: the input does not identify a video at all.
    """
    video_id  = parse_video_id(url_or_id)
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        response = requests.get(
            _OEMBED_ENDPOINT,
            params  = {"url": watch_url, "format": "json"},
            timeout = _OEMBED_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):   # valid JSON but not an object (null, list)
            raise ValueError("oembed payload was not a JSON object")
    except (requests.RequestException, ValueError):
        return Video(video_id=video_id, title=video_id, url=watch_url, channel=None)

    return Video(
        video_id = video_id,
        title    = payload.get("title") or video_id,
        url      = watch_url,
        channel  = payload.get("author_name"),
    )
