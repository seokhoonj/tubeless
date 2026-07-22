"""Digest -> Markdown rendering: tiers, content, empty and skipped notes."""

from tubeless.digest import Digest, DigestEntry
from tubeless.feed import Upload
from tubeless.importance import Importance
from tubeless.render import to_markdown
from tubeless.source import Video
from tubeless.summary import Summary
from tubeless.synthesis import DailySynthesis
from tubeless.transcript import Transcript


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
        transcript = Transcript(video_id="vid00000001", language="ko",
                                is_auto_generated=False, segments=()),
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


def test_to_markdown_leads_with_the_synthesis_when_present():
    synthesis = DailySynthesis(
        tone          = "cautious",
        overview      = "a corrective day",
        agreements    = ("chips fell",),
        disagreements = ("A says bottomed; B distrusts",),
    )
    digest = Digest(
        date="2026-07-21", entries=(_entry(title="Example Video", score=0.5),),
        skipped=(), synthesis=synthesis,
    )

    md = to_markdown(digest)

    assert "## Today — across the sources" in md
    assert "**Tone:** cautious" in md
    assert "a corrective day" in md
    assert "**Where they agree**" in md
    assert "- chips fell" in md
    assert "**Where they differ**" in md
    assert "- A says bottomed; B distrusts" in md
    # the synthesis leads, before the per-video entries
    assert md.index("Today — across the sources") < md.index("Example Video")


def test_to_markdown_omits_the_synthesis_section_when_absent():
    digest = Digest(date="d", entries=(_entry(title="v", score=0.5),), skipped=())

    assert "across the sources" not in to_markdown(digest)


def test_to_markdown_notes_an_empty_day():
    md = to_markdown(Digest(date="2026-07-21", entries=(), skipped=()))

    assert "No new videos" in md


def test_to_markdown_lists_skipped_channels():
    digest = Digest(date="d", entries=(), skipped=("lectures: feed down",))

    md = to_markdown(digest)

    assert "Skipped channels" in md
    assert "lectures: feed down" in md
