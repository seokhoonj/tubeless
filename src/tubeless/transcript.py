"""Fetch a video's transcript as immutable, typed segments.

The vendor boundary lives here: youtube-transcript-api is imported and its
exceptions are translated into the tubeless hierarchy in exactly one place,
so no other module needs to know which library does the fetching.

Written against youtube-transcript-api >= 1.0: ``YouTubeTranscriptApi().list()``
returns a transcript list with ``find_transcript()``; each listed transcript
carries ``language_code`` / ``is_generated`` and ``fetch()`` yields snippets
with ``.text`` / ``.start`` / ``.duration``. The pre-1.0 module-level
``get_transcript()`` API is not supported.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests
from youtube_transcript_api import (
    CouldNotRetrieveTranscript,
    IpBlocked,
    RequestBlocked,
    YouTubeRequestFailed,
    YouTubeTranscriptApi,
)

from tubeless.errors import TranscriptFetchBlocked, TranscriptUnavailable

__all__ = ["TranscriptSegment", "Transcript", "fetch_transcript"]

_FETCH_TIMEOUT_SECONDS = 30.0


class _TimeoutSession(requests.Session):
    """A ``requests`` session with a default per-request timeout. The transcript
    API exposes no timeout of its own, so without this a wedged fetch would hang
    the digest's per-video loop -- the same bound the other network calls carry."""

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", _FETCH_TIMEOUT_SECONDS)
        return super().request(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """One caption cue: its text, onset in seconds, and duration in seconds."""

    text:     str
    start:    float
    duration: float


@dataclass(frozen=True, slots=True)
class Transcript:
    """A whole transcript, kept segment-complete: downstream layers decide
    what to trim or chunk, the fetch layer never does (constitution 4.10)."""

    video_id:          str
    language:          str
    is_auto_generated: bool
    segments:          tuple[TranscriptSegment, ...]

    @property
    def text(self) -> str:
        """The full transcript as one space-joined string."""
        return " ".join(segment.text for segment in self.segments)


def fetch_transcript(
    video_id:   str,
    *,
    languages:  tuple[str, ...] = ("ko", "en"),
) -> Transcript:
    """Fetch the transcript, preferring the first requested language available.

    Within each language the vendor library prefers a manually created
    transcript over an auto-generated one; ``is_auto_generated`` records which
    kind was actually served, so the summarizer can hedge auto-caption
    mis-transcriptions.

    Args:
        video_id:  a validated 11-character id (see ``source.parse_video_id``).
        languages: language codes in preference order.

    Raises:
        TranscriptFetchBlocked: YouTube transiently rate-limited or IP-blocked
            this request (not a property of the video).
        TranscriptUnavailable: captions are permanently absent -- disabled, none
            of the requested languages exist, or the video does not exist.
    """
    try:
        listed  = YouTubeTranscriptApi(http_client=_TimeoutSession()).list(video_id)
        chosen  = listed.find_transcript(list(languages))
        fetched = chosen.fetch()
    except (RequestBlocked, IpBlocked, YouTubeRequestFailed) as err:
        # Transient: the run's IP is blocked/throttled. Kept separate from the
        # permanent case so the digest aborts rather than marking the video
        # processed -- see TranscriptFetchBlocked. These subclass
        # CouldNotRetrieveTranscript, so this arm MUST come first.
        raise TranscriptFetchBlocked(
            f"transcript fetch blocked for video {video_id!r}: {err}"
        ) from err
    except CouldNotRetrieveTranscript as err:
        raise TranscriptUnavailable(
            f"no transcript for video {video_id!r} in languages {languages!r}: {err}"
        ) from err

    segments = tuple(
        TranscriptSegment(text=snippet.text, start=snippet.start, duration=snippet.duration)
        for snippet in fetched
    )
    return Transcript(
        video_id          = video_id,
        language          = chosen.language_code,
        is_auto_generated = chosen.is_generated,
        segments          = segments,
    )
