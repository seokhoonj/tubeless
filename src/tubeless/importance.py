"""Score how important a summarized video is, for ranking the daily digest.

Importance is a judgement, so it is delegated to the LLM backend: given the
finished summary, the model returns a 0..1 score and a one-line reason. The
digest sorts by this so the most consequential videos lead. A neutral criterion
is used on purpose ("important for a follower of this channel's topic"), so the
scorer stays domain-agnostic like the rest of the core.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from tubeless.llm import LLMBackend
from tubeless.summary import DEFAULT_LANGUAGE, Summary, language_name

__all__ = ["Importance", "ImportanceTier", "score"]

# Score -> tier cutoffs. At or above _HIGH_TIER is a must-read; at or above
# _MID_TIER is worth a glance; below that is background. Deliberately coarse (the
# exact score is shown alongside). These live here, beside the score they
# classify -- the renderer only maps a tier to its display marker.
_HIGH_TIER = 0.7
_MID_TIER  = 0.4

ImportanceTier = Literal["high", "mid", "low"]

_SYSTEM_PROMPT = (
    "You rate how important a video is for someone who follows this channel's "
    "topic. Judge only from the summary you are given."
)

_PROMPT = (
    "Rate the importance of this video from 0.0 (skippable) to 1.0 (must-see) "
    "for a regular follower of its topic, weighing how consequential, "
    "actionable, and time-sensitive it is. Reply in exactly two lines:\n"
    "SCORE: <a number from 0.0 to 1.0>\n"
    "REASON: <one short sentence in {language}>\n\n"
    "Title: {title}\n"
    "TL;DR: {tldr}\n"
    "Points:\n{points}"
)

# Capture any number the model writes (e.g. "0.85", "1", a stray "1.5"); the
# caller clamps it to 0..1, so an out-of-range reply is pulled to the nearest
# bound instead of silently discarded.
_SCORE_PATTERN  = re.compile(r"(?i)score\s*[:=]\s*([+-]?\d*[.,]?\d+)")
_REASON_PATTERN = re.compile(r"(?i)reason\s*[:=]\s*(.+)")


@dataclass(frozen=True, slots=True)
class Importance:
    """A video's digest ranking: a 0..1 score and the reason the model gave."""

    score:  float
    reason: str

    @property
    def tier(self) -> ImportanceTier:
        """Coarse tier from ``score``: 'high' (must-read), 'mid', or 'low'."""
        if self.score >= _HIGH_TIER:
            return "high"
        if self.score >= _MID_TIER:
            return "mid"
        return "low"


def score(
    summaries: Sequence[Summary], backend: LLMBackend, *, language: str = DEFAULT_LANGUAGE
) -> list[Importance]:
    """Rate each summary's importance from 0 to 1, one ``Importance`` per input.

    A total, order-preserving map: the result has exactly one entry per summary,
    in input order, with no drops or reordering -- so a caller can zip the scores
    back onto the summaries positionally (there is no id join). An empty input
    makes no backend call and returns ``[]``.

    A reply that cannot be parsed falls back to a neutral 0.5 (with the reply's
    first line as the reason), so one unparseable score never sinks the digest or
    shifts the alignment.

    Raises:
        LLMError: propagated from the backend (a global credential/credit problem
            should stop the run rather than be scored as neutral).
    """
    return [_score_one(summary, backend, language=language) for summary in summaries]


def _score_one(summary: Summary, backend: LLMBackend, *, language: str) -> Importance:
    points = "\n".join(f"- {point}" for point in summary.points)
    reply = backend.complete(
        _PROMPT.format(
            language = language_name(language),
            title    = summary.video.title,
            tldr     = summary.tldr,
            points   = points,
        ),
        system=_SYSTEM_PROMPT,
    )
    return _parse_importance(reply)


def _parse_importance(reply: str) -> Importance:
    score_match = _SCORE_PATTERN.search(reply)
    if score_match:
        # Normalize a comma decimal ("0,7") and a leading '+' before parsing, so a
        # locale-formatted score is not silently truncated; then clamp to 0..1.
        raw   = score_match.group(1).replace(",", ".").lstrip("+")
        score = max(0.0, min(1.0, float(raw)))
    else:
        score = 0.5

    reason_match = _REASON_PATTERN.search(reply)
    if reason_match:
        reason = reason_match.group(1).strip()
    else:
        # No REASON line: use the first line that is not the SCORE line, so a
        # score-only reply does not show "SCORE: 0.8" as its reason. If every line
        # is a SCORE line (or the reply is empty), there is no reason to show.
        lines           = [line.strip() for line in reply.splitlines() if line.strip()]
        non_score_lines = [line for line in lines if not _SCORE_PATTERN.search(line)]
        reason          = non_score_lines[0] if non_score_lines else ""
    return Importance(score=score, reason=reason)
