import pytest

import tubeless.transcript as transcript_module
from tubeless import (
    Transcript,
    TranscriptFetchBlocked,
    TranscriptSegment,
    TranscriptUnavailable,
    Video,
    fetch_transcript,
)

# fetch_transcript takes a Video (the object every pipeline stage passes along),
# so tests hand it one whose id is the track being faked.
VIDEO = Video(
    video_id = "dQw4w9WgXcQ",
    title    = "A talk",
    url      = "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    channel  = None,
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # fetch_transcript reads two machine-dependent settings: proxy secrets (env or
    # credentials.json) and the whisper model (env or config.toml). Default the
    # whole suite to "no proxy, whisper off" so no test depends on the box it runs
    # on; the proxy tests below re-stub secret and the whisper tests re-stub
    # _whisper_model_name to exercise those paths.
    monkeypatch.setattr(transcript_module, "secret", lambda name: None)
    monkeypatch.setattr(transcript_module, "_whisper_model_name", lambda: None)


def test_transcript_text_joins_segments_with_spaces() -> None:
    transcript = Transcript(
        video             = VIDEO,
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
        video=VIDEO, language="en", is_auto_generated=False, segments=()
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
    last_http_client:  object = None
    last_proxy_config: object = None

    def __init__(self, *, http_client: object = None, proxy_config: object = None) -> None:
        # Record both so a test can assert fetch_transcript wires in its
        # timeout-bearing session rather than letting the vendor default hang, and
        # passes through whatever proxy config was resolved.
        type(self).last_http_client  = http_client
        type(self).last_proxy_config = proxy_config

    def list(self, video_id: str) -> _FakeTranscriptList:
        return _FakeTranscriptList()


def test_fetch_transcript_maps_vendor_snippets_to_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transcript_module, "YouTubeTranscriptApi", _FakeTranscriptAPI)

    fetched = fetch_transcript(VIDEO)

    assert isinstance(_FakeTranscriptAPI.last_http_client, transcript_module._TimeoutSession)
    assert _FakeTranscriptAPI.last_proxy_config is None   # no proxy configured -> direct fetch
    assert fetched.video.video_id == "dQw4w9WgXcQ"
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

    fetched = fetch_transcript(VIDEO)

    assert fetched.language      == "ja"
    assert fetched.segments[0].text == "only caption"


class _GeneratedFallbackTrack:
    language_code = "ja"
    is_generated  = True

    def fetch(self) -> list[_FakeSnippet]:
        return [_FakeSnippet("auto caption", 0.0, 2.0)]


class _ManualFallbackTrack:
    language_code = "ja"
    is_generated  = False

    def fetch(self) -> list[_FakeSnippet]:
        return [_FakeSnippet("manual caption", 0.0, 2.0)]


class _ListPreferringManual:
    def find_transcript(self, languages: list[str]) -> object:
        raise _VendorNoTranscriptForLanguage()

    def __iter__(self):
        # generated first, manual second: only a real preference (not "take the
        # first") picks the manual track.
        return iter([_GeneratedFallbackTrack(), _ManualFallbackTrack()])


class _PreferManualAPI:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def list(self, video_id: str) -> _ListPreferringManual:
        return _ListPreferringManual()


def test_fetch_transcript_fallback_prefers_a_manual_track(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # When no preferred-language track exists, the fallback must pick a manually
    # created track over an auto-generated one -- manual captions are materially
    # more accurate, and a regression to plain available[0] would take the auto one.
    monkeypatch.setattr(transcript_module, "NoTranscriptFound", _VendorNoTranscriptForLanguage)
    monkeypatch.setattr(transcript_module, "YouTubeTranscriptApi", _PreferManualAPI)

    fetched = fetch_transcript(VIDEO)

    assert fetched.is_auto_generated is False
    assert fetched.segments[0].text == "manual caption"


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
        fetch_transcript(VIDEO)

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
        fetch_transcript(VIDEO)

    assert not isinstance(raised.value, TranscriptUnavailable)
    assert isinstance(raised.value.__cause__, _VendorBlocked)
    assert "dQw4w9WgXcQ" in str(raised.value)


class _FakeCouldNotRetrieve(Exception):
    """Stands in for the vendor's CouldNotRetrieveTranscript base class."""


class _VendorBlockedSubclass(_FakeCouldNotRetrieve):
    """A transient block that -- like the real RequestBlocked/IpBlocked -- IS a
    subclass of CouldNotRetrieveTranscript, so only the ORDER of the two except
    arms decides which handler catches it."""


class _BlockedSubclassAPI:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def list(self, video_id: str) -> None:
        raise _VendorBlockedSubclass("IP temporarily blocked")


def test_fetch_transcript_prefers_the_blocked_arm_over_its_permanent_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # RequestBlocked/IpBlocked/YouTubeRequestFailed really DO subclass
    # CouldNotRetrieveTranscript, so if the two except arms were reordered a
    # transient block would be caught as the permanent TranscriptUnavailable and
    # the digest would drop the video forever. Mirror that subclassing here so the
    # test fails the moment the arms are swapped (the plain _VendorBlocked above
    # cannot catch a reorder because it is not a CouldNotRetrieveTranscript).
    monkeypatch.setattr(transcript_module, "CouldNotRetrieveTranscript", _FakeCouldNotRetrieve)
    monkeypatch.setattr(transcript_module, "RequestBlocked", _VendorBlockedSubclass)
    monkeypatch.setattr(transcript_module, "YouTubeTranscriptApi", _BlockedSubclassAPI)

    with pytest.raises(TranscriptFetchBlocked) as raised:
        fetch_transcript(VIDEO)

    assert not isinstance(raised.value, TranscriptUnavailable)
    assert isinstance(raised.value.__cause__, _VendorBlockedSubclass)


class _TimingOutAPI:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def list(self, video_id: str) -> None:
        raise transcript_module.requests.Timeout("read timed out")


def test_fetch_transcript_maps_a_transport_timeout_to_fetch_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The _TimeoutSession adds a timeout the vendor library lacks, so a slow or
    # dropped fetch raises a raw requests.Timeout -- not a CouldNotRetrieveTranscript
    # subclass. It must be translated to the transient TranscriptFetchBlocked (retry
    # later), not escape fetch_transcript as a bare network stack trace.
    monkeypatch.setattr(transcript_module, "YouTubeTranscriptApi", _TimingOutAPI)

    with pytest.raises(TranscriptFetchBlocked) as raised:
        fetch_transcript(VIDEO)

    assert not isinstance(raised.value, TranscriptUnavailable)
    assert "dQw4w9WgXcQ" in str(raised.value)


# --- whisper fallback (videos with no captions) ----------------------------

def test_fetch_transcript_falls_back_to_whisper_when_captions_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Captions permanently absent + the operator opted in -> transcribe the audio
    # locally and return that transcript, rather than fail the video.
    import tubeless.transcribe as transcribe_module
    monkeypatch.setattr(transcript_module, "CouldNotRetrieveTranscript", _VendorFetchFailure)
    monkeypatch.setattr(transcript_module, "YouTubeTranscriptApi", _FailingTranscriptAPI)
    monkeypatch.setattr(transcript_module, "_whisper_model_name", lambda: "small")

    from_audio = Transcript(
        video=VIDEO, language="en", is_auto_generated=True,
        segments=(TranscriptSegment("spoken words", 0.0, 1.0),),
    )
    calls: dict[str, object] = {}

    def fake_transcribe(video: Video, *, model_name: str) -> Transcript:
        calls["video"], calls["model_name"] = video, model_name
        return from_audio

    monkeypatch.setattr(transcribe_module, "transcribe_audio", fake_transcribe)

    assert fetch_transcript(VIDEO) is from_audio
    assert calls == {"video": VIDEO, "model_name": "small"}


def test_fetch_transcript_does_not_transcribe_when_fallback_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With the fallback off (the default, from the autouse fixture), a caption-less
    # video stays a permanent miss -- the audio is never downloaded.
    import tubeless.transcribe as transcribe_module
    monkeypatch.setattr(transcript_module, "CouldNotRetrieveTranscript", _VendorFetchFailure)
    monkeypatch.setattr(transcript_module, "YouTubeTranscriptApi", _FailingTranscriptAPI)

    def must_not_run(*_args: object, **_kwargs: object) -> Transcript:
        raise AssertionError("transcribe_audio must not run when the fallback is off")

    monkeypatch.setattr(transcribe_module, "transcribe_audio", must_not_run)

    with pytest.raises(TranscriptUnavailable):
        fetch_transcript(VIDEO)


def test_fetch_transcript_does_not_fall_back_to_whisper_on_a_transient_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A block is transient: the same IP would fail the audio download too, and the
    # video should be retried later, not turned into a whisper attempt now.
    import tubeless.transcribe as transcribe_module
    monkeypatch.setattr(transcript_module, "RequestBlocked", _VendorBlocked)
    monkeypatch.setattr(transcript_module, "YouTubeTranscriptApi", _BlockedTranscriptAPI)
    monkeypatch.setattr(transcript_module, "_whisper_model_name", lambda: "small")

    def must_not_run(*_args: object, **_kwargs: object) -> Transcript:
        raise AssertionError("a transient block must not trigger the whisper fallback")

    monkeypatch.setattr(transcribe_module, "transcribe_audio", must_not_run)

    with pytest.raises(TranscriptFetchBlocked):
        fetch_transcript(VIDEO)


def test_fetch_transcript_does_not_use_whisper_when_captions_are_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fallback is for ABSENT captions only: a video with captions must be
    # served from them even with the fallback enabled -- the audio is never fetched.
    import tubeless.transcribe as transcribe_module
    monkeypatch.setattr(transcript_module, "YouTubeTranscriptApi", _FakeTranscriptAPI)
    monkeypatch.setattr(transcript_module, "_whisper_model_name", lambda: "small")

    def must_not_run(*_args: object, **_kwargs: object) -> Transcript:
        raise AssertionError("transcribe_audio must not run when captions are present")

    monkeypatch.setattr(transcribe_module, "transcribe_audio", must_not_run)

    fetched = fetch_transcript(VIDEO)

    assert fetched.language == "ko"
    assert fetched.segments[0].text == "first sentence"


# --- proxy configuration (the IP-block escape hatch) -----------------------

def test_proxy_config_is_none_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcript_module, "secret", lambda name: None)
    assert transcript_module._proxy_config() is None


def test_proxy_config_builds_webshare_from_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = {"TUBELESS_WEBSHARE_USER": "user1", "TUBELESS_WEBSHARE_PASS": "pass1"}
    monkeypatch.setattr(transcript_module, "secret", secrets.get)
    config = transcript_module._proxy_config()
    assert isinstance(config, transcript_module.WebshareProxyConfig)
    assert config.proxy_username == "user1"
    assert config.proxy_password == "pass1"


def test_proxy_config_builds_generic_from_a_single_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # One url is reused for both schemes -- the common "I have one proxy" case.
    secrets = {"TUBELESS_PROXY_HTTP": "http://box:8080"}
    monkeypatch.setattr(transcript_module, "secret", secrets.get)
    config = transcript_module._proxy_config()
    assert isinstance(config, transcript_module.GenericProxyConfig)
    assert config.to_requests_dict() == {"http": "http://box:8080", "https": "http://box:8080"}


def test_proxy_config_prefers_webshare_over_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both configured -> the rotating-residential Webshare path wins, since it is
    # the one that survives a block; the generic url is the fallback.
    secrets = {
        "TUBELESS_WEBSHARE_USER": "user1",
        "TUBELESS_WEBSHARE_PASS": "pass1",
        "TUBELESS_PROXY_HTTP": "http://box:8080",
    }
    monkeypatch.setattr(transcript_module, "secret", secrets.get)
    assert isinstance(transcript_module._proxy_config(), transcript_module.WebshareProxyConfig)
