# tubeless

Fetch a YouTube video's transcript and summarize it with an LLM — one video from
the command line, or a daily digest of the channels and series you follow.
Works with OpenAI, Claude, or a local model via Ollama.

**[English](#english) · [한국어](#한국어)**

---

## English

### Install

Requires Python 3.11+. Installing with [pipx](https://pipx.pypa.io) keeps the CLI
in its own isolated environment.

**macOS**
```sh
brew install python pipx      # if you don't have them
pipx install tubeless         # or: pip install tubeless
```

**Linux**
```sh
sudo apt install python3 python3-pip pipx   # Debian/Ubuntu; use your distro's manager
pipx install tubeless                       # or: pip install tubeless
```

**Windows** (PowerShell)
```powershell
# install Python 3 from https://python.org (check "Add python.exe to PATH")
py -m pip install --user pipx
py -m pipx install tubeless    # or: py -m pip install tubeless
```

Add the Claude backend with the `anthropic` extra: `pipx install "tubeless[anthropic]"`.

### Backends

Pick the LLM with `--backend`. The default is OpenAI.

| backend | flag | key | notes |
|---|---|---|---|
| OpenAI | `--backend openai` (default) | `OPENAI_SECRET_KEY` | default model `gpt-4o-mini` |
| Claude | `--backend anthropic` | `ANTHROPIC_SECRET_KEY` | needs `tubeless[anthropic]`; default `claude-haiku-4-5` |
| Ollama (local) | `--backend ollama` | none | runs on your machine, free/offline; default model `llama3.1` |

Cloud keys go in `~/.tubeless/config.env` (or the environment; the standard
`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` names are honored as a fallback). The key
value is never printed or logged.

```sh
# ~/.tubeless/config.env
OPENAI_SECRET_KEY=sk-...
# ANTHROPIC_SECRET_KEY=sk-ant-...
```

**Ollama** needs no key — just a running server and a pulled model:

```sh
# macOS:  brew install ollama          (or download from https://ollama.com)
# Linux:  curl -fsSL https://ollama.com/install.sh | sh
# Windows: download the installer from https://ollama.com
ollama pull llama3.1
tubeless VIDEO_ID_XX --backend ollama --model llama3.1
```

Point at a non-default host with the `OLLAMA_HOST` environment variable
(e.g. `OLLAMA_HOST=http://192.168.0.10:11434`).

### Summarize one video

```sh
tubeless "https://www.youtube.com/watch?v=VIDEO_ID_XX"
tubeless VIDEO_ID_XX --detail deep --lang en --points 20
```

| option | meaning |
|---|---|
| `--detail brief\|normal\|deep` | how full the summary is (default `normal`). `deep` preserves every stated figure with its period, and item-by-item lists. |
| `--lang` | summary language (default `ko`); works across languages — an English video can be summarized in Korean. |
| `--points N` | max key points; overrides the per-detail default (5 / 8 / 14). |
| `--backend openai\|anthropic\|ollama` | LLM vendor (default `openai`). |
| `--model` | model id; defaults to the backend's small-tier model. |
| `--json` | machine-readable output instead of text. |

### Daily digest

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

Then:

```sh
tubeless digest              # write ~/.tubeless/digests/YYYY-MM-DD.md
tubeless digest --dry-run    # print it instead, and don't record state
```

Each run finds every channel's new uploads via YouTube's public RSS feed (no API
key), summarizes and importance-scores them, and writes one Markdown file per day
ranked most-important first. A JSON seen-set skips videos already handled, so it
is safe to run daily from cron:

```cron
0 22 * * * tubeless digest >> ~/.tubeless/digest.log 2>&1
```

### As a library

```python
from tubeless import OpenAIBackend, fetch_transcript, fetch_video_meta, summarize

video      = fetch_video_meta("https://youtu.be/VIDEO_ID_XX")
transcript = fetch_transcript(video.video_id)
summary    = summarize(transcript, video, OpenAIBackend(), detail="deep")
print(summary.tldr)
```

### How it works

The core is a domain-neutral single-video engine: identify the video, fetch its
transcript, and summarize it through a pluggable LLM backend (map-reducing long
transcripts). The digest layer is built on top — feeds, an importance score, and
a Markdown renderer — and stays domain-neutral too; the summary adapts to
whatever the transcript actually contains.

### License

MIT.

---

## 한국어

유튜브 영상의 자막을 받아 LLM으로 요약합니다 — 명령줄에서 영상 한 개를, 또는
구독하는 채널·시리즈의 하루치 다이제스트를. OpenAI·Claude, 또는 Ollama로 로컬
모델까지 씁니다.

### 설치

Python 3.11 이상이 필요합니다. [pipx](https://pipx.pypa.io)로 설치하면 CLI가 격리된
환경에 깔립니다.

**macOS**
```sh
brew install python pipx      # 없으면
pipx install tubeless         # 또는: pip install tubeless
```

**Linux**
```sh
sudo apt install python3 python3-pip pipx   # 데비안/우분투; 배포판 패키지매니저 사용
pipx install tubeless                       # 또는: pip install tubeless
```

**Windows** (PowerShell)
```powershell
# https://python.org 에서 Python 3 설치 ("Add python.exe to PATH" 체크)
py -m pip install --user pipx
py -m pipx install tubeless    # 또는: py -m pip install tubeless
```

Claude 백엔드는 `anthropic` 추가옵션으로: `pipx install "tubeless[anthropic]"`.

### 백엔드

`--backend`로 LLM을 고릅니다. 기본은 OpenAI입니다.

| 백엔드 | 플래그 | 키 | 비고 |
|---|---|---|---|
| OpenAI | `--backend openai` (기본) | `OPENAI_SECRET_KEY` | 기본 모델 `gpt-4o-mini` |
| Claude | `--backend anthropic` | `ANTHROPIC_SECRET_KEY` | `tubeless[anthropic]` 필요; 기본 `claude-haiku-4-5` |
| Ollama (로컬) | `--backend ollama` | 불필요 | 내 컴퓨터에서 실행, 무료·오프라인; 기본 모델 `llama3.1` |

클라우드 키는 `~/.tubeless/config.env`(또는 환경변수; 표준 `OPENAI_API_KEY` /
`ANTHROPIC_API_KEY` 이름도 폴백으로 인정)에 넣습니다. 키 값은 출력·로그에 남지
않습니다.

```sh
# ~/.tubeless/config.env
OPENAI_SECRET_KEY=sk-...
# ANTHROPIC_SECRET_KEY=sk-ant-...
```

**Ollama**는 키가 필요 없습니다 — 서버가 떠 있고 모델을 받아두면 됩니다:

```sh
# macOS:  brew install ollama          (또는 https://ollama.com 에서 다운로드)
# Linux:  curl -fsSL https://ollama.com/install.sh | sh
# Windows: https://ollama.com 에서 설치 파일 다운로드
ollama pull llama3.1
tubeless VIDEO_ID_XX --backend ollama --model llama3.1
```

기본이 아닌 호스트는 `OLLAMA_HOST` 환경변수로 지정합니다
(예: `OLLAMA_HOST=http://192.168.0.10:11434`).

### 영상 한 개 요약

```sh
tubeless "https://www.youtube.com/watch?v=VIDEO_ID_XX"
tubeless VIDEO_ID_XX --detail deep --lang ko --points 20
```

| 옵션 | 뜻 |
|---|---|
| `--detail brief\|normal\|deep` | 요약 깊이 (기본 `normal`). `deep`은 화자가 말한 모든 수치를 기간과 함께, 항목별 나열까지 보존합니다. |
| `--lang` | 요약 언어 (기본 `ko`). 언어 교차 가능 — 영어 영상을 한국어로 요약. |
| `--points N` | 핵심 포인트 최대 개수. `--detail` 기본값(5 / 8 / 14)을 덮어씀. |
| `--backend openai\|anthropic\|ollama` | LLM 벤더 (기본 `openai`). |
| `--model` | 모델 id. 미지정 시 백엔드의 소형 모델. |
| `--json` | 텍스트 대신 기계용 JSON 출력. |

### 데일리 다이제스트

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
seen-set이 기억해 건너뛰므로 cron으로 매일 돌려도 안전합니다:

```cron
0 22 * * * tubeless digest >> ~/.tubeless/digest.log 2>&1
```

### 라이브러리로

```python
from tubeless import OpenAIBackend, fetch_transcript, fetch_video_meta, summarize

video      = fetch_video_meta("https://youtu.be/VIDEO_ID_XX")
transcript = fetch_transcript(video.video_id)
summary    = summarize(transcript, video, OpenAIBackend(), detail="deep")
print(summary.tldr)
```

### 동작 방식

코어는 도메인 중립 단건 엔진입니다 — 영상을 식별하고, 자막을 받아, 교체 가능한
LLM 백엔드로 요약합니다(긴 자막은 map-reduce). 다이제스트 층은 그 위에 피드·
중요도 점수·마크다운 렌더러를 얹은 것으로, 역시 도메인 중립입니다. 요약은
자막에 실제로 담긴 내용에 맞춰 적응합니다.

### 라이선스

MIT.
