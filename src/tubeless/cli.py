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

from tubeless import config
from tubeless.channels import CHANNELS_PATH, load_channels
from tubeless.corpus import CORPUS_ROOT
from tubeless.digest import DEFAULT_PER_CHANNEL_LIMIT, Digest, build_digest, record_entry
from tubeless.errors import ConfigError, CorpusError, TubelessError
from tubeless.llm import BACKENDS, make_backend
from tubeless.render import to_markdown
from tubeless.source import fetch_video_meta
from tubeless.state import STATE_PATH, read_seen, write_seen
from tubeless.summary import DEFAULT_DETAIL, DEFAULT_LANGUAGE, DETAIL_LEVELS, Summary, summarize
from tubeless.transcript import fetch_transcript

__all__ = ["main"]

_SUBCOMMANDS = ("summarize", "digest")
_DIGEST_DIR  = Path.home() / ".tubeless" / "digests"


def main(argv: list[str] | None = None) -> int:
    """Run the chosen subcommand; return the process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = _build_parser().parse_args(_with_default_subcommand(argv))
        return args.run(args)
    except KeyboardInterrupt:
        # Ctrl-C is a BaseException, so it slips past `except TubelessError`;
        # catch it here for a clean exit instead of a network-stack traceback.
        print("tubeless: cancelled", file=sys.stderr)
        return 130   # 128 + SIGINT, the shell convention
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
    backend    = make_backend(args.backend, model=args.model)
    _print_run_settings(args.backend, backend.model,
                        detail=args.detail, max_points=args.max_points, lang=args.lang)
    summary    = summarize(
        transcript, video, backend,
        target_language = args.lang,
        detail          = args.detail,
        max_points      = args.max_points,
    )
    if args.json:
        print(json.dumps(dataclasses.asdict(summary), ensure_ascii=False, indent=2))
    else:
        print(_render_text(summary))
    return 0


def _run_digest(args: argparse.Namespace) -> int:
    channels = load_channels(args.channels)
    if args.only:
        channels = tuple(c for c in channels if args.only.lower() in c.label.lower())
        if not channels:
            raise ConfigError(f"no channel label contains {args.only!r} in {args.channels}")
    backend           = make_backend(args.backend, model=args.model)
    _print_run_settings(args.backend, backend.model, lang=args.lang, limit=args.limit)
    seen              = read_seen(args.state)
    digest, processed = build_digest(
        channels, backend,
        date              = _today(),
        seen              = seen,
        language          = args.lang,
        per_channel_limit = args.limit,
        with_synthesis    = args.synthesize,
    )
    markdown = to_markdown(digest)
    if args.dry_run:
        print(markdown)
        return 0

    out_path = _write_digest(args.out, digest.date, markdown)
    write_seen(seen | processed, args.state)
    _record_to_corpus(digest, root=args.corpus)
    skipped_note = f", {len(digest.skipped)} channels skipped" if digest.skipped else ""
    print(f"digest written: {out_path} ({len(digest.entries)} videos{skipped_note})")
    return 0


def _record_to_corpus(digest: Digest, *, root: Path) -> None:
    """Archive each summarized video to the on-disk corpus (see
    ``corpus.record_entry`` for the record it builds).

    Best-effort per entry: the corpus is a secondary archive, and the digest file
    and seen-set are already persisted by now, so one entry's I/O failure is
    reported and skipped rather than aborting the loop and dropping every entry
    that follows -- those are already marked seen and would never be retried.
    """
    for entry in digest.entries:
        try:
            record_entry(entry, digest.date, root=root)
        except CorpusError as err:
            print(f"tubeless: corpus not updated for {entry.upload.video_id} ({err})",
                  file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    parser     = argparse.ArgumentParser(prog="tubeless", description="Summarize YouTube videos.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summarize_parser = subparsers.add_parser("summarize", help="summarize one video")
    summarize_parser.add_argument("url", help="YouTube URL or bare 11-character video id")
    _add_backend_args(summarize_parser)
    summarize_parser.add_argument("--lang", default=config.setting("TUBELESS_LANG") or DEFAULT_LANGUAGE,
                                  help=f"language of the summary (default: {DEFAULT_LANGUAGE}, or $TUBELESS_LANG)")
    summarize_parser.add_argument("--detail", choices=DETAIL_LEVELS,
                                  default=_configured_choice("TUBELESS_DETAIL", DETAIL_LEVELS, DEFAULT_DETAIL),
                                  help=f"summary depth: {' | '.join(DETAIL_LEVELS)} "
                                       f"(default: {DEFAULT_DETAIL}, or $TUBELESS_DETAIL)")
    summarize_parser.add_argument("--max-points", type=_positive_int,
                                  default=_configured_positive_int("TUBELESS_MAX_POINTS", None),
                                  help="max key points; overrides the per-detail default (or $TUBELESS_MAX_POINTS)")
    summarize_parser.add_argument("--json", action="store_true",
                                  help="print the summary as JSON instead of text")
    summarize_parser.set_defaults(run=_run_summarize)

    digest_parser = subparsers.add_parser("digest", help="build the daily multi-channel digest")
    _add_backend_args(digest_parser)
    digest_parser.add_argument("--lang", default=config.setting("TUBELESS_LANG") or DEFAULT_LANGUAGE,
                               help=f"language of the summaries (default: {DEFAULT_LANGUAGE}, or $TUBELESS_LANG)")
    digest_parser.add_argument("--channels", type=Path, default=CHANNELS_PATH,
                               help=f"channels TOML file (default: {CHANNELS_PATH})")
    digest_parser.add_argument("--state", type=Path, default=STATE_PATH,
                               help=f"processed-id state file (default: {STATE_PATH})")
    digest_parser.add_argument("--out", type=Path, default=_DIGEST_DIR,
                               help=f"directory for the digest file (default: {_DIGEST_DIR})")
    digest_parser.add_argument("--corpus", type=Path, default=CORPUS_ROOT,
                               help=f"directory for the analysis corpus of summaries and "
                                    f"transcripts (default: {CORPUS_ROOT})")
    digest_parser.add_argument("--only", default=None,
                               help="run only channels whose label contains this text")
    digest_parser.add_argument("--limit", type=_positive_int,
                               default=_configured_positive_int("TUBELESS_LIMIT", DEFAULT_PER_CHANNEL_LIMIT),
                               help=f"max recent uploads to check per channel "
                                    f"(default: {DEFAULT_PER_CHANNEL_LIMIT}, or $TUBELESS_LIMIT)")
    digest_parser.add_argument("--synthesize", action="store_true",
                               default=_configured_flag("TUBELESS_SYNTHESIZE"),
                               help="lead the digest with a cross-video synthesis (tone, "
                                    "agreement, divergence); needs 2+ videos (or $TUBELESS_SYNTHESIZE)")
    digest_parser.add_argument("--dry-run", action="store_true",
                               help="print the digest instead of writing it and updating state")
    digest_parser.set_defaults(run=_run_digest)
    return parser


def _add_backend_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--backend", choices=BACKENDS, default=_default_backend(),
                     help="LLM vendor (default: openai, or $TUBELESS_BACKEND)")
    sub.add_argument("--model", default=config.setting("TUBELESS_MODEL"),
                     help="model id; default is the backend's small-tier model (or $TUBELESS_MODEL)")


def _configured_choice(name: str, choices: tuple[str, ...], fallback: str) -> str:
    """A CLI default read from the environment/config.env, constrained to a closed
    set. Unset -> ``fallback``; set-but-invalid -> a clean ConfigError."""
    value = config.setting(name)
    if value is None:
        return fallback
    if value not in choices:
        raise ConfigError(f"{name} must be one of {choices}, got {value!r}")
    return value


def _configured_positive_int(name: str, fallback: int | None) -> int | None:
    """A CLI default read from the environment/config.env, required positive."""
    value = config.setting(name)
    if value is None:
        return fallback
    try:
        return _as_positive_int(value)
    except ValueError as err:
        raise ConfigError(f"{name} must be a positive integer: {err}") from None


def _configured_flag(name: str) -> bool:
    """A boolean CLI default read from the environment/config.env: true for
    ``1`` / ``true`` / ``yes`` / ``on`` (case-insensitive), false otherwise."""
    value = config.setting(name)
    return value is not None and value.strip().lower() in ("1", "true", "yes", "on")


def _default_backend() -> str:
    """The backend used when --backend is not given (``TUBELESS_BACKEND``, else openai)."""
    return _configured_choice("TUBELESS_BACKEND", BACKENDS, "openai")


def _as_positive_int(text: str) -> int:
    """Parse a positive int or raise ValueError. One home for the rule, shared by
    the argparse type and the config-default reader, which wrap the failure in
    their own error class."""
    value = int(text)
    if value < 1:
        raise ValueError(f"must be a positive integer, got {value}")
    return value


def _positive_int(text: str) -> int:
    """argparse ``type=`` for --max-points/--limit (a non-positive cap slices
    instead of capping -- see summary.summarize)."""
    try:
        return _as_positive_int(text)
    except ValueError as err:
        raise argparse.ArgumentTypeError(str(err)) from None


def _print_run_settings(backend: str, model: str, **fields: object) -> None:
    """Print the resolved run settings to stderr, so a bare ``tubeless <url>``
    shows which backend/model and options it actually used.

    Goes to stderr, not stdout, so it never contaminates the summary text or the
    ``--json`` payload. The model line matters most: a small model on an
    unfamiliar name can quietly mangle it, and seeing the model makes that legible
    rather than mysterious. ``model`` is read from the constructed backend, so it
    reflects the class default when ``--model`` was not given. ``None`` fields are
    omitted (an unset --max-points is not worth a line)."""
    parts = [f"backend={backend}", f"model={model}"]
    parts += [f"{name}={value}" for name, value in fields.items() if value is not None]
    print("tubeless: " + "  ".join(parts), file=sys.stderr)


def _render_text(summary: Summary) -> str:
    header = summary.video.title
    if summary.video.channel:
        header += f" — {summary.video.channel}"
    lines = [header, summary.video.url, "", f"TL;DR: {summary.tldr}"]
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
