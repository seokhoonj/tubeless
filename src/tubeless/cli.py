"""Command-line entry point.

Five subcommands compose the library's atoms into user-facing actions:
``summarize <url>`` prints one video's summary, ``transcript <url>`` prints one
video's raw captions, ``videos <source>`` lists a channel's recent uploads,
``digest`` builds the ranked multi-channel digest (fresh, or ``--since/--until``
over stored summaries), and ``schedule`` registers the daily digest job. A bare
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
import re
import sys
from pathlib import Path

from tubeless import config
from tubeless.channels import CHANNELS_PATH, Channel, load_channels
from tubeless.digest import Skip, curate_summaries, summarize_videos
from tubeless.discover import DEFAULT_SCAN, fetch_recent_videos
from tubeless.errors import ConfigError, FeedError, ScheduleError, TubelessError
from tubeless.llm import BACKENDS, LLMBackend, make_backend
from tubeless.render import render_markdown
from tubeless.schedule import (
    DEFAULT_DAILY_TIME,
    DigestSchedule,
    parse_daily_time,
    resolve_digest_command,
    scheduler_for_platform,
)
from tubeless.source import fetch_video
from tubeless.state import STATE_PATH, read_seen, write_seen
from tubeless.store import CORPUS_ROOT, FileStore, latest_per_video
from tubeless.summary import (
    DEFAULT_DETAIL,
    DEFAULT_LANGUAGE,
    DETAIL_LEVELS,
    Summary,
    summarize_transcript,
)
from tubeless.transcript import fetch_transcript

__all__ = ["main"]

_SUBCOMMANDS = ("summarize", "transcript", "videos", "digest", "schedule")
_DIGEST_DIR  = Path.home() / ".tubeless" / "digests"

# How many recent uploads to check per plain channel on a fresh digest when the
# caller does not say. A channel with a title filter scans the full feed window
# instead (matches are sparse among the rest), so this cap applies only to
# unfiltered channels.
DEFAULT_PER_CHANNEL_LIMIT = 5

# A YouTube id is 11 base64url characters, so ~1 in 64 starts with '-'. argparse
# would read such a bare id as an option flag, so a leading-dash id is rewritten
# to its watch URL (which never starts with '-') before parsing. The pattern is
# strict, so a genuine mistyped flag (e.g. '--jsonn') is NOT rewritten and still
# gets argparse's normal error.
_LEADING_DASH_ID = re.compile(r"^-[A-Za-z0-9_-]{10}$")


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
    help flag, so a bare ``tubeless <url>`` still means ``tubeless summarize``.

    Also rewrite a leading-dash bare video id (which argparse would treat as an
    option) to its watch URL, whether it heads a bare run or follows an explicit
    ``summarize``."""
    argv = list(argv)
    if argv and argv[0] == "summarize" and len(argv) > 1 and _LEADING_DASH_ID.match(argv[1]):
        argv[1] = _watch_url(argv[1])
    if argv and argv[0] not in _SUBCOMMANDS and argv[0] not in ("-h", "--help"):
        head = _watch_url(argv[0]) if _LEADING_DASH_ID.match(argv[0]) else argv[0]
        return ["summarize", head, *argv[1:]]
    return argv


def _watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _run_summarize(args: argparse.Namespace) -> int:
    backend = make_backend(args.backend, model=args.model)
    _print_run_settings(args.backend, backend.model,
                        detail=args.detail, max_points=args.max_points, lang=args.lang)
    transcript = fetch_transcript(fetch_video(args.url))
    summary    = summarize_transcript(
        transcript, backend,
        detail     = args.detail,
        language   = args.lang,
        max_points = args.max_points,
    )
    if args.json:
        print(json.dumps(dataclasses.asdict(summary), ensure_ascii=False, indent=2))
    else:
        print(_render_text(summary))
    return 0


def _run_transcript(args: argparse.Namespace) -> int:
    # Raw captions, no LLM: fetch the video and its transcript and print the
    # plain text (or the full structure with --json).
    transcript = fetch_transcript(fetch_video(args.url))
    if args.json:
        print(json.dumps(dataclasses.asdict(transcript), ensure_ascii=False, indent=2))
    else:
        print(transcript.text)
    return 0


def _run_videos(args: argparse.Namespace) -> int:
    for video in fetch_recent_videos(args.source, limit=args.limit):
        print(f"{video.video_id}  {video.published or '?':<20}  {video.title}")
    return 0


def _run_digest(args: argparse.Namespace) -> int:
    """Build a digest. A fresh run discovers each channel's new videos,
    summarizes and ranks them; --since/--until instead re-curates already-stored
    summaries over a date range (no discovery, no fetching)."""
    backend = make_backend(args.backend, model=args.model)
    if args.since or args.until or args.channel:
        return _digest_stored(args, backend)
    return _digest_fresh(args, backend)


def _digest_fresh(args: argparse.Namespace, backend: LLMBackend) -> int:
    channels = _selected_channels(args.channels, args.only)
    _print_run_settings(args.backend, backend.model, lang=args.lang, limit=args.limit)
    seen = frozenset(read_seen(args.state))
    # A dry run persists nothing (store=None): the summaries and transcripts a
    # real run writes through to the corpus are skipped, and the seen-set is left
    # untouched, so the run can be repeated.
    store = None if args.dry_run else FileStore(args.corpus)

    summaries: list[Summary] = []
    skipped:   list[Skip] = []
    processed: set[str] = set()
    for channel in channels:
        # A filtered channel scans the full feed window, not just --limit: the
        # wanted uploads are sparse among the rest, so a small window silently
        # drops them (the documented missed-video incident). A plain channel
        # keeps the small limit.
        has_filter = bool(channel.includes or channel.excludes)
        limit      = DEFAULT_SCAN if has_filter else args.limit
        try:
            videos = fetch_recent_videos(
                channel.source, limit=limit,
                includes=channel.includes, excludes=channel.excludes,
            )
        except FeedError as err:
            skipped.append(Skip("feed-failure", channel.source, str(err)))
            continue
        # Only ids not already seen (or handled earlier this run) are summarized,
        # so a video shared by two channel sources is handled once.
        fresh = tuple(
            video for video in videos
            if video.video_id not in seen and video.video_id not in processed
        )
        result = summarize_videos(
            fresh, backend, detail=channel.detail, language=args.lang, store=store,
        )
        summaries.extend(result.summaries)
        skipped.extend(result.skipped)
        processed |= result.processed

    digest   = curate_summaries(summaries, backend, period=_today(),
                                language=args.lang, skipped=skipped)
    markdown = render_markdown(digest)
    if args.dry_run:
        print(markdown)
        return 0

    out_path = _write_digest(args.out, digest.period, markdown)
    write_seen(set(seen | processed), args.state)
    skipped_note = f", {len(digest.skipped)} skipped" if digest.skipped else ""
    print(f"digest written: {out_path} ({len(digest.entries)} videos{skipped_note})")
    return 0


def _digest_stored(args: argparse.Namespace, backend: LLMBackend) -> int:
    # Re-curate stored summaries over [since, until): no discovery or fetching,
    # read-only on the corpus. Keeps the most recent summary per video so a video
    # with several stored variants is never double-counted.
    if args.only is not None:
        # --only narrows a fresh discovery; it has no meaning over stored
        # summaries. Reject it rather than silently ignore it (--channel is the
        # stored-mode equivalent).
        raise ConfigError("--only applies to a fresh digest run, not a --since/--until re-curate")
    _print_run_settings(args.backend, backend.model, lang=args.lang,
                        since=args.since, until=args.until, channel=args.channel)
    stored    = FileStore(args.corpus).load_summaries(
        since=args.since, until=args.until, channel=args.channel)
    summaries = latest_per_video(stored)
    digest    = curate_summaries(summaries, backend,
                                 period=_range_label(args.since, args.until), language=args.lang)
    markdown  = render_markdown(digest)
    if args.dry_run:
        print(markdown)
        return 0

    out_path = _write_digest(args.out, digest.period, markdown)
    print(f"digest written: {out_path} ({len(digest.entries)} videos)")
    return 0


def _run_schedule_install(args: argparse.Namespace) -> int:
    schedule = DigestSchedule(command=resolve_digest_command(), daily_time=args.at)
    status   = scheduler_for_platform().install(schedule)
    print(f"scheduled: {' '.join(schedule.command)}  daily at {args.at:%H:%M}")
    print(f"  {status.description}")
    print("Backend and language come from ~/.tubeless/config.env.")
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

    transcript_parser = subparsers.add_parser(
        "transcript", help="print one video's raw transcript (no LLM)")
    transcript_parser.add_argument("url", help="YouTube URL or bare 11-character video id")
    transcript_parser.add_argument("--json", action="store_true",
                                   help="print the full transcript structure as JSON instead of plain text")
    transcript_parser.set_defaults(run=_run_transcript)

    videos_parser = subparsers.add_parser(
        "videos", help="list a channel or playlist's recent videos (id, published, title)")
    videos_parser.add_argument("source", help="a channel @handle / URL / 'UC...' id, or a playlist")
    videos_parser.add_argument("--limit", type=_positive_int, default=DEFAULT_SCAN,
                               help=f"how many recent feed entries to scan (default: {DEFAULT_SCAN})")
    videos_parser.set_defaults(run=_run_videos)

    digest_parser = subparsers.add_parser(
        "digest", help="build the ranked multi-channel digest (fresh, or --since/--until over stored summaries)")
    _add_backend_args(digest_parser)
    digest_parser.add_argument("--lang", default=config.setting("TUBELESS_LANG") or DEFAULT_LANGUAGE,
                               help=f"language of the summaries (default: {DEFAULT_LANGUAGE}, or $TUBELESS_LANG)")
    digest_parser.add_argument("--channels", type=Path, default=CHANNELS_PATH,
                               help=f"channels TOML file for a fresh run (default: {CHANNELS_PATH})")
    digest_parser.add_argument("--state", type=Path, default=STATE_PATH,
                               help=f"processed-id state file for a fresh run (default: {STATE_PATH})")
    digest_parser.add_argument("--out", type=Path, default=_DIGEST_DIR,
                               help=f"directory for the digest file (default: {_DIGEST_DIR})")
    digest_parser.add_argument("--corpus", type=Path, default=CORPUS_ROOT,
                               help=f"directory for the analysis corpus of summaries and "
                                    f"transcripts (default: {CORPUS_ROOT})")
    digest_parser.add_argument("--only", default=None,
                               help="fresh run: only channels whose source contains this text")
    digest_parser.add_argument("--limit", type=_positive_int,
                               default=_configured_positive_int("TUBELESS_LIMIT", DEFAULT_PER_CHANNEL_LIMIT),
                               help=f"fresh run: max recent uploads to check per channel "
                                    f"(default: {DEFAULT_PER_CHANNEL_LIMIT}, or $TUBELESS_LIMIT)")
    digest_parser.add_argument("--since", type=_iso_date, default=None,
                               help="re-curate stored summaries from this date, inclusive (e.g. 2026-07-01)")
    digest_parser.add_argument("--until", type=_iso_date, default=None,
                               help="re-curate stored summaries up to this date, exclusive (e.g. 2026-07-08)")
    digest_parser.add_argument("--channel", default=None,
                               help="re-curate only stored summaries whose channel matches this name")
    digest_parser.add_argument("--dry-run", action="store_true",
                               help="print the digest instead of writing it and updating state")
    digest_parser.set_defaults(run=_run_digest)

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


def _selected_channels(path: Path, only: str | None) -> tuple[Channel, ...]:
    """Load the channel list, optionally narrowed to sources containing ``only``
    (case-insensitive). Raises ``ConfigError`` when ``only`` matches nothing."""
    channels = load_channels(path)
    if only is None:
        return channels
    needle  = only.lower()
    matched = tuple(channel for channel in channels if needle in channel.source.lower())
    if not matched:
        raise ConfigError(f"no channel source contains {only!r} in {path}")
    return matched


def _range_label(since: str | None, until: str | None) -> str:
    """A display label for a stored re-curate's span: ``since..until`` (an open
    end left blank), or ``all`` when the range is unbounded."""
    if since is None and until is None:
        return "all"
    return f"{since or ''}..{until or ''}"


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


def _iso_date(text: str) -> str:
    """argparse ``type=`` for --since/--until: validate a ``YYYY-MM-DD`` date and
    return it normalised. Without this a malformed bound (e.g. ``2026-7-1``) would
    slip into ``load_summaries``' lexicographic string compare and silently
    mis-filter to an empty digest instead of failing as a usage error."""
    try:
        return datetime.date.fromisoformat(text).isoformat()
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"must be a YYYY-MM-DD date: {err}") from None


def _daily_time(text: str) -> datetime.time:
    """argparse ``type=`` for --at: parse an ``HH:MM`` time, reporting a bad value
    the argparse way (a usage error) like --max-points/--limit do."""
    try:
        return parse_daily_time(text)
    except ScheduleError as err:
        raise argparse.ArgumentTypeError(str(err)) from None


def _positive_int(text: str) -> int:
    """argparse ``type=`` for --max-points/--limit (a non-positive cap slices
    instead of capping -- see summary.summarize_transcript)."""
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
