import pytest

from tubeless import InvalidVideoURL, parse_video_id

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
    ],
)
def test_parse_video_id_extracts_id_from_every_url_form(url_form: str) -> None:
    assert parse_video_id(url_form) == VALID_ID


def test_parse_video_id_accepts_a_bare_eleven_char_id() -> None:
    assert parse_video_id(VALID_ID) == VALID_ID


def test_parse_video_id_strips_surrounding_whitespace() -> None:
    assert parse_video_id(f"  {VALID_ID}\n") == VALID_ID


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
def test_parse_video_id_raises_invalid_video_url_on_junk(junk: str) -> None:
    with pytest.raises(InvalidVideoURL):
        parse_video_id(junk)
