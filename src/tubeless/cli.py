"""Command-line entry point: ``tubeless <url>`` prints one video's summary.

The CLI is a thin imperative shell: parse arguments, wire the pipeline, choose
an output shape. Every expected failure surfaces as a one-line stderr message
and a non-zero exit -- stack traces are reserved for actual bugs.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from tubeless.errors import TubelessError
from tubeless.llm import AnthropicBackend, LLMBackend, OpenAIBackend
from tubeless.source import fetch_video_meta
from tubeless.summary import Summary, summarize
from tubeless.transcript import fetch_transcript

__all__ = ["main"]

# Per-vendor default model, used when --model is not given. Kept cheap: the
# summary map-reduce can be many calls, so the small-tier model is the default.
_DEFAULT_MODEL = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
}


def _make_backend(backend: str, model: str | None) -> LLMBackend:
    """Construct the chosen vendor's backend, defaulting its model per vendor."""
    resolved = model or _DEFAULT_MODEL[backend]
    if backend == "anthropic":
        return AnthropicBackend(model=resolved)
    return OpenAIBackend(model=resolved)


def main(argv: list[str] | None = None) -> int:
    """Run the summary pipeline for one video URL; return the exit code."""
    args = _build_parser().parse_args(argv)

    try:
        video      = fetch_video_meta(args.url)
        transcript = fetch_transcript(video.video_id)
        backend    = _make_backend(args.backend, args.model)
        summary    = summarize(
            transcript, video, backend,
            target_language = args.lang,
            detail          = args.detail,
            max_points      = args.points,
        )
    except TubelessError as err:
        print(f"tubeless: {err}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(dataclasses.asdict(summary), ensure_ascii=False, indent=2))
    else:
        print(_render_text(summary))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog        = "tubeless",
        description = "Summarize a YouTube video from its transcript.",
    )
    parser.add_argument("url", help="YouTube URL or bare 11-character video id")
    parser.add_argument("--backend", choices=("openai", "anthropic"), default="openai",
                        help="LLM vendor (default: openai)")
    parser.add_argument("--lang", default="ko",
                        help="language of the summary (default: ko)")
    parser.add_argument("--detail", choices=("brief", "normal", "deep"), default="normal",
                        help="summary depth: brief | normal | deep (default: normal)")
    parser.add_argument("--model", default=None,
                        help="model id; defaults to the backend's small-tier model")
    parser.add_argument("--points", type=int, default=None,
                        help="max key points; overrides the per-detail default")
    parser.add_argument("--json", action="store_true",
                        help="print the summary as JSON instead of text")
    return parser


def _render_text(summary: Summary) -> str:
    header = summary.video.title
    if summary.video.channel:
        header += f" — {summary.video.channel}"
    lines = [header, summary.video.url, "", f"TLDR: {summary.tldr}"]
    if summary.points:
        lines.append("")
        lines.extend(f"- {point}" for point in summary.points)
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
