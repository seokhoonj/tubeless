import json

import pytest

import tubeless.cli as cli_module
from tubeless import Transcript, TranscriptSegment, TranscriptUnavailable, Video, config
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
    video_id          = SAMPLE_VIDEO.video_id,
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
    monkeypatch.setattr(cli_module, "fetch_video_meta", lambda url: SAMPLE_VIDEO)
    monkeypatch.setattr(cli_module, "fetch_transcript", lambda video_id: SAMPLE_TRANSCRIPT)
    monkeypatch.setattr(cli_module, "OpenAIBackend", CannedBackend)


@pytest.fixture
def _no_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blind the config-file read and clear the TUBELESS_* env vars, so a real
    ~/.tubeless/config.env or a stray export cannot set a default during tests."""
    monkeypatch.setattr(config, "read_config", lambda *a, **k: {})
    for name in ("TUBELESS_BACKEND", "TUBELESS_MODEL", "TUBELESS_DETAIL",
                 "TUBELESS_MAX_POINTS", "TUBELESS_LANG", "TUBELESS_LIMIT"):
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
    monkeypatch.setattr(cli_module, "fetch_video_meta", lambda url: SAMPLE_VIDEO)
    monkeypatch.setattr(cli_module, "fetch_transcript", lambda video_id: SAMPLE_TRANSCRIPT)
    built = {}

    # The fake mirrors GeminiBackend's real default model, so a bare run (model
    # unset -> _make_backend calls the class with no model) proves both that
    # TUBELESS_BACKEND routed to gemini and that the class default applies.
    class _RecordingGemini(CannedBackend):
        def __init__(self, *, model: str = "gemini-flash-lite-latest") -> None:
            built["model"] = model
            super().__init__(model=model)

    monkeypatch.setattr(cli_module, "GeminiBackend", _RecordingGemini)

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
    monkeypatch.setattr(cli_module, "fetch_video_meta", lambda url: SAMPLE_VIDEO)
    monkeypatch.setattr(cli_module, "fetch_transcript", lambda video_id: SAMPLE_TRANSCRIPT)
    monkeypatch.setattr(cli_module, "OpenAIBackend", CannedBackend)
    seen: dict[str, object] = {}
    real_summarize = cli_module.summarize

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_summarize(*args, **kwargs)

    monkeypatch.setattr(cli_module, "summarize", spy)

    assert main([SAMPLE_VIDEO.url]) == 0   # no --detail flag
    assert seen["detail"] == "deep"


def test_main_handles_keyboard_interrupt_cleanly(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Ctrl-C mid-run must exit 130 with a one-line message, not a traceback.
    monkeypatch.setattr(cli_module, "fetch_video_meta", lambda url: SAMPLE_VIDEO)

    def interrupted(video_id):
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
    assert "TLDR: Ducks are great." in captured.out
    assert "- They float." in captured.out
    # The run's settings header goes to stderr, so stdout stays the summary alone;
    # it names the backend and the model actually used (the name-mangling hint).
    assert "backend=openai" in captured.err
    assert "model=unused"   in captured.err


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
    # No fakes needed: parse_video_id rejects the junk before any network call.
    exit_code = main(["definitely-not-a-video"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.startswith("tubeless:")
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_main_reports_a_missing_transcript_cleanly(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_module, "fetch_video_meta", lambda url: SAMPLE_VIDEO)

    def raise_unavailable(video_id: str) -> Transcript:
        raise TranscriptUnavailable(f"no transcript for video {video_id!r}")

    monkeypatch.setattr(cli_module, "fetch_transcript", raise_unavailable)

    exit_code = main([SAMPLE_VIDEO.url])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "no transcript" in captured.err
    assert "Traceback" not in captured.err


def test_bare_url_still_routes_to_summarize(
    pipeline_with_fakes: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # No 'summarize' token: the backward-compat shim must insert it.
    exit_code = main([SAMPLE_VIDEO.url])

    assert exit_code == 0
    assert "TLDR: Ducks are great." in capsys.readouterr().out


def test_explicit_summarize_subcommand_works(
    pipeline_with_fakes: None, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["summarize", SAMPLE_VIDEO.url])

    assert exit_code == 0
    assert "A talk about ducks" in capsys.readouterr().out


def test_digest_dry_run_prints_markdown_without_writing(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tubeless.digest import Digest, DigestEntry
    from tubeless.feed import Upload
    from tubeless.importance import Importance
    from tubeless.summary import Summary

    entry = DigestEntry(
        channel="예시 채널",
        upload=Upload(video_id="dQw4w9WgXcQ", title="예시 영상", published="",
                      channel_id="UC", channel_title="예시 채널"),
        summary=Summary(video=SAMPLE_VIDEO, tldr="핵심", points=("a",), language="ko"),
        importance=Importance(score=0.9, reason="큰 뉴스"),
    )
    monkeypatch.setattr(cli_module, "load_channels", lambda path: ())
    monkeypatch.setattr(cli_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(
        cli_module, "build_digest",
        lambda channels, backend, **kw: (
            Digest(date="2026-07-21", entries=(entry,), skipped=()), {"dQw4w9WgXcQ"}
        ),
    )

    exit_code = main(["digest", "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "유튜브 다이제스트 — 2026-07-21" in captured.out
    # header uses the summary's video title (SAMPLE_VIDEO), tier from the score
    assert "🔴 예시 채널 — A talk about ducks" in captured.out


def test_digest_only_filters_channels_by_label(_no_config_file, monkeypatch: pytest.MonkeyPatch) -> None:
    from tubeless.channels import Channel
    from tubeless.digest import Digest

    monkeypatch.setattr(cli_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(cli_module, "load_channels", lambda path: (
        Channel(source="@a", label="Market Inside"),
        Channel(source="@b", label="Closing Bell"),
    ))
    seen_channels = {}

    def capture(channels, backend, **kw):
        seen_channels["labels"] = [c.label for c in channels]
        return Digest(date="2026-07-21", entries=(), skipped=()), set()

    monkeypatch.setattr(cli_module, "build_digest", capture)

    assert main(["digest", "--only", "closing", "--dry-run"]) == 0
    assert seen_channels["labels"] == ["Closing Bell"]


def test_digest_only_with_no_match_errors(_no_config_file, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    from tubeless.channels import Channel
    monkeypatch.setattr(cli_module, "load_channels",
                        lambda path: (Channel(source="@a", label="Market Inside"),))

    exit_code = main(["digest", "--only", "nonexistent", "--dry-run"])

    assert exit_code == 1
    assert "tubeless:" in capsys.readouterr().err
