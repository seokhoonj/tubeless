"""Digest -> Markdown rendering: tiers, content, empty and skipped notes."""

from tubeless.digest import Digest, DigestEntry
from tubeless.feed import Upload
from tubeless.importance import Importance
from tubeless.render import to_markdown
from tubeless.source import Video
from tubeless.summary import Summary


def _entry(*, title: str, score: float) -> DigestEntry:
    video = Video(
        video_id="vid00000001", title=title,
        url="https://www.youtube.com/watch?v=vid00000001", channel="Example Channel",
    )
    return DigestEntry(
        channel    = "Example Channel",
        upload     = Upload(video_id="vid00000001", title=title, published="",
                            channel_id="UC", channel_title="Example Channel"),
        summary    = Summary(video=video, tldr="the gist", points=("point 1", "point 2"),
                             language="ko"),
        importance = Importance(score=score, reason="big news"),
    )


def test_to_markdown_renders_header_score_and_points():
    digest = Digest(date="2026-07-21", entries=(_entry(title="Example Video", score=0.9),), skipped=())

    md = to_markdown(digest)

    assert "# YouTube digest — 2026-07-21" in md
    assert "🔴" in md                    # 0.9 is the high tier
    assert "importance 0.90" in md
    assert "> big news" in md
    assert "**TL;DR:** the gist" in md
    assert "- point 1" in md


def test_to_markdown_tiers_track_the_score():
    high = to_markdown(Digest(date="d", entries=(_entry(title="a", score=0.8),), skipped=()))
    mid  = to_markdown(Digest(date="d", entries=(_entry(title="a", score=0.5),), skipped=()))
    low  = to_markdown(Digest(date="d", entries=(_entry(title="a", score=0.1),), skipped=()))

    assert "🔴" in high
    assert "🟡" in mid
    assert "⚪" in low


def test_to_markdown_notes_an_empty_day():
    md = to_markdown(Digest(date="2026-07-21", entries=(), skipped=()))

    assert "No new videos" in md


def test_to_markdown_lists_skipped_channels():
    digest = Digest(date="d", entries=(), skipped=("lectures: feed down",))

    md = to_markdown(digest)

    assert "Skipped channels" in md
    assert "lectures: feed down" in md
