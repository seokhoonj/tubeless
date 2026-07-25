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
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal, get_args

from tubeless.channels import Channel
from tubeless.errors import TranscriptUnavailable
from tubeless.importance import Importance, score_summaries
from tubeless.llm import LLMBackend
from tubeless.source import Video
from tubeless.summary import (
    DEFAULT_DETAIL,
    DEFAULT_LANGUAGE,
    DETAIL_LEVELS,
    DetailLevel,
    Summary,
    summarize_transcript,
    summary_from_dict,
)
from tubeless.synthesis import Synthesis, synthesize_summaries
from tubeless.transcript import fetch_transcript

if TYPE_CHECKING:
    # Only a type hint on summarize_videos' ``store`` parameter; importing it at
    # runtime would loop (store persists digests, so it imports this module). The
    # ``from __future__`` annotations make the hint a string, so this suffices.
    from tubeless.store import Store

__all__ = [
    "Digest",
    "Entry",
    "RunProvenance",
    "Skip",
    "SummarizedVideos",
    "curate_summaries",
    "digest_from_dict",
    "digest_to_dict",
    "summarize_videos",
]

SkipCategory = Literal["feed-failure", "no-transcript"]


@dataclass(frozen=True, slots=True)
class Skip:
    """One thing left out of the digest, tagged by why. ``category`` separates a
    channel-level miss (``feed-failure`` -- ``subject`` is the channel source) from
    a video-level one (``no-transcript`` -- ``subject`` is the video id), so an
    empty digest with a feed failure is never mistaken for a quiet day, and a
    captionless video is legible rather than silently dropped. ``message`` explains
    it."""

    category: SkipCategory
    subject:  str
    message:  str


@dataclass(frozen=True, slots=True)
class Entry:
    """One ranked video in a digest: its summary and the importance it was scored
    at. The display name is the summary's own ``video.channel`` -- no separate
    label is carried, so there is nothing to drift from the summary."""

    summary:    Summary
    importance: Importance


@dataclass(frozen=True, slots=True)
class RunProvenance:
    """A value-copied record of the configuration that produced a digest, so a
    stored digest is a faithful point-in-time snapshot -- what config concluded
    what, on that day -- and never depends on the living config, which may have
    changed since.

    ``channels`` is the resolved channel set a fresh run actually scanned (each a
    value copy of the Channel: source, detail, includes, excludes); it is empty
    for a re-curate over stored summaries, which reads the corpus by date range
    instead of scanning feeds. ``backend``/``model`` matter because the ranking and
    synthesis pass through a non-deterministic LLM, so which model reached this
    conclusion is part of the record. A fresh run records ``source_match`` (how it
    narrowed the channel list); a re-curate records ``since``/``until``/``channel``."""

    backend:      str
    model:        str
    language:     str
    channels:     tuple[Channel, ...] = ()
    per_channel:  int | None = None
    source_match: str | None = None
    since:        str | None = None
    until:        str | None = None
    channel:      str | None = None


@dataclass(frozen=True, slots=True)
class Digest:
    """One assembled digest (entries most-important first), the things left out
    (feed failures and captionless videos), and an optional cross-source synthesis.

    ``created`` is when the digest was generated (an ISO date). ``start``/``end``
    are the date range a re-curate covers -- both ``None`` for a fresh run of
    newly discovered videos, which has no range and is identified by ``created``.
    ``label`` derives the display string from these; the fields are structured so
    a reader need never parse it back. ``provenance`` records the config that
    produced this digest (see ``RunProvenance``) so a stored run is self-explaining;
    it is ``None`` only for a digest built without one (an in-memory or dry run)."""

    created:    str
    entries:    tuple[Entry, ...]
    skipped:    tuple[Skip, ...] = ()
    synthesis:  Synthesis | None = None
    start:      str | None = None
    end:        str | None = None
    provenance: RunProvenance | None = None

    @property
    def label(self) -> str:
        """A display string for the span the digest covers: the ``start..end``
        range, a single open-ended bound, or -- for a fresh run -- the date it was
        created. Safe as a filename (no spaces)."""
        if self.start and self.end:
            return f"{self.start}..{self.end}"
        if self.start:
            return f"since-{self.start}"
        if self.end:
            return f"until-{self.end}"
        return self.created


@dataclass(frozen=True, slots=True)
class SummarizedVideos:
    """What ``summarize_videos`` produced from a set of videos: the finished
    summaries and the videos it skipped for having no transcript. ``processed`` is
    derived (not stored, so it cannot drift): every id that was handled --
    summarized or skipped -- and so must not be retried."""

    summaries: tuple[Summary, ...]
    skipped:   tuple[Skip, ...]

    @property
    def processed(self) -> frozenset[str]:
        return (frozenset(summary.video.video_id for summary in self.summaries)
                | frozenset(skip.subject for skip in self.skipped))


def summarize_videos(
    videos:   Sequence[Video],
    backend:  LLMBackend,
    *,
    detail:   DetailLevel = DEFAULT_DETAIL,
    language: str = DEFAULT_LANGUAGE,
    store:    Store | None = None,
) -> SummarizedVideos:
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
        CorpusError: a store write (transcript or summary) failed; propagated so
            the run aborts rather than continuing with a half-persisted corpus.
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
    return SummarizedVideos(tuple(summaries), tuple(skipped))


def curate_summaries(
    summaries: Sequence[Summary],
    backend:   LLMBackend,
    *,
    created:   str,
    start:     str | None = None,
    end:       str | None = None,
    language:   str = DEFAULT_LANGUAGE,
    skipped:    Sequence[Skip] = (),
    focus:      str | None = None,
    provenance: RunProvenance | None = None,
) -> Digest:
    """Assemble a ``Digest`` from already-produced summaries: score each, rank by
    importance, and synthesize across them.

    The sole ``Digest`` constructor -- channel-agnostic and pure of I/O (only
    backend calls) -- so a fresh run and a re-curate produce the same shape.
    ``score_summaries`` returns exactly one score per input, in input order, so
    ``zip(strict=True)`` pairs each summary with its own importance. ``skipped``
    is surfaced unchanged.

    The synthesis is always attempted; ``synthesize_summaries`` returns ``None``
    (and makes no backend call) below two summaries, so a one-video or empty
    digest simply carries no synthesis. ``focus`` personalises the importance
    scoring when given. ``provenance``, when given, is surfaced on the digest
    unchanged (the CLI builds it from the run's settings) -- so the sole Digest
    constructor stays channel-agnostic and free of I/O.

    Raises:
        LLMError: propagated from the backend.
    """
    # score_summaries returns exactly one score per summary; zip(strict=True) fails
    # loudly (even under -O) if that count ever diverges from the inputs, so no
    # separate length assert is needed. (It guards the count, not the order.)
    importances = score_summaries(summaries, backend, language=language, focus=focus)
    entries = [Entry(summary=summary, importance=importance)
               for summary, importance in zip(summaries, importances, strict=True)]
    entries.sort(key=lambda entry: entry.importance.score, reverse=True)

    synthesis = synthesize_summaries(summaries, backend, language=language)
    return Digest(
        created    = created,
        entries    = tuple(entries),
        skipped    = tuple(skipped),
        synthesis  = synthesis,
        start      = start,
        end        = end,
        provenance = provenance,
    )


# --- serialization -------------------------------------------------------------
# A digest is persisted as a point-in-time record (store.save_digest). Conversion
# lives here with the types, so store.py only does the file I/O. ``digest_to_dict``
# leans on the dataclass field names as the on-disk schema; ``digest_from_dict``
# rebuilds each nested type with validation, treating a malformed record as absent
# (returning ``None``) -- the same graceful mode the corpus loaders use.

def digest_to_dict(digest: Digest) -> dict[str, object]:
    """Serialise a Digest to a dict for JSON storage. Nested frozen dataclasses
    (entries, summaries, provenance, channels, synthesis) recurse to dicts; tuple
    fields stay tuples here (``asdict``) and serialize as JSON arrays when written,
    so ``digest_from_dict`` accepts either. Derived properties (``label``,
    ``Importance.tier``) are not fields, so they are not stored -- they recompute."""
    return asdict(digest)


def digest_from_dict(record: object) -> Digest | None:
    """Rebuild a Digest from a stored dict, or ``None`` if it is not a well-formed
    digest. Digests are written by tubeless, so this validates the shape at each
    construction boundary rather than every leaf; a corrupt file reads as absent."""
    if not isinstance(record, dict):
        return None
    created = record.get("created")
    if not isinstance(created, str):
        return None

    entries_raw = record.get("entries")
    if not _is_seq(entries_raw):
        return None
    entries: list[Entry] = []
    for item in entries_raw:
        if not isinstance(item, dict):
            return None
        summary    = summary_from_dict(item.get("summary"))
        importance = _importance_from_dict(item.get("importance"))
        if summary is None or importance is None:
            return None
        entries.append(Entry(summary=summary, importance=importance))

    skipped = _skips_from_list(record.get("skipped", []))
    if skipped is None:
        return None

    synthesis_raw = record.get("synthesis")
    synthesis = None if synthesis_raw is None else _synthesis_from_dict(synthesis_raw)
    if synthesis_raw is not None and synthesis is None:
        return None

    start = record.get("start")
    end   = record.get("end")
    if not _is_optional_str(start) or not _is_optional_str(end):
        return None

    prov_raw = record.get("provenance")
    provenance = None if prov_raw is None else _provenance_from_dict(prov_raw)
    if prov_raw is not None and provenance is None:
        return None

    return Digest(
        created    = created,
        entries    = tuple(entries),
        skipped    = tuple(skipped),
        synthesis  = synthesis,
        start      = start,
        end        = end,
        provenance = provenance,
    )


def _is_optional_str(value: object) -> bool:
    return value is None or isinstance(value, str)


def _is_seq(value: object) -> bool:
    # Accept both list (JSON on disk) and tuple (an in-memory digest_to_dict result,
    # since dataclasses.asdict keeps tuple fields as tuples) -- but never a str,
    # which is iterable and would validate character by character.
    return isinstance(value, (list, tuple))


def _all_str(value: object) -> bool:
    return _is_seq(value) and all(isinstance(item, str) for item in value)


def _importance_from_dict(body: object) -> Importance | None:
    if not isinstance(body, dict):
        return None
    score  = body.get("score")
    reason = body.get("reason")
    # bool is an int subclass, so reject it explicitly -- a score is a number.
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not isinstance(reason, str):
        return None
    return Importance(score=float(score), reason=reason)


def _synthesis_from_dict(body: object) -> Synthesis | None:
    if not isinstance(body, dict):
        return None
    tone          = body.get("tone")
    overview      = body.get("overview")
    agreements    = body.get("agreements")
    disagreements = body.get("disagreements")
    if not isinstance(tone, str) or not isinstance(overview, str):
        return None
    if not _all_str(agreements) or not _all_str(disagreements):
        return None
    return Synthesis(tone=tone, overview=overview,
                     agreements=tuple(agreements), disagreements=tuple(disagreements))


def _channel_from_dict(body: object) -> Channel | None:
    if not isinstance(body, dict):
        return None
    source   = body.get("source")
    detail   = body.get("detail", DEFAULT_DETAIL)
    includes = body.get("includes", [])
    excludes = body.get("excludes", [])
    if not isinstance(source, str) or detail not in DETAIL_LEVELS:
        return None
    if not _all_str(includes) or not _all_str(excludes):
        return None
    return Channel(source=source, detail=detail,
                   includes=tuple(includes), excludes=tuple(excludes))


def _skips_from_list(raw: object) -> list[Skip] | None:
    if not _is_seq(raw):
        return None
    skips: list[Skip] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        category = item.get("category")
        subject  = item.get("subject")
        message  = item.get("message")
        if category not in get_args(SkipCategory):
            return None
        if not isinstance(subject, str) or not isinstance(message, str):
            return None
        skips.append(Skip(category=category, subject=subject, message=message))
    return skips


def _provenance_from_dict(body: object) -> RunProvenance | None:
    if not isinstance(body, dict):
        return None
    backend  = body.get("backend")
    model    = body.get("model")
    language = body.get("language")
    if not isinstance(backend, str) or not isinstance(model, str) or not isinstance(language, str):
        return None
    channels_raw = body.get("channels", [])
    if not _is_seq(channels_raw):
        return None
    channels = tuple(_channel_from_dict(item) for item in channels_raw)
    if any(channel is None for channel in channels):
        return None
    per_channel = body.get("per_channel")
    if per_channel is not None and (isinstance(per_channel, bool) or not isinstance(per_channel, int)):
        return None
    narrowing = {key: body.get(key) for key in ("source_match", "since", "until", "channel")}
    if not all(_is_optional_str(value) for value in narrowing.values()):
        return None
    return RunProvenance(
        backend=backend, model=model, language=language,
        channels=channels,  # type: ignore[arg-type]  # None-checked just above
        per_channel=per_channel, **narrowing,
    )
