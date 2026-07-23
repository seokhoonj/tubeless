"""Synthesize a day's per-video summaries into one cross-source briefing.

The digest summarizes each video on its own; this reduces *across* them -- the
overall tone, what the sources agree on, and where they diverge -- so the reader
gets one briefing instead of N separate summaries. Like importance.py, the
judgement is delegated to the LLM backend and the criterion is domain-neutral:
a set of market-commentary videos yields a market read, a set of tech-news videos
yields a tech read, without this module knowing which.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from tubeless.llm import LLMBackend
from tubeless.summary import DEFAULT_LANGUAGE, Summary, language_name

__all__ = ["Synthesis", "synthesize"]

_SYSTEM_PROMPT = (
    "You synthesize several video summaries from one day into a single briefing. "
    "Work only from the summaries given. Attribute a contested claim to the source "
    "that made it; never invent a consensus that is not there, and surface genuine "
    "disagreement rather than averaging it away."
)

# One labelled block, so parsing stays as forgiving as the summary parser: a
# TONE line, an OVERVIEW line, then AGREEMENT / DISAGREEMENT bullet sections.
_PROMPT = (
    "Below are today's video summaries, each headed by its source in [brackets]. "
    "Combine them into one overview in {language}. Reply in exactly this format:\n"
    "TONE: <one line -- the overall mood and its main drivers>\n"
    "OVERVIEW: <2-4 sentences synthesizing the day across the sources>\n"
    "AGREEMENT:\n"
    "- <a point most sources share>\n"
    "DISAGREEMENT:\n"
    "- <a point where sources differ, naming which source takes which side>\n"
    "Keep every specific figure the sources stated. If there is no real "
    "disagreement, write '- (none)' under DISAGREEMENT.\n\n"
    "{sources}"
)

# The model often decorates the label -- "**TONE:**", "## AGREEMENT", "1. TONE:"
# -- especially on longer, non-English replies. Tolerate any leading markdown /
# numbering before the keyword and any "**" around the colon, so a bold label is
# still recognised instead of silently dropping the whole section.
_SECTION_LABEL = re.compile(
    r"(?i)^[\s>*#.\d()-]*(TONE|OVERVIEW|AGREEMENT|DISAGREEMENT)\**\s*:\s*\**\s*(.*)$"
)
_BULLET_PREFIXES = ("- ", "* ", "• ")
_EMPTY_BULLETS = {"(none)", "none", "n/a", "-", "(없음)", "없음", "해당 없음"}


@dataclass(frozen=True, slots=True)
class Synthesis:
    """A cross-source read of one day: the overall tone, a short synthesis, the
    points the sources agree on, and where they diverge (with attribution)."""

    tone:          str
    overview:      str
    agreements:    tuple[str, ...]
    disagreements: tuple[str, ...]


def synthesize(
    summaries: Sequence[Summary],
    backend:   LLMBackend,
    *,
    language:  str = DEFAULT_LANGUAGE,
) -> Synthesis | None:
    """Combine ``summaries`` into one cross-source ``Synthesis``.

    Returns ``None`` when fewer than two summaries are given -- one source cannot
    agree or disagree with itself -- and makes no backend call in that case, so
    the caller need not guard the count itself. Each summary is attributed to its
    own channel in the prompt, so the model can name which source said what.

    Raises:
        LLMError: propagated from the backend.
    """
    if len(summaries) < 2:
        return None
    reply = backend.complete(
        _PROMPT.format(language=language_name(language), sources=_sources_block(summaries)),
        system=_SYSTEM_PROMPT,
    )
    return _parse_synthesis(reply)


def _sources_block(summaries: Sequence[Summary]) -> str:
    blocks = []
    for summary in summaries:
        source = summary.video.channel or summary.video.title
        points = "\n".join(f"- {point}" for point in summary.points)
        blocks.append(f"[{source}] {summary.video.title}\nTLDR: {summary.tldr}\n{points}")
    return "\n\n".join(blocks)


def _unwrap(text: str) -> str:
    """Strip surrounding whitespace and markdown emphasis (``**`` / ``*``) the
    model sometimes wraps a value or bullet in."""
    return text.strip().strip("*").strip()


def _parse_synthesis(reply: str) -> Synthesis:
    """Extract the four fields from the model's reply, tolerating drift.

    The TONE/OVERVIEW lines carry their text inline; AGREEMENT/DISAGREEMENT own
    the bullets that follow them until the next label. A stray '(none)' bullet
    under DISAGREEMENT is dropped, and an OVERVIEW that wraps onto extra lines is
    re-joined.
    """
    tone:          str = ""
    overview:      str = ""
    agreements:    list[str] = []
    disagreements: list[str] = []
    section: str | None = None

    for raw_line in reply.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        label_match = _SECTION_LABEL.match(line)
        if label_match:
            section = label_match.group(1).upper()
            inline  = _unwrap(label_match.group(2))
            if section == "TONE":
                tone = inline
            elif section == "OVERVIEW":
                overview = inline
            continue
        if line.startswith(_BULLET_PREFIXES):
            point = _unwrap(line[2:])
            if point.lower() in _EMPTY_BULLETS:
                # A "- (none)" placeholder under either section: the model was
                # told to write it when a section is empty, so drop it rather
                # than render it as a real shared or contested point.
                continue
            if section == "AGREEMENT":
                agreements.append(point)
            elif section == "DISAGREEMENT":
                disagreements.append(point)
        elif section == "OVERVIEW":
            # A wrapped OVERVIEW: join the continuation onto what we have.
            overview = f"{overview} {line}".strip()

    return Synthesis(
        tone          = tone,
        overview      = overview,
        agreements    = tuple(agreements),
        disagreements = tuple(disagreements),
    )
