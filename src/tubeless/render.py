"""Render a Digest to Markdown for the daily file drop.

The digest's entries arrive already ranked; this only turns them into text. A
score tier gets an emoji so the eye lands on the important items first.
"""

from __future__ import annotations

from tubeless.digest import Digest, DigestEntry
from tubeless.synthesis import DailySynthesis

__all__ = ["to_markdown"]

# The tier itself (the cutoffs) is domain judgment and lives in importance.py;
# here we only map each tier to its display marker.
_TIER_MARKER = {"high": "🔴", "mid": "🟡", "low": "⚪"}


def to_markdown(digest: Digest) -> str:
    """Render one day's digest as a Markdown document."""
    lines = [f"# YouTube digest — {digest.date}", ""]
    if digest.synthesis is not None:
        lines.extend(_synthesis_lines(digest.synthesis))
    if not digest.entries:
        lines.append("_No new videos._")
    for entry in digest.entries:
        lines.extend(_entry_lines(entry))
    if digest.skipped:
        lines.append("---")
        lines.append("### Skipped channels")
        lines.extend(f"- {note}" for note in digest.skipped)
    return "\n".join(lines).rstrip() + "\n"


def _synthesis_lines(synthesis: DailySynthesis) -> list[str]:
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


def _entry_lines(entry: DigestEntry) -> list[str]:
    summary = entry.summary
    marker  = _TIER_MARKER[entry.importance.tier]
    lines   = [f"## {marker} {entry.channel} — {summary.video.title} (importance {entry.importance.score:.2f})"]
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
