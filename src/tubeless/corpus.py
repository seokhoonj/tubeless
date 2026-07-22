"""Archive what the digest learns so later analysis can reuse it.

A digest run's summaries otherwise vanish with the rendered output, so this
module keeps a durable on-disk corpus: every summary record is appended to one
JSONL file, and each video's original transcript is archived once alongside.
A later analysis can then reload one source's summaries chronologically, or
re-read a video's full transcript, without refetching anything.

Two tiers under one root: ``summaries.jsonl`` (append-only, one JSON object
per line, the ``source`` field inside each record) and
``transcripts/<video_id>.json`` (one immutable file per video, so archiving
is idempotent).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from tubeless.errors import CorpusError
from tubeless.importance import Importance
from tubeless.source import Video
from tubeless.transcript import Transcript, TranscriptSegment

__all__ = [
    "CORPUS_ROOT",
    "CorpusEntry",
    "append_entry",
    "load_summaries",
    "archive_transcript",
    "load_transcript",
]

CORPUS_ROOT = Path.home() / ".tubeless" / "corpus"

_SUMMARIES_FILENAME  = "summaries.jsonl"
_TRANSCRIPTS_DIRNAME = "transcripts"


@dataclass(frozen=True, slots=True)
class CorpusEntry:
    """One archived summary record: what a digest run learned about one video,
    under the channel label it was captured for. Whether the source transcript
    was archived is not stored -- it is answered authoritatively by
    ``load_transcript(video.video_id) is not None``, so it cannot drift from the
    files on disk."""

    channel:    str              # digest channel label it was captured under (distinguishes two series on one channel)
    captured:   str              # ISO date (YYYY-MM-DD) of the digest run that captured it
    published:  str              # the upload's publish timestamp from the feed (ISO 8601); may be ""
    video:      Video
    tldr:       str
    points:     tuple[str, ...]
    importance: Importance
    language:   str              # the language the summary was written in


def append_entry(entry: CorpusEntry, *, root: Path | None = None) -> None:
    """Append one summary record to the corpus, creating the root if needed.

    Append-only on purpose, with no dedup: the digest's seen-id state already
    prevents re-summarizing a video, so a re-append does not normally occur --
    and a plain append keeps each write atomic and cheap.

    Raises:
        CorpusError: the corpus file could not be written (I/O error).
    """
    path = (root or CORPUS_ROOT) / _SUMMARIES_FILENAME
    line = json.dumps(_entry_to_dict(entry), ensure_ascii=False)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
    except OSError as err:
        raise CorpusError(f"could not append to corpus file {path}: {err}") from err


def load_summaries(
    channel: str,
    *,
    since:   str | None  = None,
    until:   str | None  = None,
    root:    Path | None = None,
) -> tuple[CorpusEntry, ...]:
    """Return ``channel``'s archived summaries, oldest first, optionally bounded
    to the inclusive date range [``since``, ``until``].

    ``since`` and ``until`` must be zero-padded ``YYYY-MM-DD`` strings (e.g.
    "2026-07-01"). The chronological key is the ``YYYY-MM-DD`` prefix of
    ``published`` (falling back to ``captured`` when the feed gave no publish
    time), and the bounds are compared lexicographically against that prefix --
    so ``until`` includes the whole last day even for a record whose publish
    time is a full timestamp on that day.

    A missing file reads as an empty corpus, and a malformed line is skipped:
    one bad partial write must not lose the rest of the corpus, mirroring how
    the seen-set treats a corrupt state file as absent. A genuine I/O error is
    *not* swallowed.

    Raises:
        CorpusError: the corpus file exists but could not be read (I/O error).
    """
    path = (root or CORPUS_ROOT) / _SUMMARIES_FILENAME
    if not path.exists():
        return ()

    entries = []
    try:
        # Stream and decode line by line: one garbled byte in one record must
        # not fail a whole-file decode, and a long-lived corpus is not slurped
        # whole into memory just to return one channel's rows.
        with path.open("rb") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = _entry_from_dict(json.loads(line.decode("utf-8")))
                except (UnicodeDecodeError, ValueError, KeyError, TypeError):
                    continue   # a malformed line is skipped, never fatal
                if entry.channel != channel:
                    continue
                date = _entry_date(entry)
                if since is not None and date < since:
                    continue
                if until is not None and date > until:
                    continue
                entries.append(entry)
    except OSError as err:
        raise CorpusError(f"could not read corpus file {path}: {err}") from err

    entries.sort(key=_entry_date)   # stable: same-day records keep append order
    return tuple(entries)


def archive_transcript(transcript: Transcript, *, root: Path | None = None) -> None:
    """Archive the transcript under ``transcripts/<video_id>.json``, creating
    the directory if needed.

    Idempotent: an archived transcript is immutable, so if the file already
    exists this returns without rewriting -- the digest can archive on every
    run without needless writes.

    The write is atomic (temp file + rename): a crash mid-write leaves only the
    temp file, never a truncated ``<video_id>.json`` that ``exists()`` would then
    treat as archived while ``load_transcript`` reads it as corrupt.

    Raises:
        CorpusError: the transcript file could not be written (I/O error).
    """
    path = (root or CORPUS_ROOT) / _TRANSCRIPTS_DIRNAME / f"{transcript.video_id}.json"
    try:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{transcript.video_id}.json.tmp")
        tmp.write_text(
            json.dumps(_transcript_to_dict(transcript), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)   # atomic on the same filesystem
    except OSError as err:
        raise CorpusError(f"could not archive transcript {path}: {err}") from err


def load_transcript(video_id: str, *, root: Path | None = None) -> Transcript | None:
    """Return the archived transcript for ``video_id``, or None when it was
    never archived.

    A corrupt (unparseable) file also reads as None -- treated as absent, like
    the seen-set's handling of a corrupt state file -- so one bad write cannot
    crash an analysis. A genuine I/O error is *not* swallowed.

    Raises:
        CorpusError: the transcript file exists but could not be read (I/O
            error).
    """
    path = (root or CORPUS_ROOT) / _TRANSCRIPTS_DIRNAME / f"{video_id}.json"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    except OSError as err:
        raise CorpusError(f"could not read archived transcript {path}: {err}") from err
    try:
        return _transcript_from_dict(json.loads(text))
    except (ValueError, KeyError, TypeError):
        return None


def _entry_date(entry: CorpusEntry) -> str:
    """The entry's chronological key: the YYYY-MM-DD prefix of ``published``,
    or of ``captured`` when the feed gave no publish time."""
    return (entry.published or entry.captured)[:10]


def _entry_to_dict(entry: CorpusEntry) -> dict[str, object]:
    return {
        "channel":    entry.channel,
        "captured":   entry.captured,
        "published":  entry.published,
        "video":      asdict(entry.video),
        "tldr":       entry.tldr,
        "points":     list(entry.points),
        "importance": asdict(entry.importance),
        "language":   entry.language,
    }


def _entry_from_dict(data: dict[str, object]) -> CorpusEntry:
    # Deliberately strict: a missing or misshapen field raises (KeyError /
    # TypeError / ValueError) so the caller can skip that line as malformed.
    return CorpusEntry(
        channel    = data["channel"],
        captured   = data["captured"],
        published  = data["published"],
        video      = Video(**data["video"]),
        tldr       = data["tldr"],
        points     = tuple(data["points"]),
        importance = Importance(**data["importance"]),
        language   = data["language"],
    )


def _transcript_to_dict(transcript: Transcript) -> dict[str, object]:
    return {
        "video_id":          transcript.video_id,
        "language":          transcript.language,
        "is_auto_generated": transcript.is_auto_generated,
        "segments":          [asdict(segment) for segment in transcript.segments],
    }


def _transcript_from_dict(data: dict[str, object]) -> Transcript:
    # Same strictness as _entry_from_dict: raise on a misshapen dict so the
    # caller can treat the file as corrupt and return None.
    return Transcript(
        video_id          = data["video_id"],
        language          = data["language"],
        is_auto_generated = data["is_auto_generated"],
        segments          = tuple(TranscriptSegment(**segment) for segment in data["segments"]),
    )
