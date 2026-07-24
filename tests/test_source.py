import pytest

import tubeless.source as source_module
from tubeless import InvalidVideoURL, extract_video_id, fetch_video

VALID_ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url_form",
    [
        f"https://www.youtube.com/watch?v={VALID_ID}",
        f"http://youtube.com/watch?v={VALID_ID}",
        f"https://m.youtube.com/watch?v={VALID_ID}&t=42s",
        f"youtube.com/watch?v={VALID_ID}",
        f"https://youtu.be/{VALID_ID}",
        f"https://youtu.be/{VALID_ID}?si=share_junk",
        f"https://www.youtube.com/shorts/{VALID_ID}",
        f"https://www.youtube.com/embed/{VALID_ID}",
        f"https://www.youtube.com/live/{VALID_ID}",
        f"https://www.youtube.com/v/{VALID_ID}",
    ],
)
def test_extract_video_id_extracts_id_from_every_url_form(url_form: str) -> None:
    assert extract_video_id(url_form) == VALID_ID


def test_extract_video_id_accepts_a_bare_eleven_char_id() -> None:
    assert extract_video_id(VALID_ID) == VALID_ID


def test_extract_video_id_strips_surrounding_whitespace() -> None:
    assert extract_video_id(f"  {VALID_ID}\n") == VALID_ID


@pytest.mark.parametrize(
    "junk",
    [
        "",
        "not a url at all",
        "https://example.com/watch?v=dQw4w9WgXcQ",  # right shape, wrong host
        "https://www.youtube.com/watch",             # no v= parameter
        "https://www.youtube.com/watch?v=too_short",
        "https://youtu.be/",
        "abc",                                        # bare but not 11 chars
    ],
)
def test_extract_video_id_raises_invalid_video_url_on_junk(junk: str) -> None:
    with pytest.raises(InvalidVideoURL):
        extract_video_id(junk)


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self._payload


def test_fetch_video_reads_title_and_channel_from_oembed(monkeypatch):
    monkeypatch.setattr(
        source_module.requests, "get",
        lambda *a, **k: _FakeResponse({"title": "A talk", "author_name": "Duck Channel"}),
    )

    video = fetch_video(VALID_ID)

    assert video.video_id == VALID_ID
    assert video.title    == "A talk"
    assert video.channel  == "Duck Channel"
    assert video.url      == f"https://www.youtube.com/watch?v={VALID_ID}"


def test_fetch_video_falls_back_when_the_request_fails(monkeypatch):
    def boom(*a, **k):
        raise source_module.requests.RequestException("network down")

    monkeypatch.setattr(source_module.requests, "get", boom)

    video = fetch_video(VALID_ID)

    assert video.title   == VALID_ID   # decoration is optional: title falls back to the id
    assert video.channel is None


def test_fetch_video_falls_back_on_a_non_dict_payload(monkeypatch):
    # A valid-JSON but non-object body (null, a list) must take the fallback, not
    # crash on payload.get(...).
    monkeypatch.setattr(
        source_module.requests, "get", lambda *a, **k: _FakeResponse(["not", "a", "dict"]),
    )

    video = fetch_video(VALID_ID)

    assert video.title   == VALID_ID
    assert video.channel is None
