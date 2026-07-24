"""Render a Digest to Markdown for the daily file drop.

The digest's entries arrive already ranked; this only turns them into text. A
score tier gets an emoji so the eye lands on the important items first.
"""

from __future__ import annotations

from typing import get_args

from tubeless.digest import Digest, Entry, Skip
from tubeless.importance import ImportanceTier
from tubeless.synthesis import Synthesis

__all__ = ["render_markdown"]

# The tier itself (the cutoffs) is domain judgment and lives in importance.py;
# here we only map each tier to its display marker. The dict[ImportanceTier, str]
# hint makes a *mistyped* key a static error, but a type checker does not verify
# the dict is exhaustive over the Literal -- so the assert below does, at import,
# turning "a new tier with no marker" into an immediate failure instead of a
# runtime KeyError at render time.
_TIER_MARKER: dict[ImportanceTier, str] = {"high": "🔴", "mid": "🟡", "low": "⚪"}
assert set(_TIER_MARKER) == set(get_args(ImportanceTier)), "every ImportanceTier needs a marker"


def render_markdown(digest: Digest) -> str:
    """Render one digest as a Markdown document."""
    lines = [f"# YouTube digest — {digest.label}", ""]
    if digest.synthesis is not None:
        lines.extend(_synthesis_lines(digest.synthesis))
    if not digest.entries:
        lines.append("_No new videos._")
    for entry in digest.entries:
        lines.extend(_entry_lines(entry))
    lines.extend(_skipped_lines(digest.skipped))
    return "\n".join(lines).rstrip() + "\n"


def _synthesis_lines(synthesis: Synthesis) -> list[str]:
    """The cross-source briefing that leads the digest: overall tone, a short
    synthesis, and where the sources agree and differ."""
    lines = ["## Today — across the sources", ""]
    if synthesis.tone:
        lines += [f"**Tone:** {synthesis.tone}", ""]
    if synthesis.overview:
        lines += [synthesis.overview, ""]
    if synthesis.agreements:
        lines.append("**Where they agree**")
        lines += [f"- {point}" for point in synthesis.agreements]
        lines.append("")
    if synthesis.disagreements:
        lines.append("**Where they differ**")
        lines += [f"- {point}" for point in synthesis.disagreements]
        lines.append("")
    lines += ["---", ""]
    return lines


def _entry_lines(entry: Entry) -> list[str]:
    summary = entry.summary
    marker  = _TIER_MARKER[entry.importance.tier]
    channel = summary.video.channel or "Unknown channel"
    lines   = [f"## {marker} {channel} — {summary.video.title} (importance {entry.importance.score:.2f})"]
    if entry.importance.reason:
        lines.append(f"> {entry.importance.reason}")
    lines.append(summary.video.url)
    lines.append("")
    lines.append(f"**TL;DR:** {summary.tldr}")
    if summary.points:
        lines.append("")
        lines.extend(f"- {point}" for point in summary.points)
    lines.append("")
    return lines


def _skipped_lines(skipped: tuple[Skip, ...]) -> list[str]:
    """List what was left out, split by kind: channels whose feed could not be
    read, and videos with no transcript. Kept distinct so an empty digest caused
    by a feed outage reads differently from a genuinely quiet day."""
    if not skipped:
        return []
    feed_failures  = [skip for skip in skipped if skip.category == "feed-failure"]
    no_transcripts = [skip for skip in skipped if skip.category == "no-transcript"]
    lines = ["---"]
    if feed_failures:
        lines.append("### Skipped channels")
        lines += [f"- {skip.subject}: {skip.message}" for skip in feed_failures]
    if no_transcripts:
        lines.append("### Videos without a transcript")
        lines += [f"- {skip.subject}: {skip.message}" for skip in no_transcripts]
    return lines
