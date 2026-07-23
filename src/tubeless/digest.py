"""Curate one day's digest: for each channel, fetch new uploads, summarize and
score them, and collect the results ranked by importance.

This is the orchestration layer over the single-video engine (transcript +
summary) and the feed/importance modules. It holds no state of its own beyond
the run: it takes the already-seen ids in and reports which ids it processed, so
the caller persists them.
"""

from __future__ import annotations

from collections.abc import Container, Iterable
from dataclasses import dataclass
from pathlib import Path

from tubeless.channels import Channel
from tubeless.corpus import CorpusEntry, append_entry, archive_transcript
from tubeless.errors import FeedError, TranscriptUnavailable
from tubeless.feed import Upload, fetch_uploads
from tubeless.importance import Importance, score_importance
from tubeless.llm import LLMBackend
from tubeless.source import Video
from tubeless.summary import DEFAULT_LANGUAGE, Summary, summarize_transcript
from tubeless.synthesis import Synthesis, synthesize
from tubeless.transcript import Transcript, fetch_transcript

__all__ = ["DEFAULT_PER_CHANNEL_LIMIT", "Digest", "DigestEntry", "curate", "record_entry"]

# YouTube's RSS feed tops out near 15 entries; a filtered source scans them all.
_FILTERED_FETCH_LIMIT = 15

# How many recent uploads to check per channel when the caller does not say.
# Named here (not re-spelled in the CLI) so the default has one home.
DEFAULT_PER_CHANNEL_LIMIT = 5


@dataclass(frozen=True, slots=True)
class DigestEntry:
    """One summarized, scored video in a digest. ``transcript`` is the source it
    was summarized from -- kept so the caller can archive it to the corpus, since
    the summary alone is lossy and the transcript cannot be refetched once a
    video's captions are disabled or it is removed."""

    channel:    str
    upload:     Upload
    summary:    Summary
    importance: Importance
    transcript: Transcript


@dataclass(frozen=True, slots=True)
class Digest:
    """One day's collected entries (most-important first), one note per channel
    that could not be read (so the render can surface the gap), and an optional
    cross-source synthesis of the day (``None`` unless it was requested)."""

    date:      str
    entries:   tuple[DigestEntry, ...]
    skipped:   tuple[str, ...]
    synthesis: Synthesis | None = None


def curate(
    channels: Iterable[Channel],
    backend:  LLMBackend,
    *,
    date:              str,
    seen:              Container[str],
    language:          str = DEFAULT_LANGUAGE,
    per_channel_limit: int = DEFAULT_PER_CHANNEL_LIMIT,
    with_synthesis:    bool = False,
) -> tuple[Digest, set[str]]:
    """Curate the digest for ``date`` from the new uploads of ``channels``.

    Only uploads whose id is not in ``seen`` are processed. A video with no
    transcript is skipped (not fatal) but still counted as processed, so it is
    not retried on the next run. A channel whose feed cannot be read is recorded
    in ``Digest.skipped`` and the run continues.

    Returns the Digest and the set of newly processed video ids; the caller
    merges these into ``seen`` and persists them.

    Raises:
        LLMError: propagated from the backend (a credential/credit problem is
            global, so it should stop the run rather than be swallowed per video).
        TranscriptFetchBlocked: propagated from ``fetch_transcript`` when YouTube
            transiently blocks the run. Aborts before the caller persists state,
            so the affected videos are retried next run rather than lost.
    """
    entries:   list[DigestEntry] = []
    skipped:   list[str] = []
    processed: set[str] = set()

    for channel in channels:
        # A filtered channel scans the full feed window, not just the first few:
        # the wanted uploads (e.g. one host's episodes) are sparse among the rest.
        has_title_filter = bool(channel.title_includes or channel.title_excludes)
        fetch_limit      = _FILTERED_FETCH_LIMIT if has_title_filter else per_channel_limit
        try:
            uploads = fetch_uploads(channel.source, limit=fetch_limit)
        except FeedError as err:
            skipped.append(f"{channel.label}: {err}")
            continue
        uploads = _uploads_matching_title(uploads, channel.title_includes, channel.title_excludes)

        for upload in uploads:
            if upload.video_id in seen or upload.video_id in processed:
                continue
            processed.add(upload.video_id)
            entry = _summarize_upload(upload, channel, backend, language=language)
            if entry is not None:
                entries.append(entry)

    entries.sort(key=lambda entry: entry.importance.score, reverse=True)

    # A synthesis needs at least two videos -- one source cannot agree or disagree
    # with itself -- and costs one extra backend call, so it is opt-in.
    synthesis = None
    if with_synthesis and len(entries) >= 2:
        synthesis = synthesize(
            [(entry.channel, entry.summary) for entry in entries], backend, language=language
        )
    digest = Digest(date=date, entries=tuple(entries), skipped=tuple(skipped), synthesis=synthesis)
    return digest, processed


def _uploads_matching_title(
    uploads:  tuple[Upload, ...],
    includes: tuple[str, ...],
    excludes: tuple[str, ...],
) -> tuple[Upload, ...]:
    """Keep uploads whose title contains every ``includes`` keyword and none of
    the ``excludes`` keywords (case-insensitive). Empty ``includes`` keeps all;
    empty ``excludes`` drops none."""
    if not includes and not excludes:
        return uploads
    wanted   = [word.lower() for word in includes]
    unwanted = [word.lower() for word in excludes]

    def matches(upload: Upload) -> bool:
        title = upload.title.lower()   # lower once per upload, not once per keyword
        return (all(word in title for word in wanted)
                and not any(word in title for word in unwanted))

    return tuple(upload for upload in uploads if matches(upload))


def _summarize_upload(
    upload: Upload, channel: Channel, backend: LLMBackend, *, language: str
) -> DigestEntry | None:
    video = Video(
        video_id = upload.video_id,
        title    = upload.title,
        url      = f"https://www.youtube.com/watch?v={upload.video_id}",
        channel  = upload.channel or channel.label,
    )
    try:
        transcript = fetch_transcript(video)
    except TranscriptUnavailable:
        return None

    summary    = summarize_transcript(video, transcript, backend, detail=channel.detail, language=language)
    importance = score_importance(summary, backend, language=language)
    return DigestEntry(
        channel=channel.label, upload=upload, summary=summary,
        importance=importance, transcript=transcript,
    )


def record_entry(entry: DigestEntry, captured: str, *, root: Path | None = None) -> None:
    """Archive one digest entry to the corpus: append its summary as a record
    (under the channel it was captured for, dated ``captured``) and archive its
    source transcript once.

    This owns the projection from a ``DigestEntry`` to a durable ``CorpusEntry``
    -- which of the summary/importance/upload fields make up the record -- so a
    caller only orchestrates the loop over the entries and decides how to report a
    failure. It lives here, beside ``DigestEntry``, and calls down into the corpus
    storage layer (the orchestration -> storage direction), rather than the corpus
    reaching up for a ``DigestEntry`` it should not know about.

    Raises:
        CorpusError: the summary record or the transcript could not be written.
    """
    append_entry(
        CorpusEntry(
            channel    = entry.channel,
            captured   = captured,
            published  = entry.upload.published,
            video      = entry.summary.video,
            tldr       = entry.summary.tldr,
            points     = entry.summary.points,
            importance = entry.importance,
            language   = entry.summary.language,
        ),
        root=root,
    )
    archive_transcript(entry.transcript, root=root)
