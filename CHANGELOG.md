# Changelog

All notable changes to tubeless are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/); the project is pre-1.0, so a
minor bump (`0.x`) may change behavior.

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
  automatically: the corpus, digests, `state.json`, and `digest.log` are moved
  from the config dir to their new homes, once and idempotently. Users who pass
  explicit `--corpus`, `--out`, or `--state` paths (e.g. in a cron line) must
  update those paths by hand -- the automatic migration only touches the
  default locations.

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
