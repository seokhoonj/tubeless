import json

import pytest

import tubeless.cli as cli_module
from tubeless import Transcript, TranscriptSegment, TranscriptUnavailable, Video
from tubeless.cli import main

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
def pipeline_with_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "fetch_video_meta", lambda url: SAMPLE_VIDEO)
    monkeypatch.setattr(cli_module, "fetch_transcript", lambda video_id: SAMPLE_TRANSCRIPT)
    monkeypatch.setattr(cli_module, "OpenAIBackend", CannedBackend)


def test_main_prints_the_summary_and_returns_zero(
    pipeline_with_fakes: None, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([SAMPLE_VIDEO.url])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "A talk about ducks" in captured.out
    assert "TLDR: Ducks are great." in captured.out
    assert "- They float." in captured.out
    assert captured.err == ""


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
    capsys: pytest.CaptureFixture[str],
) -> None:
    # No fakes needed: parse_video_id rejects the junk before any network call.
    exit_code = main(["definitely-not-a-video"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.startswith("tubeless:")
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_main_reports_a_missing_transcript_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
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
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
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


def test_digest_only_filters_channels_by_label(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_digest_only_with_no_match_errors(monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    from tubeless.channels import Channel
    monkeypatch.setattr(cli_module, "load_channels",
                        lambda path: (Channel(source="@a", label="Market Inside"),))

    exit_code = main(["digest", "--only", "nonexistent", "--dry-run"])

    assert exit_code == 1
    assert "tubeless:" in capsys.readouterr().err
