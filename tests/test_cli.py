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
