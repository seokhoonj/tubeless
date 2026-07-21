"""Remember which videos the digest has already processed.

A digest run must not re-summarize a video it handled yesterday, so the set of
processed video ids is persisted between runs as a small JSON file. A video is
marked processed even when it had no transcript, so a captionless upload is not
retried every day.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["STATE_PATH", "read_seen", "write_seen"]

STATE_PATH = Path.home() / ".tubeless" / "state.json"


def read_seen(path: Path | None = None) -> set[str]:
    """Return the set of already-processed video ids; empty if there is no state
    yet or the file is unreadable (a corrupt state must not crash a run)."""
    path = path or STATE_PATH
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    seen = data.get("seen", [])
    return set(seen) if isinstance(seen, list) else set()


def write_seen(ids: set[str], path: Path | None = None) -> None:
    """Persist the processed-video id set, creating the parent directory if
    needed. Ids are written sorted so the file diffs cleanly between runs."""
    path = path or STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"seen": sorted(ids)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
