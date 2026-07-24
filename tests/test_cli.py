import json

import pytest

import tubeless.cli as cli_module
import tubeless.digest as digest_module
import tubeless.llm as llm_module
from tubeless import (
    FeedError,
    Transcript,
    TranscriptFetchBlocked,
    TranscriptSegment,
    TranscriptUnavailable,
    Video,
    config,
)
from tubeless.cli import (
    _configured_choice,
    _configured_positive_int,
    _default_backend,
    main,
)
from tubeless.errors import ConfigError
from tubeless.summary import DETAIL_LEVELS

SAMPLE_VIDEO = Video(
    video_id = "dQw4w9WgXcQ",
    title    = "A talk about ducks",
    url      = "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    channel  = "Duck Channel",
)

SAMPLE_TRANSCRIPT = Transcript(
    video             = SAMPLE_VIDEO,
    language          = "en",
    is_auto_generated = False,
    segments          = (TranscriptSegment(text="ducks are great", start=0.0, duration=3.0),),
)


class CannedBackend:
    def __init__(self, *, model: str = "unused") -> None:
        self.model = model

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        return "TLDR: Ducks are great.\n- They float.\n"


@pytest.fixture
def pipeline_with_fakes(monkeypatch: pytest.MonkeyPatch, _no_config_file) -> None:
    # The single-video path (summarize / transcript) composes these atoms in the
    # CLI module, so the fakes are patched there, not in summary.py.
    monkeypatch.setattr(cli_module, "fetch_video", lambda url: SAMPLE_VIDEO)
    monkeypatch.setattr(cli_module, "fetch_transcript", lambda video: SAMPLE_TRANSCRIPT)
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)


@pytest.fixture
def _no_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blind the config-file read and clear the TUBELESS_* env vars, so a real
    ~/.tubeless/config.env or a stray export cannot set a default during tests."""
    monkeypatch.setattr(config, "read_config", lambda *a, **k: {})
    for name in ("TUBELESS_BACKEND", "TUBELESS_MODEL", "TUBELESS_DETAIL",
                 "TUBELESS_MAX_POINTS", "TUBELESS_LANG", "TUBELESS_PER_CHANNEL"):
        monkeypatch.delenv(name, raising=False)


def test_default_backend_is_openai_without_configuration(_no_config_file) -> None:
    assert _default_backend() == "openai"


def test_default_backend_reads_tubeless_backend(_no_config_file, monkeypatch) -> None:
    monkeypatch.setenv("TUBELESS_BACKEND", "gemini")
    assert _default_backend() == "gemini"


def test_default_backend_rejects_an_unknown_vendor(_no_config_file, monkeypatch) -> None:
    monkeypatch.setenv("TUBELESS_BACKEND", "gpt")
    with pytest.raises(ConfigError):
        _default_backend()


def test_tubeless_backend_env_routes_a_bare_run_to_that_vendor(
    _no_config_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TUBELESS_BACKEND", "gemini")
    monkeypatch.setattr(cli_module, "fetch_video", lambda url: SAMPLE_VIDEO)
    monkeypatch.setattr(cli_module, "fetch_transcript", lambda video: SAMPLE_TRANSCRIPT)
    built = {}

    # The fake mirrors GeminiBackend's real default model, so a bare run (model
    # unset -> make_backend calls the class with no model) proves both that
    # TUBELESS_BACKEND routed to gemini and that the class default applies.
    class _RecordingGemini(CannedBackend):
        def __init__(self, *, model: str = "gemini-flash-lite-latest") -> None:
            built["model"] = model
            super().__init__(model=model)

    monkeypatch.setattr(llm_module, "GeminiBackend", _RecordingGemini)

    exit_code = main([SAMPLE_VIDEO.url])   # no --backend flag

    assert exit_code == 0
    assert built["model"] == "gemini-flash-lite-latest"  # routed to gemini, default applied


def test_configured_choice_falls_back_and_validates(_no_config_file, monkeypatch) -> None:
    assert _configured_choice("TUBELESS_DETAIL", DETAIL_LEVELS, "normal") == "normal"
    monkeypatch.setenv("TUBELESS_DETAIL", "deep")
    assert _configured_choice("TUBELESS_DETAIL", DETAIL_LEVELS, "normal") == "deep"
    monkeypatch.setenv("TUBELESS_DETAIL", "huge")
    with pytest.raises(ConfigError):
        _configured_choice("TUBELESS_DETAIL", DETAIL_LEVELS, "normal")


def test_configured_positive_int_reads_and_validates(_no_config_file, monkeypatch) -> None:
    assert _configured_positive_int("TUBELESS_MAX_POINTS", None) is None
    monkeypatch.setenv("TUBELESS_MAX_POINTS", "20")
    assert _configured_positive_int("TUBELESS_MAX_POINTS", None) == 20
    monkeypatch.setenv("TUBELESS_MAX_POINTS", "0")
    with pytest.raises(ConfigError):
        _configured_positive_int("TUBELESS_MAX_POINTS", 5)
    monkeypatch.setenv("TUBELESS_MAX_POINTS", "lots")
    with pytest.raises(ConfigError):
        _configured_positive_int("TUBELESS_MAX_POINTS", 5)


def test_tubeless_detail_env_sets_the_default_detail(
    _no_config_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TUBELESS_DETAIL", "deep")
    monkeypatch.setattr(cli_module, "fetch_video", lambda url: SAMPLE_VIDEO)
    monkeypatch.setattr(cli_module, "fetch_transcript", lambda video: SAMPLE_TRANSCRIPT)
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    seen: dict[str, object] = {}
    real_summarize = cli_module.summarize_transcript

    def spy(transcript, backend, **kwargs):
        seen.update(kwargs)
        return real_summarize(transcript, backend, **kwargs)

    monkeypatch.setattr(cli_module, "summarize_transcript", spy)

    assert main([SAMPLE_VIDEO.url]) == 0   # no --detail flag
    assert seen["detail"] == "deep"


def test_tubeless_max_points_env_caps_points(
    _no_config_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TUBELESS_MAX_POINTS", "3")
    monkeypatch.setattr(cli_module, "fetch_video", lambda url: SAMPLE_VIDEO)
    monkeypatch.setattr(cli_module, "fetch_transcript", lambda video: SAMPLE_TRANSCRIPT)
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    seen: dict[str, object] = {}
    real_summarize = cli_module.summarize_transcript

    def spy(transcript, backend, **kwargs):
        seen.update(kwargs)
        return real_summarize(transcript, backend, **kwargs)

    monkeypatch.setattr(cli_module, "summarize_transcript", spy)

    assert main([SAMPLE_VIDEO.url]) == 0   # no --max-points flag
    assert seen["max_points"] == 3


def test_main_handles_keyboard_interrupt_cleanly(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Ctrl-C mid-run must exit 130 with a one-line message, not a traceback.
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(cli_module, "fetch_video", lambda url: SAMPLE_VIDEO)

    def interrupted(video):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "fetch_transcript", interrupted)

    exit_code = main([SAMPLE_VIDEO.url])

    assert exit_code == 130
    assert "cancelled" in capsys.readouterr().err


def test_main_prints_the_summary_and_returns_zero(
    pipeline_with_fakes: None, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([SAMPLE_VIDEO.url])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "A talk about ducks" in captured.out
    assert "TL;DR: Ducks are great." in captured.out
    assert "- They float." in captured.out
    # The run's settings header goes to stderr, so stdout stays the summary alone;
    # it names the backend and the model actually used (the name-mangling hint).
    assert "backend=openai" in captured.err
    assert "model=unused"   in captured.err
    assert "tubeless:"  not in captured.out   # header must not leak into stdout


def test_main_with_json_flag_prints_machine_readable_output(
    pipeline_with_fakes: None, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([SAMPLE_VIDEO.url, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["tldr"] == "Ducks are great."
    assert payload["points"] == ["They float."]
    assert payload["video"]["video_id"] == SAMPLE_VIDEO.video_id


def test_main_reports_an_invalid_url_cleanly_without_a_traceback(
    _no_config_file, capsys: pytest.CaptureFixture[str],
) -> None:
    # No fakes needed: extract_video_id rejects the junk before any network call.
    exit_code = main(["definitely-not-a-video"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.startswith("tubeless:")
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_main_reports_a_missing_transcript_cleanly(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(cli_module, "fetch_video", lambda url: SAMPLE_VIDEO)

    def raise_unavailable(video):
        raise TranscriptUnavailable(f"no transcript for video {video.video_id!r}")

    monkeypatch.setattr(cli_module, "fetch_transcript", raise_unavailable)

    exit_code = main([SAMPLE_VIDEO.url])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "no transcript" in captured.err
    assert "Traceback" not in captured.err


def test_transcript_prints_the_raw_text(
    pipeline_with_fakes: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # The transcript subcommand is LLM-free: it prints the captions verbatim.
    exit_code = main(["transcript", SAMPLE_VIDEO.url])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "ducks are great" in captured.out


def test_transcript_json_prints_the_structure(
    pipeline_with_fakes: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # --json dumps the full transcript structure (video + segments), not the text.
    exit_code = main(["transcript", SAMPLE_VIDEO.url, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["video"]["video_id"] == SAMPLE_VIDEO.video_id
    assert payload["language"] == "en"
    assert payload["segments"][0]["text"] == "ducks are great"


def test_bare_url_still_routes_to_summarize(
    pipeline_with_fakes: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # No 'summarize' token: the backward-compat shim must insert it.
    exit_code = main([SAMPLE_VIDEO.url])

    assert exit_code == 0
    assert "TL;DR: Ducks are great." in capsys.readouterr().out


def test_explicit_summarize_subcommand_works(
    pipeline_with_fakes: None, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["summarize", SAMPLE_VIDEO.url])

    assert exit_code == 0
    assert "A talk about ducks" in capsys.readouterr().out


def test_bare_video_id_starting_with_a_dash_is_accepted(
    pipeline_with_fakes: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # A YouTube id can start with '-' (base64url); argparse would read it as an
    # option, so it must be rewritten to a URL and still summarize -- both bare
    # and after an explicit `summarize`.
    assert main(["-bcdefghij0"]) == 0
    assert main(["summarize", "-bcdefghij0"]) == 0


def test_with_default_subcommand_rewrites_a_leading_dash_id_to_a_url() -> None:
    from tubeless.cli import _with_default_subcommand

    assert _with_default_subcommand(["-bcdefghij0"]) == [
        "summarize", "https://www.youtube.com/watch?v=-bcdefghij0",
    ]
    assert _with_default_subcommand(["summarize", "-bcdefghij0", "--json"]) == [
        "summarize", "https://www.youtube.com/watch?v=-bcdefghij0", "--json",
    ]
    # a real mistyped flag is NOT rewritten (11-char id pattern does not match)
    assert _with_default_subcommand(["--jsonn"]) == ["summarize", "--jsonn"]


def test_digest_dry_run_prints_markdown_without_writing(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tubeless.digest import Digest, Entry
    from tubeless.importance import Importance
    from tubeless.summary import Summary

    entry = Entry(
        summary    = Summary(video=SAMPLE_VIDEO, tldr="gist", points=("a",), language="ko", detail="normal"),
        importance = Importance(score=0.9, reason="big news"),
    )
    monkeypatch.setattr(cli_module, "load_channels", lambda path: ())
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(
        cli_module, "curate_summaries",
        lambda summaries, backend, **kw: Digest(created="2026-07-21", entries=(entry,)),
    )

    exit_code = main(["digest", "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "YouTube digest — 2026-07-21" in captured.out
    # header uses the summary's own video channel and title, tier from the score
    assert "🔴 Duck Channel — A talk about ducks" in captured.out


def test_digest_writes_summaries_and_transcripts_to_the_store(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from tubeless.channels import Channel
    from tubeless.store import FileStore

    monkeypatch.setattr(cli_module, "load_channels",
                        lambda path: (Channel(source="@x"),))
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(cli_module, "fetch_recent_videos",
                        lambda source, *, limit, includes=(), excludes=(): (SAMPLE_VIDEO,))
    monkeypatch.setattr(digest_module, "fetch_transcript", lambda video: SAMPLE_TRANSCRIPT)
    corpus_dir = tmp_path / "corpus"

    exit_code = main([
        "digest",
        "--out",    str(tmp_path / "digests"),
        "--state",  str(tmp_path / "state.json"),
        "--corpus", str(corpus_dir),
    ])

    assert exit_code == 0
    store     = FileStore(corpus_dir)
    summaries = store.load_summaries()
    assert [s.video.video_id for s in summaries] == ["dQw4w9WgXcQ"]
    assert summaries[0].tldr == "Ducks are great."
    archived = store.load_transcript("dQw4w9WgXcQ")
    assert archived is not None
    assert archived.text == "ducks are great"


def test_digest_source_match_filters_channels(_no_config_file, monkeypatch: pytest.MonkeyPatch) -> None:
    from tubeless.channels import Channel

    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(cli_module, "load_channels", lambda path: (
        Channel(source="@market-inside"),
        Channel(source="@closing-bell"),
    ))
    scanned: list[str] = []

    def record(source, *, limit, includes=(), excludes=()):
        scanned.append(source)
        return ()

    monkeypatch.setattr(cli_module, "fetch_recent_videos", record)

    assert main(["digest", "--source-match", "closing", "--dry-run"]) == 0
    assert scanned == ["@closing-bell"]


def test_digest_source_match_with_no_match_errors(_no_config_file, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    from tubeless.channels import Channel
    monkeypatch.setattr(cli_module, "load_channels",
                        lambda path: (Channel(source="@market-inside"),))

    exit_code = main(["digest", "--source-match", "nonexistent", "--dry-run"])

    assert exit_code == 1
    assert "tubeless:" in capsys.readouterr().err


def test_digest_since_until_recurates_stored_summaries_and_prints(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tubeless.digest import Digest

    class _EmptyStore:
        def __init__(self, root):
            pass

        def load_summaries(self, *, since=None, until=None, channel=None):
            return ()

    seen_kwargs: dict[str, object] = {}
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(cli_module, "FileStore", _EmptyStore)

    def capture(summaries, backend, **kwargs):
        seen_kwargs.update(kwargs)
        return Digest(created=kwargs["created"], start=kwargs.get("start"),
                      end=kwargs.get("end"), entries=())

    monkeypatch.setattr(cli_module, "curate_summaries", capture)

    exit_code = main(["digest", "--since", "2026-07-01", "--until", "2026-07-08", "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 0
    # the stored path was taken with the range passed through
    assert seen_kwargs["start"] == "2026-07-01"
    assert seen_kwargs["end"]   == "2026-07-08"
    assert "YouTube digest — 2026-07-01..2026-07-08" in captured.out


def test_videos_lists_the_sources_videos(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    videos = (
        Video(video_id="aaaaaaaaaaa", title="First", url="u1", channel="C", published="2026-07-20T09:00:00Z"),
        Video(video_id="bbbbbbbbbbb", title="Second", url="u2", channel="C", published=None),
    )
    monkeypatch.setattr(cli_module, "fetch_recent_videos", lambda source, *, limit: videos)

    exit_code = main(["videos", "@somechannel"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "aaaaaaaaaaa" in captured.out
    assert "First" in captured.out
    assert "bbbbbbbbbbb" in captured.out


# --- digest fresh-run orchestration (moved into the CLI handler) --------------

def _transcript_for(video: Video) -> Transcript:
    return Transcript(video=video, language="en", is_auto_generated=False,
                      segments=(TranscriptSegment(text="ducks", start=0.0, duration=1.0),))


def _wire_fresh_digest(monkeypatch, *, channels, by_source) -> None:
    """Wire the fresh-digest path: channels, per-source discovery, transcript, backend."""
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(cli_module, "load_channels", lambda path: channels)
    monkeypatch.setattr(cli_module, "fetch_recent_videos",
                        lambda source, *, limit, includes=(), excludes=(): by_source.get(source, ()))
    monkeypatch.setattr(digest_module, "fetch_transcript", _transcript_for)


def test_digest_fresh_scans_filtered_channel_full_window_and_plain_with_limit(
    _no_config_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tubeless.channels import Channel
    from tubeless.discover import DEFAULT_SCAN
    seen_limits: dict[str, int] = {}

    def record(source, *, limit, includes=(), excludes=()):
        seen_limits[source] = limit
        return ()

    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(cli_module, "load_channels", lambda path: (
        Channel(source="@filtered", includes=("live",)),
        Channel(source="@plain"),
    ))
    monkeypatch.setattr(cli_module, "fetch_recent_videos", record)

    assert main(["digest", "--per-channel", "5", "--dry-run"]) == 0
    # a filtered channel must scan the full window (matches are sparse); a plain
    # one keeps the small per-channel cap
    assert seen_limits["@filtered"] == DEFAULT_SCAN
    assert seen_limits["@plain"] == 5


def test_digest_fresh_skips_a_failed_feed_and_still_digests_the_rest(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tubeless.channels import Channel

    def discover(source, *, limit, includes=(), excludes=()):
        if source == "@dead":
            raise FeedError("feed down")
        return (SAMPLE_VIDEO,)

    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(cli_module, "load_channels",
                        lambda path: (Channel(source="@dead"), Channel(source="@live")))
    monkeypatch.setattr(cli_module, "fetch_recent_videos", discover)
    monkeypatch.setattr(digest_module, "fetch_transcript", _transcript_for)

    exit_code = main(["digest", "--out", str(tmp_path / "d"), "--state", str(tmp_path / "s.json"),
                      "--corpus", str(tmp_path / "c")])

    assert exit_code == 0
    # the live channel still produced; the dead feed is surfaced as one skip
    assert "1 videos, 1 skipped" in capsys.readouterr().out


def test_digest_fresh_summarizes_a_video_shared_by_two_channels_once(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from tubeless.channels import Channel
    from tubeless.store import FileStore
    corpus = tmp_path / "c"
    _wire_fresh_digest(monkeypatch,
                       channels=(Channel(source="@a"), Channel(source="@b")),
                       by_source={"@a": (SAMPLE_VIDEO,), "@b": (SAMPLE_VIDEO,)})

    assert main(["digest", "--out", str(tmp_path / "d"), "--state", str(tmp_path / "s.json"),
                 "--corpus", str(corpus)]) == 0
    # shared across two channels, summarized once
    assert [s.video.video_id for s in FileStore(corpus).load_summaries()] == ["dQw4w9WgXcQ"]


def test_digest_fresh_skips_a_video_already_in_the_seen_state(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tubeless.channels import Channel
    from tubeless.state import write_seen
    from tubeless.store import FileStore
    state  = tmp_path / "s.json"
    corpus = tmp_path / "c"
    write_seen({SAMPLE_VIDEO.video_id}, state)
    _wire_fresh_digest(monkeypatch, channels=(Channel(source="@a"),),
                       by_source={"@a": (SAMPLE_VIDEO,)})

    assert main(["digest", "--out", str(tmp_path / "d"), "--state", str(state),
                 "--corpus", str(corpus)]) == 0
    # already seen -> not re-summarized
    assert FileStore(corpus).load_summaries() == ()
    assert "0 videos" in capsys.readouterr().out


def test_digest_fresh_persists_processed_ids_and_writes_the_md(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import datetime

    from tubeless.channels import Channel
    from tubeless.state import read_seen
    state   = tmp_path / "s.json"
    out_dir = tmp_path / "d"
    _wire_fresh_digest(monkeypatch, channels=(Channel(source="@a"),),
                       by_source={"@a": (SAMPLE_VIDEO,)})

    assert main(["digest", "--out", str(out_dir), "--state", str(state),
                 "--corpus", str(tmp_path / "c")]) == 0
    # the processed id is persisted so tomorrow's run does not re-summarize it
    assert SAMPLE_VIDEO.video_id in read_seen(state)
    # and the dated markdown file is written with the video
    md = (out_dir / f"{datetime.date.today().isoformat()}.md").read_text(encoding="utf-8")
    assert "A talk about ducks" in md


def test_digest_fresh_aborts_without_writing_state_when_a_fetch_is_blocked(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from tubeless.channels import Channel
    state = tmp_path / "s.json"
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(cli_module, "load_channels", lambda path: (Channel(source="@a"),))
    monkeypatch.setattr(cli_module, "fetch_recent_videos",
                        lambda source, *, limit, includes=(), excludes=(): (SAMPLE_VIDEO,))

    def blocked(video):
        raise TranscriptFetchBlocked("ip blocked")

    monkeypatch.setattr(digest_module, "fetch_transcript", blocked)

    exit_code = main(["digest", "--out", str(tmp_path / "d"), "--state", str(state),
                      "--corpus", str(tmp_path / "c")])

    assert exit_code == 1
    # a transient block must not mark the video seen -- it must be retried tomorrow
    assert not state.exists()


# --- digest --since/--until (stored re-curate) --------------------------------

def test_digest_channel_without_a_date_range_is_rejected(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # --channel narrows a --since/--until re-curate; without a date range it must
    # error, not run a fresh discovery (which would ignore it) nor a stored
    # re-curate labelled as today (which would overwrite the fresh daily digest).
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)

    exit_code = main(["digest", "--channel", "Some Channel", "--dry-run"])

    assert exit_code == 1
    assert "tubeless:" in capsys.readouterr().err


def test_digest_since_with_channel_narrows_the_stored_recurate(
    _no_config_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --channel is a modifier on a --since/--until re-curate: it passes through to
    # the store's channel filter.
    from tubeless.digest import Digest
    seen: dict[str, object] = {}

    class _RecordingStore:
        def __init__(self, root):
            pass

        def load_summaries(self, *, since=None, until=None, channel=None):
            seen["channel"] = channel
            return ()

    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(cli_module, "FileStore", _RecordingStore)
    monkeypatch.setattr(cli_module, "curate_summaries",
                        lambda summaries, backend, **kw: Digest(
                            created=kw["created"], start=kw.get("start"),
                            end=kw.get("end"), entries=()))

    assert main(["digest", "--since", "2026-07-01", "--channel", "Some Channel", "--dry-run"]) == 0
    assert seen["channel"] == "Some Channel"


def test_digest_source_match_combined_with_a_stored_recurate_is_rejected(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # --source-match is a fresh-run flag; mixed with --since/--until it must error,
    # not be silently dropped.
    class _EmptyStore:
        def __init__(self, root):
            pass

        def load_summaries(self, *, since=None, until=None, channel=None):
            return ()

    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(cli_module, "FileStore", _EmptyStore)

    exit_code = main(["digest", "--since", "2026-07-01", "--source-match", "foo", "--dry-run"])

    assert exit_code == 1
    assert "tubeless:" in capsys.readouterr().err


def test_digest_rejects_a_malformed_since_date(_no_config_file) -> None:
    # A malformed bound must be a clean usage error, not a silent empty digest via
    # the lexicographic string compare.
    with pytest.raises(SystemExit) as exc:
        main(["digest", "--since", "2026-7-1", "--dry-run"])
    assert exc.value.code == 2   # argparse usage error


def test_digest_since_until_dedups_stored_variants_before_curating(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Two stored variants of one video (different detail) must reach curate as one
    # (the most recent), exercising load_summaries -> latest_per_video -> curate.
    from tubeless.store import FileStore
    from tubeless.summary import Summary
    corpus = tmp_path / "c"
    store  = FileStore(corpus)
    video  = Video(video_id="dQw4w9WgXcQ", title="ducks", url="u", channel="C",
                   published="2026-07-05T09:00:00Z")
    store.save_summary(Summary(video=video, tldr="old", points=("a",), language="en", detail="brief"))
    store.save_summary(Summary(video=video, tldr="new", points=("a",), language="en", detail="deep"))
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)

    exit_code = main(["digest", "--since", "2026-07-01", "--until", "2026-07-08",
                      "--corpus", str(corpus), "--out", str(tmp_path / "d")])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "(1 videos)" in captured.out   # two stored variants deduped to one
