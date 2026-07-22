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
    "archive_transcript",
    "load_summaries",
    "load_transcript",
]


class _MalformedRecordError(ValueError):
    """A corpus line decoded as JSON but did not have a CorpusEntry/Transcript
    shape. The ``_*_from_dict`` decoders raise it and the loaders catch it, so a
    genuinely malformed line is skipped while an unrelated ``KeyError`` /
    ``TypeError`` (a real defect) still propagates instead of being swallowed."""

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
                except (UnicodeDecodeError, json.JSONDecodeError, _MalformedRecordError):
                    continue   # a garbled or misshaped line is skipped, never fatal
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
    except (json.JSONDecodeError, _MalformedRecordError):
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


def _entry_from_dict(record: dict[str, object]) -> CorpusEntry:
    # Extract, validate types, then construct -- in that order, so the three error
    # classes stay distinct: a missing field (KeyError) or a wrong JSON type is a
    # malformed *line* (-> _MalformedRecordError, skipped by the loader); a wrong
    # sub-object shape is likewise malformed *data*; but the CorpusEntry
    # construction is our own code, so a TypeError there (a field we renamed and
    # forgot to update) propagates as the real defect it is rather than being
    # swallowed as "malformed". The isinstance guards also reject a scalar where a
    # container is expected (a JSON string "points": "abc" that tuple() would
    # otherwise split into ('a','b','c')).
    try:
        channel    = record["channel"]
        captured   = record["captured"]
        published  = record["published"]
        video      = record["video"]
        tldr       = record["tldr"]
        points     = record["points"]
        importance = record["importance"]
        language   = record["language"]
    except KeyError as err:
        raise _MalformedRecordError(f"corpus record is missing field {err}") from err
    if not (isinstance(channel, str) and isinstance(captured, str) and isinstance(published, str)
            and isinstance(tldr, str) and isinstance(language, str)
            and isinstance(points, list) and isinstance(video, dict) and isinstance(importance, dict)):
        raise _MalformedRecordError("corpus record has a field of the wrong JSON type")
    try:
        video_obj      = Video(**video)
        importance_obj = Importance(**importance)
    except TypeError as err:
        raise _MalformedRecordError(f"corpus record sub-object is malformed: {err}") from err
    return CorpusEntry(
        channel    = channel,
        captured   = captured,
        published  = published,
        video      = video_obj,
        tldr       = tldr,
        points     = tuple(points),
        importance = importance_obj,
        language   = language,
    )


def _transcript_to_dict(transcript: Transcript) -> dict[str, object]:
    return {
        "video_id":          transcript.video_id,
        "language":          transcript.language,
        "is_auto_generated": transcript.is_auto_generated,
        "segments":          [asdict(segment) for segment in transcript.segments],
    }


def _transcript_from_dict(record: dict[str, object]) -> Transcript:
    # Same extract -> validate -> construct order as _entry_from_dict, for the same
    # reason: a malformed line is skipped, a real Transcript field rename propagates.
    try:
        video_id          = record["video_id"]
        language          = record["language"]
        is_auto_generated = record["is_auto_generated"]
        segments          = record["segments"]
    except KeyError as err:
        raise _MalformedRecordError(f"transcript record is missing field {err}") from err
    if not (isinstance(video_id, str) and isinstance(language, str)
            and isinstance(is_auto_generated, bool) and isinstance(segments, list)):
        raise _MalformedRecordError("transcript record has a field of the wrong JSON type")
    try:
        segment_objs = tuple(TranscriptSegment(**segment) for segment in segments)
    except TypeError as err:
        raise _MalformedRecordError(f"transcript segment is malformed: {err}") from err
    return Transcript(
        video_id          = video_id,
        language          = language,
        is_auto_generated = is_auto_generated,
        segments          = segment_objs,
    )
