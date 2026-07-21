"""Importance scoring: reply parsing, clamping, and the neutral fallback."""

from tubeless.importance import _parse_importance, score_importance
from tubeless.source import Video
from tubeless.summary import Summary

SAMPLE_VIDEO = Video(
    video_id = "dQw4w9WgXcQ",
    title    = "A talk",
    url      = "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    channel  = "Chan",
)
SAMPLE_SUMMARY = Summary(
    video=SAMPLE_VIDEO, tldr="gist", points=("a", "b"), language="ko",
)


class OneReplyBackend:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        return self.reply


def test_parse_importance_reads_score_and_reason():
    got = _parse_importance("SCORE: 0.8\nREASON: big market news")

    assert got.score  == 0.8
    assert got.reason == "big market news"


def test_parse_importance_clamps_out_of_range_scores():
    assert _parse_importance("SCORE: 1.0\nREASON: x").score == 1.0
    assert _parse_importance("SCORE: 0\nREASON: y").score == 0.0


def test_parse_importance_falls_back_to_neutral_when_unparseable():
    got = _parse_importance("I could not decide.")

    assert got.score  == 0.5
    assert got.reason == "I could not decide."


def test_score_importance_calls_the_backend_and_returns_importance():
    got = score_importance(SAMPLE_SUMMARY, OneReplyBackend("SCORE: 0.42\nREASON: 보통"))

    assert got.score  == 0.42
    assert got.reason == "보통"
