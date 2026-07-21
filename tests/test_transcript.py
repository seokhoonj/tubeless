import pytest

import tubeless.transcript as transcript_module
from tubeless import Transcript, TranscriptSegment, TranscriptUnavailable, fetch_transcript


def test_transcript_text_joins_segments_with_spaces() -> None:
    transcript = Transcript(
        video_id          = "dQw4w9WgXcQ",
        language          = "en",
        is_auto_generated = False,
        segments          = (
            TranscriptSegment(text="never gonna", start=0.0, duration=1.5),
            TranscriptSegment(text="give you up", start=1.5, duration=1.5),
        ),
    )
    assert transcript.text == "never gonna give you up"


def test_transcript_text_is_empty_for_no_segments() -> None:
    transcript = Transcript(
        video_id="dQw4w9WgXcQ", language="en", is_auto_generated=False, segments=()
    )
    assert transcript.text == ""


class _FakeSnippet:
    def __init__(self, text: str, start: float, duration: float) -> None:
        self.text     = text
        self.start    = start
        self.duration = duration


class _FakeListedTranscript:
    language_code = "ko"
    is_generated  = True

    def fetch(self) -> list[_FakeSnippet]:
        return [
            _FakeSnippet("첫 문장", 0.0, 2.0),
            _FakeSnippet("둘째 문장", 2.0, 2.5),
        ]


class _FakeTranscriptList:
    def find_transcript(self, languages: list[str]) -> _FakeListedTranscript:
        assert languages == ["ko", "en"]
        return _FakeListedTranscript()


class _FakeTranscriptAPI:
    def list(self, video_id: str) -> _FakeTranscriptList:
        return _FakeTranscriptList()


def test_fetch_transcript_maps_vendor_snippets_to_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transcript_module, "YouTubeTranscriptApi", _FakeTranscriptAPI)

    fetched = fetch_transcript("dQw4w9WgXcQ")

    assert fetched.video_id == "dQw4w9WgXcQ"
    assert fetched.language == "ko"
    assert fetched.is_auto_generated is True
    assert fetched.segments == (
        TranscriptSegment(text="첫 문장", start=0.0, duration=2.0),
        TranscriptSegment(text="둘째 문장", start=2.0, duration=2.5),
    )


class _VendorFetchFailure(Exception):
    """Stands in for the vendor's CouldNotRetrieveTranscript, whose real
    constructor wants live objects we do not build in tests."""


class _FailingTranscriptAPI:
    def list(self, video_id: str) -> None:
        raise _VendorFetchFailure("captions are disabled on this video")


def test_fetch_transcript_reraises_vendor_failure_as_transcript_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transcript_module, "CouldNotRetrieveTranscript", _VendorFetchFailure)
    monkeypatch.setattr(transcript_module, "YouTubeTranscriptApi", _FailingTranscriptAPI)

    with pytest.raises(TranscriptUnavailable) as raised:
        fetch_transcript("dQw4w9WgXcQ")

    assert isinstance(raised.value.__cause__, _VendorFetchFailure)
    assert "dQw4w9WgXcQ" in str(raised.value)
