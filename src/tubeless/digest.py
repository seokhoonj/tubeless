"""Assemble one day's digest: for each channel, fetch new uploads, summarize and
score them, and collect the results ranked by importance.

This is the orchestration layer over the single-video engine (transcript +
summary) and the feed/importance modules. It holds no state of its own beyond
the run: it takes the already-seen ids in and reports which ids it processed, so
the caller persists them.
"""

from __future__ import annotations

from dataclasses import dataclass

from tubeless.channels import Channel
from tubeless.errors import FeedError, TranscriptUnavailable
from tubeless.feed import Upload, fetch_uploads
from tubeless.importance import Importance, score_importance
from tubeless.llm import LLMBackend
from tubeless.source import Video
from tubeless.summary import Summary, summarize
from tubeless.transcript import fetch_transcript

__all__ = ["Digest", "DigestEntry", "build_digest"]


@dataclass(frozen=True, slots=True)
class DigestEntry:
    """One summarized, scored video in a digest."""

    channel:    str
    upload:     Upload
    summary:    Summary
    importance: Importance


@dataclass(frozen=True, slots=True)
class Digest:
    """One day's collected entries (most-important first) plus one note per
    channel that could not be read, so the render can surface the gap."""

    date:    str
    entries: tuple[DigestEntry, ...]
    skipped: tuple[str, ...]


def build_digest(
    channels: tuple[Channel, ...],
    backend:  LLMBackend,
    *,
    date:              str,
    seen:              set[str],
    language:          str = "ko",
    per_channel_limit: int = 5,
) -> tuple[Digest, set[str]]:
    """Build the digest for ``date`` from the new uploads of ``channels``.

    Only uploads whose id is not in ``seen`` are processed. A video with no
    transcript is skipped (not fatal) but still counted as processed, so it is
    not retried on the next run. A channel whose feed cannot be read is recorded
    in ``Digest.skipped`` and the run continues.

    Returns the Digest and the set of newly processed video ids; the caller
    merges these into ``seen`` and persists them.

    Raises:
        LLMError: propagated from the backend (a credential/credit problem is
            global, so it should stop the run rather than be swallowed per video).
    """
    entries:   list[DigestEntry] = []
    skipped:   list[str] = []
    processed: set[str] = set()

    for channel in channels:
        try:
            uploads = fetch_uploads(channel.source, limit=per_channel_limit)
        except FeedError as err:
            skipped.append(f"{channel.label}: {err}")
            continue

        for upload in uploads:
            if upload.video_id in seen or upload.video_id in processed:
                continue
            processed.add(upload.video_id)
            entry = _summarize_upload(upload, channel, backend, language=language)
            if entry is not None:
                entries.append(entry)

    entries.sort(key=lambda entry: entry.importance.score, reverse=True)
    return Digest(date=date, entries=tuple(entries), skipped=tuple(skipped)), processed


def _summarize_upload(
    upload: Upload, channel: Channel, backend: LLMBackend, *, language: str
) -> DigestEntry | None:
    try:
        transcript = fetch_transcript(upload.video_id)
    except TranscriptUnavailable:
        return None

    video = Video(
        video_id = upload.video_id,
        title    = upload.title,
        url      = f"https://www.youtube.com/watch?v={upload.video_id}",
        channel  = upload.channel_title or channel.label,
    )
    summary    = summarize(transcript, video, backend, target_language=language, detail=channel.detail)
    importance = score_importance(summary, backend, language=language)
    return DigestEntry(channel=channel.label, upload=upload, summary=summary, importance=importance)
