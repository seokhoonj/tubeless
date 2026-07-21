import pytest

import tubeless.transcript as transcript_module
from tubeless import (
    Transcript,
    TranscriptFetchBlocked,
    TranscriptSegment,
    TranscriptUnavailable,
    fetch_transcript,
)


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
            _FakeSnippet("first sentence", 0.0, 2.0),
            _FakeSnippet("second sentence", 2.0, 2.5),
        ]


class _FakeTranscriptList:
    def find_transcript(self, languages: list[str]) -> _FakeListedTranscript:
        assert languages == list(transcript_module._PREFERRED_LANGUAGES)
        return _FakeListedTranscript()


class _FakeTranscriptAPI:
    last_http_client: object = None

    def __init__(self, *, http_client: object = None) -> None:
        # Record the client so a test can assert fetch_transcript wires in its
        # timeout-bearing session rather than letting the vendor default hang.
        type(self).last_http_client = http_client

    def list(self, video_id: str) -> _FakeTranscriptList:
        return _FakeTranscriptList()


def test_fetch_transcript_maps_vendor_snippets_to_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transcript_module, "YouTubeTranscriptApi", _FakeTranscriptAPI)

    fetched = fetch_transcript("dQw4w9WgXcQ")

    assert isinstance(_FakeTranscriptAPI.last_http_client, transcript_module._TimeoutSession)
    assert fetched.video_id == "dQw4w9WgXcQ"
    assert fetched.language == "ko"
    assert fetched.is_auto_generated is True
    assert fetched.segments == (
        TranscriptSegment(text="first sentence", start=0.0, duration=2.0),
        TranscriptSegment(text="second sentence", start=2.0, duration=2.5),
    )


def test_timeout_session_supplies_a_default_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # The vendor API exposes no timeout knob, so the session must default one in;
    # without it a wedged fetch would hang the digest's per-video loop.
    seen: dict[str, object] = {}

    def fake_request(self: object, *args: object, **kwargs: object) -> str:
        seen.update(kwargs)
        return "ok"

    monkeypatch.setattr(transcript_module.requests.Session, "request", fake_request)
    session = transcript_module._TimeoutSession()

    assert session.request("GET", "https://example.test") == "ok"
    assert seen["timeout"] == transcript_module._FETCH_TIMEOUT_SECONDS

    seen.clear()
    session.request("GET", "https://example.test", timeout=5.0)
    assert seen["timeout"] == 5.0   # an explicit per-call timeout is not overridden


class _VendorNoTranscriptForLanguage(Exception):
    """Stands in for the vendor's NoTranscriptFound (the video has captions, but
    none in the requested languages)."""


class _UnpreferredOnlyTrack:
    language_code = "ja"
    is_generated  = True

    def fetch(self) -> list[_FakeSnippet]:
        return [_FakeSnippet("only caption", 0.0, 2.0)]


class _ListWithoutPreferred:
    def find_transcript(self, languages: list[str]) -> object:
        raise _VendorNoTranscriptForLanguage()

    def __iter__(self):
        return iter([_UnpreferredOnlyTrack()])


class _UnpreferredOnlyAPI:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def list(self, video_id: str) -> _ListWithoutPreferred:
        return _ListWithoutPreferred()


def test_fetch_transcript_falls_back_to_any_available_caption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A video captioned only in a language outside the preferred list must still
    # summarize -- the summary language is set separately, so any caption will do.
    monkeypatch.setattr(transcript_module, "NoTranscriptFound", _VendorNoTranscriptForLanguage)
    monkeypatch.setattr(transcript_module, "YouTubeTranscriptApi", _UnpreferredOnlyAPI)

    fetched = fetch_transcript("dQw4w9WgXcQ")

    assert fetched.language      == "ja"
    assert fetched.segments[0].text == "only caption"


class _VendorFetchFailure(Exception):
    """Stands in for the vendor's CouldNotRetrieveTranscript, whose real
    constructor wants live objects we do not build in tests."""


class _FailingTranscriptAPI:
    def __init__(self, **_kwargs: object) -> None:
        pass

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


class _VendorBlocked(Exception):
    """Stands in for the vendor's RequestBlocked/IpBlocked (a transient IP block)."""


class _BlockedTranscriptAPI:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def list(self, video_id: str) -> None:
        raise _VendorBlocked("your IP has been blocked by YouTube")


def test_fetch_transcript_reraises_a_transient_block_as_fetch_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A transient block must NOT come back as TranscriptUnavailable, or the digest
    # would mark the video permanently processed and never retry it.
    monkeypatch.setattr(transcript_module, "RequestBlocked", _VendorBlocked)
    monkeypatch.setattr(transcript_module, "YouTubeTranscriptApi", _BlockedTranscriptAPI)

    with pytest.raises(TranscriptFetchBlocked) as raised:
        fetch_transcript("dQw4w9WgXcQ")

    assert not isinstance(raised.value, TranscriptUnavailable)
    assert isinstance(raised.value.__cause__, _VendorBlocked)
    assert "dQw4w9WgXcQ" in str(raised.value)
