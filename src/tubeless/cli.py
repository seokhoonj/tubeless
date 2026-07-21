"""Command-line entry point.

Two subcommands: ``summarize <url>`` prints one video's summary, and ``digest``
builds the daily multi-channel digest into a Markdown file. A bare
``tubeless <url>`` with no subcommand still runs summarize, so the original
one-shot form keeps working.

The CLI is a thin imperative shell: parse arguments, wire the pipeline, choose
an output shape. Every expected failure surfaces as a one-line stderr message
and a non-zero exit -- stack traces are reserved for actual bugs.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import sys
from pathlib import Path

from tubeless.channels import CHANNELS_PATH, load_channels
from tubeless.digest import build_digest
from tubeless.errors import TubelessError
from tubeless.llm import AnthropicBackend, LLMBackend, OpenAIBackend
from tubeless.render import to_markdown
from tubeless.source import fetch_video_meta
from tubeless.state import STATE_PATH, read_seen, write_seen
from tubeless.summary import Summary, summarize
from tubeless.transcript import fetch_transcript

__all__ = ["main"]

# Per-vendor default model, used when --model is not given. Kept cheap: the
# summary map-reduce can be many calls, so the small-tier model is the default.
_DEFAULT_MODEL = {
    "openai":    "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
}
_SUBCOMMANDS = ("summarize", "digest")
_DIGEST_DIR  = Path.home() / ".tubeless" / "digests"


def main(argv: list[str] | None = None) -> int:
    """Run the chosen subcommand; return the process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _build_parser().parse_args(_with_default_subcommand(argv))
    try:
        return args.run(args)
    except TubelessError as err:
        print(f"tubeless: {err}", file=sys.stderr)
        return 1


def _with_default_subcommand(argv: list[str]) -> list[str]:
    """Insert 'summarize' when the first token is neither a subcommand nor a
    help flag, so a bare ``tubeless <url>`` still means ``tubeless summarize``."""
    if argv and argv[0] not in _SUBCOMMANDS and argv[0] not in ("-h", "--help"):
        return ["summarize", *argv]
    return argv


def _run_summarize(args: argparse.Namespace) -> int:
    video      = fetch_video_meta(args.url)
    transcript = fetch_transcript(video.video_id)
    backend    = _make_backend(args.backend, args.model)
    summary    = summarize(
        transcript, video, backend,
        target_language = args.lang,
        detail          = args.detail,
        max_points      = args.points,
    )
    if args.json:
        print(json.dumps(dataclasses.asdict(summary), ensure_ascii=False, indent=2))
    else:
        print(_render_text(summary))
    return 0


def _run_digest(args: argparse.Namespace) -> int:
    channels          = load_channels(args.channels)
    backend           = _make_backend(args.backend, args.model)
    seen              = read_seen(args.state)
    digest, processed = build_digest(
        channels, backend,
        date              = _today(),
        seen              = seen,
        language          = args.lang,
        per_channel_limit = args.limit,
    )
    markdown = to_markdown(digest)
    if args.dry_run:
        print(markdown)
        return 0

    out_path = _write_digest(args.out, digest.date, markdown)
    write_seen(seen | processed, args.state)
    skipped_note = f", {len(digest.skipped)} channels skipped" if digest.skipped else ""
    print(f"digest written: {out_path} ({len(digest.entries)} videos{skipped_note})")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser     = argparse.ArgumentParser(prog="tubeless", description="Summarize YouTube videos.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summarize_parser = subparsers.add_parser("summarize", help="summarize one video")
    summarize_parser.add_argument("url", help="YouTube URL or bare 11-character video id")
    _add_backend_args(summarize_parser)
    summarize_parser.add_argument("--lang", default="ko",
                                  help="language of the summary (default: ko)")
    summarize_parser.add_argument("--detail", choices=("brief", "normal", "deep"), default="normal",
                                  help="summary depth: brief | normal | deep (default: normal)")
    summarize_parser.add_argument("--points", type=int, default=None,
                                  help="max key points; overrides the per-detail default")
    summarize_parser.add_argument("--json", action="store_true",
                                  help="print the summary as JSON instead of text")
    summarize_parser.set_defaults(run=_run_summarize)

    digest_parser = subparsers.add_parser("digest", help="build the daily multi-channel digest")
    _add_backend_args(digest_parser)
    digest_parser.add_argument("--lang", default="ko",
                               help="language of the summaries (default: ko)")
    digest_parser.add_argument("--channels", type=Path, default=CHANNELS_PATH,
                               help=f"channels TOML file (default: {CHANNELS_PATH})")
    digest_parser.add_argument("--state", type=Path, default=STATE_PATH,
                               help=f"processed-id state file (default: {STATE_PATH})")
    digest_parser.add_argument("--out", type=Path, default=_DIGEST_DIR,
                               help=f"directory for the digest file (default: {_DIGEST_DIR})")
    digest_parser.add_argument("--limit", type=int, default=5,
                               help="max recent uploads to check per channel (default: 5)")
    digest_parser.add_argument("--dry-run", action="store_true",
                               help="print the digest instead of writing it and updating state")
    digest_parser.set_defaults(run=_run_digest)
    return parser


def _add_backend_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--backend", choices=("openai", "anthropic"), default="openai",
                     help="LLM vendor (default: openai)")
    sub.add_argument("--model", default=None,
                     help="model id; defaults to the backend's small-tier model")


def _make_backend(backend: str, model: str | None) -> LLMBackend:
    """Construct the chosen vendor's backend, defaulting its model per vendor."""
    resolved = model or _DEFAULT_MODEL[backend]
    if backend == "anthropic":
        return AnthropicBackend(model=resolved)
    return OpenAIBackend(model=resolved)


def _render_text(summary: Summary) -> str:
    header = summary.video.title
    if summary.video.channel:
        header += f" — {summary.video.channel}"
    lines = [header, summary.video.url, "", f"TLDR: {summary.tldr}"]
    if summary.points:
        lines.append("")
        lines.extend(f"- {point}" for point in summary.points)
    return "\n".join(lines)


def _write_digest(out_dir: Path, date: str, markdown: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{date}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def _today() -> str:
    return datetime.date.today().isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
