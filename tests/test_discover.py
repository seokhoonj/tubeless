"""Feed discovery -> Video rows, no network (parser fed a fixed XML)."""

import importlib

import pytest

from tubeless import DEFAULT_SCAN, discover
from tubeless.discover import _matching_title, _normalise_published, _parse_feed
from tubeless.errors import FeedError
from tubeless.source import Video

# The package re-exports the discover() function, which shadows the
# tubeless.discover attribute; reach the module itself for monkeypatching.
discover_module = importlib.import_module("tubeless.discover")

_PLAYLIST_ID = "PLexampleexampleexampleexample01"
_CHANNEL_ID  = "UCabcdefghijklmnopqrstuv"  # UC + 22 chars

SAMPLE_FEED = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
  <yt:channelId>{_CHANNEL_ID}</yt:channelId>
  <title>Example Channel</title>
  <entry>
    <yt:videoId>vid00000001</yt:videoId>
    <title>First Video</title>
    <published>2026-07-20T09:00:00+00:00</published>
  </entry>
  <entry>
    <yt:videoId>vid00000002</yt:videoId>
    <title>Second Video</title>
    <published>2026-07-19T09:00:00+00:00</published>
  </entry>
</feed>"""


def test_parse_feed_yields_videos_in_order_with_all_fields():
    videos = _parse_feed(SAMPLE_FEED, limit=15)

    assert [v.video_id for v in videos] == ["vid00000001", "vid00000002"]
    assert videos[0].title     == "First Video"
    assert videos[0].url       == "https://www.youtube.com/watch?v=vid00000001"
    assert videos[0].channel   == "Example Channel"
    assert videos[0].published == "2026-07-20T09:00:00Z"   # normalised to Z


def test_parse_feed_honors_the_limit():
    assert len(_parse_feed(SAMPLE_FEED, limit=1)) == 1


def test_parse_feed_skips_entries_without_a_video_id():
    feed = SAMPLE_FEED.replace("<yt:videoId>vid00000002</yt:videoId>", "")

    videos = _parse_feed(feed, limit=15)

    assert [v.video_id for v in videos] == ["vid00000001"]


def test_parse_feed_raises_on_malformed_xml():
    with pytest.raises(FeedError):
        _parse_feed("this is not xml", limit=15)


def test_normalise_published_converts_an_offset_to_utc_z():
    # A +09:00 timestamp must become the equivalent UTC instant with a Z suffix,
    # so lexicographic order matches chronological order across time zones.
    assert _normalise_published("2026-07-20T18:00:00+09:00") == "2026-07-20T09:00:00Z"


def test_normalise_published_passes_a_z_time_through():
    assert _normalise_published("2026-07-20T09:00:00+00:00") == "2026-07-20T09:00:00Z"


def test_normalise_published_degrades_unparseable_to_none():
    # A missing or unparseable date must not sink an otherwise-good video.
    assert _normalise_published(None) is None
    assert _normalise_published("") is None
    assert _normalise_published("last Tuesday") is None


def _video(video_id: str, title: str) -> Video:
    return Video(video_id=video_id, title=title, url="", channel=None)


def test_matching_title_keeps_all_when_no_filter():
    videos = (_video("aaaaaaaaaaa", "Anything"), _video("bbbbbbbbbbb", "Other"))
    assert _matching_title(videos, (), ()) == videos


def test_matching_title_requires_every_include_word_case_insensitively():
    videos = (
        _video("aaaaaaaaaaa", "Morning SHOW with ALICE"),
        _video("bbbbbbbbbbb", "Morning show with Bob"),
    )
    kept = _matching_title(videos, ("show", "alice"), ())
    assert [v.video_id for v in kept] == ["aaaaaaaaaaa"]


def test_matching_title_drops_any_exclude_word():
    videos = (
        _video("aaaaaaaaaaa", "Episode 5 LIVE"),
        _video("bbbbbbbbbbb", "Episode 5"),
    )
    kept = _matching_title(videos, (), ("live",))
    assert [v.video_id for v in kept] == ["bbbbbbbbbbb"]


def test_discover_routes_a_playlist_and_a_channel_to_the_right_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # discover dispatches: a PL... source feeds by playlist_id; anything else
    # resolves to a channel id and feeds by channel_id. A regression that swapped
    # the branches would fetch the wrong feed with all other tests still green.
    seen: dict[str, str] = {}
    monkeypatch.setattr(
        discover_module, "_scan_feed", lambda params, *, limit: seen.update(params) or ()
    )

    discover(_PLAYLIST_ID)
    assert seen.get("playlist_id") == _PLAYLIST_ID

    seen.clear()
    discover(_CHANNEL_ID)   # a bare 'UC...' id resolves to itself, no page fetch
    assert seen.get("channel_id") == _CHANNEL_ID


def test_discover_scans_the_default_window_and_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    def fake_scan(params, *, limit):
        captured["limit"] = limit
        return (
            _video("aaaaaaaaaaa", "Daily Show LIVE"),
            _video("bbbbbbbbbbb", "Daily Show"),
        )

    monkeypatch.setattr(discover_module, "_scan_feed", fake_scan)

    kept = discover(_CHANNEL_ID, includes=("daily",), excludes=("live",))

    assert captured["limit"] == DEFAULT_SCAN          # default scans the full window
    assert [v.video_id for v in kept] == ["bbbbbbbbbbb"]


def test_discover_wraps_a_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A network failure must surface as FeedError, not a raw requests traceback
    # past the boundary the CLI relies on for one-line errors.
    def boom(*a, **k):
        raise discover_module.requests.RequestException("connection reset")

    monkeypatch.setattr(discover_module.requests, "get", boom)

    with pytest.raises(FeedError):
        discover(_CHANNEL_ID)
