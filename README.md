# tubeless

Fetch a YouTube video's transcript and summarize it with an LLM — one video from
the command line, or a daily digest of the channels and series you follow.
Works with OpenAI, Claude, or a local model via Ollama.

**[English](#english) · [한국어](#한국어)**

---

## English

- [Install](#install) — macOS, Linux, Windows
- [Set up your keys](#set-up-your-keys-tubelessconfigenv) (`~/.tubeless/config.env`)
- [Backends: OpenAI, Claude, Ollama — and what each costs](#backends-openai-claude-ollama)
- [Summarize one video](#summarize-one-video) (`--detail` / `--points` / `--backend` / `--model` / `--lang`)
- [Daily digest](#daily-digest)
- [Run it every day with cron (Linux)](#run-it-every-day-with-cron-linux)
- [Use it from Claude Code](#use-it-from-claude-code-tubeless-skill) (`/tubeless`)
- [Use it as a Python library](#use-it-as-a-python-library)
- [Limits](#limits)

### Install

You need **Python 3.11 or newer**. Check with `python3 --version`. Installing with
[pipx](https://pipx.pypa.io) keeps the `tubeless` command in its own isolated
environment so it never clashes with your other Python packages.

tubeless is installed straight from GitHub. Follow the block for your OS top to
bottom — after the last line, the `tubeless` command is on your PATH.

**macOS**
```sh
brew install python pipx            # skip if you already have them
pipx ensurepath                     # adds pipx's bin dir to PATH (open a new terminal after)
pipx install git+https://github.com/seokhoonj/tubeless.git
```

**Linux** (Debian/Ubuntu; use your distro's package manager elsewhere)
```sh
sudo apt update && sudo apt install -y python3 python3-pip pipx
pipx ensurepath                     # open a new terminal afterwards
pipx install git+https://github.com/seokhoonj/tubeless.git
```

**Windows** (PowerShell)
```powershell
# 1. Install Python 3.11+ from https://python.org — tick "Add python.exe to PATH".
py -m pip install --user pipx
py -m pipx ensurepath               # close and reopen PowerShell afterwards
py -m pipx install git+https://github.com/seokhoonj/tubeless.git
```

Add the Claude backend at install time with the `anthropic` extra:
```sh
pipx install "git+https://github.com/seokhoonj/tubeless.git#egg=tubeless[anthropic]"
```

Confirm it works:
```sh
tubeless --help
```

> **No pipx?** Plain `pip install git+https://github.com/seokhoonj/tubeless.git`
> works too (ideally inside a virtualenv). pipx is only recommended so the CLI
> stays isolated. Once tubeless is published to PyPI, `pipx install tubeless`
> will also work.

### Set up your keys (`~/.tubeless/config.env`)

OpenAI and Claude need an API key. Ollama does not (it runs locally). Keys are
secrets, so tubeless reads them from a file in your home directory — never from
the repo — and never prints or logs the value.

Create `~/.tubeless/config.env` with one `KEY=VALUE` per line:

```sh
mkdir -p ~/.tubeless
cat > ~/.tubeless/config.env <<'EOF'
# Only fill in the backend(s) you actually use.
OPENAI_SECRET_KEY=sk-...
# ANTHROPIC_SECRET_KEY=sk-ant-...
# GEMINI_SECRET_KEY=...
EOF
```

- **Get an OpenAI key:** [platform.openai.com](https://platform.openai.com) → API keys.
- **Get a Claude key:** [console.anthropic.com](https://console.anthropic.com) → API keys.
- **Get a Gemini key:** [aistudio.google.com](https://aistudio.google.com) → Get API key. (Add `GEMINI_SECRET_KEY=...` to the file.)

If your machine already exports the SDK-standard names `OPENAI_API_KEY` /
`ANTHROPIC_API_KEY` / `GEMINI_API_KEY`, tubeless honors those as a fallback, so
you can skip the file. The tubeless name (`<VENDOR>_SECRET_KEY`) wins when both
are set.

### Backends: OpenAI, Claude, Ollama

Pick the model with `--backend`. The default is OpenAI.

| backend | flag | key needed | default model | runs where | cost |
|---|---|---|---|---|---|
| **OpenAI** | `--backend openai` (default) | `OPENAI_SECRET_KEY` | `gpt-4o-mini` | OpenAI's servers | paid, prepaid credits |
| **Claude** | `--backend anthropic` | `ANTHROPIC_SECRET_KEY` | `claude-haiku-4-5` | Anthropic's servers | paid, prepaid credits |
| **Gemini** | `--backend gemini` | `GEMINI_SECRET_KEY` | `gemini-flash-lite-latest` | Google's servers | free tier + pay-as-you-go |
| **Ollama** | `--backend ollama` | none | `llama3.1` | your own machine | free |

**Where to pay, and how much.** Both cloud vendors are **prepaid**: you buy usage
credits up front, and each summary draws down from that balance.

- **OpenAI** — buy credits at [platform.openai.com](https://platform.openai.com)
  → Settings → Billing. **Minimum top-up is $5.** Prices per model are on the
  [pricing page](https://openai.com/api/pricing/). The default `gpt-4o-mini` is
  the cheapest tier — one video summary costs a fraction of a cent, so a **$5**
  top-up covers *thousands* of videos.
- **Claude** — buy credits at [console.anthropic.com](https://console.anthropic.com)
  → Billing → Buy credits. **Minimum top-up is also $5.** The default
  `claude-haiku-4-5` is Anthropic's cheapest model (about $1 per million input
  tokens / $5 per million output); a typical video is a cent or two, so **$5**
  still covers hundreds of videos. Full rates: [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing).
- **Gemini** — get a key at [aistudio.google.com](https://aistudio.google.com).
  Gemini has a **genuine free tier** (rate-limited) — enough to try tubeless
  without paying at all. For higher volume, enable pay-as-you-go billing in AI
  Studio; the default `gemini-flash-lite-latest` is a cheap, fast model that
  runs on the free tier. (It's a `-latest` alias — pinned names like
  `gemini-2.5-flash` can 404 for a newly created key, so the alias is the safe
  default; override with `--model`.) Rates: [Gemini pricing](https://ai.google.dev/pricing).
- **Ollama** — no key, no bill; the model runs on your own computer (see below).
  Free and offline, at the cost of the summary quality your local model can give.

For OpenAI and Claude, credits **expire one year** after purchase and are
non-refundable — so top up small.

**Which to choose?** OpenAI `gpt-4o-mini` (the default) is the cheapest cloud
all-rounder. Claude tends to *hedge* an uncertain number rather than invent one,
which is safer on noisy auto-captions. Gemini's free tier is the easiest way to
try a cloud model without paying. Ollama is the private/offline/free option.

**Using Ollama (local, free).** Install the server, pull a model, then point
tubeless at it:
```sh
# macOS:   brew install ollama          (or download from https://ollama.com)
# Linux:   curl -fsSL https://ollama.com/install.sh | sh
# Windows: download the installer from https://ollama.com
ollama pull llama3.1
tubeless VIDEO_ID_XX --backend ollama --model llama3.1
```
Point at a non-default host with the `OLLAMA_HOST` environment variable
(e.g. `OLLAMA_HOST=http://192.168.0.10:11434`).

**Ollama in practice — quality depends on your machine and model.** Tested on a
Ryzen 3700X / 128 GB RAM / RTX 2070 SUPER (8 GB VRAM): a small model like
`llama3.1` (8B) runs fast and is fine for a quick gist, but a 14B model such as
**Qwen 14B did not perform that well** for summarization here — a 14B only partly
fits in 8 GB of VRAM, so it spills over to the CPU/RAM and runs slower, and its
summaries held numbers and structure noticeably worse than the cloud backends.
Treat Ollama as the free / offline / private option, not a quality match for
OpenAI or Claude; when summary quality matters, use a cloud backend.

### Summarize one video

```sh
tubeless "https://www.youtube.com/watch?v=VIDEO_ID_XX"
tubeless VIDEO_ID_XX --detail deep --lang en --points 20
```

You can pass a full URL (`watch?v=`, `youtu.be/`, `/shorts/`, `/embed/`,
`/live/`) or just the bare 11-character video id.

| option | what it does | default |
|---|---|---|
| `--detail brief\|normal\|deep` | How full the summary is. **`deep` keeps every number** — see below. | `normal` |
| `--points N` | Max key points. Overrides the per-detail default (brief 5 / normal 8 / deep 14). | per-detail |
| `--backend openai\|anthropic\|gemini\|ollama` | Which LLM to use. | `openai` |
| `--model NAME` | Model id. Defaults to the backend's small/cheap model. | per-backend |
| `--lang CODE` | Language of the summary. Works across languages — an English video can be summarized in Korean. | `ko` |
| `--json` | Print machine-readable JSON instead of text. | off |

**Does it keep the numbers?** For number-heavy videos (markets, earnings, sports
scores, spec sheets), that is exactly what `--detail deep` is for. At `deep`,
tubeless instructs the model to **preserve every figure the speaker states** —
each index move, rate, price, percentage, and named entity with its number —
attached to its period (the year/quarter/date it applies to) and kept item by
item instead of collapsed into one vague sentence. The default `normal` and the
terse `brief` do **not** do this — they favor readable points and may drop some
numbers. So:

```sh
# A market recap where every figure matters:
tubeless VIDEO_ID_XX --detail deep

# ...and raise the point cap if the video lists many items:
tubeless VIDEO_ID_XX --detail deep --points 30
```

### Daily digest

Instead of one video, tubeless can watch a set of channels and produce one
Markdown file a day, with the most important videos first.

List the channels and series you follow in `~/.tubeless/channels.toml`:

```toml
[[channel]]
source = "@examplechannel"      # a handle, channel URL, 'UC...' id, or playlist
label  = "Example Channel"
detail = "deep"

[[channel]]
# A playlist narrows a channel to one series; title_includes narrows it further
# to uploads whose title contains every listed word (e.g. one recurring host).
source         = "PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
label          = "A Daily Show"
detail         = "deep"
title_includes = ["Some Host"]
```

Then run:

```sh
tubeless digest              # write ~/.tubeless/digests/YYYY-MM-DD.md
tubeless digest --dry-run    # print it instead, and don't record state
```

Each run finds every channel's new uploads via YouTube's public RSS feed (no API
key), summarizes and importance-scores them, and writes one Markdown file per day
ranked most-important first. A JSON "seen" set remembers what it already handled,
so running it again (or daily from cron) never re-summarizes the same video.

| digest option | what it does | default |
|---|---|---|
| `--only TEXT` | Run only channels whose label contains this text. | all |
| `--limit N` | Max recent uploads to check per channel. | `5` |
| `--dry-run` | Print the digest instead of writing it / updating state. | off |
| `--channels PATH` | Channels TOML file. | `~/.tubeless/channels.toml` |
| `--state PATH` | The "already seen" state file. | `~/.tubeless/state.json` |
| `--out DIR` | Directory for the dated digest file. | `~/.tubeless/digests/` |
| `--backend` / `--model` / `--lang` | Same as for a single video. | |

### Run it every day with cron (Linux)

Cron runs a command on a schedule. To build the digest every night at 22:00:

1. Open your crontab:
   ```sh
   crontab -e
   ```
2. Add one line (adjust the time — `0 22 * * *` means 22:00 daily). Use the full
   path to `tubeless` so cron can find it; get it with `which tubeless`:
   ```cron
   0 22 * * * /home/you/.local/bin/tubeless digest >> /home/you/.tubeless/digest.log 2>&1
   ```
   `>> ...digest.log 2>&1` appends both normal output and errors to a log file so
   you can see what happened.
3. Save and exit. Check it's registered with `crontab -l`.

Because the digest keeps a "seen" set, a daily run only summarizes genuinely new
uploads. Read the result each morning at `~/.tubeless/digests/`.

> **macOS** has cron too, but `launchd` / a Calendar-triggered Automator action
> is the native way. **Windows**: use Task Scheduler to run `tubeless digest`.

### Use it from Claude Code (`/tubeless` skill)

If you use [Claude Code](https://claude.com/claude-code), you can wrap the CLI in
a local skill so `/tubeless <url>` summarizes a video without leaving your editor.
The skill just shells out to the `tubeless` command you installed above.

Create `~/.claude/skills/tubeless/SKILL.md` (these agent files stay local to your
machine — they are not part of this repo):

```markdown
---
name: tubeless
description: Summarize a YouTube video. Trigger on a YouTube URL or "summarize this video".
---

Run the installed `tubeless` CLI on the URL the user gave and show the result:

    tubeless "<url>" --detail deep --lang ko

Pass `--backend anthropic` for Claude or `--backend ollama` for a local model.
Show the TLDR and key points back to the user.
```

Then in Claude Code: `/tubeless https://youtu.be/VIDEO_ID_XX`. The same idea works
for other agent CLIs (Codex `AGENTS.md`, Gemini `GEMINI.md`) — point them at the
`tubeless` command.

### Use it as a Python library

```python
from tubeless import OpenAIBackend, fetch_transcript, fetch_video_meta, summarize

video      = fetch_video_meta("https://youtu.be/VIDEO_ID_XX")
transcript = fetch_transcript(video.video_id)
summary    = summarize(transcript, video, OpenAIBackend(), detail="deep")
print(summary.tldr)
for point in summary.points:
    print("-", point)
```

`AnthropicBackend` and `OllamaBackend` are drop-in replacements for
`OpenAIBackend`. The digest pieces (`load_channels`, `build_digest`,
`to_markdown`) are exported too.

### How it works

The core is a domain-neutral single-video engine: identify the video, fetch its
transcript, and summarize it through a pluggable LLM backend (map-reducing long
transcripts so nothing is truncated). The digest layer is built on top — feeds,
an importance score, and a Markdown renderer — and stays domain-neutral too; the
summary adapts to whatever the transcript actually contains, which is why the
same tool works for a market recap, a lecture, or a match report.

### Limits

- **A video with no transcript can't be summarized.** tubeless reads captions; it
  does **not** transcribe audio itself (no speech-to-text fallback). Videos with
  captions disabled, or none in the requested languages, are skipped (in a digest
  they're listed under "channels/videos not read").
- **Auto-generated captions are noisy.** When the caption track is auto-generated,
  tubeless warns the model to hedge uncertain names and numbers rather than state
  them as fact — but a garbled caption can still produce a garbled point.
- **Summaries cost money on the cloud backends** (see [Backends](#backends-openai-claude-ollama)).
  Use Ollama to run free and offline.
- **The importance score is the model's judgment**, not a hard metric — it ranks
  the digest, it isn't ground truth.

### License

MIT — see [LICENSE](LICENSE).

---

## 한국어

유튜브 영상의 자막을 받아 LLM으로 요약합니다 — 명령줄에서 영상 한 개를, 또는
구독하는 채널·시리즈의 하루치 다이제스트를. OpenAI·Claude, 또는 Ollama로 로컬
모델까지 씁니다.

- [설치](#설치) — macOS, Linux, Windows
- [키 설정](#키-설정-tubelessconfigenv) (`~/.tubeless/config.env`)
- [백엔드: OpenAI, Claude, Ollama — 그리고 결제](#백엔드-openai-claude-ollama)
- [영상 한 개 요약](#영상-한-개-요약) (`--detail` / `--points` / `--backend` / `--model` / `--lang`)
- [데일리 다이제스트](#데일리-다이제스트)
- [cron으로 매일 자동 실행 (Linux)](#cron으로-매일-자동-실행-linux)
- [Claude Code에서 쓰기](#claude-code에서-쓰기-tubeless-스킬) (`/tubeless`)
- [파이썬 라이브러리로 쓰기](#파이썬-라이브러리로-쓰기)
- [한계](#한계)

### 설치

**Python 3.11 이상**이 필요합니다(`python3 --version`으로 확인). [pipx](https://pipx.pypa.io)로
설치하면 `tubeless` 명령이 격리된 환경에 깔려 다른 파이썬 패키지와 충돌하지
않습니다.

tubeless는 GitHub에서 바로 설치합니다. 본인 OS 블록을 위에서 아래로 그대로
따라가면 됩니다 — 마지막 줄까지 실행하면 `tubeless` 명령을 쓸 수 있습니다.

**macOS**
```sh
brew install python pipx            # 이미 있으면 생략
pipx ensurepath                     # pipx 실행 경로를 PATH에 추가(끝나면 새 터미널 열기)
pipx install git+https://github.com/seokhoonj/tubeless.git
```

**Linux** (데비안/우분투; 다른 배포판은 각자 패키지매니저 사용)
```sh
sudo apt update && sudo apt install -y python3 python3-pip pipx
pipx ensurepath                     # 끝나면 새 터미널 열기
pipx install git+https://github.com/seokhoonj/tubeless.git
```

**Windows** (PowerShell)
```powershell
# 1. https://python.org 에서 Python 3.11+ 설치 — "Add python.exe to PATH" 체크.
py -m pip install --user pipx
py -m pipx ensurepath               # 끝나면 PowerShell 닫았다 다시 열기
py -m pipx install git+https://github.com/seokhoonj/tubeless.git
```

Claude 백엔드까지 함께 설치하려면 `anthropic` 추가옵션:
```sh
pipx install "git+https://github.com/seokhoonj/tubeless.git#egg=tubeless[anthropic]"
```

동작 확인:
```sh
tubeless --help
```

> **pipx가 없어도** `pip install git+https://github.com/seokhoonj/tubeless.git`로
> 설치됩니다(가상환경 안에서 권장). pipx는 CLI를 격리하려는 권장일 뿐입니다.
> 나중에 PyPI에 올라가면 `pipx install tubeless`로도 됩니다.

### 키 설정 (`~/.tubeless/config.env`)

OpenAI·Claude는 API 키가 필요합니다. Ollama는 필요 없습니다(로컬 실행). 키는
비밀값이라, tubeless는 저장소가 아니라 홈 디렉터리의 파일에서 읽고 그 값을
화면·로그에 절대 남기지 않습니다.

`~/.tubeless/config.env`에 `KEY=VALUE`를 한 줄씩 적습니다:

```sh
mkdir -p ~/.tubeless
cat > ~/.tubeless/config.env <<'EOF'
# 실제로 쓰는 백엔드만 채우세요.
OPENAI_SECRET_KEY=sk-...
# ANTHROPIC_SECRET_KEY=sk-ant-...
# GEMINI_SECRET_KEY=...
EOF
```

- **OpenAI 키 발급:** [platform.openai.com](https://platform.openai.com) → API keys.
- **Claude 키 발급:** [console.anthropic.com](https://console.anthropic.com) → API keys.
- **Gemini 키 발급:** [aistudio.google.com](https://aistudio.google.com) → Get API key. (파일에 `GEMINI_SECRET_KEY=...` 추가.)

머신에 이미 표준 이름 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY`가
있으면 tubeless가 폴백으로 인정하므로 파일을 건너뛰어도 됩니다. 둘 다 있으면
tubeless 이름(`<VENDOR>_SECRET_KEY`)이 우선합니다.

### 백엔드: OpenAI, Claude, Ollama

`--backend`로 모델을 고릅니다. 기본은 OpenAI입니다.

| 백엔드 | 플래그 | 필요한 키 | 기본 모델 | 실행 위치 | 비용 |
|---|---|---|---|---|---|
| **OpenAI** | `--backend openai` (기본) | `OPENAI_SECRET_KEY` | `gpt-4o-mini` | OpenAI 서버 | 유료, 선불 크레딧 |
| **Claude** | `--backend anthropic` | `ANTHROPIC_SECRET_KEY` | `claude-haiku-4-5` | Anthropic 서버 | 유료, 선불 크레딧 |
| **Gemini** | `--backend gemini` | `GEMINI_SECRET_KEY` | `gemini-flash-lite-latest` | Google 서버 | 무료 티어 + 종량제 |
| **Ollama** | `--backend ollama` | 불필요 | `llama3.1` | 내 컴퓨터 | 무료 |

**어디서, 얼마부터 결제하나.** 두 클라우드 벤더 모두 **선불(prepaid)**입니다 —
크레딧을 미리 사두면 요약할 때마다 그 잔액에서 차감됩니다.

- **OpenAI** — [platform.openai.com](https://platform.openai.com) → Settings →
  Billing에서 크레딧 구매. **최소 충전 $5.** 모델별 요금은
  [요금 페이지](https://openai.com/api/pricing/) 참고. 기본 `gpt-4o-mini`가 가장 싼
  등급이라 영상 하나 요약에 1센트도 안 들고, **$5**면 *수천 편*을 커버합니다.
- **Claude** — [console.anthropic.com](https://console.anthropic.com) → Billing →
  Buy credits에서 구매. **최소 충전도 $5.** 기본 `claude-haiku-4-5`는 Anthropic에서
  가장 싼 모델로(입력 100만 토큰당 약 $1 / 출력 $5), 영상 하나에 1~2센트라 **$5**로
  수백 편을 커버합니다. 전체 요금: [Claude 요금](https://platform.claude.com/docs/en/about-claude/pricing).
- **Gemini** — [aistudio.google.com](https://aistudio.google.com)에서 키 발급.
  Gemini는 **진짜 무료 티어**(요율 제한)가 있어 결제 없이도 tubeless를 시험해볼 수
  있습니다. 사용량이 많으면 AI Studio에서 종량제 결제를 켭니다. 기본
  기본 `gemini-flash-lite-latest`는 저렴하고 빠르며 무료 티어에서 돕니다.
  (`-latest` 별칭입니다 — `gemini-2.5-flash` 같은 고정 이름은 새로 만든 키에서
  404가 날 수 있어 별칭을 기본으로 씁니다. `--model`로 바꿀 수 있습니다.)
  요금: [Gemini 요금](https://ai.google.dev/pricing).
- **Ollama** — 키도 청구서도 없습니다. 모델이 내 컴퓨터에서 돕니다(아래 참고).
  무료·오프라인이지만 요약 품질은 로컬 모델 성능만큼입니다.

OpenAI·Claude 크레딧은 구매 **1년 뒤 만료**되고 환불되지 않으니 조금씩
충전하세요.

**뭘 고를까?** 기본 OpenAI `gpt-4o-mini`가 가장 저렴한 클라우드 올라운더입니다.
Claude는 불확실한 숫자를 지어내기보다 *유보*하는 경향이라 노이즈 많은 자동자막에
더 안전합니다. Gemini는 무료 티어라 결제 없이 클라우드 모델을 가장 쉽게 시험해볼
수 있습니다. Ollama는 비공개·오프라인·무료 옵션입니다.

**Ollama 쓰기(로컬, 무료).** 서버를 설치하고 모델을 받은 뒤 tubeless를 가리키게
합니다:
```sh
# macOS:   brew install ollama          (또는 https://ollama.com 에서 다운로드)
# Linux:   curl -fsSL https://ollama.com/install.sh | sh
# Windows: https://ollama.com 에서 설치 파일 다운로드
ollama pull llama3.1
tubeless VIDEO_ID_XX --backend ollama --model llama3.1
```
기본이 아닌 호스트는 `OLLAMA_HOST` 환경변수로 지정합니다
(예: `OLLAMA_HOST=http://192.168.0.10:11434`).

**Ollama 실사용 — 품질은 머신·모델에 좌우됩니다.** 실제 테스트 환경
(Ryzen 3700X / 128GB RAM / RTX 2070 SUPER, VRAM 8GB): `llama3.1`(8B) 같은 소형
모델은 빠르고 대략적인 요지엔 무난했지만, 14B급인 **Qwen 14B는 요약 성능이 그리
좋지 못했습니다** — 14B는 8GB VRAM에 일부만 올라가 CPU/RAM으로 넘치며 느려지고,
숫자·구조 보존이 클라우드 백엔드보다 눈에 띄게 약했습니다. Ollama는 무료·오프
라인·비공개 옵션으로 보고, OpenAI·Claude의 품질 대체재로는 보지 마세요. 요약
품질이 중요하면 클라우드 백엔드를 쓰는 게 좋습니다.

### 영상 한 개 요약

```sh
tubeless "https://www.youtube.com/watch?v=VIDEO_ID_XX"
tubeless VIDEO_ID_XX --detail deep --lang ko --points 20
```

전체 URL(`watch?v=`, `youtu.be/`, `/shorts/`, `/embed/`, `/live/`)이나 11자리
영상 id만 줘도 됩니다.

| 옵션 | 뜻 | 기본값 |
|---|---|---|
| `--detail brief\|normal\|deep` | 요약 깊이. **`deep`은 모든 숫자를 보존** — 아래 참고. | `normal` |
| `--points N` | 핵심 포인트 최대 개수. `--detail` 기본값(brief 5 / normal 8 / deep 14)을 덮어씀. | 깊이별 |
| `--backend openai\|anthropic\|gemini\|ollama` | 어떤 LLM을 쓸지. | `openai` |
| `--model NAME` | 모델 id. 미지정 시 백엔드의 소형·저가 모델. | 백엔드별 |
| `--lang CODE` | 요약 언어. 언어 교차 가능 — 영어 영상을 한국어로 요약. | `ko` |
| `--json` | 텍스트 대신 기계용 JSON 출력. | 꺼짐 |

**숫자를 살려주나?** 숫자가 핵심인 영상(증시·실적·스포츠 스코어·스펙표)에 바로
`--detail deep`이 그 용도입니다. `deep`에서는 tubeless가 모델에게 **화자가 말한
모든 수치를 보존**하라고 지시합니다 — 지수 등락·금리·가격·비율, 숫자가 붙은 고유
명사 하나하나를, 적용 시점(연/분기/날짜)과 함께, 한 문장으로 뭉개지 말고 항목별로.
기본 `normal`과 간결한 `brief`는 이렇게 하지 **않습니다** — 읽기 좋은 요점 위주라
일부 숫자를 뺄 수 있습니다. 그래서:

```sh
# 모든 수치가 중요한 시황 정리:
tubeless VIDEO_ID_XX --detail deep

# ...항목이 많으면 포인트 상한을 올려서:
tubeless VIDEO_ID_XX --detail deep --points 30
```

### 데일리 다이제스트

영상 한 개 대신, tubeless는 여러 채널을 지켜보며 하루에 마크다운 한 파일을,
중요한 영상부터 순서대로 만들 수 있습니다.

구독할 채널·시리즈를 `~/.tubeless/channels.toml`에 적습니다:

```toml
[[channel]]
source = "@examplechannel"      # 핸들 · 채널URL · 'UC...' id · 재생목록
label  = "예시 채널"
detail = "deep"

[[channel]]
# 재생목록은 채널을 한 시리즈로 좁히고, title_includes는 제목에 나열된 단어를
# 모두 포함하는 영상만 남깁니다(예: 특정 진행자 회차만).
source         = "PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
label          = "어떤 데일리 쇼"
detail         = "deep"
title_includes = ["진행자이름"]
```

그리고:

```sh
tubeless digest              # ~/.tubeless/digests/YYYY-MM-DD.md 로 저장
tubeless digest --dry-run    # 저장 없이 화면에만 출력
```

매 실행마다 각 채널의 새 영상을 유튜브 공개 RSS(키 불필요)로 찾아 요약·중요도
채점하고, 하루에 마크다운 한 파일을 중요도순으로 씁니다. 처리한 영상은 JSON
"seen" 세트가 기억하므로 다시 돌리거나(cron으로 매일 돌려도) 같은 영상을 두 번
요약하지 않습니다.

| digest 옵션 | 뜻 | 기본값 |
|---|---|---|
| `--only TEXT` | 라벨에 이 텍스트가 든 채널만 실행. | 전체 |
| `--limit N` | 채널당 확인할 최근 업로드 최대 개수. | `5` |
| `--dry-run` | 저장/상태 갱신 없이 화면 출력만. | 꺼짐 |
| `--channels PATH` | 채널 TOML 파일. | `~/.tubeless/channels.toml` |
| `--state PATH` | "이미 본" 상태 파일. | `~/.tubeless/state.json` |
| `--out DIR` | 날짜별 다이제스트 파일 디렉터리. | `~/.tubeless/digests/` |
| `--backend` / `--model` / `--lang` | 단건 요약과 동일. | |

### cron으로 매일 자동 실행 (Linux)

cron은 명령을 정해진 시각에 실행합니다. 매일 밤 22:00에 다이제스트를 만들려면:

1. crontab을 엽니다:
   ```sh
   crontab -e
   ```
2. 한 줄 추가합니다(시각 조정 — `0 22 * * *`은 매일 22:00). cron이 찾을 수 있게
   `tubeless`의 전체 경로를 씁니다. 경로는 `which tubeless`로 확인:
   ```cron
   0 22 * * * /home/you/.local/bin/tubeless digest >> /home/you/.tubeless/digest.log 2>&1
   ```
   `>> ...digest.log 2>&1`은 정상 출력과 에러를 모두 로그 파일에 덧붙여, 무슨 일이
   있었는지 볼 수 있게 합니다.
3. 저장하고 나옵니다. `crontab -l`로 등록됐는지 확인.

다이제스트가 "seen" 세트를 유지하므로 매일 실행해도 진짜 새 영상만 요약합니다.
결과는 매일 아침 `~/.tubeless/digests/`에서 읽으면 됩니다.

> **macOS**도 cron이 있지만 `launchd` / 캘린더로 트리거하는 Automator가 더
> 네이티브합니다. **Windows**: 작업 스케줄러로 `tubeless digest`를 실행하세요.

### Claude Code에서 쓰기 (`/tubeless` 스킬)

[Claude Code](https://claude.com/claude-code)를 쓴다면, CLI를 로컬 스킬로 감싸서
`/tubeless <url>` 한 번으로 에디터를 떠나지 않고 영상을 요약할 수 있습니다. 스킬은
위에서 설치한 `tubeless` 명령을 그대로 호출할 뿐입니다.

`~/.claude/skills/tubeless/SKILL.md`를 만듭니다(이 에이전트 파일들은 내 머신에만
남는 로컬 파일이며 이 저장소에 포함되지 않습니다):

```markdown
---
name: tubeless
description: 유튜브 영상을 요약. 유튜브 URL이나 "이 영상 요약해줘"에 반응.
---

사용자가 준 URL로 설치된 `tubeless` CLI를 실행하고 결과를 보여준다:

    tubeless "<url>" --detail deep --lang ko

Claude는 `--backend anthropic`, 로컬 모델은 `--backend ollama`를 붙인다.
TLDR과 핵심 포인트를 사용자에게 돌려준다.
```

그러면 Claude Code에서 `/tubeless https://youtu.be/VIDEO_ID_XX`. 다른 에이전트
CLI(Codex `AGENTS.md`, Gemini `GEMINI.md`)도 같은 방식으로 `tubeless` 명령을
가리키게 하면 됩니다.

### 파이썬 라이브러리로 쓰기

```python
from tubeless import OpenAIBackend, fetch_transcript, fetch_video_meta, summarize

video      = fetch_video_meta("https://youtu.be/VIDEO_ID_XX")
transcript = fetch_transcript(video.video_id)
summary    = summarize(transcript, video, OpenAIBackend(), detail="deep")
print(summary.tldr)
for point in summary.points:
    print("-", point)
```

`AnthropicBackend`·`OllamaBackend`는 `OpenAIBackend`와 그대로 바꿔 끼울 수
있습니다. 다이제스트 조각(`load_channels`, `build_digest`, `to_markdown`)도
export되어 있습니다.

### 동작 방식

코어는 도메인 중립 단건 엔진입니다 — 영상을 식별하고, 자막을 받아, 교체 가능한
LLM 백엔드로 요약합니다(긴 자막은 map-reduce로 나눠 아무것도 잘리지 않게). 다이
제스트 층은 그 위에 피드·중요도 점수·마크다운 렌더러를 얹은 것으로, 역시 도메인
중립입니다. 요약은 자막에 실제로 담긴 내용에 맞춰 적응하며, 그래서 같은 도구가
시황 정리에도, 강의에도, 경기 리포트에도 통합니다.

### 한계

- **자막이 없는 영상은 요약할 수 없습니다.** tubeless는 자막을 읽을 뿐, 음성을
  직접 받아쓰지 **않습니다**(STT 폴백 없음). 자막이 꺼졌거나 요청 언어에 없는
  영상은 건너뜁니다(다이제스트에서는 "읽지 못한 채널/영상"으로 표시).
- **자동 생성 자막은 노이즈가 있습니다.** 자막이 자동 생성일 때 tubeless는 모델
  에게 불확실한 이름·숫자를 단정하지 말고 유보하라고 경고하지만, 뭉개진 자막은
  여전히 뭉개진 포인트를 낳을 수 있습니다.
- **클라우드 백엔드 요약은 유료입니다**([백엔드](#백엔드-openai-claude-ollama) 참고).
  무료·오프라인으로 쓰려면 Ollama를 쓰세요.
- **중요도 점수는 모델의 판단**이지 확정 지표가 아닙니다 — 다이제스트를 정렬할
  뿐 정답은 아닙니다.

### 라이선스

MIT — [LICENSE](LICENSE) 참고.
