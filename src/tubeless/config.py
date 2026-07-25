"""tubeless's non-secret settings, and the base directories for everything it
writes on the machine.

Settings -- which backend, which model, the digest defaults -- are not secrets,
so they live in a plain, hand-editable TOML file, ``config.toml`` in
``config_dir()``. The API keys and the proxy credentials live apart, in
``credentials`` (a 0600 JSON file in the same config dir), so a settings file
that is safe to read is never where a key can leak.

Files are placed by *kind*, not all in one directory. Each kind has its own base
directory, resolved once here via ``platformdirs`` so the paths are native on
macOS/Windows and honour the XDG env vars on Linux:

- ``config_dir()`` -- hand-editable settings and secrets (``config.toml``,
  ``credentials.json``, ``channels.toml``); small and safe to sync.
- ``data_dir()`` -- durable, hard-to-regenerate user data: the summary/transcript
  corpus and the rendered digests.
- ``state_dir()`` -- run state that persists but is neither hand-edited nor
  precious: the processed-id ledger and the scheduler's log.

Every consumer module hangs its own file off one of these bases rather than
re-deriving a path. ``migrate_legacy_layout()`` relocates the files a <=0.2.0
install left under ``config_dir()`` into ``data_dir()`` / ``state_dir()`` once,
so an upgrade never loses the corpus or re-processes the whole backlog.

A setting is read from the environment first (``TUBELESS_BACKEND`` ...), then the
file, so a one-off ``TUBELESS_BACKEND=gemini tubeless ...`` overrides it without
editing anything.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path

import platformdirs

from tubeless.errors import ConfigError

_APP = "tubeless"

__all__ = [
    "config_dir",
    "config_path",
    "data_dir",
    "state_dir",
    "load_settings",
    "migrate_legacy_layout",
    "setting",
]


def config_dir() -> Path:
    """Hand-editable settings and secrets: ``config.toml``, ``credentials.json``,
    ``channels.toml``.

    ``$XDG_CONFIG_HOME/tubeless`` when that variable is set, else the platform
    config dir (``~/.config/tubeless`` on Linux). git never tracks it.
    """
    return Path(platformdirs.user_config_dir(_APP, appauthor=False))


def data_dir() -> Path:
    """Durable, hard-to-regenerate user data: the summary/transcript corpus and
    the rendered digests.

    ``$XDG_DATA_HOME/tubeless`` when set, else the platform data dir
    (``~/.local/share/tubeless`` on Linux). Kept apart from ``config_dir()`` so
    resetting settings never destroys the corpus.
    """
    return Path(platformdirs.user_data_dir(_APP, appauthor=False))


def state_dir() -> Path:
    """Run state that persists but is neither hand-edited nor precious: the
    processed-id ledger and the scheduler's log.

    ``$XDG_STATE_HOME/tubeless`` when set, else the platform state dir
    (``~/.local/state/tubeless`` on Linux).
    """
    return Path(platformdirs.user_state_dir(_APP, appauthor=False))


def config_path() -> Path:
    """Where the non-secret settings live: ``config.toml`` in ``config_dir()``."""
    return config_dir() / "config.toml"


def _legacy_config_root() -> Path:
    """Where a <=0.2.0 install kept everything: its hand-rolled config dir,
    ``$XDG_CONFIG_HOME`` (or ``~/.config``) / ``tubeless`` -- the exact formula
    0.2.0 used, not today's ``config_dir()``.

    The two differ off Linux: 0.2.0 hand-rolled the XDG path even on macOS/Windows,
    so it wrote to ``~/.config/tubeless`` there, whereas 0.3.0's ``config_dir()``
    resolves to the native config location. The migration source must be this old
    formula, or an upgrade on macOS/Windows would look in the (empty) new dir and
    move nothing while the real data sits in ``~/.config/tubeless``."""
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "tubeless"


# The files a <=0.2.0 install wrote under its config dir, mapped to their 0.3.0
# home. The leaf names are the legacy layout, fixed history -- the consumer
# modules (store/cli/state/schedule) build the same leaves off the new bases.
def _legacy_moves() -> list[tuple[Path, Path]]:
    old = _legacy_config_root()
    return [
        # Config files stay put on Linux (old == config_dir(), so these are no-ops)
        # but relocate to the native config dir on macOS/Windows, where config_dir()
        # now differs -- otherwise the keys and settings would be orphaned there.
        (old / "config.toml",      config_dir() / "config.toml"),
        (old / "credentials.json", config_dir() / "credentials.json"),
        (old / "channels.toml",    config_dir() / "channels.toml"),
        # Durable data and run state always leave the old config dir.
        (old / "corpus",     data_dir()  / "corpus"),
        (old / "digests",    data_dir()  / "digests"),
        (old / "state.json", state_dir() / "state.json"),
        (old / "digest.log", state_dir() / "digest.log"),
    ]


def migrate_legacy_layout() -> None:
    """Relocate a <=0.2.0 install's files from its old config dir to the 0.3.0
    layout, once.

    0.2.0 and earlier put the corpus, digests, state ledger, and log alongside the
    config, all under one hand-rolled config dir (see ``_legacy_config_root``); 0.3.0
    separates data and state from config and resolves each base natively. For each
    file, if the new location is absent and the legacy one exists, move it. Idempotent
    -- a second run finds the new path present and does nothing -- so it is safe to
    call at every CLI entry. On Linux the config dir is unchanged, so the config files
    are no-ops and only data and state move; on macOS/Windows the config dir is now a
    native location, so the config files relocate there too.

    Raises:
        ConfigError: a file could not be relocated (I/O error) -- surfaced as a
            one-line CLI error, since silently reading a moved-but-missing state
            ledger as empty would re-process the whole backlog.
    """
    for old, new in _legacy_moves():
        if new.exists() or not old.exists():
            continue
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
        except OSError as err:
            raise ConfigError(f"could not migrate {old} to {new}: {err}") from err


def load_settings(path: Path | None = None) -> dict[str, object]:
    """Parse ``config.toml`` into a dict; empty when the file is absent.

    Raises:
        ConfigError: the file exists but is not readable TOML -- surfaced as a
            one-line CLI error, not a traceback.
    """
    path = path or config_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as err:
        # tomllib decodes UTF-8 internally, so a non-UTF-8 file raises
        # UnicodeDecodeError (a ValueError, not an OSError) -- name it explicitly
        # or it escapes this boundary as a bare traceback (as credentials._load does).
        raise ConfigError(f"could not read config file {path}: {err}") from err


def setting(name: str) -> str | None:
    """Return a setting from the environment (which wins) or ``config.toml``.

    ``name`` is the environment-variable spelling (``TUBELESS_LANG``); in the file
    it is the same key without the ``TUBELESS_`` prefix, lower-cased (``lang``),
    since the file already namespaces it. An absent key or an empty string reads as
    absent (``None``) -- the same on both sides, so an empty field falls back to
    the default instead of overriding it. Any other TOML scalar is stringified with
    ``str()`` (a number including ``0``, a bool as ``"True"``/``"False"``): matching
    the environment, where a caller that needs the int (``max_points``) parses the
    returned digits and an out-of-range ``0`` still reaches its "must be positive" error.
    """
    from_env = os.environ.get(name)
    if from_env:
        return from_env
    value = load_settings().get(name.removeprefix("TUBELESS_").lower())
    return None if value is None or value == "" else str(value)
