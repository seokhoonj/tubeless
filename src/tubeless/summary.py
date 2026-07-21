"""Summarize a transcript into a TLDR and key points via an injected backend.

Pure transform territory: the only side effects are the backend calls, and the
backend is a parameter, so the whole module tests with a fake and never
touches the network itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tubeless.llm import LLMBackend
from tubeless.source import Video
from tubeless.transcript import Transcript

__all__ = ["DETAIL_LEVELS", "Summary", "summarize"]

# One map-phase chunk is ~3000 words (~4000 tokens of English; Korean is
# denser per word, still far inside any current chat model's context). Small
# enough that every chunk summary comes back at a comparable grain, large
# enough that a typical 20-30 minute video stays a single call and skips the
# reduce phase entirely.
CHUNK_WORD_LIMIT = 3000

_SYSTEM_PROMPT = (
    "You are a precise video-transcript summarizer. Work only from the "
    "transcript given; never invent facts that are not in it."
)

# The model is asked for exactly this shape so parsing stays trivial:
# one "TLDR:" line, then "- " bullets. The three "<...>" slots are filled from
# the chosen detail level so the same skeleton yields a terse or a rich summary.
_FORMAT_INSTRUCTION = (
    "Answer in {language}. Use exactly this format:\n"
    "TLDR: <{tldr}>\n"
    "- <key point>\n"
    "- <key point>\n"
    "Give at most {max_points} key points; each key point is {point}. "
    "Keep each key point on its own single line.{note} No other text."
)

# Appended (via _DetailSpec.note) when the level wants data kept, not smoothed.
# Domain-neutral on purpose: a market recap, a match report, and an earnings
# call are all "data-dense briefings" whose value is the exact figures. Without
# this, "at most N key points" makes the model keep the headline claim and drop
# the numbers around it (every index move, rate, and sector but one).
_PRESERVE_FIGURES = (
    " This may be a data-dense briefing; preserve EVERY specific figure the "
    "speaker states -- each index and its move, each rate, price, percentage, "
    "and named entity with its number. Attach each figure to its period and "
    "unit -- the year, quarter, or date it applies to, and whether it is a past "
    "result or a forecast -- because a number without its timeframe is "
    "incomplete. If the speaker gives only a month or a relative period ('this "
    "year', 'next quarter', 'in August'), keep it exactly as said -- never infer "
    "or add a specific year or date the speaker did not state. Do not write a "
    "four-digit year at all unless the speaker said that exact year aloud. Never fold two "
    "figures into one vague phrase, and never drop a "
    "stated number. When the speaker walks through a list item by item -- each "
    "with its own value or direction -- keep every item as its own point instead "
    "of collapsing the list into one statement. Add points beyond the cap only "
    "if needed to hold the figures."
)


@dataclass(frozen=True, slots=True)
class _DetailSpec:
    """How expansive one detail level is: TLDR length, per-point fullness, the
    default point cap a typical-length video warrants, and an optional trailing
    instruction (e.g. keep every figure) appended to the format block."""

    tldr:   str
    point:  str
    points: int
    note:   str = ""


# --detail chooses one of these. "normal" is the default: fuller than a bare
# headline list, short of the exhaustive "deep" notes.
_DETAIL = {
    "brief": _DetailSpec(
        tldr   = "one-sentence gist",
        point  = "a single concise clause",
        points = 5,
    ),
    "normal": _DetailSpec(
        tldr   = "two- to three-sentence gist",
        point  = "one full sentence that states the specific claim, not just its topic",
        points = 8,
    ),
    "deep": _DetailSpec(
        tldr   = "three- to four-sentence overview",
        point  = (
            "two to four sentences that spell out the specifics -- the names, "
            "numbers, and reasoning the speaker actually gave, not just the topic"
        ),
        points = 14,
        note   = _PRESERVE_FIGURES,
    ),
}
DETAIL_LEVELS = tuple(_DETAIL)

_AUTO_CAPTION_HEDGE = (
    "The captions are auto-generated and may mis-transcribe names, numbers, "
    "and technical terms; hedge any specific that looks uncertain instead of "
    "stating it as fact.\n"
)

_BULLET_PREFIXES = ("- ", "* ", "• ")


@dataclass(frozen=True, slots=True)
class Summary:
    """The finished summary of one video, in ``language``."""

    video:    Video
    tldr:     str
    points:   tuple[str, ...]
    language: str


def summarize(
    transcript: Transcript,
    video:      Video,
    backend:    LLMBackend,
    *,
    target_language: str = "ko",
    detail:          str = "normal",
    max_points:      int | None = None,
) -> Summary:
    """Summarize ``transcript`` into a TLDR plus key points.

    ``detail`` ('brief' / 'normal' / 'deep') sets how fully the summary is
    written -- the TLDR length, how many sentences each point carries, and the
    default number of points. ``max_points`` overrides that default count when
    given; ``None`` keeps the per-detail default.

    Long transcripts are map-reduced: each ~``CHUNK_WORD_LIMIT``-word chunk is
    summarized on its own, then the chunk summaries are combined into the
    final answer. When the transcript is auto-generated, every prompt warns
    the model to hedge uncertain names and numbers.

    Raises:
        ValueError: ``detail`` is not one of ``DETAIL_LEVELS``.
        LLMError: propagated from the backend.
    """
    spec = _DETAIL.get(detail)
    if spec is None:
        raise ValueError(f"detail must be one of {DETAIL_LEVELS}, got {detail!r}")
    cap = spec.points if max_points is None else max_points

    hedge  = _AUTO_CAPTION_HEDGE if transcript.is_auto_generated else ""
    chunks = _split_into_chunks(transcript.text, word_limit=CHUNK_WORD_LIMIT)

    if len(chunks) == 1:
        reply = backend.complete(
            _single_pass_prompt(
                chunks[0], video, hedge=hedge,
                language=target_language, spec=spec, max_points=cap,
            ),
            system=_SYSTEM_PROMPT,
        )
    else:
        chunk_summaries = [
            backend.complete(
                _chunk_prompt(chunk, video, hedge=hedge, language=target_language),
                system=_SYSTEM_PROMPT,
            )
            for chunk in chunks
        ]
        reply = backend.complete(
            _combine_prompt(
                chunk_summaries, video, hedge=hedge,
                language=target_language, spec=spec, max_points=cap,
            ),
            system=_SYSTEM_PROMPT,
        )

    tldr, points = _parse_reply(reply, max_points=cap)
    return Summary(video=video, tldr=tldr, points=points, language=target_language)


def _split_into_chunks(text: str, *, word_limit: int) -> list[str]:
    """Split on whitespace into chunks of at most ``word_limit`` words.

    Word-boundary splitting is deliberately crude: caption cues carry no
    sentence structure worth preserving, and the reduce phase re-synthesizes
    across chunk seams anyway.
    """
    words = text.split()
    if not words:
        return [""]
    return [
        " ".join(words[chunk_start : chunk_start + word_limit])
        for chunk_start in range(0, len(words), word_limit)
    ]


def _single_pass_prompt(
    chunk: str, video: Video, *, hedge: str, language: str, spec: _DetailSpec, max_points: int
) -> str:
    return (
        f"Summarize the transcript of the video {video.title!r}.\n"
        f"{hedge}"
        f"{_format_instruction(spec, language=language, max_points=max_points)}\n\n"
        f"Transcript:\n{chunk}"
    )


def _chunk_prompt(chunk: str, video: Video, *, hedge: str, language: str) -> str:
    return (
        f"This is one part of the transcript of the video {video.title!r}. "
        f"Summarize just this part in {language}, as short plain prose, "
        "keeping every concrete claim you will need later.\n"
        f"{hedge}\n"
        f"Transcript part:\n{chunk}"
    )


def _combine_prompt(
    chunk_summaries: list[str], video: Video, *,
    hedge: str, language: str, spec: _DetailSpec, max_points: int,
) -> str:
    numbered = "\n\n".join(
        f"[part {part_number}]\n{part_summary}"
        for part_number, part_summary in enumerate(chunk_summaries, start=1)
    )
    return (
        f"Below are part-by-part summaries of the video {video.title!r}, in order. "
        "Combine them into one summary of the whole video.\n"
        f"{hedge}"
        f"{_format_instruction(spec, language=language, max_points=max_points)}\n\n"
        f"{numbered}"
    )


def _format_instruction(spec: _DetailSpec, *, language: str, max_points: int) -> str:
    return _FORMAT_INSTRUCTION.format(
        language=language, max_points=max_points,
        tldr=spec.tldr, point=spec.point, note=spec.note,
    )


def _parse_reply(reply: str, *, max_points: int) -> tuple[str, tuple[str, ...]]:
    """Extract (tldr, points) from the model's reply, tolerating drift.

    Models mostly follow the requested "TLDR: ... / - ..." shape but drift on
    details (bold markers, missing label, extra prose), so parsing is
    forgiving: the TLDR is the labelled line if present, otherwise the first
    non-bullet line; points are every bullet line, capped at ``max_points``.
    """
    tldr:   str = ""
    points: list[str] = []

    for raw_line in reply.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(_BULLET_PREFIXES):
            points.append(line[2:].strip())
            continue
        # Bold markers ("**TLDR:** ...") are the most common format drift;
        # strip them only on non-bullet lines so "* " bullets survive above.
        unbolded   = line.strip("*").strip()
        tldr_match = re.match(r"(?i)^tl;?dr\s*[:\-]\s*(.*)$", unbolded)
        if tldr_match and not tldr:
            tldr = tldr_match.group(1).strip().strip("*").strip()
        elif not tldr and not points:
            # Unlabelled leading prose before any bullet: treat as the TLDR.
            tldr = unbolded

    return tldr, tuple(points[:max_points])
