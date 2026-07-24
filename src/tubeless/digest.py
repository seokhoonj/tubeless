"""Assemble a digest from a set of channels: discover each channel's new videos,
summarize and score them, and rank the results into one document.

The layering is deliberate. ``summarize_videos`` is the channel-agnostic engine
that turns videos into stored summaries; ``curate`` is the sole ``Digest``
constructor, a pure assembler that scores and ranks already-produced summaries;
``run_digest`` is the top orchestrator that discovers per channel and feeds the
two. ``recompute`` reuses ``curate`` over previously stored summaries, so a
weekly or monthly re-synthesis needs no refetching. None of them holds state
beyond the run: the caller passes the already-seen ids in and gets the merged
set back to persist.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple

from tubeless.channels import Channel
from tubeless.discover import DEFAULT_SCAN, fetch_recent_videos
from tubeless.errors import FeedError, TranscriptUnavailable
from tubeless.importance import Importance, score
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
from tubeless.synthesis import Synthesis, synthesize
from tubeless.transcript import fetch_transcript

__all__ = [
    "DEFAULT_PER_CHANNEL_LIMIT",
    "Digest",
    "DigestRun",
    "Entry",
    "Skip",
    "SummarizeVideosResult",
    "curate",
    "recompute",
    "run_digest",
    "summarize_videos",
]

# How many recent uploads to check per plain channel when the caller does not
# say. A channel with a title filter scans the full feed window instead (matches
# are sparse among the rest), so this cap applies only to unfiltered channels.
DEFAULT_PER_CHANNEL_LIMIT = 5

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
    fresh run, a ``since..until`` range for a recompute; it is never structurally
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


class DigestRun(NamedTuple):
    """What ``run_digest`` produced: the assembled ``digest`` and the merged
    ``seen`` set (the input seen union everything processed this run), ready to
    persist directly with ``write_seen``."""

    digest: Digest
    seen:   frozenset[str]


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
        TranscriptFetchBlocked: a transient block (propagated so the run aborts
            before the caller persists state, rather than marking the video seen).
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


def curate(
    summaries:      Sequence[Summary],
    backend:        LLMBackend,
    *,
    period:         str,
    language:       str = DEFAULT_LANGUAGE,
    with_synthesis: bool = False,
    skipped:        Sequence[Skip] = (),
    focus:          str | None = None,
) -> Digest:
    """Assemble a ``Digest`` from already-produced summaries: score each, rank by
    importance, and optionally synthesize across them.

    The sole ``Digest`` constructor -- channel-agnostic and pure of I/O (only
    backend calls) -- so a fresh run and a recompute produce the same shape. The
    scores align to the summaries positionally (``score`` is an order-preserving
    map), asserted below. ``skipped`` is surfaced on the digest unchanged.

    Raises:
        LLMError: propagated from the backend.
    """
    # score is a total, order-preserving map, so zip(strict=True) pairs each
    # summary with its own importance and raises (even under -O) if that ever
    # breaks -- no separate length assert needed.
    importances = score(summaries, backend, language=language, focus=focus)
    entries = [Entry(summary=summary, importance=importance)
               for summary, importance in zip(summaries, importances, strict=True)]
    entries.sort(key=lambda entry: entry.importance.score, reverse=True)

    # synthesize returns None below two summaries (and makes no backend call), so
    # with_synthesis alone gates it -- no separate count check here.
    synthesis = synthesize(summaries, backend, language=language) if with_synthesis else None
    return Digest(
        period    = period,
        entries   = tuple(entries),
        skipped   = tuple(skipped),
        synthesis = synthesis,
    )


def run_digest(
    channels:          Sequence[Channel],
    backend:           LLMBackend,
    *,
    period:            str,
    seen:              frozenset[str] = frozenset(),
    language:          str = DEFAULT_LANGUAGE,
    per_channel_limit: int = DEFAULT_PER_CHANNEL_LIMIT,
    with_synthesis:    bool = False,
    store:             Store | None = None,
) -> DigestRun:
    """Discover each channel's new videos, summarize and score them, and curate
    the ranked digest for ``period``.

    Only videos whose id is not in ``seen`` (or already processed earlier in this
    run) are summarized, so a video shared by two channel sources is handled once.
    A filtered channel scans the full feed window; a plain one scans
    ``per_channel_limit``. A channel whose feed cannot be read becomes a
    ``feed-failure`` Skip and the run continues. ``store=None`` is a dry run
    (fetches and summarizes, but persists nothing).

    Returns the digest and the merged seen set (input ``seen`` union everything
    processed this run), so the caller persists it directly.

    Raises:
        TranscriptFetchBlocked / LLMError: propagated (see ``summarize_videos``).
    """
    all_summaries: list[Summary] = []
    skipped:       list[Skip] = []
    processed:     set[str] = set()

    for channel in channels:
        # A filtered channel scans the full feed window, not just per_channel_limit:
        # the wanted uploads are sparse among the rest, so a small window silently
        # drops them (the documented missed-video incident).
        has_filter = bool(channel.includes or channel.excludes)
        limit      = DEFAULT_SCAN if has_filter else per_channel_limit
        try:
            videos = fetch_recent_videos(
                channel.source, limit=limit,
                includes=channel.includes, excludes=channel.excludes,
            )
        except FeedError as err:
            skipped.append(Skip("feed-failure", channel.source, str(err)))
            continue

        fresh = tuple(
            video for video in videos
            if video.video_id not in seen and video.video_id not in processed
        )
        result = summarize_videos(
            fresh, backend, detail=channel.detail, language=language, store=store,
        )
        all_summaries.extend(result.summaries)
        skipped.extend(result.skipped)
        processed |= result.processed

    digest = curate(
        all_summaries, backend, period=period, language=language,
        with_synthesis=with_synthesis, skipped=skipped,
    )
    return DigestRun(digest, frozenset(seen) | processed)


def recompute(
    backend:        LLMBackend,
    store:          Store,
    *,
    since:          str | None = None,
    until:          str | None = None,
    channel:        str | None = None,
    language:       str = DEFAULT_LANGUAGE,
    with_synthesis: bool = True,
) -> Digest:
    """Re-assemble a digest from previously stored summaries over ``[since,
    until)``, without fetching or discovering anything.

    Reads summaries from ``store`` (optionally narrowed to one ``channel``),
    keeps the most recent per video, and curates them. The ``period`` label is
    derived from the range. Read-only: it never writes to the store. Its default
    ``with_synthesis=True`` reflects its purpose -- a cross-source read over a
    span -- unlike a fresh run's opt-in synthesis.

    Raises:
        LLMError: propagated from the backend.
    """
    stored    = store.load_summaries(since=since, until=until, channel=channel)
    summaries = _latest_per_video(stored)
    return curate(
        summaries, backend, period=_range_label(since, until),
        language=language, with_synthesis=with_synthesis,
    )


def _latest_per_video(summaries: Sequence[Summary]) -> list[Summary]:
    """One summary per video: the last in the store's load order, which orders
    variants of one video by save time, so the most recently stored summary of
    each video wins and ``curate`` never double-counts a video that has several
    stored variants (different detail or language)."""
    latest: dict[str, Summary] = {}
    for summary in summaries:
        latest[summary.video.video_id] = summary
    return list(latest.values())


def _range_label(since: str | None, until: str | None) -> str:
    """A display label for a recompute's span: ``since..until`` (an open end left
    blank), or ``all`` when the range is unbounded."""
    if since is None and until is None:
        return "all"
    return f"{since or ''}..{until or ''}"
