"""FileStore round-trips, keying, date/channel filtering, and atomic writes."""

import json

import pytest

from tubeless.errors import CorpusError
from tubeless.source import Video
from tubeless.store import FileStore, latest_per_video
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
        video=_video(video_id), language="en", is_auto_generated=False,
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


def _write_summary_file(tmp_path, *, detail: str, saved_at: str, tldr: str, video: Video) -> None:
    """Author a summary envelope directly, so a test can control saved_at (which
    FileStore stamps with the wall clock) and inject a deliberately corrupt one."""
    path = tmp_path / "summaries" / f"{video.video_id}.en.{detail}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "saved_at":       saved_at,
        "summary": {
            "video":    {"video_id": video.video_id, "title": video.title,
                         "url": video.url, "channel": video.channel, "published": video.published},
            "tldr":     tldr,
            "points":   ["a"],
            "language": "en",
            "detail":   detail,
        },
    }), encoding="utf-8")


def test_load_orders_variants_of_one_video_by_save_time(tmp_path):
    # Two variants of one video (same published) must come back in save order, so
    # a caller keeping the last per video gets the most recently stored one --
    # not the alphabetically-last filename.
    video = _video(published="2026-07-20T09:00:00Z")
    _write_summary_file(tmp_path, detail="normal", saved_at="2026-07-20T10:00:00Z", tldr="older", video=video)
    _write_summary_file(tmp_path, detail="deep",   saved_at="2026-07-20T11:00:00Z", tldr="newer", video=video)

    loaded = FileStore(tmp_path).load_summaries()

    # 'deep' sorts before 'normal' by filename, but it was saved later, so it is last
    assert [s.detail for s in loaded] == ["normal", "deep"]
    assert loaded[-1].tldr == "newer"


def test_load_summaries_falls_back_to_saved_at_when_published_is_absent(tmp_path):
    # A summary whose feed gave no publish time is filtered by its saved_at, and
    # must not raise on the None published value.
    video = _video("aaaaaaaaaaa", published=None)
    _write_summary_file(tmp_path, detail="normal", saved_at="2026-07-05T09:00:00Z", tldr="g", video=video)

    kept    = FileStore(tmp_path).load_summaries(since="2026-07-01", until="2026-07-08")
    dropped = FileStore(tmp_path).load_summaries(since="2026-07-06")

    assert [s.video.video_id for s in kept] == ["aaaaaaaaaaa"]
    assert dropped == ()   # saved_at 2026-07-05 is before since=2026-07-06


def test_a_summary_file_with_an_illegal_detail_reads_as_absent(tmp_path):
    # A schema-drifted file must not mint a Summary whose detail violates the
    # DetailLevel Literal; it reads as absent, like a corrupt file.
    _write_summary_file(tmp_path, detail="wobble", saved_at="2026-07-20T10:00:00Z",
                        tldr="g", video=_video("aaaaaaaaaaa"))

    assert FileStore(tmp_path).load_summaries() == ()


def test_a_corrupt_transcript_file_reads_as_absent(tmp_path):
    store = FileStore(tmp_path)
    store.save_transcript(_transcript("aaaaaaaaaaa"))
    # invalid JSON, and a valid-JSON-but-shapeless body: both must read as absent
    (tmp_path / "transcripts" / "bbbbbbbbbbb.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "transcripts" / "ccccccccccc.json").write_text('{"transcript": 42}', encoding="utf-8")

    assert store.load_transcript("aaaaaaaaaaa") is not None
    assert store.load_transcript("bbbbbbbbbbb") is None
    assert store.load_transcript("ccccccccccc") is None


def test_latest_per_video_keeps_the_last_summary_per_video():
    # load_summaries returns variants oldest-first, so the last occurrence of a
    # video id is its most recently stored summary -- the one a re-curate keeps.
    video    = _video("aaaaaaaaaaa")
    older    = _summary(video, tldr="old", detail="brief")
    newer    = _summary(video, tldr="new", detail="deep")
    other    = _summary(_video("bbbbbbbbbbb"), tldr="other")

    result = latest_per_video([older, newer, other])

    assert {s.video.video_id for s in result} == {"aaaaaaaaaaa", "bbbbbbbbbbb"}
    kept = next(s for s in result if s.video.video_id == "aaaaaaaaaaa")
    assert kept.tldr == "new"   # the later variant wins, not the earlier


def test_latest_per_video_of_nothing_is_empty():
    assert latest_per_video([]) == []


# --- digest persistence: point-in-time records addressed by the output dir -------

def _digest(created: str, *, entries=()):
    from tubeless.digest import Digest, RunProvenance
    return Digest(created=created, entries=entries,
                  provenance=RunProvenance(backend="gemini", model="m", language="en"))


def test_save_digest_writes_json_named_by_label(tmp_path):
    from tubeless.store import save_digest
    path = save_digest(_digest("2026-07-25"), tmp_path)
    assert path == tmp_path / "2026-07-25.json"
    assert path.exists()


def test_load_digests_returns_saved_oldest_first(tmp_path):
    from tubeless.store import load_digests, save_digest
    save_digest(_digest("2026-07-25"), tmp_path)
    save_digest(_digest("2026-07-24"), tmp_path)
    assert [d.created for d in load_digests(tmp_path)] == ["2026-07-24", "2026-07-25"]


def test_load_digests_range_is_half_open(tmp_path):
    from tubeless.store import load_digests, save_digest
    for created in ("2026-07-24", "2026-07-25", "2026-07-26"):
        save_digest(_digest(created), tmp_path)
    got = [d.created for d in load_digests(tmp_path, since="2026-07-25", until="2026-07-26")]
    assert got == ["2026-07-25"]   # since inclusive, until exclusive


def test_save_digest_overwrites_the_same_label_in_place(tmp_path):
    from tubeless.store import load_digests, save_digest
    save_digest(_digest("2026-07-25"), tmp_path)
    save_digest(_digest("2026-07-25"), tmp_path)   # same created -> same filename
    assert len(load_digests(tmp_path)) == 1


def test_load_digests_skips_corrupt_or_foreign_json(tmp_path):
    from tubeless.store import load_digests, save_digest
    save_digest(_digest("2026-07-25"), tmp_path)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "foreign.json").write_text('{"hello": 1}', encoding="utf-8")
    assert [d.created for d in load_digests(tmp_path)] == ["2026-07-25"]


def test_save_digest_round_trips_the_provenance(tmp_path):
    from tubeless.channels import Channel
    from tubeless.digest import Digest, RunProvenance
    from tubeless.store import load_digests, save_digest
    prov = RunProvenance(
        backend="gemini", model="gemini-2.5-flash", language="en",
        channels=(Channel(source="PL1", detail="deep", includes=(), excludes=("LIVE",)),),
        per_channel=5, source_match="PL1",
    )
    save_digest(Digest(created="2026-07-25", entries=(), provenance=prov), tmp_path)
    assert load_digests(tmp_path)[0].provenance == prov


def test_load_digests_filters_on_created_not_the_covered_span(tmp_path):
    # A re-curate is filed under its label (start..end) but its `created` is the run
    # date; load_digests filters on `created` (when it was produced), not the span it
    # covers. Pin that contract so a regression that filtered on the filename is caught.
    from tubeless.digest import Digest, RunProvenance
    from tubeless.store import load_digests, save_digest
    recurate = Digest(
        created="2026-07-25", entries=(), start="2026-07-01", end="2026-07-08",
        provenance=RunProvenance(backend="gemini", model="m", language="en",
                                 since="2026-07-01", until="2026-07-08"),
    )
    path = save_digest(recurate, tmp_path)
    assert path.name == "2026-07-01..2026-07-08.json"          # addressed by the covered span
    # found by a window over the RUN date...
    assert [d.created for d in load_digests(tmp_path, since="2026-07-25", until="2026-07-26")] == ["2026-07-25"]
    # ...not by a window over the span it covers
    assert load_digests(tmp_path, since="2026-07-01", until="2026-07-02") == ()


def test_load_digests_since_equal_until_is_empty(tmp_path):
    # The degenerate boundary of the half-open [since, until): a record exactly on the
    # shared bound is excluded (moment < until is false, moment >= until is true).
    from tubeless.store import load_digests, save_digest
    save_digest(_digest("2026-07-25"), tmp_path)
    assert load_digests(tmp_path, since="2026-07-25", until="2026-07-25") == ()
