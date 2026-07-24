"""Importance scoring: batched id-keyed parsing, clamping, focus, neutral fallback."""

import pytest

from tubeless.importance import Importance, _parse_scores, score
from tubeless.source import Video
from tubeless.summary import Summary


def _summary(video_id: str, *, tldr: str = "gist", points: tuple[str, ...] = ("a", "b")) -> Summary:
    video = Video(video_id=video_id, title=f"V {video_id}",
                  url=f"https://www.youtube.com/watch?v={video_id}", channel="Chan")
    return Summary(video=video, tldr=tldr, points=points, language="ko", detail="normal")


SAMPLE_SUMMARY = _summary("dQw4w9WgXcQ")


class OneReplyBackend:
    """Fake backend: records the prompt and call count, returns one canned reply."""

    def __init__(self, reply: str) -> None:
        self.reply  = reply
        self.calls  = 0
        self.prompt = ""

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        self.calls += 1
        self.prompt = prompt
        return self.reply


# --- _parse_scores (one reply line per video, keyed by id) --------------------

def test_parse_scores_reads_id_score_and_reason():
    got = _parse_scores("dQw4w9WgXcQ SCORE: 0.8 REASON: big market news")

    assert got["dQw4w9WgXcQ"].score  == pytest.approx(0.8)
    assert got["dQw4w9WgXcQ"].reason == "big market news"


def test_parse_scores_accepts_a_bracketed_id_and_a_missing_reason():
    got = _parse_scores("[dQw4w9WgXcQ] SCORE: 0.8")

    assert got["dQw4w9WgXcQ"].score  == pytest.approx(0.8)
    assert got["dQw4w9WgXcQ"].reason == ""


@pytest.mark.parametrize(
    "line, expected",
    [
        ("aaaaaaaaaaa SCORE: 1 REASON: r",     1.0),   # integer
        ("aaaaaaaaaaa SCORE: 1.0 REASON: r",   1.0),
        ("aaaaaaaaaaa SCORE: 0.85 REASON: r",  0.85),
        ("aaaaaaaaaaa score = 0.5 reason: r",  0.5),   # '=' separator, lower-case
        ("aaaaaaaaaaa SCORE: 1.5 REASON: r",   1.0),   # clamp high
        ("aaaaaaaaaaa SCORE: -0.3 REASON: r",  0.0),   # clamp low
        ("aaaaaaaaaaa SCORE: 0,7 REASON: r",   0.7),   # comma decimal
    ],
)
def test_parse_scores_reads_the_number_forms(line, expected):
    assert _parse_scores(line)["aaaaaaaaaaa"].score == pytest.approx(expected)


def test_parse_scores_skips_lines_that_do_not_match():
    got = _parse_scores("here are the ratings:\ndQw4w9WgXcQ SCORE: 0.5 REASON: r\nthanks!")

    assert set(got) == {"dQw4w9WgXcQ"}


# --- tier ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "score_value, expected_tier",
    [(0.95, "high"), (0.70, "high"), (0.69, "mid"), (0.40, "mid"), (0.39, "low"), (0.00, "low")],
)
def test_importance_tier_classifies_the_score(score_value, expected_tier):
    assert Importance(score=score_value, reason="r").tier == expected_tier


# --- score (one batched call) -------------------------------------------------

def test_score_returns_one_importance_per_summary_in_input_order():
    # The reply lists the videos in a DIFFERENT order than the input; matching by
    # id must still align each score to its own summary, in input order.
    backend = OneReplyBackend(
        "bbbbbbbbbbb SCORE: 0.9 REASON: big\naaaaaaaaaaa SCORE: 0.2 REASON: small"
    )

    got = score([_summary("aaaaaaaaaaa"), _summary("bbbbbbbbbbb")], backend)

    assert [round(imp.score, 1) for imp in got] == [0.2, 0.9]   # input order
    assert [imp.reason for imp in got] == ["small", "big"]
    assert backend.calls == 1                                    # ONE call, not one-per-summary


def test_score_fills_a_summary_the_reply_omitted_with_neutral():
    backend = OneReplyBackend("aaaaaaaaaaa SCORE: 0.9 REASON: big")   # bbbb... omitted

    got = score([_summary("aaaaaaaaaaa"), _summary("bbbbbbbbbbb")], backend)

    assert got[0].score  == pytest.approx(0.9)
    assert got[1].score  == pytest.approx(0.5)   # neutral fallback, no drop, no shift
    assert got[1].reason == ""


def test_score_of_no_summaries_makes_no_backend_call():
    class _ExplodingBackend:
        def complete(self, prompt: str, *, system: str | None = None) -> str:
            raise AssertionError("backend must not be called for an empty input")

    assert score([], _ExplodingBackend()) == []


def test_score_focus_reaches_the_prompt():
    backend = OneReplyBackend("dQw4w9WgXcQ SCORE: 0.5 REASON: r")

    score([SAMPLE_SUMMARY], backend, focus="semiconductors, the Fed")

    assert "semiconductors, the Fed" in backend.prompt


def test_score_without_focus_uses_the_neutral_criterion():
    backend = OneReplyBackend("dQw4w9WgXcQ SCORE: 0.5 REASON: r")

    score([SAMPLE_SUMMARY], backend)

    assert "regular follower" in backend.prompt
