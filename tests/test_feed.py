"""Feed parsing and channel-id handling, no network (parser fed a fixed XML)."""

import pytest

import tubeless.feed as feed_module
from tubeless.errors import FeedError
from tubeless.feed import (
    _parse_feed,
    _playlist_id_of,
    fetch_channel_uploads,
    fetch_playlist_uploads,
    resolve_channel_id,
)

_PLAYLIST_ID = "PLexampleexampleexampleexample01"

_CHANNEL_ID = "UCabcdefghijklmnopqrstuv"  # UC + 22 chars

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


def test_parse_feed_reads_entries_in_order():
    uploads = _parse_feed(SAMPLE_FEED, limit=15)

    assert [u.video_id for u in uploads] == ["vid00000001", "vid00000002"]
    assert uploads[0].title         == "First Video"
    assert uploads[0].channel_id    == _CHANNEL_ID
    assert uploads[0].channel_title == "Example Channel"
    assert uploads[0].published     == "2026-07-20T09:00:00+00:00"


def test_parse_feed_honors_the_limit():
    assert len(_parse_feed(SAMPLE_FEED, limit=1)) == 1


def test_parse_feed_skips_entries_without_a_video_id():
    # A malformed entry (no <yt:videoId>) must be skipped, not turned into an
    # upload with an empty id.
    feed = SAMPLE_FEED.replace("<yt:videoId>vid00000002</yt:videoId>", "")

    uploads = _parse_feed(feed, limit=15)

    assert [u.video_id for u in uploads] == ["vid00000001"]


def test_parse_feed_raises_on_malformed_xml():
    with pytest.raises(FeedError):
        _parse_feed("this is not xml", limit=15)


def test_fetch_channel_uploads_rejects_a_non_channel_id():
    with pytest.raises(FeedError):
        fetch_channel_uploads("@handle")


def test_resolve_channel_id_passes_through_a_bare_id():
    assert resolve_channel_id(_CHANNEL_ID) == _CHANNEL_ID


def test_resolve_channel_id_prefers_canonical_over_a_recommended_channel_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A channel page carries recommended channels' "channelId" values too, often
    # BEFORE the page's own channel -- taking the first match resolved a handle to
    # the wrong channel. Resolution must return the canonical channel link's id.
    own       = "UC" + "o" * 22
    recommended = "UC" + "r" * 22
    page = (
        f'<script>{{"channelId":"{recommended}"}}</script>'   # a recommended channel, earlier in the HTML
        f'<link rel="canonical" href="https://www.youtube.com/channel/{own}">'
    )

    class _Response:
        status_code = 200
        text        = page

        def raise_for_status(self) -> None:
            pass

    monkeypatch.setattr(feed_module.requests, "get", lambda *a, **k: _Response())

    assert resolve_channel_id("@somehandle") == own


def test_resolve_channel_id_falls_back_to_the_external_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A page with no canonical link but its own "externalId" resolves from that.
    own  = "UC" + "e" * 22
    page = f'<script>{{"externalId":"{own}"}}</script>'

    class _Response:
        status_code = 200
        text        = page

        def raise_for_status(self) -> None:
            pass

    monkeypatch.setattr(feed_module.requests, "get", lambda *a, **k: _Response())

    assert resolve_channel_id("@somehandle") == own


def test_resolve_channel_id_raises_when_the_page_has_no_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        status_code = 200
        text        = "<html>no channel id anywhere here</html>"

        def raise_for_status(self) -> None:
            pass

    monkeypatch.setattr(feed_module.requests, "get", lambda *a, **k: _Response())

    with pytest.raises(FeedError):
        resolve_channel_id("@somehandle")


def test_resolve_channel_id_wraps_a_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise feed_module.requests.RequestException("network down")

    monkeypatch.setattr(feed_module.requests, "get", boom)

    with pytest.raises(FeedError):
        resolve_channel_id("@somehandle")


def test_fetch_channel_uploads_wraps_a_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A network failure inside _fetch_feed must surface as FeedError, not a raw
    # requests traceback past the boundary the CLI relies on for one-line errors.
    def boom(*a, **k):
        raise feed_module.requests.RequestException("connection reset")

    monkeypatch.setattr(feed_module.requests, "get", boom)

    with pytest.raises(FeedError):
        fetch_channel_uploads(_CHANNEL_ID)


def test_fetch_uploads_routes_a_playlist_and_a_channel_to_the_right_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # fetch_uploads dispatches: a PL... source feeds by playlist_id; anything else
    # resolves to a channel id and feeds by channel_id. A regression that swapped
    # the branches would fetch the wrong feed with all other tests still green.
    from tubeless.feed import fetch_uploads

    seen: dict[str, str] = {}
    monkeypatch.setattr(feed_module, "_fetch_feed", lambda params, *, limit: seen.update(params) or ())

    fetch_uploads(_PLAYLIST_ID)
    assert seen.get("playlist_id") == _PLAYLIST_ID

    seen.clear()
    fetch_uploads(_CHANNEL_ID)   # a bare 'UC...' id resolves to itself, no page fetch
    assert seen.get("channel_id") == _CHANNEL_ID


def test_playlist_id_of_reads_a_bare_id():
    assert _playlist_id_of(_PLAYLIST_ID) == _PLAYLIST_ID


def test_playlist_id_of_extracts_from_a_watch_url_with_a_list_param():
    url = f"https://www.youtube.com/watch?v=vid00000003&list={_PLAYLIST_ID}"
    assert _playlist_id_of(url) == _PLAYLIST_ID


def test_playlist_id_of_returns_none_for_a_channel_source():
    assert _playlist_id_of("@examplechannel") is None
    assert _playlist_id_of(_CHANNEL_ID) is None


def test_fetch_playlist_uploads_rejects_a_non_playlist_id():
    with pytest.raises(FeedError):
        fetch_playlist_uploads(_CHANNEL_ID)
