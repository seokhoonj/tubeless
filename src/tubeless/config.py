"""tubeless's non-secret settings, and the base directories for everything it
writes on the machine.

Settings -- which backend, which model, the digest defaults -- are not secrets,
so they live in a plain, hand-editable TOML file, ``config.toml`` in
``config_dir()``. The API keys and the proxy credentials live apart, in
``credentials`` (a 0600 JSON file in the same config dir), so a settings file
that is safe to read is never where a key can leak.

Files are placed by *kind*, not all in one directory. Each kind has its own base
directory, resolved here from the XDG base-directory env vars (falling back to
``~/.config``, ``~/.local/share``, ``~/.local/state``) -- the *same* layout on
every OS, no platform library. macOS and Windows get the XDG locations too, which
is the convention git / ssh / aws already use there, and keeps the package light:

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

from tubeless.errors import ConfigError

_APP = "tubeless"


def _xdg_base(env_name: str, default: str) -> Path:
    """Resolve one XDG base dir for tubeless: ``$<env>/tubeless`` when the env var is
    set, else ``~/<default>/tubeless`` (the XDG spec's own fallbacks -- ``.config``,
    ``.local/share``, ``.local/state``). Applied on every OS, so the layout is uniform
    and the package needs no platform-dirs library."""
    base = os.environ.get(env_name)
    return (Path(base) if base else Path.home() / default) / _APP

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

    ``$XDG_CONFIG_HOME/tubeless`` when that variable is set, else
    ``~/.config/tubeless`` -- the same on every OS (the git / ssh / aws convention),
    not a platform-native dir. git never tracks it.
    """
    return _xdg_base("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    """Durable, hard-to-regenerate user data: the summary/transcript corpus and
    the rendered digests.

    A ``data_dir`` in ``config.toml`` (or the ``TUBELESS_DATA_DIR`` env var) wins,
    taken as an explicit path used as-is (``~`` expanded, no app-name appended) --
    read every run, so a large corpus can live on another volume and an interactive
    run and a cron run agree without touching the environment. Otherwise
    ``$XDG_DATA_HOME/tubeless``, else ``~/.local/share/tubeless`` (the same on every
    OS). (``config_dir`` has no such key -- config cannot name its own location.)
    Kept apart from ``config_dir()`` so resetting settings never destroys the corpus.
    """
    override = _dir_override("TUBELESS_DATA_DIR")
    if override is not None:
        return override
    return _xdg_base("XDG_DATA_HOME", ".local/share")


def _dir_override(env_name: str) -> Path | None:
    """A base-dir override from the environment (which wins) or ``config.toml``, as
    an explicit path with ``~`` expanded, or ``None`` when unset.

    Tolerates an unreadable config file by reading it as absent: this resolves paths
    needed at import time (``store.CORPUS_ROOT``), so it must not raise -- a malformed
    config still surfaces cleanly when the run reads a real setting through ``setting``
    (the CLI also validates the config before this decides any relocation). A
    non-string value (``data_dir = 12345``) reads as absent too, so it never becomes a
    ``Path("12345")`` relative to the working directory.
    """
    from_env = os.environ.get(env_name)
    if from_env:
        return Path(from_env).expanduser()
    try:
        value = load_settings().get(env_name.removeprefix("TUBELESS_").lower())
    except ConfigError:
        return None
    if not isinstance(value, str) or value == "":
        return None
    return Path(value).expanduser()


def state_dir() -> Path:
    """Run state that persists but is neither hand-edited nor precious: the
    processed-id ledger and the scheduler's log.

    A ``state_dir`` in ``config.toml`` (or ``TUBELESS_STATE_DIR``) wins, as an
    explicit path used as-is (``~`` expanded) -- symmetric with ``data_dir``, so a
    caller who wants to relocate state can, though it is small enough that most do
    not. Otherwise ``$XDG_STATE_HOME/tubeless``, else ``~/.local/state/tubeless``
    (the same on every OS).
    """
    override = _dir_override("TUBELESS_STATE_DIR")
    if override is not None:
        return override
    return _xdg_base("XDG_STATE_HOME", ".local/state")


def config_path() -> Path:
    """Where the non-secret settings live: ``config.toml`` in ``config_dir()``."""
    return config_dir() / "config.toml"


# The files a <=0.2.0 install wrote under config_dir() (everything lived there),
# mapped to their new data/state home. config_dir() is the same ~/.config/tubeless
# it resolved to in 0.2.0, so it is the legacy source directly; only durable data and
# run state move out of it -- the config files were and stay in config_dir().
def _legacy_moves() -> list[tuple[Path, Path]]:
    old = config_dir()
    return [
        (old / "corpus",     data_dir()  / "corpus"),
        (old / "digests",    data_dir()  / "digests"),
        (old / "state.json", state_dir() / "state.json"),
        (old / "digest.log", state_dir() / "digest.log"),
    ]


def migrate_legacy_layout() -> None:
    """Relocate a <=0.2.0 install's files from the config dir to the split layout,
    once.

    0.2.0 and earlier put the corpus, digests, state ledger, and log alongside the
    config, all under ``config_dir()``; the split moves durable data to ``data_dir()``
    and run state to ``state_dir()``. For each file, if the new location is absent and
    the legacy one exists, move it. Idempotent -- a second run finds the new path
    present and does nothing -- so it is safe to call at every CLI entry. The config
    files never move: ``config_dir()`` is unchanged across versions on every OS.

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


def setting(env_name: str) -> str | None:
    """Return a setting from the environment (which wins) or ``config.toml``.

    ``env_name`` is the environment-variable spelling (``TUBELESS_LANG``); in the
    file it is the same key without the ``TUBELESS_`` prefix, lower-cased (``lang``),
    since the file already namespaces it (same mapping ``_dir_override`` uses). An
    absent key or an empty string reads as absent (``None``) -- the same on both
    sides, so an empty field falls back to the default instead of overriding it. Any
    other TOML scalar is stringified with ``str()`` (a number including ``0``, a bool
    as ``"True"``/``"False"``): matching the environment, where a caller that needs
    the int (``max_points``) parses the returned digits and an out-of-range ``0``
    still reaches its "must be positive" error.
    """
    from_env = os.environ.get(env_name)
    if from_env:
        return from_env
    value = load_settings().get(env_name.removeprefix("TUBELESS_").lower())
    return None if value is None or value == "" else str(value)
