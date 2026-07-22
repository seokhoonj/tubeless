"""Importance scoring: reply parsing, clamping, and the neutral fallback."""

import pytest

from tubeless.importance import Importance, _parse_importance, score_importance
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

    assert got.score  == pytest.approx(0.8)
    assert got.reason == "big market news"


def test_parse_importance_score_only_reply_has_no_reason():
    # A reply with only a SCORE line has no reason; it must NOT echo the SCORE line
    # itself as the reason (which the score-line filter exists to prevent).
    got = _parse_importance("SCORE: 0.8")

    assert got.score  == pytest.approx(0.8)
    assert got.reason == ""


def test_parse_importance_clamps_out_of_range_scores():
    # The model occasionally overshoots the 0..1 range ("SCORE: 1.5") or writes a
    # negative; the parser pulls each to the nearest bound rather than discarding.
    assert _parse_importance("SCORE: 1.5\nREASON: x").score  == pytest.approx(1.0)
    assert _parse_importance("SCORE: -0.3\nREASON: y").score == pytest.approx(0.0)
    assert _parse_importance("SCORE: 42\nREASON: z").score   == pytest.approx(1.0)


def test_parse_importance_reads_a_comma_decimal_and_skips_the_score_line():
    # A locale-formatted "0,7" must parse (not truncate to 0), and when there is no
    # REASON line the reason must fall to real text, not the SCORE line itself.
    got = _parse_importance("SCORE: 0,7\nMarket rallied on chip demand")

    assert got.score  == pytest.approx(0.7)
    assert got.reason == "Market rallied on chip demand"


def test_parse_importance_falls_back_to_neutral_when_unparseable():
    got = _parse_importance("I could not decide.")

    assert got.score  == pytest.approx(0.5)
    assert got.reason == "I could not decide."


@pytest.mark.parametrize(
    "score, expected_tier",
    [
        (0.95, "high"),
        (0.70, "high"),   # cutoff is inclusive
        (0.69, "mid"),
        (0.40, "mid"),    # cutoff is inclusive
        (0.39, "low"),
        (0.00, "low"),
    ],
)
def test_importance_tier_classifies_the_score(score, expected_tier):
    assert Importance(score=score, reason="r").tier == expected_tier


def test_score_importance_calls_the_backend_and_returns_importance():
    got = score_importance(SAMPLE_SUMMARY, OneReplyBackend("SCORE: 0.42\nREASON: moderate"))

    assert got.score  == pytest.approx(0.42)
    assert got.reason == "moderate"


@pytest.mark.parametrize(
    "reply, expected_score",
    [
        ("SCORE: 1\nREASON: r",   1.0),    # integer form
        ("SCORE: 1.0\nREASON: r", 1.0),    # one-point-zero
        ("SCORE: 0.85\nREASON: r", 0.85),  # decimal
        ("score = 0.5\nreason: r", 0.5),   # '=' separator, lower-case
        ("no score anywhere here", 0.5),   # unparseable -> neutral fallback
    ],
)
def test_score_importance_parses_the_score_forms(reply, expected_score):
    got = score_importance(SAMPLE_SUMMARY, OneReplyBackend(reply))

    assert got.score == pytest.approx(expected_score)
