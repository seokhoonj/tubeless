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

from youtube_transcript_api import CouldNotRetrieveTranscript, YouTubeTranscriptApi

from tubeless.errors import TranscriptUnavailable

__all__ = ["TranscriptSegment", "Transcript", "fetch_transcript"]


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
        TranscriptUnavailable: captions are disabled, none of the requested
            languages exist, or the video is unreachable.
    """
    try:
        listed  = YouTubeTranscriptApi().list(video_id)
        chosen  = listed.find_transcript(list(languages))
        fetched = chosen.fetch()
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
