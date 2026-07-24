---
name: summarize
description: "Summarize a YouTube video from its transcript into a TL;DR plus key points. Holds no logic of its own -- it calls the tubeless package's CLI (`tubeless`) and shows the result to the user. Backend is one of gemini (free) / openai / claude / ollama (local). Trigger phrases: summarize this video, youtube summary, summarize this youtube link, tl;dr this video, summarize this talk."
---

# tubeless — YouTube video summaries

Take a YouTube URL and run **transcript -> summary**. The summarizing logic is not
in this skill but in the tubeless package (on PyPI); this skill is a thin wrapper
that calls its CLI and relays the result to the user. A video with no captions, an
LLM key/credit problem, and the like come back from the CLI as a one-line error --
relay that message as-is rather than throwing a stack trace at the user.

## Prerequisite

This plugin calls the `tubeless` CLI, so it must be installed first:

```
pipx install tubeless        # or: pip install tubeless
```

That puts the `tubeless` command on PATH. **tubeless finds the key itself** -- it
reads `OPENAI_API_KEY` / `CLAUDE_API_KEY` / `GEMINI_API_KEY` from
`~/.config/tubeless/credentials.json` (or the environment) per backend, so this
skill never has to pull a key out and pass it. Never print a key value anywhere.

## Running

Call `tubeless` from PATH with the `summarize` subcommand:

```
tubeless summarize "<URL>" [options]
```

(A bare `tubeless "<URL>"` also runs summarize as a shortcut, but the canonical
form is `tubeless summarize`.)

Options (`tubeless summarize --help` is the source of truth for exact defaults and models):
- `--backend claude|openai|gemini|ollama` — the LLM vendor. Defaults to openai (or
  `backend` in `config.toml`). gemini has a free tier; ollama is local (no key).
  Each backend's default model is decided by the CLI -- do not hardcode a model here.
- `--model <id>` — override the backend's default model (e.g. `--backend gemini --model gemini-2.5-pro`).
- `--lang <code>` — summary language (CLI default when unset).
- `--detail brief|normal|deep` — summary depth. "in detail / thorough / long" -> `deep`, "short / brief" -> `brief`.
- `--max-points N` — cap on the number of key points (CLI default per `--detail` when unset).
- `--json` — JSON instead of human-readable text.

On each run the CLI writes a one-line settings header to stderr
(`tubeless: backend=... model=... detail=... lang=...`). It records which model ran,
so relay it to the user when useful -- especially when a small model has mangled an
unfamiliar proper noun and you need to explain why.

## Procedure

1. **Get the URL.** Find a YouTube URL (or an 11-character video id) in the user's
   message. If there is none, ask for one. If there are several, handle them one at a time.

2. **Run.** Just call the CLI -- tubeless finds the key in `~/.config/tubeless/credentials.json`.

   ```bash
   tubeless summarize "<URL>"
   ```

   Add `--backend` only when running a non-default (non-openai) backend. E.g. for free,
   `--backend gemini` (needs `GEMINI_API_KEY` in `credentials.json`); for Claude,
   `--backend claude` (needs `CLAUDE_API_KEY`); fully local, `--backend ollama` (no key,
   but an `ollama` server must be running).

3. **Relay the result.** Show the CLI's stdout (title, URL, TL;DR, bullets) to the user
   as-is. You may trim to the essentials if it is long, but never drop the TL;DR or the points.

4. **Error handling.** When the CLI exits non-zero (`exit 1`), relay the one-line
   `tubeless: <message>` from stderr as-is. Common ones:
   - `command not found: tubeless` -> the package is not installed. Point the user at
     `pipx install tubeless` (or `pip install tubeless`).
   - `insufficient_quota` / `429` -> LLM credits exhausted. Point them at the vendor
     console (OpenAI / Claude / Google AI Studio) to top up.
   - `TranscriptUnavailable` -> the video has no captions (disabled or none generated).
     Not an LLM problem -- the video itself has no transcript.
   - `transcript fetch blocked` (`TranscriptFetchBlocked`) -> YouTube is temporarily
     rate-limiting or IP-blocking this run. Not a missing transcript -- retry later or
     run from a different network. The video itself is fine.
   - `no ... API key` -> check that the backend's key
     (`OPENAI_API_KEY` / `CLAUDE_API_KEY` / `GEMINI_API_KEY`) is in
     `~/.config/tubeless/credentials.json`.

## What this skill does not do

- It does not re-implement transcript extraction or summarizing here (the package does);
  it always calls the CLI.
- It never prints, logs, or includes an API key value in output.
- It does not force a summary on a video with no captions (no captions -> no summary; there
  is no speech-to-text fallback yet).

## See also — digest

`tubeless digest`, which summarizes the channels you follow each day into a single ranked
digest, is the next layer of the package. This skill only handles single-URL summaries.
For the digest, see `tubeless digest --help` and the project README.
