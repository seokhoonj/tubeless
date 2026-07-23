"""Summarize a transcript into a TL;DR and key points via an injected backend.

Pure transform territory: the only side effects are the backend calls, and the
backend is a parameter, so the whole module tests with a fake and never
touches the network itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, get_args

from tubeless.llm import LLMBackend
from tubeless.source import Video, fetch_video
from tubeless.transcript import Transcript, fetch_transcript

__all__ = [
    "DEFAULT_DETAIL",
    "DEFAULT_LANGUAGE",
    "DETAIL_LEVELS",
    "DetailLevel",
    "Summary",
    "language_name",
    "summarize",
    "summarize_transcript",
]

# YouTube's ISO codes are what --lang carries; a model follows a full language
# name ("Korean") far more reliably than a bare code ("ko"), which it sometimes
# ignores on long or English-associated content -- summarizing in English despite
# a Korean transcript and a `ko` request. Map the codes we use into names; pass an
# unrecognised value through unchanged, so a full name given directly ("Korean")
# or an unlisted code still works. Shared by summary, synthesis, and importance.
_LANGUAGE_NAMES = {
    "en": "English",  "ko": "Korean",   "ja": "Japanese",
    "zh": "Chinese",  "zh-hans": "Simplified Chinese", "zh-hant": "Traditional Chinese",
    "es": "Spanish",  "fr": "French",   "de": "German",
    "pt": "Portuguese", "ru": "Russian",
}


def language_name(language: str) -> str:
    """Map a language code ('ko') to the English language name ('Korean') a model
    follows reliably in a prompt. An unrecognised value (already a name, or an
    unlisted code) is returned unchanged."""
    return _LANGUAGE_NAMES.get(language.strip().lower(), language)

# The closed set of summary depths, in two forms. `DETAIL_LEVELS` (below) is the
# runtime tuple, derived from `_DETAIL`, and validates a caller who bypasses the
# type checker. `DetailLevel` is the static mirror -- a Literal cannot be computed
# from the dict, so it is hand-written and MUST be kept in sync with `_DETAIL`.
DetailLevel = Literal["brief", "normal", "deep"]

# The package-wide summary defaults, named once here so a consumer (the CLI)
# references them instead of re-spelling the literal at its call site -- a
# restated default freezes consumer users on the old value if it ever changes.
DEFAULT_LANGUAGE: str = "en"
DEFAULT_DETAIL:   DetailLevel = "normal"

# One map-phase chunk is ~3000 words (~4000 tokens of English; Korean is
# denser per word, still far inside any current chat model's context). Small
# enough that every chunk summary comes back at a comparable grain, large
# enough that a typical 20-30 minute video stays a single call and skips the
# reduce phase entirely.
CHUNK_WORD_LIMIT = 3000

_SYSTEM_PROMPT = (
    "You are a precise video-transcript summarizer. Work only from the "
    "transcript given; never invent facts that are not in it. Keep proper nouns "
    "-- names of people, companies, products, and models -- exactly as the "
    "transcript gives them; do not 'correct' an unfamiliar name to a similar "
    "well-known one, even if it looks like a mistake. Summarize the video's "
    "content in the third person -- never address, thank, or comment on the "
    "speaker, and never write as if replying to them."
)

# The model is asked for exactly this shape so parsing stays trivial:
# one "TL;DR:" line, then "- " bullets. The three "<...>" slots are filled from
# the chosen detail level so the same skeleton yields a terse or a rich summary.
_FORMAT_INSTRUCTION = (
    "Answer in {language}. Write no greeting or preamble; the very first line "
    "must start with 'TL;DR:'. Use exactly this format:\n"
    "TL;DR: <{tldr}>\n"
    "- <key point>\n"
    "- <key point>\n"
    "Give at most {max_points} key points; each key point is {point}. Start "
    "every key point with '- ', and keep it on its own single line.{note} No "
    "other text before or after."
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
    """How expansive one detail level is: TL;DR length, per-point fullness, the
    default point cap a typical-length video warrants, and an optional trailing
    instruction (e.g. keep every figure) appended to the format block."""

    tldr:       str
    point:      str
    max_points: int
    note:       str = ""


# --detail chooses one of these. "normal" is the default: fuller than a bare
# headline list, short of the exhaustive "deep" notes.
_DETAIL = {
    "brief": _DetailSpec(
        tldr       = "one-sentence gist",
        point      = "a single concise clause",
        max_points = 5,
    ),
    "normal": _DetailSpec(
        tldr       = "two- to three-sentence gist",
        point      = "one full sentence that states the specific claim, not just its topic",
        max_points = 8,
    ),
    "deep": _DetailSpec(
        tldr       = "three- to four-sentence overview",
        point      = (
            "two to four sentences that spell out the specifics -- the names, "
            "numbers, and reasoning the speaker actually gave, not just the topic"
        ),
        max_points = 14,
        note       = _PRESERVE_FIGURES,
    ),
}
DETAIL_LEVELS = tuple(_DETAIL)
# The hand-written DetailLevel Literal cannot be computed from _DETAIL, so guard
# the "keep in sync" comment at import (mirroring render._TIER_MARKER): a level
# added to one but not the other fails immediately, not at a later call.
assert set(DETAIL_LEVELS) == set(get_args(DetailLevel)), "every DetailLevel needs a _DETAIL spec"

_AUTO_CAPTION_HEDGE = (
    "The captions are auto-generated and may mis-transcribe names, numbers, "
    "and technical terms; hedge any specific that looks uncertain instead of "
    "stating it as fact.\n"
)

_BULLET_PREFIXES = ("- ", "* ", "• ")
# Some models (esp. smaller local ones) answer with a numbered list instead of
# "- " bullets. Recognize "1. ", "2) " etc. as points too. One or two digits
# only, so a sentence opening with a year ("2026. ") is not mistaken for a list.
_NUMBERED_POINT = re.compile(r"^\d{1,2}[.)]\s+(.*)$")
# The TL;DR label, tolerating "TLDR"/"TL;DR" and a colon, hyphen, or dash
# separator. Hoisted to module scope like _NUMBERED_POINT rather than rebuilt per
# reply line.
_TLDR_LABEL = re.compile(r"(?i)^tl;?dr\s*[:\-–—]\s*(.*)$")


@dataclass(frozen=True, slots=True)
class Summary:
    """The finished summary of one video, in ``language`` at depth ``detail``.

    ``detail`` records which level produced this summary, so a stored summary
    carries the depth it was written at (a re-summary at another depth is a
    distinct record, not an overwrite)."""

    video:    Video
    tldr:     str
    points:   tuple[str, ...]
    language: str
    detail:   DetailLevel


def summarize_transcript(
    transcript: Transcript,
    backend:    LLMBackend,
    *,
    detail:     DetailLevel = DEFAULT_DETAIL,
    language:   str = DEFAULT_LANGUAGE,
    max_points: int | None = None,
) -> Summary:
    """Summarize an already-fetched ``transcript`` into a TL;DR plus key points.

    This is the core: it takes the transcript the caller already has (which
    carries its own ``video``), so a digest that fetched many transcripts
    summarizes each without re-fetching. ``summarize`` is the one-URL wrapper.

    ``detail`` ('brief' / 'normal' / 'deep') sets how fully the summary is
    written -- the TL;DR length, how many sentences each point carries, and the
    default number of points. ``max_points`` overrides that default count when
    given; ``None`` keeps the per-detail default.

    Long transcripts are map-reduced: each ~``CHUNK_WORD_LIMIT``-word chunk is
    summarized on its own, then the chunk summaries are combined into the
    final answer. When the transcript is auto-generated, every prompt warns
    the model to hedge uncertain names and numbers.

    Raises:
        ValueError: ``detail`` is not one of ``DETAIL_LEVELS``, or ``max_points``
            is given and is less than 1.
        LLMError: propagated from the backend.
    """
    video = transcript.video
    spec  = _DETAIL.get(detail)
    if spec is None:
        raise ValueError(f"detail must be one of {DETAIL_LEVELS}, got {detail!r}")
    if max_points is not None and max_points < 1:
        # A non-positive cap would ask the model for "at most 0 points" and, via
        # the negative-slice `points[:max_points]`, silently drop trailing points
        # instead of capping. Reject it at the boundary.
        raise ValueError(f"max_points must be >= 1, got {max_points}")
    cap = spec.max_points if max_points is None else max_points

    hedge           = _AUTO_CAPTION_HEDGE if transcript.is_auto_generated else ""
    chunks          = _split_into_chunks(transcript.text, word_limit=CHUNK_WORD_LIMIT)
    prompt_language = language_name(language)   # a name, not a bare code, in the prompt

    if len(chunks) == 1:
        reply = backend.complete(
            _single_pass_prompt(
                chunks[0], video, hedge=hedge,
                language=prompt_language, spec=spec, max_points=cap,
            ),
            system=_SYSTEM_PROMPT,
        )
    else:
        chunk_summaries = [
            backend.complete(
                _chunk_prompt(chunk, video, hedge=hedge, language=prompt_language, spec=spec),
                system=_SYSTEM_PROMPT,
            )
            for chunk in chunks
        ]
        reply = backend.complete(
            _combine_prompt(
                chunk_summaries, video, hedge=hedge,
                language=prompt_language, spec=spec, max_points=cap,
            ),
            system=_SYSTEM_PROMPT,
        )

    tldr, points = _parse_reply(reply, max_points=cap)
    return Summary(video=video, tldr=tldr, points=points, language=language, detail=detail)


def summarize(
    url_or_id:  str,
    backend:    LLMBackend,
    *,
    detail:     DetailLevel = DEFAULT_DETAIL,
    language:   str = DEFAULT_LANGUAGE,
    max_points: int | None = None,
) -> Summary:
    """Summarize one video from its URL or id: fetch its metadata and transcript,
    then summarize. The convenience path for a single video -- ``fetch_video``
    then ``fetch_transcript`` then ``summarize_transcript`` in one call.

    Raises:
        InvalidVideoURL: ``url_or_id`` is not a recognizable video URL or id.
        TranscriptUnavailable / TranscriptFetchBlocked: from ``fetch_transcript``.
        ValueError: bad ``detail`` or ``max_points`` (see ``summarize_transcript``).
        LLMError: propagated from the backend.
    """
    transcript = fetch_transcript(fetch_video(url_or_id))
    return summarize_transcript(
        transcript, backend,
        detail=detail, language=language, max_points=max_points,
    )


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


def _chunk_prompt(chunk: str, video: Video, *, hedge: str, language: str, spec: _DetailSpec) -> str:
    # spec.note (figure/period/enumeration preservation) MUST ride along here,
    # not only in the combine step: a chunk summary that already dropped the
    # numbers gives the reduce phase nothing to preserve. This is why long
    # (map-reduced) videos lost figures that short single-pass ones kept.
    return (
        f"This is one part of the transcript of the video {video.title!r}. "
        f"Summarize just this part in {language} as plain prose, keeping every "
        f"concrete claim you will need later.{spec.note}\n"
        f"{hedge}\n"
        f"Transcript part:\n{chunk}"
    )


def _combine_prompt(
    chunk_summaries: list[str], video: Video, *,
    hedge: str, language: str, spec: _DetailSpec, max_points: int,
) -> str:
    numbered_parts = "\n\n".join(
        f"[part {part_number}]\n{part_summary}"
        for part_number, part_summary in enumerate(chunk_summaries, start=1)
    )
    return (
        f"Below are part-by-part summaries of the video {video.title!r}, in order. "
        "Combine them into one summary of the whole video.\n"
        f"{hedge}"
        f"{_format_instruction(spec, language=language, max_points=max_points)}\n\n"
        f"{numbered_parts}"
    )


def _format_instruction(spec: _DetailSpec, *, language: str, max_points: int) -> str:
    return _FORMAT_INSTRUCTION.format(
        language=language, max_points=max_points,
        tldr=spec.tldr, point=spec.point, note=spec.note,
    )


def _parse_reply(reply: str, *, max_points: int) -> tuple[str, tuple[str, ...]]:
    """Extract (tldr, points) from the model's reply, tolerating drift.

    Models mostly follow the requested "TL;DR: ... / - ..." shape but drift on
    details (bold markers, missing label, numbered lists, extra prose), so
    parsing is forgiving: the TL;DR is the labelled line if present, otherwise the
    first non-point line; points are every bullet or numbered line, capped at
    ``max_points``.
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
        numbered_match = _NUMBERED_POINT.match(line)
        if numbered_match:
            points.append(numbered_match.group(1).strip())
            continue
        # Bold markers ("**TL;DR:** ...") are the most common format drift;
        # strip them only on non-bullet lines so "* " bullets survive above.
        unbolded   = line.strip("*").strip()
        tldr_match = _TLDR_LABEL.match(unbolded)
        if tldr_match and not tldr:
            tldr = tldr_match.group(1).strip().strip("*").strip()
        elif not tldr and not points:
            # Unlabelled leading prose before any bullet: treat as the TL;DR.
            tldr = unbolded
        elif points and not points[-1].rstrip().endswith((".", "!", "?")):
            # A key point wrapped onto a second physical line: the continuation is
            # neither a bullet nor a label. Attach it to the last point rather than
            # drop it (which would truncate the point to a half-sentence) -- but
            # ONLY when that point does not already end a sentence, so a trailing
            # remark after the list ("In summary, ...") is not glued onto a
            # complete point and corrupt it. Summary has no sections to bound the
            # rejoin the way synthesis._parse_synthesis bounds OVERVIEW by section.
            points[-1] = f"{points[-1]} {line}".strip()

    return tldr, tuple(points[:max_points])
