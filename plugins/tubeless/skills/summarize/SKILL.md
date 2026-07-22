---
name: summarize
description: "유튜브 영상 URL을 받아 자막을 뽑아 요약(TLDR + 핵심 포인트)한다. 자체 로직 없이 tubeless 패키지의 CLI(`tubeless`)를 호출하며, 결과를 사용자에게 보여준다. 백엔드는 gemini(무료)/openai/claude/ollama(로컬) 중 선택. Trigger phrases: 유튜브 요약, 영상 요약, 이 영상 요약해줘, 유튜브 정리해줘, summarize this video, youtube summary, 이 링크 요약."
---

# tubeless — 유튜브 영상 요약

유튜브 영상 URL을 받아 **자막 → 요약**을 돌린다. 요약 로직은 이 skill이 아니라
tubeless 패키지(PyPI)에 있고, 이 skill은 그 CLI를 호출해 결과를 사용자에게 전달하는
얇은 래퍼다. 자막이 없는 영상, LLM 키/크레딧 문제 등은 CLI가 한 줄 에러로 돌려주므로,
스택트레이스를 사용자에게 던지지 말고 그 메시지를 그대로 전한다.

## 사전 준비

이 플러그인은 `tubeless` CLI를 부른다. 먼저 설치돼 있어야 한다:

```
pipx install tubeless        # 또는: pip install tubeless
```

설치하면 `tubeless` 명령이 PATH에 올라간다. **키는 tubeless가 스스로 찾는다** --
`~/.tubeless/config.env`(또는 환경변수)의 `OPENAI_API_KEY` / `CLAUDE_API_KEY` /
`GEMINI_API_KEY`를 백엔드에 맞춰 읽으므로, 이 skill이 키를 꺼내 넘길 필요가 없다.
키 값은 어디에도 출력하지 말 것.

## 실행

PATH의 `tubeless`를 `summarize` 서브커맨드로 부른다:

```
tubeless summarize "<URL>" [옵션]
```

(서브커맨드 없이 `tubeless "<URL>"`도 summarize로 동작하는 단축이 있지만, 정석은
`tubeless summarize`다.)

옵션 (정확한 기본값·모델은 `tubeless summarize --help`가 정본):
- `--backend claude|openai|gemini|ollama` — LLM 벤더. 미지정 시 openai(또는
  `config.env`의 `TUBELESS_BACKEND`). gemini는 무료 티어, ollama는 로컬(키 불필요).
  각 백엔드의 기본 모델은 CLI가 정한다 — 여기 숫자/모델명을 적어두지 않는다.
- `--model <id>` — 백엔드의 기본 모델을 덮어씀 (예: `--backend gemini --model gemini-2.5-pro`).
- `--lang <code>` — 요약 언어 (미지정 시 CLI 기본).
- `--detail brief|normal|deep` — 요약 깊이. "자세히/상세히/길게"라고 하면 `deep`, "짧게/간단히"면 `brief`.
- `--max-points N` — 핵심 포인트 최대 개수 (미지정 시 --detail에 따른 CLI 기본; 지정하면 그 값).
- `--json` — 사람이 읽는 텍스트 대신 JSON.

실행하면 CLI가 stderr에 한 줄짜리 설정 헤더(`tubeless: backend=... model=... detail=... lang=...`)를
찍는다. 어떤 모델로 돌았는지 확인용이니, 필요하면 사용자에게 같이 전해도 된다(특히
작은 모델이 낯선 고유명사를 잘못 바꿔 적을 때 원인 파악에 쓸모 있다).

## 절차

1. **URL 확보.** 사용자 메시지에서 유튜브 URL(또는 11자 video id)을 찾는다. 없으면
   하나 물어본다. 여러 개면 하나씩 순서대로 처리.

2. **실행.** 그냥 CLI를 부른다 — 키는 tubeless가 `~/.tubeless/config.env`에서 찾는다.

   ```bash
   tubeless summarize "<URL>"
   ```

   기본(openai)이 아닌 백엔드로 돌릴 때만 `--backend`를 붙인다. 예: 무료로 돌리려면
   `--backend gemini`(`config.env`에 `GEMINI_API_KEY` 필요), Claude면
   `--backend claude`(`CLAUDE_API_KEY` 필요), 완전 로컬이면 `--backend ollama`(키 불필요,
   `ollama` 서버가 떠 있어야 함).

3. **실행 후 결과 전달.** CLI stdout(제목 · URL · TLDR · 불릿)을 사용자에게 그대로
   보여준다. 길면 핵심만 추려도 되지만, TLDR과 포인트는 빠뜨리지 않는다.

4. **에러 처리.** CLI가 비정상 종료(`exit 1`)하면 stderr의 `tubeless: <메시지>` 한 줄을
   그대로 전한다. 흔한 것:
   - `command not found: tubeless` → 패키지 미설치. `pipx install tubeless`(또는
     `pip install tubeless`) 안내.
   - `insufficient_quota` / `429` → LLM 크레딧 소진. 해당 벤더 콘솔(OpenAI /
     Claude / Google AI Studio) 충전 안내.
   - `TranscriptUnavailable` → 자막이 없는 영상(자막 꺼둠/미생성). LLM 문제가
     아니라 영상 자체에 자막이 없는 것.
   - `transcript fetch blocked` (`TranscriptFetchBlocked`) → YouTube가 이 실행의
     IP를 일시 차단/속도제한한 것. 자막이 없는 게 아니라 일시적 차단이므로,
     잠시 후 재시도하거나 다른 네트워크에서 실행. 영상 자체 문제가 아니다.
   - `no ... API key` → `~/.tubeless/config.env`에 해당 백엔드 키
     (`OPENAI_API_KEY` / `CLAUDE_API_KEY` / `GEMINI_API_KEY`)가 있는지 확인.

## 이 skill이 하지 않는 것

- 자막 추출·요약 로직을 여기서 다시 구현하지 않는다(패키지가 함). 항상 CLI를 부른다.
- API 키 값을 출력·로그·요약에 절대 남기지 않는다.
- 자막이 없는 영상을 억지로 요약하려 하지 않는다(자막 없으면 요약 불가; STT 폴백은
  아직 없음).

## 참고 — 다이제스트

관심 채널을 매일 요약해 하나의 다이제스트로 묶는 `tubeless digest`는 패키지의 다음
층이다. 이 skill은 단건 URL 요약만 담당한다. 다이제스트는 `tubeless digest --help`와
프로젝트 README를 참고.
