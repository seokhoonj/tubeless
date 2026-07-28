"""The immutable transcript records shared by the caption and whisper paths.

These two frozen dataclasses sit in their own module -- below both the caption
fetcher (``transcript``) and the whisper fallback (``transcribe``) that produce
them -- so ``transcribe`` can import the records without importing ``transcript``,
which is what breaks the import cycle (``transcript`` still imports ``transcribe``
to call the fallback). ``transcript`` re-exports them, so ``from tubeless.transcript
import Transcript`` keeps working for every existing caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from tubeless.source import Video

__all__ = ["TranscriptSegment", "Transcript"]


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """One transcript segment: its text, onset in seconds, and duration in seconds."""

    text:     str
    start:    float
    duration: float


@dataclass(frozen=True, slots=True)
class Transcript:
    """A whole transcript, kept segment-complete: downstream layers decide
    what to trim or chunk, the fetch layer never does.

    ``video`` is the video this transcript is of -- carried whole (not just the
    id) so the transcript is self-describing and every pipeline object (Video ->
    Transcript -> Summary) threads the same identity without re-fetching it."""

    video:             Video
    language:          str
    is_auto_generated: bool
    segments:          tuple[TranscriptSegment, ...]

    @property
    def text(self) -> str:
        """The full transcript as one space-joined string."""
        return " ".join(segment.text for segment in self.segments)
