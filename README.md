# tubeless

Fetch a YouTube video's transcript and summarize it with an LLM, from the
command line or as a library.

## Install

```sh
pip install -e .
```

## Usage

Set `OPENAI_API_KEY` in the environment, then:

```sh
tubeless "https://www.youtube.com/watch?v=VIDEO_ID_XX"
tubeless VIDEO_ID_XX --lang en --points 5 --json
```

`--lang` picks the summary language (default `ko`), `--model` the OpenAI model
(default `gpt-4o-mini`), `--points` the maximum number of key points, and
`--json` switches to machine-readable output.

As a library:

```python
from tubeless import OpenAIBackend, fetch_transcript, fetch_video_meta, summarize

video      = fetch_video_meta("https://youtu.be/VIDEO_ID_XX")
transcript = fetch_transcript(video.video_id)
summary    = summarize(transcript, video, OpenAIBackend())
print(summary.tldr)
```

## Scope

This is the single-video engine, deliberately domain-neutral. A daily-digest
layer -- watching chosen channels and surfacing only what a config marks
important -- is a later layer built on top of it.
