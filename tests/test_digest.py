"""Digest orchestration with fakes: the engine (summarize_videos), the pure
assembler (curate), the top orchestrator (run_digest), and the store-backed
recompute. The discover and transcript boundaries are monkeypatched; summarizing
and importance scoring run for real against a fake backend, so the assembly logic
and the score-driven sort are exercised end to end without a network call.
"""

import pytest

import tubeless.digest as digest_module
from tubeless.channels import Channel
from tubeless.digest import (
    Skip,
    curate,
    recompute,
    run_digest,
    summarize_videos,
)
from tubeless.discover import DEFAULT_SCAN
from tubeless.errors import FeedError, TranscriptFetchBlocked, TranscriptUnavailable
from tubeless.source import Video
from tubeless.summary import Summary
from tubeless.transcript import Transcript, TranscriptSegment


def _video(video_id: str, title: str, *, channel: str = "Example Channel", published: str | None = None) -> Video:
    return Video(
        video_id=video_id, title=title,
        url=f"https://www.youtube.com/watch?v={video_id}", channel=channel, published=published,
    )


def _transcript(video_id: str) -> Transcript:
    return Transcript(
        video_id=video_id, language="ko", is_auto_generated=False,
        segments=(TranscriptSegment(text="words words words", start=0.0, duration=3.0),),
    )


def _summary(video_id: str, *, channel: str = "Example Channel", published: str | None = None) -> Summary:
    return Summary(
        video=_video(video_id, f"V {video_id}", channel=channel, published=published),
        tldr="gist", points=("a", "b"), language="en", detail="normal",
    )


class ScoringBackend:
    """Fake backend: a summary-shaped reply for summarizing, a score-shaped reply
    for importance scoring. The score is high when the prompt names a 'big' video,
    so sorting has something to order by."""

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        if "importance" in prompt.lower():
            score = 0.9 if "big" in prompt.lower() else 0.2
            return f"SCORE: {score}\nREASON: reason"
        return "TLDR: gist\n- point one\n- point two"


class SynthesizingBackend(ScoringBackend):
    """ScoringBackend plus a synthesis-shaped reply when asked to combine sources
    (the synthesis prompt is the only one carrying a 'TONE:' format line)."""

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        if "TONE:" in prompt:
            return "TONE: cautious\nOVERVIEW: a corrective day\nAGREEMENT:\n- chips fell\nDISAGREEMENT:\n- (none)"
        return super().complete(prompt, system=system)


class FakeStore:
    """In-memory Store: records what was written through, serves what it holds."""

    def __init__(self) -> None:
        self.summaries:   list[Summary] = []
        self.transcripts: list[Transcript] = []

    def save_summary(self, summary: Summary) -> None:
        self.summaries.append(summary)

    def save_transcript(self, transcript: Transcript) -> None:
        self.transcripts.append(transcript)

    def load_summaries(self, *, since=None, until=None, channel=None) -> tuple[Summary, ...]:
        return tuple(self.summaries)

    def load_transcript(self, video_id: str):
        return next((t for t in self.transcripts if t.video_id == video_id), None)


_ONE_CHANNEL = (Channel(source="@x", detail="normal"),)


# --- summarize_videos (the channel-agnostic engine) ---------------------------

def test_summarize_videos_summarizes_each_and_reports_processed(monkeypatch):
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video: _transcript(video.video_id))
    videos = (_video("aaaaaaaaaaa", "one"), _video("bbbbbbbbbbb", "two"))

    result = summarize_videos(videos, ScoringBackend(), detail="normal")

    assert [s.video.video_id for s in result.summaries] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert result.skipped == []
    assert result.processed == frozenset({"aaaaaaaaaaa", "bbbbbbbbbbb"})


def test_summarize_videos_records_a_captionless_video_as_a_skip_but_processed(monkeypatch):
    def fetch(video):
        if video.video_id == "ccccccccccc":
            raise TranscriptUnavailable("captions off")
        return _transcript(video.video_id)

    monkeypatch.setattr(digest_module, "fetch_transcript", fetch)
    videos = (_video("aaaaaaaaaaa", "ok"), _video("ccccccccccc", "no captions"))

    result = summarize_videos(videos, ScoringBackend(), detail="normal")

    assert [s.video.video_id for s in result.summaries] == ["aaaaaaaaaaa"]
    assert result.skipped == [Skip("no-transcript", "ccccccccccc", "captions off")]
    # the captionless video still counts as processed, so it is not retried
    assert result.processed == frozenset({"aaaaaaaaaaa", "ccccccccccc"})


def test_summarize_videos_writes_through_to_the_store(monkeypatch):
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video: _transcript(video.video_id))
    store = FakeStore()

    summarize_videos((_video("aaaaaaaaaaa", "one"),), ScoringBackend(), detail="normal", store=store)

    assert [t.video_id for t in store.transcripts] == ["aaaaaaaaaaa"]
    assert [s.video.video_id for s in store.summaries] == ["aaaaaaaaaaa"]


def test_summarize_videos_dry_run_persists_nothing(monkeypatch):
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video: _transcript(video.video_id))

    result = summarize_videos((_video("aaaaaaaaaaa", "one"),), ScoringBackend(), detail="normal", store=None)

    assert len(result.summaries) == 1   # still summarized, just not stored


def test_summarize_videos_propagates_a_transient_block(monkeypatch):
    def blocked(video):
        raise TranscriptFetchBlocked("ip blocked")

    monkeypatch.setattr(digest_module, "fetch_transcript", blocked)

    with pytest.raises(TranscriptFetchBlocked):
        summarize_videos((_video("aaaaaaaaaaa", "one"),), ScoringBackend(), detail="normal")


# --- curate (the pure assembler) ----------------------------------------------

def test_curate_ranks_entries_by_importance():
    summaries = [_summary("aaaaaaaaaaa"), _summary("bbbbbbbbbbb")]
    summaries[1] = Summary(video=_video("bbbbbbbbbbb", "big news"), tldr="g", points=("a",),
                           language="en", detail="normal")

    digest = curate(summaries, ScoringBackend(), period="2026-07-21")

    assert [e.summary.video.title for e in digest.entries] == ["big news", "V aaaaaaaaaaa"]
    assert digest.period == "2026-07-21"


def test_curate_surfaces_the_skips_it_is_given():
    skip = Skip("feed-failure", "@dead", "feed down")

    digest = curate([_summary("aaaaaaaaaaa")], ScoringBackend(), period="d", skipped=[skip])

    assert digest.skipped == (skip,)


def test_curate_adds_a_synthesis_when_requested():
    summaries = [_summary("aaaaaaaaaaa"), _summary("bbbbbbbbbbb")]

    digest = curate(summaries, SynthesizingBackend(), period="d", with_synthesis=True)

    assert digest.synthesis is not None
    assert digest.synthesis.tone == "cautious"


def test_curate_omits_the_synthesis_by_default():
    summaries = [_summary("aaaaaaaaaaa"), _summary("bbbbbbbbbbb")]

    digest = curate(summaries, SynthesizingBackend(), period="d")

    assert digest.synthesis is None


def test_curate_of_no_summaries_is_an_empty_digest():
    digest = curate([], ScoringBackend(), period="d")

    assert digest.entries == ()


# --- run_digest (the top orchestrator) ----------------------------------------

def _discover_returns(monkeypatch, by_source):
    def fake_discover(source, *, limit, includes=(), excludes=()):
        return by_source.get(source, ())
    monkeypatch.setattr(digest_module, "fetch_recent_videos", fake_discover)
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video: _transcript(video.video_id))


def test_run_digest_discovers_summarizes_and_ranks(monkeypatch):
    _discover_returns(monkeypatch, {
        "@x": (_video("aaaaaaaaaaa", "small note"), _video("bbbbbbbbbbb", "big news")),
    })

    run = run_digest(_ONE_CHANNEL, ScoringBackend(), period="2026-07-21")

    assert [e.summary.video.title for e in run.digest.entries] == ["big news", "small note"]
    assert run.seen == frozenset({"aaaaaaaaaaa", "bbbbbbbbbbb"})


def test_run_digest_skips_already_seen_videos(monkeypatch):
    _discover_returns(monkeypatch, {
        "@x": (_video("aaaaaaaaaaa", "one"), _video("bbbbbbbbbbb", "two")),
    })

    run = run_digest(_ONE_CHANNEL, ScoringBackend(), period="d", seen=frozenset({"aaaaaaaaaaa"}))

    assert [e.summary.video.video_id for e in run.digest.entries] == ["bbbbbbbbbbb"]
    # the merged seen keeps the old id and adds the new one
    assert run.seen == frozenset({"aaaaaaaaaaa", "bbbbbbbbbbb"})


def test_run_digest_handles_a_video_shared_by_two_channels_once(monkeypatch):
    _discover_returns(monkeypatch, {
        "@a": (_video("aaaaaaaaaaa", "shared"),),
        "@b": (_video("aaaaaaaaaaa", "shared"),),
    })
    channels = (Channel(source="@a"), Channel(source="@b"))

    run = run_digest(channels, ScoringBackend(), period="d")

    assert len(run.digest.entries) == 1


def test_run_digest_records_a_feed_failure_as_a_skip_and_continues(monkeypatch):
    def fake_discover(source, *, limit, includes=(), excludes=()):
        if source == "@dead":
            raise FeedError("feed down")
        return (_video("aaaaaaaaaaa", "one"),)

    monkeypatch.setattr(digest_module, "fetch_recent_videos", fake_discover)
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video: _transcript(video.video_id))
    channels = (Channel(source="@dead"), Channel(source="@x"))

    run = run_digest(channels, ScoringBackend(), period="d")

    assert len(run.digest.entries) == 1                       # the live channel still produced
    assert run.digest.skipped == (Skip("feed-failure", "@dead", "feed down"),)


def test_run_digest_scans_the_full_window_for_a_filtered_channel(monkeypatch):
    # A channel with a title filter must scan the full feed window, not just
    # per_channel_limit -- the wanted uploads are sparse among the rest (the
    # documented missed-video incident). A plain channel keeps the small limit.
    seen_limits: dict[str, int] = {}

    def fake_discover(source, *, limit, includes=(), excludes=()):
        seen_limits[source] = limit
        return ()

    monkeypatch.setattr(digest_module, "fetch_recent_videos", fake_discover)
    channels = (
        Channel(source="@filtered", includes=("live",)),
        Channel(source="@plain"),
    )

    run_digest(channels, ScoringBackend(), period="d", per_channel_limit=5)

    assert seen_limits["@filtered"] == DEFAULT_SCAN
    assert seen_limits["@plain"] == 5


def test_run_digest_surfaces_a_captionless_video_as_a_skip(monkeypatch):
    def fetch(video):
        raise TranscriptUnavailable("captions off")

    def fake_discover(source, *, limit, includes=(), excludes=()):
        return (_video("ccccccccccc", "no captions"),)

    monkeypatch.setattr(digest_module, "fetch_recent_videos", fake_discover)
    monkeypatch.setattr(digest_module, "fetch_transcript", fetch)

    run = run_digest(_ONE_CHANNEL, ScoringBackend(), period="d")

    assert run.digest.entries == ()
    assert run.digest.skipped == (Skip("no-transcript", "ccccccccccc", "captions off"),)
    assert run.seen == frozenset({"ccccccccccc"})   # not retried tomorrow


def test_run_digest_aborts_on_a_transient_block_before_persisting(monkeypatch):
    def fake_discover(source, *, limit, includes=(), excludes=()):
        return (_video("ddddddddddd", "blocked"),)

    def blocked(video):
        raise TranscriptFetchBlocked("ip blocked")

    monkeypatch.setattr(digest_module, "fetch_recent_videos", fake_discover)
    monkeypatch.setattr(digest_module, "fetch_transcript", blocked)

    with pytest.raises(TranscriptFetchBlocked):
        run_digest(_ONE_CHANNEL, ScoringBackend(), period="d")


def test_run_digest_writes_through_to_the_store(monkeypatch):
    _discover_returns(monkeypatch, {"@x": (_video("aaaaaaaaaaa", "one"),)})
    store = FakeStore()

    run_digest(_ONE_CHANNEL, ScoringBackend(), period="d", store=store)

    assert [s.video.video_id for s in store.summaries] == ["aaaaaaaaaaa"]


# --- recompute (store-backed re-assembly) -------------------------------------

def test_recompute_reassembles_a_digest_from_stored_summaries():
    store = FakeStore()
    store.summaries.extend([_summary("aaaaaaaaaaa"), _summary("bbbbbbbbbbb")])

    digest = recompute(ScoringBackend(), store, since="2026-07-01", until="2026-07-08")

    assert {e.summary.video.video_id for e in digest.entries} == {"aaaaaaaaaaa", "bbbbbbbbbbb"}
    assert digest.period == "2026-07-01..2026-07-08"


def test_recompute_keeps_one_summary_per_video():
    # The store may hold several variants of one video (different detail); recompute
    # keeps the most recent (last in load order) so curate never double-counts it.
    store = FakeStore()
    old = Summary(video=_video("aaaaaaaaaaa", "old"), tldr="old", points=("a",),
                  language="en", detail="brief")
    new = Summary(video=_video("aaaaaaaaaaa", "new"), tldr="new", points=("a",),
                  language="en", detail="deep")
    store.summaries.extend([old, new])   # load order is oldest-first

    digest = recompute(ScoringBackend(), store)

    assert len(digest.entries) == 1
    assert digest.entries[0].summary.tldr == "new"
    assert digest.period == "all"
