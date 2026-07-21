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

__all__ = ["Summary", "summarize"]

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
# one "TLDR:" line, then "- " bullets.
_FORMAT_INSTRUCTION = (
    "Answer in {language}. Use exactly this format:\n"
    "TLDR: <one- or two-sentence gist>\n"
    "- <key point>\n"
    "- <key point>\n"
    "Give at most {max_points} key points. No other text."
)

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
    max_points:      int = 7,
) -> Summary:
    """Summarize ``transcript`` into a TLDR plus at most ``max_points`` points.

    Long transcripts are map-reduced: each ~``CHUNK_WORD_LIMIT``-word chunk is
    summarized on its own, then the chunk summaries are combined into the
    final answer. When the transcript is auto-generated, every prompt warns
    the model to hedge uncertain names and numbers.

    Raises:
        LLMError: propagated from the backend.
    """
    hedge  = _AUTO_CAPTION_HEDGE if transcript.is_auto_generated else ""
    chunks = _split_into_chunks(transcript.text, word_limit=CHUNK_WORD_LIMIT)

    if len(chunks) == 1:
        reply = backend.complete(
            _single_pass_prompt(
                chunks[0], video, hedge=hedge,
                language=target_language, max_points=max_points,
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
                language=target_language, max_points=max_points,
            ),
            system=_SYSTEM_PROMPT,
        )

    tldr, points = _parse_reply(reply, max_points=max_points)
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
    chunk: str, video: Video, *, hedge: str, language: str, max_points: int
) -> str:
    return (
        f"Summarize the transcript of the video {video.title!r}.\n"
        f"{hedge}"
        f"{_FORMAT_INSTRUCTION.format(language=language, max_points=max_points)}\n\n"
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
    chunk_summaries: list[str], video: Video, *, hedge: str, language: str, max_points: int
) -> str:
    numbered = "\n\n".join(
        f"[part {part_number}]\n{part_summary}"
        for part_number, part_summary in enumerate(chunk_summaries, start=1)
    )
    return (
        f"Below are part-by-part summaries of the video {video.title!r}, in order. "
        "Combine them into one summary of the whole video.\n"
        f"{hedge}"
        f"{_FORMAT_INSTRUCTION.format(language=language, max_points=max_points)}\n\n"
        f"{numbered}"
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
