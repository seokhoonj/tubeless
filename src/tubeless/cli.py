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
from tubeless.digest import DEFAULT_PER_CHANNEL_LIMIT, recompute, run_digest
from tubeless.discover import DEFAULT_SCAN, discover
from tubeless.errors import ConfigError, ScheduleError, TubelessError
from tubeless.llm import BACKENDS, make_backend
from tubeless.render import to_markdown
from tubeless.schedule import (
    DEFAULT_DAILY_TIME,
    DigestSchedule,
    parse_daily_time,
    resolve_digest_command,
    scheduler_for_platform,
)
from tubeless.state import STATE_PATH, read_seen, write_seen
from tubeless.store import CORPUS_ROOT, FileStore
from tubeless.summary import (
    DEFAULT_DETAIL,
    DEFAULT_LANGUAGE,
    DETAIL_LEVELS,
    Summary,
    summarize,
)

__all__ = ["main"]

_SUBCOMMANDS = ("summarize", "digest", "recompute", "discover", "schedule")
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
    backend = make_backend(args.backend, model=args.model)
    _print_run_settings(args.backend, backend.model,
                        detail=args.detail, max_points=args.max_points, lang=args.lang)
    summary = summarize(
        args.url, backend,
        detail     = args.detail,
        language   = args.lang,
        max_points = args.max_points,
    )
    if args.json:
        print(json.dumps(dataclasses.asdict(summary), ensure_ascii=False, indent=2))
    else:
        print(_render_text(summary))
    return 0


def _run_digest(args: argparse.Namespace) -> int:
    channels = load_channels(args.channels)
    if args.only:
        channels = tuple(c for c in channels if args.only.lower() in c.source.lower())
        if not channels:
            raise ConfigError(f"no channel source contains {args.only!r} in {args.channels}")
    backend = make_backend(args.backend, model=args.model)
    _print_run_settings(args.backend, backend.model, lang=args.lang, limit=args.limit)
    seen  = read_seen(args.state)
    # A dry run persists nothing (store=None): the summaries and transcripts a
    # real run writes through to the corpus are skipped, and the seen-set is left
    # untouched, so the run can be repeated.
    store = None if args.dry_run else FileStore(args.corpus)
    run   = run_digest(
        channels, backend,
        period            = _today(),
        seen              = frozenset(seen),
        language          = args.lang,
        per_channel_limit = args.limit,
        with_synthesis    = args.synthesize,
        store             = store,
    )
    markdown = to_markdown(run.digest)
    if args.dry_run:
        print(markdown)
        return 0

    out_path = _write_digest(args.out, run.digest.period, markdown)
    write_seen(set(run.seen), args.state)
    skipped_note = f", {len(run.digest.skipped)} skipped" if run.digest.skipped else ""
    print(f"digest written: {out_path} ({len(run.digest.entries)} videos{skipped_note})")
    return 0


def _run_recompute(args: argparse.Namespace) -> int:
    backend = make_backend(args.backend, model=args.model)
    _print_run_settings(args.backend, backend.model, lang=args.lang,
                        since=args.since, until=args.until, channel=args.channel)
    digest = recompute(
        backend, FileStore(args.corpus),
        since          = args.since,
        until          = args.until,
        channel        = args.channel,
        language       = args.lang,
        with_synthesis = not args.no_synthesize,
    )
    markdown = to_markdown(digest)
    if args.dry_run:
        print(markdown)
        return 0

    out_path = _write_digest(args.out, digest.period, markdown)
    print(f"digest written: {out_path} ({len(digest.entries)} videos)")
    return 0


def _run_discover(args: argparse.Namespace) -> int:
    for video in discover(args.source, limit=args.limit):
        print(f"{video.video_id}  {video.published or '?':<20}  {video.title}")
    return 0


def _run_schedule_install(args: argparse.Namespace) -> int:
    schedule = DigestSchedule(command=resolve_digest_command(), daily_time=args.at)
    status   = scheduler_for_platform().install(schedule)
    print(f"scheduled: {' '.join(schedule.command)}  daily at {args.at:%H:%M}")
    print(f"  {status.description}")
    print("Backend, language, and synthesis come from ~/.tubeless/config.env.")
    return 0


def _run_schedule_uninstall(args: argparse.Namespace) -> int:
    removed = scheduler_for_platform().uninstall()
    print("removed the daily digest schedule" if removed else "nothing was scheduled")
    return 0


def _run_schedule_status(args: argparse.Namespace) -> int:
    status = scheduler_for_platform().status()
    print(f"scheduled: {status.description}" if status.installed
          else f"not scheduled ({status.description})")
    return 0


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
                               help="run only channels whose source contains this text")
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

    recompute_parser = subparsers.add_parser(
        "recompute", help="re-assemble a digest from stored summaries over a date range")
    _add_backend_args(recompute_parser)
    recompute_parser.add_argument("--lang", default=config.setting("TUBELESS_LANG") or DEFAULT_LANGUAGE,
                                  help=f"language of the synthesis (default: {DEFAULT_LANGUAGE}, or $TUBELESS_LANG)")
    recompute_parser.add_argument("--since", default=None,
                                  help="start date, inclusive (e.g. 2026-07-01); omit for no lower bound")
    recompute_parser.add_argument("--until", default=None,
                                  help="end date, exclusive (e.g. 2026-07-08); omit for no upper bound")
    recompute_parser.add_argument("--channel", default=None,
                                  help="limit to summaries whose channel matches this name")
    recompute_parser.add_argument("--corpus", type=Path, default=CORPUS_ROOT,
                                  help=f"the stored corpus to read (default: {CORPUS_ROOT})")
    recompute_parser.add_argument("--out", type=Path, default=_DIGEST_DIR,
                                  help=f"directory for the digest file (default: {_DIGEST_DIR})")
    recompute_parser.add_argument("--no-synthesize", action="store_true",
                                  help="skip the cross-source synthesis (on by default for recompute)")
    recompute_parser.add_argument("--dry-run", action="store_true",
                                  help="print the digest instead of writing it")
    recompute_parser.set_defaults(run=_run_recompute)

    discover_parser = subparsers.add_parser(
        "discover", help="list a channel or playlist's recent videos (id, published, title)")
    discover_parser.add_argument("source", help="a channel @handle / URL / 'UC...' id, or a playlist")
    discover_parser.add_argument("--limit", type=_positive_int, default=DEFAULT_SCAN,
                                 help=f"how many recent feed entries to scan (default: {DEFAULT_SCAN})")
    discover_parser.set_defaults(run=_run_discover)

    schedule_parser  = subparsers.add_parser("schedule",
                                             help="install/remove the daily digest in your OS scheduler (Linux cron)")
    schedule_actions = schedule_parser.add_subparsers(dest="action", required=True)

    schedule_install = schedule_actions.add_parser("install", help="register the daily digest job")
    schedule_install.add_argument("--at", type=_daily_time, default=DEFAULT_DAILY_TIME,
                                  help=f"daily run time, HH:MM 24-hour (default: {DEFAULT_DAILY_TIME:%H:%M})")
    schedule_install.set_defaults(run=_run_schedule_install)

    schedule_uninstall = schedule_actions.add_parser("uninstall", help="remove the daily digest job")
    schedule_uninstall.set_defaults(run=_run_schedule_uninstall)

    schedule_status = schedule_actions.add_parser("status", help="show whether the daily digest is scheduled")
    schedule_status.set_defaults(run=_run_schedule_status)
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


def _daily_time(text: str) -> datetime.time:
    """argparse ``type=`` for --at: parse an ``HH:MM`` time, reporting a bad value
    the argparse way (a usage error) like --max-points/--limit do."""
    try:
        return parse_daily_time(text)
    except ScheduleError as err:
        raise argparse.ArgumentTypeError(str(err)) from None


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
