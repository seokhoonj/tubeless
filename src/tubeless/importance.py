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

__all__ = ["Importance", "ImportanceTier", "score_summaries"]

# Score -> tier cutoffs. At or above _HIGH_TIER is a must-read; at or above
# _MID_TIER is worth a glance; below that is background. Deliberately coarse (the
# exact score is shown alongside). These live here, beside the score they
# classify -- the renderer only maps a tier to its display marker.
_HIGH_TIER = 0.7
_MID_TIER  = 0.4

ImportanceTier = Literal["high", "mid", "low"]

_SYSTEM_PROMPT = (
    "You rate how important each video is for the reader described. Judge only "
    "from the summaries you are given, and rate every video in the list."
)

_PROMPT = (
    "Rate the importance of each video below from 0.0 (skippable) to 1.0 "
    "(must-see) {criterion}, weighing how consequential, actionable, and "
    "time-sensitive it is. Reply with one line per video, in exactly this "
    "format (keep the id):\n"
    "<id> SCORE: <0.0 to 1.0> REASON: <one short sentence in {language}>\n\n"
    "{videos}"
)

# One reply line: the video id (as given, optionally bracketed), its score, and
# an optional reason. Keyed back to the summary by id, so a reordered or dropped
# line cannot misalign the scores.
_LINE_PATTERN = re.compile(
    r"(?i)^[\[(]?([A-Za-z0-9_-]{11})[\])]?\s+score\s*[:=]\s*"
    r"([+-]?\d*[.,]?\d+)(?:\s+reason\s*[:=]\s*(.*))?$"
)


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


# The fallback for a summary the reply did not score (missing or unparseable id
# line): a neutral 0.5, so one bad line never sinks the digest or shifts others.
_NEUTRAL = Importance(score=0.5, reason="")


def score_summaries(
    summaries: Sequence[Summary],
    backend:   LLMBackend,
    *,
    language:  str = DEFAULT_LANGUAGE,
    focus:     str | None = None,
) -> list[Importance]:
    """Rate every summary's importance from 0 to 1 in a single backend call,
    returning one ``Importance`` per input in input order.

    All summaries go in one prompt, each keyed by its video id, and the reply is
    matched back by id -- so a reordered or dropped reply line cannot misalign the
    scores (a summary the reply omits falls back to a neutral 0.5). The result is
    a total, order-preserving map: exactly one entry per input, no drops. An empty
    input makes no backend call and returns ``[]``.

    ``focus`` personalises the judgement: when given, importance is rated for a
    reader who cares about it; otherwise a neutral topic-follower criterion is
    used, keeping the scorer domain-agnostic.

    Raises:
        LLMError: propagated from the backend (a global credential/credit problem
            should stop the run rather than be scored as neutral).
    """
    if not summaries:
        return []
    reply  = backend.complete(_batch_prompt(summaries, language=language, focus=focus),
                              system=_SYSTEM_PROMPT)
    scored = _parse_scores(reply)
    return [scored.get(summary.video.video_id, _NEUTRAL) for summary in summaries]


def _batch_prompt(summaries: Sequence[Summary], *, language: str, focus: str | None) -> str:
    criterion = (f"for a reader who specifically cares about {focus}"
                 if focus else "for a regular follower of its topic")
    blocks = []
    for summary in summaries:
        points = "\n".join(f"- {point}" for point in summary.points)
        blocks.append(
            f"[{summary.video.video_id}] {summary.video.title}\nTL;DR: {summary.tldr}\n{points}"
        )
    return _PROMPT.format(
        criterion=criterion, language=language_name(language), videos="\n\n".join(blocks),
    )


def _parse_scores(reply: str) -> dict[str, Importance]:
    """Parse the batched reply into ``{video_id: Importance}``. Lines that do not
    match the expected ``<id> SCORE: .. REASON: ..`` shape are skipped; the caller
    fills any id the reply omitted with a neutral score."""
    scored: dict[str, Importance] = {}
    for raw_line in reply.splitlines():
        match = _LINE_PATTERN.match(raw_line.strip())
        if not match:
            continue
        # Normalize a comma decimal ("0,7") and a leading '+', then clamp to 0..1
        # so an out-of-range reply is pulled to the nearest bound, not discarded.
        number = max(0.0, min(1.0, float(match.group(2).replace(",", ".").lstrip("+"))))
        reason = (match.group(3) or "").strip()
        scored[match.group(1)] = Importance(score=number, reason=reason)
    return scored
