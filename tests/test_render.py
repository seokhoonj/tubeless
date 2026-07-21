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
        url="https://www.youtube.com/watch?v=vid00000001", channel="예시 채널",
    )
    return DigestEntry(
        channel    = "예시 채널",
        upload     = Upload(video_id="vid00000001", title=title, published="",
                            channel_id="UC", channel_title="예시 채널"),
        summary    = Summary(video=video, tldr="핵심 요약", points=("포인트 1", "포인트 2"),
                             language="ko"),
        importance = Importance(score=score, reason="큰 뉴스"),
    )


def test_to_markdown_renders_header_score_and_points():
    digest = Digest(date="2026-07-21", entries=(_entry(title="예시 영상", score=0.9),), skipped=())

    md = to_markdown(digest)

    assert "# 유튜브 다이제스트 — 2026-07-21" in md
    assert "🔴" in md                    # 0.9 is the high tier
    assert "중요도 0.90" in md
    assert "> 큰 뉴스" in md
    assert "**TL;DR:** 핵심 요약" in md
    assert "- 포인트 1" in md


def test_to_markdown_tiers_track_the_score():
    high = to_markdown(Digest(date="d", entries=(_entry(title="a", score=0.8),), skipped=()))
    mid  = to_markdown(Digest(date="d", entries=(_entry(title="a", score=0.5),), skipped=()))
    low  = to_markdown(Digest(date="d", entries=(_entry(title="a", score=0.1),), skipped=()))

    assert "🔴" in high
    assert "🟡" in mid
    assert "⚪" in low


def test_to_markdown_notes_an_empty_day():
    md = to_markdown(Digest(date="2026-07-21", entries=(), skipped=()))

    assert "새 영상이 없습니다" in md


def test_to_markdown_lists_skipped_channels():
    digest = Digest(date="d", entries=(), skipped=("강의채널: feed down",))

    md = to_markdown(digest)

    assert "읽지 못한 채널" in md
    assert "강의채널: feed down" in md
