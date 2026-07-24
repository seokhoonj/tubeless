"""Turn a set of video summaries into one ranked digest.

Two pieces, both channel-agnostic and free of discovery/persistence
orchestration (which lives in the CLI, composing these with the fetch/store
atoms):

- ``summarize_videos`` is the batch engine: it turns a list of videos into
  summaries, skipping the captionless ones and writing each through to a store.
- ``curate_summaries`` is the sole ``Digest`` constructor: it scores the
  summaries, ranks them by importance, and synthesizes across them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple

from tubeless.errors import TranscriptUnavailable
from tubeless.importance import Importance, score_summaries
from tubeless.llm import LLMBackend
from tubeless.source import Video
from tubeless.store import Store
from tubeless.summary import (
    DEFAULT_DETAIL,
    DEFAULT_LANGUAGE,
    DetailLevel,
    Summary,
    summarize_transcript,
)
from tubeless.synthesis import Synthesis, synthesize_summaries
from tubeless.transcript import fetch_transcript

__all__ = [
    "Digest",
    "Entry",
    "Skip",
    "SummarizeVideosResult",
    "curate_summaries",
    "summarize_videos",
]

SkipCategory = Literal["feed-failure", "no-transcript"]


@dataclass(frozen=True, slots=True)
class Skip:
    """One thing left out of the digest, tagged by why. ``category`` separates a
    channel-level miss (``feed-failure`` -- ``item`` is the channel source) from a
    video-level one (``no-transcript`` -- ``item`` is the video id), so an empty
    digest with a feed failure is never mistaken for a quiet day, and a captionless
    video is legible rather than silently dropped. ``message`` explains it."""

    category: SkipCategory
    item:     str
    message:  str


@dataclass(frozen=True, slots=True)
class Entry:
    """One ranked video in a digest: its summary and the importance it was scored
    at. The display name is the summary's own ``video.channel`` -- no separate
    label is carried, so there is nothing to drift from the summary."""

    summary:    Summary
    importance: Importance


@dataclass(frozen=True, slots=True)
class Digest:
    """One assembled digest (entries most-important first), the things left out
    (feed failures and captionless videos), and an optional cross-source synthesis.
    ``period`` is a display label for the span it covers -- a single date for a
    fresh run, a ``since..until`` range for a re-curate; it is never structurally
    parsed (chronological queries read ``Summary.video.published``)."""

    period:    str
    entries:   tuple[Entry, ...]
    skipped:   tuple[Skip, ...] = ()
    synthesis: Synthesis | None = None


class SummarizeVideosResult(NamedTuple):
    """What ``summarize_videos`` produced: the finished summaries and the videos
    it skipped for having no transcript. ``processed`` is derived (not stored, so
    it cannot drift): every id that was handled -- summarized or skipped -- and so
    must not be retried."""

    summaries: list[Summary]
    skipped:   list[Skip]

    @property
    def processed(self) -> frozenset[str]:
        return (frozenset(summary.video.video_id for summary in self.summaries)
                | frozenset(skip.item for skip in self.skipped))


def summarize_videos(
    videos:   Sequence[Video],
    backend:  LLMBackend,
    *,
    detail:   DetailLevel = DEFAULT_DETAIL,
    language: str = DEFAULT_LANGUAGE,
    store:    Store | None = None,
) -> SummarizeVideosResult:
    """Fetch each video's transcript and summarize it, channel-agnostically.

    When a ``store`` is given, each transcript and summary is written through as
    it is produced -- so the durable corpus grows even if a later step fails.
    ``store=None`` is a dry run: transcripts are still fetched and the backend is
    still called (that cost is unavoidable), only persistence is skipped.

    A video with no transcript is recorded as a ``no-transcript`` Skip and left
    out of the summaries, but still counts as processed (via the result's
    ``processed``) so it is not retried on the next run.

    Raises:
        TranscriptFetchBlocked: a transient block (propagated so the caller aborts
            before persisting state, rather than marking the video seen).
        LLMError: propagated from the backend.
    """
    summaries: list[Summary] = []
    skipped:   list[Skip] = []
    for video in videos:
        try:
            transcript = fetch_transcript(video)
        except TranscriptUnavailable as err:
            skipped.append(Skip("no-transcript", video.video_id, str(err)))
            continue
        if store is not None:
            store.save_transcript(transcript)
        summary = summarize_transcript(transcript, backend, detail=detail, language=language)
        if store is not None:
            store.save_summary(summary)
        summaries.append(summary)
    return SummarizeVideosResult(summaries, skipped)


def curate_summaries(
    summaries: Sequence[Summary],
    backend:   LLMBackend,
    *,
    period:    str,
    language:  str = DEFAULT_LANGUAGE,
    skipped:   Sequence[Skip] = (),
    focus:     str | None = None,
) -> Digest:
    """Assemble a ``Digest`` from already-produced summaries: score each, rank by
    importance, and synthesize across them.

    The sole ``Digest`` constructor -- channel-agnostic and pure of I/O (only
    backend calls) -- so a fresh run and a re-curate produce the same shape. The
    scores align to the summaries positionally (``score_summaries`` is an
    order-preserving map), asserted below. ``skipped`` is surfaced unchanged.

    The synthesis is always attempted; ``synthesize_summaries`` returns ``None``
    (and makes no backend call) below two summaries, so a one-video or empty
    digest simply carries no synthesis. ``focus`` personalises the importance
    scoring when given.

    Raises:
        LLMError: propagated from the backend.
    """
    # score_summaries is a total, order-preserving map, so zip(strict=True) pairs
    # each summary with its own importance and raises (even under -O) if that ever
    # breaks -- no separate length assert needed.
    importances = score_summaries(summaries, backend, language=language, focus=focus)
    entries = [Entry(summary=summary, importance=importance)
               for summary, importance in zip(summaries, importances, strict=True)]
    entries.sort(key=lambda entry: entry.importance.score, reverse=True)

    synthesis = synthesize_summaries(summaries, backend, language=language)
    return Digest(
        period    = period,
        entries   = tuple(entries),
        skipped   = tuple(skipped),
        synthesis = synthesis,
    )
