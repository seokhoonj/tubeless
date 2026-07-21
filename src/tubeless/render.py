"""Render a Digest to Markdown for the daily file drop.

The digest's entries arrive already ranked; this only turns them into text. A
score tier gets an emoji so the eye lands on the important items first.
"""

from __future__ import annotations

from tubeless.digest import Digest, DigestEntry

__all__ = ["to_markdown"]

# The tier itself (the cutoffs) is domain judgment and lives in importance.py;
# here we only map each tier to its display marker.
_TIER_MARKER = {"high": "🔴", "mid": "🟡", "low": "⚪"}


def to_markdown(digest: Digest) -> str:
    """Render one day's digest as a Markdown document."""
    lines = [f"# 유튜브 다이제스트 — {digest.date}", ""]
    if not digest.entries:
        lines.append("_새 영상이 없습니다._")
    for entry in digest.entries:
        lines.extend(_entry_lines(entry))
    if digest.skipped:
        lines.append("---")
        lines.append("### 읽지 못한 채널")
        lines.extend(f"- {note}" for note in digest.skipped)
    return "\n".join(lines).rstrip() + "\n"


def _entry_lines(entry: DigestEntry) -> list[str]:
    summary = entry.summary
    marker  = _TIER_MARKER[entry.importance.tier]
    lines   = [f"## {marker} {entry.channel} — {summary.video.title} (중요도 {entry.importance.score:.2f})"]
    if entry.importance.reason:
        lines.append(f"> {entry.importance.reason}")
    lines.append(summary.video.url)
    lines.append("")
    lines.append(f"**TLDR:** {summary.tldr}")
    if summary.points:
        lines.append("")
        lines.extend(f"- {point}" for point in summary.points)
    lines.append("")
    return lines
