"""FileStore round-trips, keying, date/channel filtering, and atomic writes."""

import json

import pytest

from tubeless.errors import CorpusError
from tubeless.source import Video
from tubeless.store import FileStore
from tubeless.summary import Summary
from tubeless.transcript import Transcript, TranscriptSegment


def _video(video_id: str = "dQw4w9WgXcQ", *, channel="Chan", published=None) -> Video:
    return Video(
        video_id  = video_id,
        title     = f"Video {video_id}",
        url       = f"https://www.youtube.com/watch?v={video_id}",
        channel   = channel,
        published = published,
    )


def _summary(video: Video, *, tldr="gist", points=("a", "b"), language="en", detail="normal") -> Summary:
    return Summary(video=video, tldr=tldr, points=points, language=language, detail=detail)


def _transcript(video_id: str = "dQw4w9WgXcQ") -> Transcript:
    return Transcript(
        video_id=video_id, language="en", is_auto_generated=False,
        segments=(TranscriptSegment(text="hello world", start=0.0, duration=2.0),),
    )


def test_save_then_load_transcript_round_trips(tmp_path):
    store = FileStore(tmp_path)
    transcript = _transcript()

    store.save_transcript(transcript)

    assert store.load_transcript("dQw4w9WgXcQ") == transcript


def test_load_transcript_returns_none_when_absent(tmp_path):
    assert FileStore(tmp_path).load_transcript("dQw4w9WgXcQ") is None


def test_save_then_load_summary_round_trips(tmp_path):
    store   = FileStore(tmp_path)
    summary = _summary(_video(published="2026-07-20T09:00:00Z"))

    store.save_summary(summary)

    assert store.load_summaries() == (summary,)


def test_summary_key_includes_language_and_detail_so_variants_coexist(tmp_path):
    store = FileStore(tmp_path)
    normal = _summary(_video(), detail="normal")
    deep   = _summary(_video(), detail="deep")

    store.save_summary(normal)
    store.save_summary(deep)   # same video, different detail -> a second file

    files = sorted(p.name for p in (tmp_path / "summaries").glob("*.json"))
    assert files == ["dQw4w9WgXcQ.en.deep.json", "dQw4w9WgXcQ.en.normal.json"]
    assert set(store.load_summaries()) == {normal, deep}


def test_re_saving_the_same_key_overwrites_in_place(tmp_path):
    store = FileStore(tmp_path)
    store.save_summary(_summary(_video(), tldr="first"))
    store.save_summary(_summary(_video(), tldr="second"))   # same (id, lang, detail)

    loaded = store.load_summaries()
    assert len(loaded) == 1
    assert loaded[0].tldr == "second"


def test_load_summaries_filters_by_channel(tmp_path):
    store = FileStore(tmp_path)
    store.save_summary(_summary(_video("aaaaaaaaaaa", channel="A")))
    store.save_summary(_summary(_video("bbbbbbbbbbb", channel="B")))

    loaded = store.load_summaries(channel="A")

    assert [s.video.video_id for s in loaded] == ["aaaaaaaaaaa"]


def test_load_summaries_filters_a_half_open_date_range_on_published(tmp_path):
    store = FileStore(tmp_path)
    store.save_summary(_summary(_video("aaaaaaaaaaa", published="2026-07-01T00:00:00Z")))
    store.save_summary(_summary(_video("bbbbbbbbbbb", published="2026-07-07T00:00:00Z")))
    store.save_summary(_summary(_video("ccccccccccc", published="2026-07-08T00:00:00Z")))

    # [2026-07-01, 2026-07-08): since inclusive, until exclusive
    loaded = store.load_summaries(since="2026-07-01", until="2026-07-08")

    assert [s.video.video_id for s in loaded] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_load_summaries_returns_them_oldest_first(tmp_path):
    store = FileStore(tmp_path)
    store.save_summary(_summary(_video("bbbbbbbbbbb", published="2026-07-07T00:00:00Z")))
    store.save_summary(_summary(_video("aaaaaaaaaaa", published="2026-07-01T00:00:00Z")))

    loaded = store.load_summaries()

    assert [s.video.published for s in loaded] == ["2026-07-01T00:00:00Z", "2026-07-07T00:00:00Z"]


def test_load_summaries_is_empty_before_anything_is_saved(tmp_path):
    assert FileStore(tmp_path).load_summaries() == ()


def test_a_corrupt_summary_file_is_skipped_not_raised(tmp_path):
    store = FileStore(tmp_path)
    store.save_summary(_summary(_video("aaaaaaaaaaa")))
    (tmp_path / "summaries" / "bbbbbbbbbbb.en.normal.json").write_text("{not json", encoding="utf-8")

    loaded = store.load_summaries()

    assert [s.video.video_id for s in loaded] == ["aaaaaaaaaaa"]


def test_save_writes_atomically_leaving_no_temp_file(tmp_path):
    store = FileStore(tmp_path)
    store.save_summary(_summary(_video()))

    leftovers = list((tmp_path / "summaries").glob("*.tmp"))
    assert leftovers == []


def test_save_summary_wraps_an_io_failure_as_corpus_error(tmp_path, monkeypatch):
    store = FileStore(tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")

    # Fail the temp-file write; the error must surface as CorpusError, not a raw OSError.
    monkeypatch.setattr("pathlib.Path.write_text", boom)

    with pytest.raises(CorpusError):
        store.save_summary(_summary(_video()))


def test_stored_summary_envelope_carries_schema_version_and_saved_at(tmp_path):
    store = FileStore(tmp_path)
    store.save_summary(_summary(_video()))

    record = json.loads((tmp_path / "summaries" / "dQw4w9WgXcQ.en.normal.json").read_text())
    assert record["schema_version"] == 1
    assert record["saved_at"].endswith("Z")
    assert record["summary"]["detail"] == "normal"
