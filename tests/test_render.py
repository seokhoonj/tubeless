"""Digest -> Markdown rendering: tiers, content, empty and skipped notes."""

from typing import get_args

from tubeless.digest import Digest, Entry, Skip
from tubeless.importance import Importance, ImportanceTier
from tubeless.render import _TIER_MARKER, render_markdown
from tubeless.source import Video
from tubeless.summary import Summary
from tubeless.synthesis import Synthesis


def test_tier_marker_covers_every_importance_tier():
    # render._TIER_MARKER is asserted exhaustive over ImportanceTier at import; lock
    # that here so adding a tier without a marker fails a test, not a live render.
    assert set(_TIER_MARKER) == set(get_args(ImportanceTier))


def _entry(*, title: str, score: float, channel: str = "Example Channel") -> Entry:
    video = Video(
        video_id="vid00000001", title=title,
        url="https://www.youtube.com/watch?v=vid00000001", channel=channel,
    )
    return Entry(
        summary    = Summary(video=video, tldr="the gist", points=("point 1", "point 2"),
                             language="ko", detail="normal"),
        importance = Importance(score=score, reason="big news"),
    )


def test_render_markdown_renders_header_score_and_points():
    digest = Digest(created="2026-07-21", entries=(_entry(title="Example Video", score=0.9),))

    md = render_markdown(digest)

    assert "# YouTube digest — 2026-07-21" in md
    assert "🔴" in md                    # 0.9 is the high tier
    assert "importance 0.90" in md
    assert "> big news" in md
    assert "**TL;DR:** the gist" in md
    assert "- point 1" in md


def test_render_markdown_header_uses_the_summary_channel():
    digest = Digest(created="d", entries=(_entry(title="v", score=0.9, channel="Duck Channel"),))

    assert "Duck Channel — v" in render_markdown(digest)


def test_render_markdown_falls_back_when_the_channel_is_unknown():
    video = Video(video_id="vid00000001", title="v", url="u", channel=None)
    entry = Entry(
        summary    = Summary(video=video, tldr="g", points=(), language="en", detail="normal"),
        importance = Importance(score=0.9, reason=""),
    )

    assert "Unknown channel — v" in render_markdown(Digest(created="d", entries=(entry,)))


def test_render_markdown_tiers_track_the_score():
    high = render_markdown(Digest(created="d", entries=(_entry(title="a", score=0.8),)))
    mid  = render_markdown(Digest(created="d", entries=(_entry(title="a", score=0.5),)))
    low  = render_markdown(Digest(created="d", entries=(_entry(title="a", score=0.1),)))

    assert "🔴" in high
    assert "🟡" in mid
    assert "⚪" in low


def test_render_markdown_leads_with_the_synthesis_when_present():
    synthesis = Synthesis(
        tone          = "cautious",
        overview      = "a corrective day",
        agreements    = ("chips fell",),
        disagreements = ("A says bottomed; B distrusts",),
    )
    digest = Digest(
        created="2026-07-21", entries=(_entry(title="Example Video", score=0.5),),
        synthesis=synthesis,
    )

    md = render_markdown(digest)

    assert "## Today — across the sources" in md
    assert "**Tone:** cautious" in md
    assert "a corrective day" in md
    assert "**Where they agree**" in md
    assert "- chips fell" in md
    assert "**Where they differ**" in md
    assert "- A says bottomed; B distrusts" in md
    # the synthesis leads, before the per-video entries
    assert md.index("Today — across the sources") < md.index("Example Video")


def test_render_markdown_omits_the_synthesis_section_when_absent():
    digest = Digest(created="d", entries=(_entry(title="v", score=0.5),))

    assert "across the sources" not in render_markdown(digest)


def test_render_markdown_notes_an_empty_day():
    md = render_markdown(Digest(created="2026-07-21", entries=()))

    assert "No new videos" in md


def test_render_markdown_lists_skipped_channels_and_captionless_videos_separately():
    digest = Digest(
        created="d", entries=(),
        skipped=(
            Skip("feed-failure", "@lectures", "feed down"),
            Skip("no-transcript", "vid00000009", "captions off"),
        ),
    )

    md = render_markdown(digest)

    assert "Skipped channels" in md
    assert "@lectures: feed down" in md
    assert "Videos without a transcript" in md
    assert "vid00000009: captions off" in md
