"""Persist and reload summaries and transcripts -- the durable analysis corpus.

A digest run's summaries otherwise vanish with the rendered output. A ``Store``
keeps them, and each video's transcript, so a later run can reload a channel's
summaries over a date range and re-synthesize, or re-read a transcript, without
refetching anything.

``Store`` is the pluggable backend interface; ``FileStore`` is the default, a
directory tree under ``CORPUS_ROOT``. A database-backed store can slot in later
behind the same four operations. Every operation is bound to a concrete store,
so there is no "no store" call -- an orchestrator that wants to skip persistence
passes ``None`` in place of a ``Store`` and never reaches these methods.

``save_digest`` / ``load_digests`` persist assembled digests as a point-in-time
record (entries plus the run provenance), addressed by the run's output directory
rather than the content-addressed corpus -- so they are functions over a directory,
not ``Store`` methods (a database backend will grow its own digest table).
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from tubeless.config import data_dir
from tubeless.errors import CorpusError
from tubeless.source import Video
from tubeless.summary import Summary, summary_from_dict, summary_to_dict
from tubeless.transcript import Transcript, TranscriptSegment

if TYPE_CHECKING:
    # Only a type hint on the digest functions, which import digest.py lazily (inside
    # the functions) to keep this persistence module's top level free of the compute
    # layer digest.py pulls in (llm/importance/synthesis). digest.py imports this
    # module only under TYPE_CHECKING, so there is no runtime cycle either way; the
    # ``from __future__`` annotations make this hint a string.
    from tubeless.digest import Digest

__all__ = [
    "CORPUS_ROOT",
    "FileStore",
    "Store",
    "latest_per_video",
    "load_digests",
    "save_digest",
]

CORPUS_ROOT = data_dir() / "corpus"

_SCHEMA_VERSION      = 1
_SUMMARIES_DIRNAME   = "summaries"
_TRANSCRIPTS_DIRNAME = "transcripts"
# The one canonical timestamp form (also used for a feed's published time): a
# single format means lexicographic order equals chronological order, so a
# date-range filter can compare the strings directly.
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class Store(Protocol):
    """Durable backend for summaries and transcripts: four operations, each
    bound to a concrete store (there is no store-less call). The contract each
    method must honour lives here on the interface -- not only on ``FileStore`` --
    so an alternate backend (a database, say) has a spec to implement against."""

    def save_summary(self, summary: Summary) -> None:
        """Persist one summary, keyed so a re-summary at another depth or language
        coexists while a re-run at the same key overwrites."""
        ...

    def save_transcript(self, transcript: Transcript) -> None:
        """Persist one transcript, keyed by video id (one per video)."""
        ...

    def load_summaries(
        self, *, since: str | None = None, until: str | None = None, channel: str | None = None
    ) -> tuple[Summary, ...]:
        """Return stored summaries oldest first, optionally narrowed to one
        ``channel`` and a half-open ISO-8601 date range ``[since, until)`` --
        compared against each summary's published-or-saved time as strings, so a
        bare ``YYYY-MM-DD`` bound works by prefix. A corrupt record reads as
        absent; variants of one video come back in save order (oldest first)."""
        ...

    def load_transcript(self, video_id: str) -> Transcript | None:
        """Return the stored transcript for ``video_id``, or ``None`` if none is
        stored or the file is corrupt (treated as absent)."""
        ...


class FileStore:
    """A ``Store`` backed by a directory tree under ``root``:
    ``summaries/<video_id>.<language>.<detail>.json`` (one file per summary
    variant) and ``transcripts/<video_id>.json`` (one per video). Writes go
    through a temp file then an atomic rename, so a reader never sees a
    half-written file, and re-saving the same key overwrites in place (idempotent).
    """

    def __init__(self, root: Path = CORPUS_ROOT) -> None:
        self._root            = root
        self._summaries_dir   = root / _SUMMARIES_DIRNAME
        self._transcripts_dir = root / _TRANSCRIPTS_DIRNAME

    def save_summary(self, summary: Summary) -> None:
        """Store one summary, keyed by ``(video_id, language, detail)`` so a
        re-summary at another depth or language coexists while a re-run at the
        same key overwrites. Raises ``CorpusError`` on an I/O failure."""
        key      = f"{summary.video.video_id}.{summary.language}.{summary.detail}.json"
        envelope = {
            "schema_version": _SCHEMA_VERSION,
            "saved_at":       _now(),
            "summary":        summary_to_dict(summary),
        }
        _write_json(self._summaries_dir / key, envelope)

    def save_transcript(self, transcript: Transcript) -> None:
        """Store one transcript, keyed by ``video_id`` (one per video). Raises
        ``CorpusError`` on an I/O failure."""
        envelope = {
            "schema_version": _SCHEMA_VERSION,
            "saved_at":       _now(),
            "transcript":     _transcript_to_dict(transcript),
        }
        _write_json(self._transcripts_dir / f"{transcript.video.video_id}.json", envelope)

    def load_summaries(
        self, *, since: str | None = None, until: str | None = None, channel: str | None = None
    ) -> tuple[Summary, ...]:
        """Return stored summaries, oldest first, optionally narrowed to one
        ``channel`` and a half-open date range ``[since, until)``.

        The range is compared against each summary's ``published`` time (falling
        back to its saved-at time when the feed gave none), as ISO-8601 strings --
        so a bare ``YYYY-MM-DD`` bound works by prefix. ``since`` is inclusive,
        ``until`` exclusive. A corrupt file is treated as absent, not raised.

        When the same video has several stored variants (different detail or
        language), each is returned in save order -- oldest ``saved_at`` first --
        so a caller keeping the last per video (see ``latest_per_video``) gets the
        most recently stored one."""
        rows: list[tuple[str, str, Summary]] = []
        for path in sorted(self._summaries_dir.glob("*.json")):
            loaded = _summary_from_envelope(_read_json(path))
            if loaded is None:
                continue
            summary, saved_at = loaded
            if channel is not None and summary.video.channel != channel:
                continue
            moment = summary.video.published or saved_at
            if since is not None and moment < since:
                continue
            if until is not None and moment >= until:
                continue
            rows.append((moment, saved_at, summary))
        # Order by publish time, then by save time -- so two variants of one video
        # (identical published) fall in save order and the most recently stored
        # one sorts last, which is the one ``latest_per_video`` keeps.
        rows.sort(key=lambda row: (row[0], row[1]))
        return tuple(summary for _, _, summary in rows)

    def load_transcript(self, video_id: str) -> Transcript | None:
        """Return the stored transcript for ``video_id``, or ``None`` if none is
        stored (or the file is corrupt -- treated as absent)."""
        return _transcript_from_envelope(_read_json(self._transcripts_dir / f"{video_id}.json"))


def latest_per_video(summaries: Sequence[Summary]) -> list[Summary]:
    """Keep one summary per video: the last in ``summaries``. ``load_summaries``
    orders variants of one video by save time (oldest first), so the last
    occurrence of each video id is its most recently stored summary -- which lets
    a re-curate over a date range never double-count a video that has several
    stored variants (different detail or language)."""
    latest: dict[str, Summary] = {}
    for summary in summaries:
        latest[summary.video.video_id] = summary
    return list(latest.values())


_DIGEST_ENVELOPE_KEY = "digest"


def save_digest(digest: Digest, out_dir: Path) -> Path:
    """Persist one digest as canonical JSON at ``out_dir/<label>.json`` and return
    the path.

    A digest is a point-in-time record -- its entries, what was skipped, and the
    run provenance (which channels and model produced it) -- kept because the LLM
    ranking and synthesis are not deterministically reproducible from the corpus.
    It is addressed by the run's output directory (alongside the rendered ``.md``),
    not by the content-addressed corpus, so this is a function over ``out_dir``
    rather than a ``Store`` method. Re-running the same span overwrites in place
    (the filename is the digest's ``label``), atomically. Raises ``CorpusError`` on
    an I/O failure.
    """
    from tubeless.digest import (
        digest_to_dict,  # lazy: keep this module's top level free of the compute layer
    )
    envelope = {
        "schema_version":     _SCHEMA_VERSION,
        "saved_at":           _now(),
        _DIGEST_ENVELOPE_KEY: digest_to_dict(digest),
    }
    path = out_dir / f"{digest.label}.json"
    _write_json(path, envelope)
    return path


def load_digests(
    out_dir: Path, *, since: str | None = None, until: str | None = None
) -> tuple[Digest, ...]:
    """Return the digests stored in ``out_dir``, oldest first, optionally narrowed
    to a half-open range ``[since, until)`` compared against each digest's
    ``created`` date as an ISO string (so a bare ``YYYY-MM-DD`` bound works by
    prefix). A corrupt or non-digest ``.json`` file is skipped (read as absent), as
    the corpus loaders do -- so an unrelated JSON in the directory is ignored."""
    from tubeless.digest import digest_from_dict  # lazy, as in save_digest
    digests: list[Digest] = []
    for path in sorted(out_dir.glob("*.json")):
        record = _read_json(path)
        if not isinstance(record, dict):
            continue
        digest = digest_from_dict(record.get(_DIGEST_ENVELOPE_KEY))
        if digest is None:
            continue
        if since is not None and digest.created < since:
            continue
        if until is not None and digest.created >= until:
            continue
        digests.append(digest)
    digests.sort(key=lambda digest: digest.created)
    return tuple(digests)


def _now() -> str:
    return datetime.now(UTC).strftime(_TIMESTAMP_FORMAT)


def _summary_from_envelope(record: object) -> tuple[Summary, str] | None:
    """Rebuild ``(summary, saved_at)`` from a stored envelope, or ``None`` if it is
    not a well-formed summary envelope (a corrupt file reads as absent). The summary
    body is rebuilt by ``summary.summary_from_dict`` -- the one home for that
    projection -- so this only owns the envelope's ``saved_at``."""
    if not isinstance(record, dict):
        return None
    saved_at = record.get("saved_at")
    if not isinstance(saved_at, str):
        return None
    summary = summary_from_dict(record.get("summary"))
    if summary is None:
        return None
    return summary, saved_at


def _transcript_to_dict(transcript: Transcript) -> dict[str, object]:
    return {
        "video":             asdict(transcript.video),
        "language":          transcript.language,
        "is_auto_generated": transcript.is_auto_generated,
        "segments":          [asdict(segment) for segment in transcript.segments],
    }


def _transcript_from_envelope(record: object) -> Transcript | None:
    if not isinstance(record, dict):
        return None
    body = record.get("transcript")
    if not isinstance(body, dict):
        return None
    video    = body.get("video")
    segments = body.get("segments")
    language = body.get("language")
    auto     = body.get("is_auto_generated")
    # Validate the leaf values, not just the containers -- as _summary_from_envelope
    # does -- so a hand-edited or schema-drifted file reads as absent rather than
    # minting a Transcript whose language/is_auto_generated violate their own types.
    if not isinstance(video, dict) or not isinstance(segments, list):
        return None
    if not isinstance(language, str) or not isinstance(auto, bool):
        return None
    try:
        return Transcript(
            video             = Video(**video),
            language          = language,
            is_auto_generated = auto,
            segments          = tuple(TranscriptSegment(**segment) for segment in segments),
        )
    except (KeyError, TypeError):
        return None


def _read_json(path: Path) -> object | None:
    """Read and parse a JSON file, returning ``None`` if it is missing or its
    bytes are not valid JSON -- the loaders treat both as 'not stored'. Only a
    genuinely absent file is swallowed; a permission or I/O error propagates
    rather than silently dropping a stored record from a digest."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """Write ``payload`` as JSON atomically: to a temp file, then an atomic
    rename over the target, so a concurrent reader never sees a partial file.
    Raises ``CorpusError`` on any I/O failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)   # atomic within one filesystem
    except OSError as err:
        tmp.unlink(missing_ok=True)
        raise CorpusError(f"could not write {path}: {err}") from err
