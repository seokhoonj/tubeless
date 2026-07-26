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

    Raises:
        ConfigError: no home directory can be determined and no absolute
            ``XDG_CONFIG_HOME`` is set (propagated from ``_xdg_app_dir``).
    """
    return _xdg_app_dir("XDG_CONFIG_HOME", ".config")


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

    Raises:
        ConfigError: no home directory can be determined and no absolute
            ``XDG_DATA_HOME`` / override is set (propagated from ``_xdg_app_dir``).
    """
    override = _dir_override("TUBELESS_DATA_DIR")
    if override is not None:
        return override
    return _xdg_app_dir("XDG_DATA_HOME", ".local/share")


def state_dir() -> Path:
    """Run state that persists but is neither hand-edited nor precious: the
    processed-id ledger and the scheduler's log.

    A ``state_dir`` in ``config.toml`` (or ``TUBELESS_STATE_DIR``) wins, as an
    explicit path used as-is (``~`` expanded) -- symmetric with ``data_dir``, so a
    caller who wants to relocate state can, though it is small enough that most do
    not. Otherwise ``$XDG_STATE_HOME/tubeless``, else ``~/.local/state/tubeless``
    (the same on every OS).

    Raises:
        ConfigError: no home directory can be determined and no absolute
            ``XDG_STATE_HOME`` / override is set (propagated from ``_xdg_app_dir``).
    """
    override = _dir_override("TUBELESS_STATE_DIR")
    if override is not None:
        return override
    return _xdg_app_dir("XDG_STATE_HOME", ".local/state")


def config_path() -> Path:
    """Where the non-secret settings live: ``config.toml`` in ``config_dir()``.

    Raises:
        ConfigError: propagated from ``config_dir()`` when no config dir can be resolved."""
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

    Raises:
        ConfigError: ``config.toml`` exists but is not readable TOML, or no config dir
            can be resolved (propagated from ``load_settings`` / ``config_dir``).
    """
    from_env = os.environ.get(env_name)
    if from_env:
        return from_env
    value = load_settings().get(env_name.removeprefix("TUBELESS_").lower())
    return None if value is None or value == "" else str(value)


# --- private resolvers (used by the base-dir functions above) ------------------

def _xdg_app_dir(env_name: str, home_subpath: str) -> Path:
    """tubeless's directory under one XDG base: ``$<env>/tubeless`` when the env var
    holds an absolute path, else ``~/<home_subpath>/tubeless`` (the XDG spec's own
    fallbacks -- ``home_subpath`` is one of ``.config`` / ``.local/share`` /
    ``.local/state``, a home-relative fragment).

    A blank, whitespace-only, *relative*, or unresolvable-``~user`` env value is ignored
    and the home fallback is used: the XDG spec says a relative path "must be ignored"
    (a relative value would put the dir under the current working directory, splitting a
    cron run at cwd ``/`` from an interactive run at cwd ``~``), and a ``~user`` whose
    home cannot be resolved must not crash a resolver an advisory env var drives.
    Applied on every OS -- no platform-dirs library.

    Raises:
        ConfigError: no absolute env value was given and no home directory can be
            determined for the ``~/<home_subpath>`` fallback (HOME unset and the uid
            has no passwd entry) -- converted from the bare ``RuntimeError``
            ``Path.home`` throws, so it stays inside the CLI's error surface."""
    base = os.environ.get(env_name, "").strip()
    root = _as_absolute(base) if base else None
    if root is not None:
        return root / _APP
    try:
        home = Path.home()
    except RuntimeError as err:
        raise ConfigError(
            f"cannot locate ~/{home_subpath}/{_APP}: no home directory "
            f"(set HOME, or set {env_name} to an absolute path)"
        ) from err
    return home / home_subpath / _APP


def _dir_override(env_name: str) -> Path | None:
    """A base-dir override from the environment (which wins) or ``config.toml``, as an
    explicit absolute path with ``~`` expanded, or ``None`` when unset (or not absolute).

    Tolerates an unreadable config file by reading it as absent, rather than raising a
    second time: the CLI validates the config once at entry, and a malformed file
    surfaces there and through ``setting``; having the dir override re-raise it would
    just duplicate that error. A non-string (``data_dir = 12345``), blank, or
    non-absolute value reads as absent too, so it never becomes a path resolved against
    the working directory."""
    from_env = os.environ.get(env_name, "").strip()
    if from_env:
        return _as_absolute(from_env)
    try:
        value = load_settings().get(env_name.removeprefix("TUBELESS_").lower())
    except ConfigError:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return _as_absolute(value)


def _as_absolute(raw: str) -> Path | None:
    """Expand ``~`` in ``raw`` and return it only if absolute, else ``None``. Never
    raises: a relative value is ignored (it would depend on the working directory), and
    a ``~user`` whose home cannot be resolved (``expanduser`` raises ``RuntimeError``)
    is treated as absent too -- an advisory env var / config value must not crash the
    resolver."""
    try:
        path = Path(raw).expanduser()
    except RuntimeError:
        return None
    return path if path.is_absolute() else None
