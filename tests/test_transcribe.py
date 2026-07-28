"""Whisper fallback: audio download + local transcription, every vendor faked.

The real yt-dlp / faster-whisper are heavy and hit the network, so these tests
stub both -- either at the module's own seams (_download_audio / _transcribe_file
/ _load_model) or by injecting a fake `yt_dlp` into sys.modules.
"""

import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

import tubeless.transcribe as transcribe_module
from tubeless import TranscriptSegment, TranscriptUnavailable, Video
from tubeless.transcribe import transcribe_audio

VIDEO = Video(
    video_id = "dQw4w9WgXcQ",
    title    = "A talk",
    url      = "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    channel  = None,
)


# --- transcribe_audio: compose download + transcription --------------------

def test_transcribe_audio_builds_a_machine_generated_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segments = (TranscriptSegment("spoken words", 0.0, 1.2),)
    monkeypatch.setattr(transcribe_module, "_download_audio",
                        lambda video, workdir: workdir / "audio.m4a")
    monkeypatch.setattr(transcribe_module, "_transcribe_file",
                        lambda path, model_name: ("en", segments))

    transcript = transcribe_audio(VIDEO, model_name="small")

    assert transcript.video is VIDEO
    assert transcript.language == "en"
    assert transcript.is_auto_generated is True
    assert transcript.segments == segments


def test_transcribe_audio_raises_when_whisper_finds_no_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty transcription is a miss, not a valid empty transcript to summarize.
    monkeypatch.setattr(transcribe_module, "_download_audio",
                        lambda video, workdir: workdir / "audio.m4a")
    monkeypatch.setattr(transcribe_module, "_transcribe_file",
                        lambda path, model_name: ("en", ()))

    with pytest.raises(TranscriptUnavailable, match="no speech"):
        transcribe_audio(VIDEO, model_name="small")


def test_transcribe_audio_materialises_segments_before_tempdir_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The lazy whisper generator MUST be drained while the downloaded audio still
    # exists -- inside transcribe_audio's TemporaryDirectory. The fake generator
    # asserts the audio path is present as it yields, so a regression that
    # materialised the segments after the temp dir was gone would fail here.
    def fake_download(video: Video, workdir: Path) -> Path:
        audio_path = workdir / "audio.m4a"
        audio_path.write_bytes(b"audio")
        return audio_path

    class _FileWatchingModel:
        def transcribe(self, audio_path: str) -> tuple[object, _FakeWhisperInfo]:
            def drain_segments():
                assert Path(audio_path).exists()   # the temp dir must still be alive
                yield _FakeWhisperSegment(" spoken words ", 0.0, 1.0)
            return drain_segments(), _FakeWhisperInfo("en")

    monkeypatch.setattr(transcribe_module, "_download_audio", fake_download)
    monkeypatch.setattr(transcribe_module, "_load_model", lambda name: _FileWatchingModel())

    transcript = transcribe_audio(VIDEO, model_name="small")

    assert transcript.segments == (TranscriptSegment("spoken words", 0.0, 1.0),)
    assert transcript.language == "en"


# --- _transcribe_file: map whisper output to TranscriptSegments ------------

@dataclass(frozen=True, slots=True)
class _FakeWhisperSegment:
    text:  str
    start: float
    end:   float


@dataclass(frozen=True, slots=True)
class _FakeWhisperInfo:
    language: str | None


class _FakeWhisperModel:
    def __init__(self, segments: list[_FakeWhisperSegment], language: str | None) -> None:
        self._segments = segments
        self._language = language

    def transcribe(self, audio_path: str) -> tuple[object, _FakeWhisperInfo]:
        # faster-whisper yields segments lazily -- return a generator to prove
        # _transcribe_file materialises it before the temp dir is gone.
        return (iter(self._segments), _FakeWhisperInfo(self._language))


def test_transcribe_file_maps_whisper_segments_to_transcript_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # duration is end - start (whisper reports an end time), and leading/trailing
    # whitespace whisper emits is trimmed.
    model = _FakeWhisperModel(
        [_FakeWhisperSegment(" hello ", 0.0, 1.5),
         _FakeWhisperSegment("world", 1.5, 3.0)],
        language="en",
    )
    monkeypatch.setattr(transcribe_module, "_load_model", lambda name: model)

    language, segments = transcribe_module._transcribe_file(Path("audio.m4a"), "small")

    assert language == "en"
    assert segments == (
        TranscriptSegment(text="hello", start=0.0, duration=1.5),
        TranscriptSegment(text="world", start=1.5, duration=1.5),
    )


def test_transcribe_file_defaults_language_when_whisper_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _FakeWhisperModel([_FakeWhisperSegment("x", 0.0, 1.0)], language=None)
    monkeypatch.setattr(transcribe_module, "_load_model", lambda name: model)

    language, _segments = transcribe_module._transcribe_file(Path("a.m4a"), "small")

    assert language == "und"   # Transcript.language is str, so None must not leak


def test_transcribe_file_clamps_a_negative_duration_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # whisper can report end <= start; the resulting duration must never go negative
    # (the caption path never yields one), so it is clamped to 0.0.
    model = _FakeWhisperModel([_FakeWhisperSegment("word", 2.0, 1.5)], language="en")
    monkeypatch.setattr(transcribe_module, "_load_model", lambda name: model)

    _language, segments = transcribe_module._transcribe_file(Path("a.m4a"), "small")

    assert segments == (TranscriptSegment(text="word", start=2.0, duration=0.0),)


class _FailingWhisperModel:
    def transcribe(self, audio_path: str) -> tuple[object, object]:
        raise RuntimeError("could not decode the audio stream")


def test_transcribe_file_maps_a_whisper_runtime_failure_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A decode failure inside whisper must become a per-video miss, not escape and
    # crash the digest run (which only handles TranscriptUnavailable).
    monkeypatch.setattr(transcribe_module, "_load_model", lambda name: _FailingWhisperModel())

    with pytest.raises(TranscriptUnavailable, match="failed to transcribe"):
        transcribe_module._transcribe_file(Path("a.m4a"), "small")


def test_transcribe_file_without_faster_whisper_names_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_import(name: str) -> object:
        raise ImportError("faster_whisper is not installed")

    monkeypatch.setattr(transcribe_module, "_load_model", raise_import)

    with pytest.raises(TranscriptUnavailable, match=r"tubeless\[whisper\]"):
        transcribe_module._transcribe_file(Path("a.m4a"), "small")


def test_transcribe_file_maps_a_model_load_failure_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Constructing a WhisperModel validates the name and downloads its weights, so
    # a bad TUBELESS_WHISPER_MODEL or an offline first-run raises a NON-ImportError.
    # It must map to a per-video miss, not escape and crash the digest run.
    def raise_bad_model(name: str) -> object:
        raise ValueError(f"invalid model size {name!r}")

    monkeypatch.setattr(transcribe_module, "_load_model", raise_bad_model)

    with pytest.raises(TranscriptUnavailable, match="could not load the whisper model"):
        transcribe_module._transcribe_file(Path("a.m4a"), "not-a-real-model")


# --- _download_audio: yt-dlp behind a fake module --------------------------

def _make_fake_yt_dlp_module(
    *, writes_file: bool = True, error: Exception | None = None,
) -> types.ModuleType:
    """A stand-in `yt_dlp` module whose YoutubeDL either writes an audio file into
    the outtmpl directory, writes nothing, or raises ``error`` on download."""
    module = types.ModuleType("yt_dlp")

    class _FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            self._options = options

        def __enter__(self) -> "_FakeYoutubeDL":
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def download(self, urls: list[str]) -> None:
            if error is not None:
                raise error
            if writes_file:
                template = str(self._options["outtmpl"])
                Path(template.replace("%(ext)s", "m4a")).write_bytes(b"audio")

    module.YoutubeDL = _FakeYoutubeDL   # type: ignore[attr-defined]
    return module


def test_download_audio_returns_the_downloaded_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "yt_dlp", _make_fake_yt_dlp_module(writes_file=True))

    path = transcribe_module._download_audio(VIDEO, tmp_path)

    assert path == tmp_path / "audio.m4a"
    assert path.read_bytes() == b"audio"


def test_download_audio_skips_a_leftover_part_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A leftover .part must never be chosen over the finished audio file.
    module = types.ModuleType("yt_dlp")

    class _PartAndFinalYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            self._dir = Path(str(options["outtmpl"])).parent

        def __enter__(self) -> "_PartAndFinalYoutubeDL":
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def download(self, urls: list[str]) -> None:
            (self._dir / "audio.m4a.part").write_bytes(b"x")             # tiny leftover
            (self._dir / "audio.m4a").write_bytes(b"the real audio")     # the finished file

    module.YoutubeDL = _PartAndFinalYoutubeDL   # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yt_dlp", module)

    assert transcribe_module._download_audio(VIDEO, tmp_path) == tmp_path / "audio.m4a"


def test_download_audio_raises_when_no_file_is_produced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "yt_dlp", _make_fake_yt_dlp_module(writes_file=False))

    with pytest.raises(TranscriptUnavailable, match="produced no file"):
        transcribe_module._download_audio(VIDEO, tmp_path)


def test_download_audio_maps_a_yt_dlp_failure_to_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "yt_dlp",
                        _make_fake_yt_dlp_module(error=RuntimeError("network died mid-download")))

    with pytest.raises(TranscriptUnavailable, match="could not download audio"):
        transcribe_module._download_audio(VIDEO, tmp_path)


def test_download_audio_without_yt_dlp_names_the_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # None in sys.modules makes `import yt_dlp` raise ImportError, regardless of
    # whether yt-dlp happens to be installed on the box running the suite.
    monkeypatch.setitem(sys.modules, "yt_dlp", None)

    with pytest.raises(TranscriptUnavailable, match=r"tubeless\[whisper\]"):
        transcribe_module._download_audio(VIDEO, tmp_path)
