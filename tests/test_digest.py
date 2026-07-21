"""Digest orchestration with fakes: sorting, seen-skip, captionless-skip, feed error.

The feed and transcript boundaries are monkeypatched; summarize and
score_importance run for real against a fake backend, so the assembly logic and
the score-driven sort are exercised end to end without a network call.
"""

import pytest

import tubeless.digest as digest_module
from tubeless.channels import Channel
from tubeless.digest import build_digest
from tubeless.errors import FeedError, TranscriptUnavailable
from tubeless.feed import Upload
from tubeless.transcript import Transcript, TranscriptSegment

_CHANNEL_ID = "UCabcdefghijklmnopqrstuv"


def _upload(video_id: str, title: str) -> Upload:
    return Upload(video_id=video_id, title=title, published="",
                  channel_id=_CHANNEL_ID, channel_title="수페TV")


def _transcript(video_id: str) -> Transcript:
    return Transcript(
        video_id=video_id, language="ko", is_auto_generated=False,
        segments=(TranscriptSegment(text="말 말 말", start=0.0, duration=3.0),),
    )


class ScoringBackend:
    """Fake backend: a summary-shaped reply for summarize, a score-shaped reply
    for score_importance. The score is high when the prompt names a 'big' video,
    so sorting has something to order by."""

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        if "importance" in prompt.lower():
            score = 0.9 if "big" in prompt.lower() else 0.2
            return f"SCORE: {score}\nREASON: 이유"
        return "TLDR: gist\n- point one\n- point two"


@pytest.fixture
def two_uploads(monkeypatch):
    monkeypatch.setattr(digest_module, "resolve_channel_id", lambda source: _CHANNEL_ID)
    monkeypatch.setattr(digest_module, "fetch_channel_uploads",
                        lambda channel_id, limit: (_upload("aaaaaaaaaaa", "small note"),
                                                   _upload("bbbbbbbbbbb", "big news")))
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video_id: _transcript(video_id))


_ONE_CHANNEL = (Channel(source="@x", label="수페TV", detail="normal"),)


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
    monkeypatch.setattr(digest_module, "resolve_channel_id", lambda source: _CHANNEL_ID)
    monkeypatch.setattr(digest_module, "fetch_channel_uploads",
                        lambda channel_id, limit: (_upload("ccccccccccc", "no captions"),))

    def no_transcript(video_id):
        raise TranscriptUnavailable("captions off")

    monkeypatch.setattr(digest_module, "fetch_transcript", no_transcript)

    digest, processed = build_digest(_ONE_CHANNEL, ScoringBackend(), date="d", seen=set())

    assert digest.entries == ()
    assert processed == {"ccccccccccc"}  # marked, so it is not retried tomorrow


def test_build_digest_records_a_channel_whose_feed_fails(monkeypatch):
    def feed_down(source):
        raise FeedError("feed unreachable")

    monkeypatch.setattr(digest_module, "resolve_channel_id", feed_down)

    digest, processed = build_digest(_ONE_CHANNEL, ScoringBackend(), date="d", seen=set())

    assert digest.entries == ()
    assert processed == set()
    assert len(digest.skipped) == 1
    assert "수페TV" in digest.skipped[0]
