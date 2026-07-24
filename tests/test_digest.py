"""Digest building with fakes: the batch engine (summarize_videos) and the pure
assembler (curate_summaries). The transcript boundary is monkeypatched;
summarizing and importance scoring run for real against a fake backend, so the
assembly logic and the score-driven sort are exercised without a network call.
"""

import re

import pytest

import tubeless.digest as digest_module
from tubeless.digest import Skip, curate_summaries, summarize_videos
from tubeless.errors import TranscriptFetchBlocked, TranscriptUnavailable
from tubeless.source import Video
from tubeless.summary import Summary
from tubeless.transcript import Transcript, TranscriptSegment


def _video(video_id: str, title: str, *, channel: str = "Example Channel", published: str | None = None) -> Video:
    return Video(
        video_id=video_id, title=title,
        url=f"https://www.youtube.com/watch?v={video_id}", channel=channel, published=published,
    )


def _transcript(video: Video) -> Transcript:
    return Transcript(
        video=video, language="ko", is_auto_generated=False,
        segments=(TranscriptSegment(text="words words words", start=0.0, duration=3.0),),
    )


def _summary(video_id: str, *, channel: str = "Example Channel", published: str | None = None) -> Summary:
    return Summary(
        video=_video(video_id, f"V {video_id}", channel=channel, published=published),
        tldr="gist", points=("a", "b"), language="en", detail="normal",
    )


class ScoringBackend:
    """Fake backend: a summary-shaped reply for summarizing, and a batched
    id-keyed reply for importance scoring -- one line per video block, high when
    that video's title names a 'big' one, so sorting has something to order by."""

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        if "importance" in prompt.lower():
            lines = []
            for match in re.finditer(r"\[([A-Za-z0-9_-]{11})\] (.+)", prompt):
                video_id, title = match.group(1), match.group(2)
                importance = 0.9 if "big" in title.lower() else 0.2
                lines.append(f"{video_id} SCORE: {importance} REASON: reason")
            return "\n".join(lines)
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
        return next((t for t in self.transcripts if t.video.video_id == video_id), None)


# --- summarize_videos (the batch engine) --------------------------------------

def test_summarize_videos_summarizes_each_and_reports_processed(monkeypatch):
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video: _transcript(video))
    videos = (_video("aaaaaaaaaaa", "one"), _video("bbbbbbbbbbb", "two"))

    result = summarize_videos(videos, ScoringBackend(), detail="normal")

    assert [s.video.video_id for s in result.summaries] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert result.skipped == []
    assert result.processed == frozenset({"aaaaaaaaaaa", "bbbbbbbbbbb"})


def test_summarize_videos_records_a_captionless_video_as_a_skip_but_processed(monkeypatch):
    def fetch(video):
        if video.video_id == "ccccccccccc":
            raise TranscriptUnavailable("captions off")
        return _transcript(video)

    monkeypatch.setattr(digest_module, "fetch_transcript", fetch)
    videos = (_video("aaaaaaaaaaa", "ok"), _video("ccccccccccc", "no captions"))

    result = summarize_videos(videos, ScoringBackend(), detail="normal")

    assert [s.video.video_id for s in result.summaries] == ["aaaaaaaaaaa"]
    assert result.skipped == [Skip("no-transcript", "ccccccccccc", "captions off")]
    # the captionless video still counts as processed, so it is not retried
    assert result.processed == frozenset({"aaaaaaaaaaa", "ccccccccccc"})


def test_summarize_videos_writes_through_to_the_store(monkeypatch):
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video: _transcript(video))
    store = FakeStore()

    summarize_videos((_video("aaaaaaaaaaa", "one"),), ScoringBackend(), detail="normal", store=store)

    assert [t.video.video_id for t in store.transcripts] == ["aaaaaaaaaaa"]
    assert [s.video.video_id for s in store.summaries] == ["aaaaaaaaaaa"]


def test_summarize_videos_dry_run_persists_nothing(monkeypatch):
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video: _transcript(video))

    result = summarize_videos((_video("aaaaaaaaaaa", "one"),), ScoringBackend(), detail="normal", store=None)

    assert len(result.summaries) == 1   # still summarized, just not stored


def test_summarize_videos_propagates_a_transient_block(monkeypatch):
    def blocked(video):
        raise TranscriptFetchBlocked("ip blocked")

    monkeypatch.setattr(digest_module, "fetch_transcript", blocked)

    with pytest.raises(TranscriptFetchBlocked):
        summarize_videos((_video("aaaaaaaaaaa", "one"),), ScoringBackend(), detail="normal")


# --- curate_summaries (the pure assembler) ------------------------------------

def test_curate_ranks_entries_by_importance():
    summaries = [_summary("aaaaaaaaaaa"), _summary("bbbbbbbbbbb")]
    summaries[1] = Summary(video=_video("bbbbbbbbbbb", "big news"), tldr="g", points=("a",),
                           language="en", detail="normal")

    digest = curate_summaries(summaries, ScoringBackend(), period="2026-07-21")

    assert [e.summary.video.title for e in digest.entries] == ["big news", "V aaaaaaaaaaa"]
    assert digest.period == "2026-07-21"


def test_curate_surfaces_the_skips_it_is_given():
    skip = Skip("feed-failure", "@dead", "feed down")

    digest = curate_summaries([_summary("aaaaaaaaaaa")], ScoringBackend(), period="d", skipped=[skip])

    assert digest.skipped == (skip,)


def test_curate_synthesizes_across_the_sources():
    # Synthesis is always attempted; with 2+ sources it produces one.
    summaries = [_summary("aaaaaaaaaaa"), _summary("bbbbbbbbbbb")]

    digest = curate_summaries(summaries, SynthesizingBackend(), period="d")

    assert digest.synthesis is not None
    assert digest.synthesis.tone == "cautious"


def test_curate_has_no_synthesis_below_two_summaries():
    # One source cannot agree or disagree with itself, so synthesize declines and
    # the digest carries no synthesis -- no backend call is spent on it.
    digest = curate_summaries([_summary("aaaaaaaaaaa")], SynthesizingBackend(), period="d")

    assert digest.synthesis is None


def test_curate_of_no_summaries_is_an_empty_digest():
    digest = curate_summaries([], ScoringBackend(), period="d")

    assert digest.entries == ()
    assert digest.synthesis is None
