"""Feed parsing and channel-id handling, no network (parser fed a fixed XML)."""

import pytest

from tubeless.errors import FeedError
from tubeless.feed import (
    _parse_feed,
    _playlist_id_of,
    fetch_channel_uploads,
    fetch_playlist_uploads,
    resolve_channel_id,
)

_PLAYLIST_ID = "PLQvqXcm97CTCf_tqMOL0QpTCoMNwjCje5"

_CHANNEL_ID = "UCabcdefghijklmnopqrstuv"  # UC + 22 chars

SAMPLE_FEED = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
  <yt:channelId>{_CHANNEL_ID}</yt:channelId>
  <title>수페TV</title>
  <entry>
    <yt:videoId>LHyuRMkDol8</yt:videoId>
    <title>버핏 이야기</title>
    <published>2026-07-20T09:00:00+00:00</published>
  </entry>
  <entry>
    <yt:videoId>0HM8M3K-2Oo</yt:videoId>
    <title>코스닥 이야기</title>
    <published>2026-07-19T09:00:00+00:00</published>
  </entry>
</feed>"""


def test_parse_feed_reads_entries_in_order():
    uploads = _parse_feed(SAMPLE_FEED, limit=15)

    assert [u.video_id for u in uploads] == ["LHyuRMkDol8", "0HM8M3K-2Oo"]
    assert uploads[0].title         == "버핏 이야기"
    assert uploads[0].channel_id    == _CHANNEL_ID
    assert uploads[0].channel_title == "수페TV"
    assert uploads[0].published     == "2026-07-20T09:00:00+00:00"


def test_parse_feed_honors_the_limit():
    assert len(_parse_feed(SAMPLE_FEED, limit=1)) == 1


def test_parse_feed_raises_on_malformed_xml():
    with pytest.raises(FeedError):
        _parse_feed("this is not xml", limit=15)


def test_fetch_channel_uploads_rejects_a_non_channel_id():
    with pytest.raises(FeedError):
        fetch_channel_uploads("@handle")


def test_resolve_channel_id_passes_through_a_bare_id():
    assert resolve_channel_id(_CHANNEL_ID) == _CHANNEL_ID


def test_playlist_id_of_reads_a_bare_id():
    assert _playlist_id_of(_PLAYLIST_ID) == _PLAYLIST_ID


def test_playlist_id_of_extracts_from_a_watch_url_with_a_list_param():
    url = f"https://www.youtube.com/watch?v=mUiN6qf_zes&list={_PLAYLIST_ID}"
    assert _playlist_id_of(url) == _PLAYLIST_ID


def test_playlist_id_of_returns_none_for_a_channel_source():
    assert _playlist_id_of("@3protv") is None
    assert _playlist_id_of(_CHANNEL_ID) is None


def test_fetch_playlist_uploads_rejects_a_non_playlist_id():
    with pytest.raises(FeedError):
        fetch_playlist_uploads(_CHANNEL_ID)
