"""Remember which videos the digest has already processed.

A digest run must not re-summarize a video it handled yesterday, so the set of
processed video ids is persisted between runs as a small JSON file. A video is
marked processed even when it had no transcript, so a captionless upload is not
retried every day.
"""

from __future__ import annotations

import json
from pathlib import Path

from tubeless.config import state_dir
from tubeless.errors import ConfigError

__all__ = ["read_seen", "state_path", "write_seen"]


def state_path() -> Path:
    """The processed-id ledger, ``state.json`` in ``state_dir()``. A function, not an
    import-time constant, so the base dir resolves when a command needs it (under the
    CLI's error surface), not as a side effect of import.

    Raises:
        ConfigError: no state directory can be resolved (propagated from ``state_dir``).
    """
    return state_dir() / "state.json"


def read_seen(path: Path | None = None) -> set[str]:
    """Return the set of already-processed video ids; empty if there is no state
    yet or the file is corrupt.

    A corrupt (unparseable) file is treated as no state so one bad write cannot
    crash every future run. A genuine I/O error is *not* swallowed, though:
    reading it as empty would let the next write overwrite and wipe the real
    seen-set, re-summarizing the whole backlog.

    Raises:
        ConfigError: the state file exists but could not be read (I/O error).
    """
    path = path or state_path()
    if not path.exists():
        return set()
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return set()   # a corrupt/garbled file must not crash a run; treat as no state
    except OSError as err:
        raise ConfigError(f"could not read state file {path}: {err}") from err
    if not isinstance(parsed, dict):
        return set()   # valid JSON but not our object shape (a list/scalar): treat as no state
    seen = parsed.get("seen", [])
    if not isinstance(seen, list):
        return set()
    # Keep only string ids: a corrupt file with e.g. {"seen": [1, 2]} must not
    # make the -> set[str] hint a lie (and set("abc") would split a bare string).
    return {video_id for video_id in seen if isinstance(video_id, str)}


def write_seen(video_ids: set[str], path: Path | None = None) -> None:
    """Persist the processed-video id set, creating the parent directory if
    needed. Ids are written sorted so the file diffs cleanly between runs.

    Raises:
        ConfigError: the state file could not be written (I/O error) -- surfaced
            as a one-line CLI error, not a traceback.
    """
    path = path or state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"seen": sorted(video_ids)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as err:
        raise ConfigError(f"could not write state file {path}: {err}") from err
