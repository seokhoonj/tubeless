import json

import pytest

import tubeless.cli as cli_module
import tubeless.llm as llm_module
from tubeless import Transcript, TranscriptSegment, TranscriptUnavailable, Video, config
from tubeless.cli import (
    _configured_choice,
    _configured_flag,
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
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)


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


def test_configured_flag_reads_a_boolean(_no_config_file, monkeypatch) -> None:
    assert _configured_flag("TUBELESS_SYNTHESIZE") is False
    monkeypatch.setenv("TUBELESS_SYNTHESIZE", "1")
    assert _configured_flag("TUBELESS_SYNTHESIZE") is True
    monkeypatch.setenv("TUBELESS_SYNTHESIZE", "no")
    assert _configured_flag("TUBELESS_SYNTHESIZE") is False


def test_digest_synthesize_flag_reaches_curate(
    _no_config_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tubeless.digest import Digest
    seen_kwargs: dict[str, object] = {}
    monkeypatch.setattr(cli_module, "load_channels", lambda path: ())
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)

    def fake_build(channels, backend, **kwargs):
        seen_kwargs.update(kwargs)
        return Digest(date="d", entries=(), skipped=()), set()

    monkeypatch.setattr(cli_module, "curate", fake_build)

    assert main(["digest", "--synthesize", "--dry-run"]) == 0
    assert seen_kwargs["with_synthesis"] is True


def test_tubeless_detail_env_sets_the_default_detail(
    _no_config_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TUBELESS_DETAIL", "deep")
    monkeypatch.setattr(cli_module, "fetch_video_meta", lambda url: SAMPLE_VIDEO)
    monkeypatch.setattr(cli_module, "fetch_transcript", lambda video_id: SAMPLE_TRANSCRIPT)
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    seen: dict[str, object] = {}
    real_summarize = cli_module.summarize

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_summarize(*args, **kwargs)

    monkeypatch.setattr(cli_module, "summarize", spy)

    assert main([SAMPLE_VIDEO.url]) == 0   # no --detail flag
    assert seen["detail"] == "deep"


def test_tubeless_max_points_env_caps_points(
    _no_config_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TUBELESS_MAX_POINTS", "3")
    monkeypatch.setattr(cli_module, "fetch_video_meta", lambda url: SAMPLE_VIDEO)
    monkeypatch.setattr(cli_module, "fetch_transcript", lambda video_id: SAMPLE_TRANSCRIPT)
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    seen: dict[str, object] = {}
    real_summarize = cli_module.summarize

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_summarize(*args, **kwargs)

    monkeypatch.setattr(cli_module, "summarize", spy)

    assert main([SAMPLE_VIDEO.url]) == 0   # no --max-points flag
    assert seen["max_points"] == 3


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
    assert "TL;DR: Ducks are great." in capsys.readouterr().out


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
    from tubeless.transcript import Transcript

    entry = DigestEntry(
        channel="Example Channel",
        upload=Upload(video_id="dQw4w9WgXcQ", title="Example Video", published="",
                      channel_id="UC", channel="Example Channel"),
        summary=Summary(video=SAMPLE_VIDEO, tldr="gist", points=("a",), language="ko"),
        importance=Importance(score=0.9, reason="big news"),
        transcript=Transcript(video_id="dQw4w9WgXcQ", language="ko",
                              is_auto_generated=False, segments=()),
    )
    monkeypatch.setattr(cli_module, "load_channels", lambda path: ())
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(
        cli_module, "curate",
        lambda channels, backend, **kw: (
            Digest(date="2026-07-21", entries=(entry,), skipped=()), {"dQw4w9WgXcQ"}
        ),
    )

    exit_code = main(["digest", "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "YouTube digest — 2026-07-21" in captured.out
    # header uses the summary's video title (SAMPLE_VIDEO), tier from the score
    assert "🔴 Example Channel — A talk about ducks" in captured.out


def test_digest_records_summaries_and_transcripts_to_the_corpus(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from tubeless.corpus import load_summaries, load_transcript
    from tubeless.digest import Digest, DigestEntry
    from tubeless.feed import Upload
    from tubeless.importance import Importance
    from tubeless.summary import Summary

    entry = DigestEntry(
        channel    = "Duck Channel",
        upload     = Upload(video_id="dQw4w9WgXcQ", title="A talk about ducks",
                            published="2026-07-21T09:00:00+00:00",
                            channel_id="UC", channel="Duck Channel"),
        summary    = Summary(video=SAMPLE_VIDEO, tldr="gist", points=("a", "b"), language="en"),
        importance = Importance(score=0.8, reason="quacks"),
        transcript = SAMPLE_TRANSCRIPT,
    )
    monkeypatch.setattr(cli_module, "load_channels", lambda path: ())
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(
        cli_module, "curate",
        lambda channels, backend, **kw: (
            Digest(date="2026-07-21", entries=(entry,), skipped=()), {"dQw4w9WgXcQ"}
        ),
    )
    corpus_dir = tmp_path / "corpus"

    exit_code = main([
        "digest",
        "--out",    str(tmp_path / "digests"),
        "--state",  str(tmp_path / "state.json"),
        "--corpus", str(corpus_dir),
    ])

    assert exit_code == 0
    records = load_summaries("Duck Channel", root=corpus_dir)
    assert len(records) == 1
    assert records[0].video.video_id == "dQw4w9WgXcQ"
    assert records[0].tldr           == "gist"
    assert records[0].importance.score == 0.8
    assert records[0].captured       == "2026-07-21"
    archived = load_transcript("dQw4w9WgXcQ", root=corpus_dir)
    assert archived is not None
    assert archived.text == "ducks are great"


def test_digest_corpus_failure_on_one_entry_does_not_abort_the_rest(
    _no_config_file, monkeypatch: pytest.MonkeyPatch, tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tubeless.corpus import load_summaries
    from tubeless.digest import Digest, DigestEntry
    from tubeless.digest import record_entry as real_record_entry
    from tubeless.errors import CorpusError
    from tubeless.feed import Upload
    from tubeless.importance import Importance
    from tubeless.summary import Summary

    def _entry(video_id: str) -> DigestEntry:
        video = Video(video_id=video_id, title=f"V {video_id}",
                      url=f"https://www.youtube.com/watch?v={video_id}", channel="Duck Channel")
        return DigestEntry(
            channel    = "Duck Channel",
            upload     = Upload(video_id=video_id, title=f"V {video_id}",
                                published="2026-07-21T09:00:00+00:00",
                                channel_id="UC", channel="Duck Channel"),
            summary    = Summary(video=video, tldr="gist", points=("a",), language="en"),
            importance = Importance(score=0.8, reason="quacks"),
            transcript = Transcript(video_id=video_id, language="en",
                                    is_auto_generated=False, segments=()),
        )

    bad, good = _entry("aaaaaaaaaaa"), _entry("bbbbbbbbbbb")
    monkeypatch.setattr(cli_module, "load_channels", lambda path: ())
    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(
        cli_module, "curate",
        lambda channels, backend, **kw: (
            Digest(date="2026-07-21", entries=(bad, good), skipped=()),
            {"aaaaaaaaaaa", "bbbbbbbbbbb"},
        ),
    )
    corpus_dir = tmp_path / "corpus"

    def flaky_record(entry, captured, *, root=None):
        if entry.upload.video_id == "aaaaaaaaaaa":
            raise CorpusError("disk full")
        real_record_entry(entry, captured, root=root)

    monkeypatch.setattr(cli_module, "record_entry", flaky_record)

    exit_code = main([
        "digest",
        "--out",    str(tmp_path / "digests"),
        "--state",  str(tmp_path / "state.json"),
        "--corpus", str(corpus_dir),
    ])

    captured = capsys.readouterr()
    assert exit_code == 0                       # one entry's failure is not fatal
    assert "aaaaaaaaaaa" in captured.err        # the failing entry is reported by id
    records = load_summaries("Duck Channel", root=corpus_dir)
    assert [record.video.video_id for record in records] == ["bbbbbbbbbbb"]  # the rest survived


def test_digest_only_filters_channels_by_label(_no_config_file, monkeypatch: pytest.MonkeyPatch) -> None:
    from tubeless.channels import Channel
    from tubeless.digest import Digest

    monkeypatch.setattr(llm_module, "OpenAIBackend", CannedBackend)
    monkeypatch.setattr(cli_module, "load_channels", lambda path: (
        Channel(source="@a", label="Market Inside"),
        Channel(source="@b", label="Closing Bell"),
    ))
    seen_channels = {}

    def capture(channels, backend, **kw):
        seen_channels["labels"] = [c.label for c in channels]
        return Digest(date="2026-07-21", entries=(), skipped=()), set()

    monkeypatch.setattr(cli_module, "curate", capture)

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
