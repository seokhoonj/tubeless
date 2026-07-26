# Changelog

All notable changes to tubeless are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/); the project is pre-1.0, so a
minor bump (`0.x`) may change behavior.

## 0.5.2

### Fixed

- When no home directory can be determined at all (HOME unset and the process's uid
  has no passwd entry -- an arbitrary-uid container), the base-dir resolver now raises
  a clear `ConfigError` naming the fix ("set HOME or an absolute `XDG_*_HOME`") instead
  of leaking the bare `RuntimeError` that `Path.home()` throws. The legacy-config
  migration hint likewise returns no hint rather than crashing in that environment.
  (0.5.1 handled an unresolvable `~user` in a supplied value; this covers the remaining
  no-home-at-all path, completing the resolver's hardening.)

## 0.5.1

### Fixed

- A `~user` in an `XDG_*_HOME` env var or a `TUBELESS_DATA_DIR` / `TUBELESS_STATE_DIR`
  / `config.toml` override whose home cannot be resolved (`expanduser()` raises
  `RuntimeError`) no longer crashes. The base-dir resolvers ran that expansion
  unguarded, so `XDG_CONFIG_HOME=~nouser/x` crashed `config_dir()` and
  `TUBELESS_DATA_DIR=~nouser/x` crashed at `import tubeless` (via `store.CORPUS_ROOT`).
  Such a value is now ignored and the default is used (the env var is advisory; the
  override is documented "must not raise"), matching how a blank/relative value is
  already handled.

## 0.5.0

### Changed

- The base directories are resolved with a small built-in XDG resolver instead of
  `platformdirs`, and the `platformdirs` dependency is dropped. The layout is now
  the *same on every OS* -- `~/.config/tubeless`, `~/.local/share/tubeless`,
  `~/.local/state/tubeless` (honouring `XDG_CONFIG_HOME` / `XDG_DATA_HOME` /
  `XDG_STATE_HOME`). On Linux this is identical to 0.3.0/0.4.0. On macOS and Windows
  the dirs move from the OS-native locations (`~/Library/Application Support`,
  `%APPDATA%`) to the XDG paths, which are the convention git / ssh / aws already
  use there; the `TUBELESS_DATA_DIR` / `TUBELESS_STATE_DIR` / `config.toml` overrides
  and the `XDG_*_HOME` env vars are unchanged. (The 0.2.0->0.3.0 auto-migration is
  simpler now that the config dir is the same across versions; a macOS/Windows user
  who installed 0.4.0 in its brief window must move files from the native dir by hand.)

## 0.4.0

### Added

- `data_dir` / `state_dir` can be set in `config.toml` (or via `TUBELESS_DATA_DIR`
  / `TUBELESS_STATE_DIR`) to relocate the data and state directories to an explicit
  path -- read every run, so a large corpus can live on another volume and both an
  interactive run and the cron digest agree without setting an environment variable.
  Unlike `XDG_*_HOME` (which moves every XDG app's dir), the key moves only tubeless.
  `config_dir` has no such key -- it is where `config.toml` lives.
- Each digest run is now persisted as a canonical JSON record beside the rendered
  Markdown: `<data>/digests/.../<label>.json`. It is a faithful point-in-time
  snapshot -- the ranked entries (with a value copy of each summary), what was
  skipped, and the run **provenance**: the channel set that was scanned (source,
  detail, and title filters, copied by value), the backend and model that produced
  the ranking and synthesis, and how the run was narrowed (`--source-match`, or the
  `--since`/`--until`/`--channel` of a re-curate). Because the LLM ranking and
  synthesis are not deterministically reproducible from the corpus, this records
  which configuration reached which conclusion on a given day. `store.save_digest`
  / `load_digests` read and write these; the Markdown remains a derived view.

## 0.3.0

### Changed (breaking)

- Files are now placed by kind instead of all under the config directory. The
  config dir (`~/.config/tubeless` on Linux) keeps only the hand-editable
  `config.toml`, `credentials.json`, and `channels.toml`. Durable data -- the
  transcript/summary corpus and the rendered digests -- moves to the data dir
  (`~/.local/share/tubeless`), and run state -- the processed-id ledger
  `state.json` and the scheduler's `digest.log` -- moves to the state dir
  (`~/.local/state/tubeless`). Paths are resolved via `platformdirs`, so
  macOS/Windows get native locations and the `XDG_*_HOME` env vars are honored
  on Linux. The move keeps a settings reset (`rm -rf ~/.config/tubeless`) from
  destroying the corpus.
- On first run after upgrading, tubeless migrates an existing `<=0.2.0` layout
  automatically: the corpus, digests, `state.json`, and `digest.log` move from the
  old config dir to their new data/state homes, once and idempotently. On
  macOS/Windows, where the config dir itself moves to a native location, the config
  files (`config.toml`, `credentials.json`, `channels.toml`) relocate there too, so
  keys and settings are never orphaned; on Linux the config dir is unchanged and
  those files stay put. Users who pass explicit `--corpus`, `--out`, or `--state`
  paths (e.g. in a cron line) must update those paths by hand -- the automatic
  migration only touches the default locations.

### Added

- `platformdirs` dependency, and `tubeless.config.data_dir()` /
  `state_dir()` alongside the existing `config_dir()`.

## 0.2.0

### Changed (breaking)

- Configuration moved out of the single `~/.tubeless/config.env` into two files
  under the XDG config directory: secrets (the LLM API keys and any proxy
  credentials) in `~/.config/tubeless/credentials.json`, readable only by their
  owner (`0600`, refused otherwise), and the non-secret settings in
  `~/.config/tubeless/config.toml`. Existing users must move their keys and
  `TUBELESS_*` settings there; while an old `~/.tubeless/config.env` is still
  present, the "no API key" error names it and points at the new locations.
- The machine-local directory relocated from `~/.tubeless/` to
  `~/.config/tubeless/`, honoring `$XDG_CONFIG_HOME` (this also holds
  `channels.toml`, saved state, the transcript corpus, digests, and logs).

### Added

- The transcript fetch can route through a proxy to work around YouTube blocking
  the caption endpoint by IP: Webshare rotating residential
  (`TUBELESS_WEBSHARE_USER` / `TUBELESS_WEBSHARE_PASS`) or a generic HTTP proxy
  (`TUBELESS_PROXY_HTTP` / `TUBELESS_PROXY_HTTPS`), read from `credentials.json`.

## 0.1.1

- Earlier releases; see the git history.
