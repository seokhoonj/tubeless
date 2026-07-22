"""Corpus persistence: summary-record roundtrip, source and date filtering,
chronological order, malformed-line resilience, and transcript archiving."""

from pathlib import Path

import pytest

from tubeless.corpus import (
    CorpusEntry,
    append_entry,
    archive_transcript,
    load_summaries,
    load_transcript,
)
from tubeless.digest import DigestEntry, record_entry
from tubeless.errors import CorpusError
from tubeless.feed import Upload
from tubeless.importance import Importance
from tubeless.source import Video
from tubeless.summary import Summary
from tubeless.transcript import Transcript, TranscriptSegment


def _entry(
    video_id:  str = "aaaaaaaaaaa",
    *,
    channel:   str = "Channel A",
    captured:  str = "2026-07-22",
    published: str = "2026-07-21T09:00:00+00:00",
) -> CorpusEntry:
    return CorpusEntry(
        channel    = channel,
        captured   = captured,
        published  = published,
        video      = Video(
            video_id = video_id,
            title    = f"Video {video_id}",
            url      = f"https://www.youtube.com/watch?v={video_id}",
            channel  = "Uploader",
        ),
        tldr       = "One-line gist.",
        points     = ("first point", "second point"),
        importance = Importance(score=0.8, reason="consequential"),
        language   = "en",
    )


def _transcript(video_id: str = "aaaaaaaaaaa", *, language: str = "en") -> Transcript:
    return Transcript(
        video_id          = video_id,
        language          = language,
        is_auto_generated = False,
        segments          = (
            TranscriptSegment(text="hello", start=0.0, duration=1.5),
            TranscriptSegment(text="world", start=1.5, duration=2.0),
        ),
    )


def test_append_then_load_roundtrips_an_entry(tmp_path):
    entry = _entry()
    append_entry(entry, root=tmp_path)

    assert load_summaries("Channel A", root=tmp_path) == (entry,)


def test_load_summaries_filters_by_channel(tmp_path):
    append_entry(_entry("aaaaaaaaaaa", channel="Channel A"), root=tmp_path)
    append_entry(_entry("bbbbbbbbbbb", channel="Channel B"), root=tmp_path)

    loaded = load_summaries("Channel B", root=tmp_path)

    assert [entry.video.video_id for entry in loaded] == ["bbbbbbbbbbb"]


def test_load_summaries_filters_by_since_and_until(tmp_path):
    append_entry(_entry("aaaaaaaaaaa", published="2026-06-30T23:00:00+00:00"), root=tmp_path)
    append_entry(_entry("bbbbbbbbbbb", published="2026-07-15T12:00:00+00:00"), root=tmp_path)
    append_entry(_entry("ccccccccccc", published="2026-07-31T18:00:00+00:00"), root=tmp_path)

    loaded = load_summaries("Channel A", since="2026-07-01", until="2026-07-31", root=tmp_path)

    # until covers its whole last day: the 18:00 timestamp on 07-31 is included.
    assert [entry.video.video_id for entry in loaded] == ["bbbbbbbbbbb", "ccccccccccc"]


def test_load_summaries_returns_oldest_first(tmp_path):
    append_entry(_entry("ccccccccccc", published="2026-07-03T08:00:00+00:00"), root=tmp_path)
    append_entry(_entry("aaaaaaaaaaa", published="2026-07-01T08:00:00+00:00"), root=tmp_path)
    # No publish time from the feed: the capture date orders this entry instead.
    append_entry(_entry("bbbbbbbbbbb", published="", captured="2026-07-02"), root=tmp_path)

    loaded = load_summaries("Channel A", root=tmp_path)

    assert [entry.video.video_id for entry in loaded] == [
        "aaaaaaaaaaa",
        "bbbbbbbbbbb",
        "ccccccccccc",
    ]


def test_load_summaries_skips_a_malformed_line(tmp_path):
    append_entry(_entry("aaaaaaaaaaa", published="2026-07-01T08:00:00+00:00"), root=tmp_path)
    with (tmp_path / "summaries.jsonl").open("a", encoding="utf-8") as file:
        file.write("{not json\n")
    append_entry(_entry("bbbbbbbbbbb", published="2026-07-02T08:00:00+00:00"), root=tmp_path)

    loaded = load_summaries("Channel A", root=tmp_path)

    assert [entry.video.video_id for entry in loaded] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_load_summaries_missing_file_is_empty(tmp_path):
    assert load_summaries("Channel A", root=tmp_path) == ()


def test_archive_then_load_roundtrips_segments(tmp_path):
    transcript = _transcript()
    archive_transcript(transcript, root=tmp_path)

    assert load_transcript("aaaaaaaaaaa", root=tmp_path) == transcript


def test_archive_transcript_is_idempotent(tmp_path):
    first  = _transcript(language="en")
    second = _transcript(language="ko")   # same video_id, different content
    archive_transcript(first, root=tmp_path)
    archive_transcript(second, root=tmp_path)

    # The first archive won: an archived transcript is immutable, so the
    # second call was a no-op rather than a rewrite.
    assert load_transcript("aaaaaaaaaaa", root=tmp_path) == first


def test_load_transcript_missing_video_is_none(tmp_path):
    assert load_transcript("aaaaaaaaaaa", root=tmp_path) is None


def test_load_transcript_corrupt_file_is_none(tmp_path):
    path = tmp_path / "transcripts" / "aaaaaaaaaaa.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert load_transcript("aaaaaaaaaaa", root=tmp_path) is None


def test_load_summaries_skips_a_wrong_shape_line(tmp_path):
    # Valid JSON but missing required fields: distinct from the invalid-JSON case,
    # this exercises the _entry_from_dict KeyError/TypeError skip path.
    append_entry(_entry("aaaaaaaaaaa", published="2026-07-01T08:00:00+00:00"), root=tmp_path)
    with (tmp_path / "summaries.jsonl").open("a", encoding="utf-8") as file:
        file.write('{"channel": "Channel A", "captured": "2026-07-02"}\n')
    append_entry(_entry("bbbbbbbbbbb", published="2026-07-03T08:00:00+00:00"), root=tmp_path)

    loaded = load_summaries("Channel A", root=tmp_path)

    assert [entry.video.video_id for entry in loaded] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_load_summaries_skips_a_scalar_points_line(tmp_path):
    # Valid JSON with the right keys but a string where "points" should be a list:
    # tuple("abc") would silently split it into ('a','b','c'), so the shape guard
    # must reject the line as malformed rather than store a bogus 3-point entry.
    append_entry(_entry("aaaaaaaaaaa", published="2026-07-01T08:00:00+00:00"), root=tmp_path)
    with (tmp_path / "summaries.jsonl").open("a", encoding="utf-8") as file:
        file.write(
            '{"channel": "Channel A", "captured": "2026-07-02", "published": "",'
            ' "video": {"video_id": "x", "title": "t", "url": "u", "channel": "c"},'
            ' "tldr": "g", "points": "abc",'
            ' "importance": {"score": 0.5, "reason": "r"}, "language": "en"}\n'
        )
    append_entry(_entry("bbbbbbbbbbb", published="2026-07-03T08:00:00+00:00"), root=tmp_path)

    loaded = load_summaries("Channel A", root=tmp_path)

    assert [entry.video.video_id for entry in loaded] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_load_summaries_skips_a_wrong_type_scalar_line(tmp_path):
    # A line matching the channel filter but whose "tldr" is a number, not a
    # string: the scalar type guard must reject it rather than build a
    # CorpusEntry(tldr=456) that lies about its declared str field.
    append_entry(_entry("aaaaaaaaaaa", published="2026-07-01T08:00:00+00:00"), root=tmp_path)
    with (tmp_path / "summaries.jsonl").open("a", encoding="utf-8") as file:
        file.write(
            '{"channel": "Channel A", "captured": "2026-07-02", "published": "",'
            ' "video": {"video_id": "x", "title": "t", "url": "u", "channel": "c"},'
            ' "tldr": 456, "points": ["p"],'
            ' "importance": {"score": 0.5, "reason": "r"}, "language": "en"}\n'
        )
    append_entry(_entry("bbbbbbbbbbb", published="2026-07-03T08:00:00+00:00"), root=tmp_path)

    loaded = load_summaries("Channel A", root=tmp_path)

    assert [entry.video.video_id for entry in loaded] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_load_transcript_wrong_shape_file_is_none(tmp_path):
    # Valid JSON but "segments" is a string, not a list: the shape guard must read
    # it as corrupt (None), mirroring the summaries-side scalar guard, rather than
    # the invalid-JSON path the other transcript test covers.
    path = tmp_path / "transcripts" / "aaaaaaaaaaa.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"video_id": "aaaaaaaaaaa", "language": "en",'
        ' "is_auto_generated": false, "segments": "abc"}',
        encoding="utf-8",
    )

    assert load_transcript("aaaaaaaaaaa", root=tmp_path) is None


def test_record_entry_appends_the_summary_and_archives_the_transcript(tmp_path):
    upload = Upload(
        video_id      = "aaaaaaaaaaa",
        title         = "Video aaaaaaaaaaa",
        published     = "2026-07-21T09:00:00+00:00",
        channel_id    = "UC00000000000000000000",
        channel = "Uploader",
    )
    summary = Summary(
        video    = Video(
            video_id = "aaaaaaaaaaa",
            title    = "Video aaaaaaaaaaa",
            url      = "https://www.youtube.com/watch?v=aaaaaaaaaaa",
            channel  = "Uploader",
        ),
        tldr     = "One-line gist.",
        points   = ("first point", "second point"),
        language = "en",
    )
    entry = DigestEntry(
        channel    = "Channel A",
        upload     = upload,
        summary    = summary,
        importance = Importance(score=0.8, reason="consequential"),
        transcript = _transcript(),
    )

    record_entry(entry, "2026-07-22", root=tmp_path)

    loaded = load_summaries("Channel A", root=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].captured  == "2026-07-22"   # the digest date, not the publish date
    assert loaded[0].published == "2026-07-21T09:00:00+00:00"
    assert loaded[0].points    == ("first point", "second point")
    assert load_transcript("aaaaaaaaaaa", root=tmp_path) == _transcript()


def test_load_summaries_with_a_single_bound(tmp_path):
    append_entry(_entry("aaaaaaaaaaa", published="2026-07-01T08:00:00+00:00"), root=tmp_path)
    append_entry(_entry("bbbbbbbbbbb", published="2026-07-10T08:00:00+00:00"), root=tmp_path)

    since_only = load_summaries("Channel A", since="2026-07-05", root=tmp_path)
    until_only = load_summaries("Channel A", until="2026-07-05", root=tmp_path)

    assert [entry.video.video_id for entry in since_only] == ["bbbbbbbbbbb"]
    assert [entry.video.video_id for entry in until_only] == ["aaaaaaaaaaa"]


def test_load_summaries_includes_the_since_and_until_days(tmp_path):
    # Records dated exactly on each bound are inclusive.
    append_entry(_entry("aaaaaaaaaaa", published="2026-07-01T00:00:00+00:00"), root=tmp_path)
    append_entry(_entry("bbbbbbbbbbb", published="2026-07-31T23:00:00+00:00"), root=tmp_path)

    loaded = load_summaries("Channel A", since="2026-07-01", until="2026-07-31", root=tmp_path)

    assert [entry.video.video_id for entry in loaded] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_append_entry_wraps_a_write_error_as_corpus_error(tmp_path):
    # A file where the corpus dir should be makes mkdir fail with OSError.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(CorpusError):
        append_entry(_entry(), root=blocker / "corpus")


def test_archive_transcript_wraps_a_write_error_as_corpus_error(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(CorpusError):
        archive_transcript(_transcript(), root=blocker / "corpus")


def test_load_summaries_wraps_a_read_error_as_corpus_error(tmp_path, monkeypatch):
    append_entry(_entry(), root=tmp_path)

    def raise_oserror(self, *args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(Path, "open", raise_oserror)

    with pytest.raises(CorpusError):
        load_summaries("Channel A", root=tmp_path)
