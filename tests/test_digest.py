"""Digest orchestration with fakes: sorting, seen-skip, captionless-skip, feed error.

The feed and transcript boundaries are monkeypatched; summarize and
score_importance run for real against a fake backend, so the assembly logic and
the score-driven sort are exercised end to end without a network call.
"""

import pytest

import tubeless.digest as digest_module
from tubeless.channels import Channel
from tubeless.digest import build_digest
from tubeless.errors import FeedError, TranscriptFetchBlocked, TranscriptUnavailable
from tubeless.feed import Upload
from tubeless.transcript import Transcript, TranscriptSegment

_CHANNEL_ID = "UCabcdefghijklmnopqrstuv"


def _upload(video_id: str, title: str) -> Upload:
    return Upload(video_id=video_id, title=title, published="",
                  channel_id=_CHANNEL_ID, channel_title="Example Channel")


def _transcript(video_id: str) -> Transcript:
    return Transcript(
        video_id=video_id, language="ko", is_auto_generated=False,
        segments=(TranscriptSegment(text="words words words", start=0.0, duration=3.0),),
    )


class ScoringBackend:
    """Fake backend: a summary-shaped reply for summarize, a score-shaped reply
    for score_importance. The score is high when the prompt names a 'big' video,
    so sorting has something to order by."""

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        if "importance" in prompt.lower():
            score = 0.9 if "big" in prompt.lower() else 0.2
            return f"SCORE: {score}\nREASON: reason"
        return "TLDR: gist\n- point one\n- point two"


@pytest.fixture
def two_uploads(monkeypatch):
    monkeypatch.setattr(digest_module, "fetch_uploads",
                        lambda source, limit: (_upload("aaaaaaaaaaa", "small note"),
                                               _upload("bbbbbbbbbbb", "big news")))
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video_id: _transcript(video_id))


class SynthesizingBackend(ScoringBackend):
    """ScoringBackend plus a synthesis-shaped reply when asked to combine sources
    (the synthesis prompt is the only one carrying a 'TONE:' format line)."""

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        if "TONE:" in prompt:
            return "TONE: cautious\nOVERVIEW: a corrective day\nAGREEMENT:\n- chips fell\nDISAGREEMENT:\n- (none)"
        return super().complete(prompt, system=system)


_ONE_CHANNEL = (Channel(source="@x", label="Example Channel", detail="normal"),)


def test_build_digest_adds_a_synthesis_when_requested(two_uploads):
    digest, _ = build_digest(
        _ONE_CHANNEL, SynthesizingBackend(), date="d", seen=set(), with_synthesis=True,
    )

    assert digest.synthesis is not None
    assert digest.synthesis.tone == "cautious"
    assert digest.synthesis.agreements == ("chips fell",)


def test_build_digest_omits_the_synthesis_by_default(two_uploads):
    digest, _ = build_digest(_ONE_CHANNEL, SynthesizingBackend(), date="d", seen=set())

    assert digest.synthesis is None


def test_build_digest_skips_the_synthesis_for_a_single_video(monkeypatch):
    monkeypatch.setattr(digest_module, "fetch_uploads",
                        lambda source, limit: (_upload("aaaaaaaaaaa", "only one"),))
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video_id: _transcript(video_id))

    # requested, but one source cannot agree or disagree with itself
    digest, _ = build_digest(
        _ONE_CHANNEL, SynthesizingBackend(), date="d", seen=set(), with_synthesis=True,
    )

    assert len(digest.entries) == 1
    assert digest.synthesis is None


def test_build_digest_sorts_entries_by_importance(two_uploads):
    digest, processed = build_digest(
        _ONE_CHANNEL, ScoringBackend(), date="2026-07-21", seen=set(),
    )

    assert [e.upload.title for e in digest.entries] == ["big news", "small note"]
    assert processed == {"aaaaaaaaaaa", "bbbbbbbbbbb"}


def test_build_digest_skips_already_seen_videos(two_uploads):
    digest, processed = build_digest(
        _ONE_CHANNEL, ScoringBackend(), date="d", seen={"aaaaaaaaaaa"},
    )

    assert [e.upload.video_id for e in digest.entries] == ["bbbbbbbbbbb"]
    assert processed == {"bbbbbbbbbbb"}


def test_build_digest_marks_captionless_videos_processed_but_drops_them(monkeypatch):
    monkeypatch.setattr(digest_module, "fetch_uploads",
                        lambda source, limit: (_upload("ccccccccccc", "no captions"),))

    def no_transcript(video_id):
        raise TranscriptUnavailable("captions off")

    monkeypatch.setattr(digest_module, "fetch_transcript", no_transcript)

    digest, processed = build_digest(_ONE_CHANNEL, ScoringBackend(), date="d", seen=set())

    assert digest.entries == ()
    assert processed == {"ccccccccccc"}  # marked, so it is not retried tomorrow


def test_build_digest_aborts_on_a_transient_transcript_block(monkeypatch):
    # A transient IP block must abort the whole run (propagate), NOT be swallowed
    # like a captionless video -- otherwise build_digest would report the video as
    # processed and the caller would persist it, losing it forever.
    monkeypatch.setattr(digest_module, "fetch_uploads",
                        lambda source, limit: (_upload("ddddddddddd", "blocked"),))

    def blocked(video_id):
        raise TranscriptFetchBlocked("ip blocked")

    monkeypatch.setattr(digest_module, "fetch_transcript", blocked)

    with pytest.raises(TranscriptFetchBlocked):
        build_digest(_ONE_CHANNEL, ScoringBackend(), date="d", seen=set())


def test_build_digest_processes_a_repeated_video_only_once(monkeypatch):
    # The same upload served by two channel sources must be summarized once.
    monkeypatch.setattr(digest_module, "fetch_uploads",
                        lambda source, limit: (_upload("aaaaaaaaaaa", "shared upload"),))
    fetched: list[str] = []

    def record(video_id):
        fetched.append(video_id)
        return _transcript(video_id)

    monkeypatch.setattr(digest_module, "fetch_transcript", record)
    two_channels = (Channel(source="@a", label="A", detail="normal"),
                    Channel(source="@b", label="B", detail="normal"))

    digest, processed = build_digest(two_channels, ScoringBackend(), date="d", seen=set())

    assert len(digest.entries) == 1
    assert processed == {"aaaaaaaaaaa"}
    assert fetched == ["aaaaaaaaaaa"]  # summarized once, not per channel


def test_build_digest_title_filter_ignores_case(monkeypatch):
    monkeypatch.setattr(digest_module, "fetch_uploads",
                        lambda source, limit: (_upload("aaaaaaaaaaa", "Morning SHOW with ALICE"),))
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video_id: _transcript(video_id))
    channels = (Channel(source="@x", label="Show", detail="normal",
                        title_includes=("show", "alice")),)   # lowercase filter, mixed-case title

    digest, processed = build_digest(channels, ScoringBackend(), date="d", seen=set())

    assert [e.upload.video_id for e in digest.entries] == ["aaaaaaaaaaa"]


def test_build_digest_applies_a_title_filter(monkeypatch):
    monkeypatch.setattr(
        digest_module, "fetch_uploads",
        lambda source, limit: (_upload("aaaaaaaaaaa", "[Show] with Alice"),
                               _upload("bbbbbbbbbbb", "[Show] with Bob"),
                               _upload("ccccccccccc", "[Other] with Alice too")),
    )
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video_id: _transcript(video_id))
    channels = (Channel(source="@x", label="Show", detail="normal",
                        title_includes=("[Show]", "Alice")),)

    digest, processed = build_digest(channels, ScoringBackend(), date="d", seen=set())

    # only the upload whose title contains BOTH "[Show]" and "Alice"
    assert [e.upload.video_id for e in digest.entries] == ["aaaaaaaaaaa"]
    assert processed == {"aaaaaaaaaaa"}


def test_build_digest_drops_titles_matching_an_exclude(monkeypatch):
    # A channel posts a LIVE broadcast and an edited replay of the same episode;
    # title_excludes=["LIVE"] keeps only the replay.
    monkeypatch.setattr(
        digest_module, "fetch_uploads",
        lambda source, limit: (_upload("aaaaaaaaaaa", "[7/22 Market] recap"),
                               _upload("bbbbbbbbbbb", "[LIVE 7/22 Market] recap")),
    )
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video_id: _transcript(video_id))
    channels = (Channel(source="@x", label="Show", detail="normal",
                        title_includes=("Market",), title_excludes=("live",)),)  # case-insensitive

    digest, processed = build_digest(channels, ScoringBackend(), date="d", seen=set())

    assert [e.upload.video_id for e in digest.entries] == ["aaaaaaaaaaa"]
    assert processed == {"aaaaaaaaaaa"}


def test_build_digest_records_a_channel_whose_feed_fails(monkeypatch):
    def feed_down(source, limit):
        raise FeedError("feed unreachable")

    monkeypatch.setattr(digest_module, "fetch_uploads", feed_down)

    digest, processed = build_digest(_ONE_CHANNEL, ScoringBackend(), date="d", seen=set())

    assert digest.entries == ()
    assert processed == set()
    assert len(digest.skipped) == 1
    assert "Example Channel" in digest.skipped[0]
