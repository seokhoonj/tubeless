"""tubeless: fetch a YouTube video's transcript and summarize it with an LLM.

The package only defines names at import time; all network and LLM work
happens inside the functions the caller invokes.
"""

from tubeless.channels import Channel, load_channels
from tubeless.digest import (
    Digest,
    Entry,
    Skip,
    SummarizedVideos,
    curate_summaries,
    summarize_videos,
)
from tubeless.discover import DEFAULT_SCAN, fetch_recent_videos
from tubeless.errors import (
    ConfigError,
    CorpusError,
    FeedError,
    InvalidVideoURL,
    LLMError,
    ScheduleError,
    TranscriptFetchBlocked,
    TranscriptUnavailable,
    TubelessError,
)
from tubeless.importance import Importance, ImportanceTier
from tubeless.llm import (
    BACKENDS,
    ClaudeBackend,
    GeminiBackend,
    LLMBackend,
    OllamaBackend,
    OpenAIBackend,
    make_backend,
)
from tubeless.render import render_markdown
from tubeless.source import Video, extract_video_id, fetch_video
from tubeless.store import FileStore, Store, corpus_root, latest_per_video
from tubeless.summary import (
    DETAIL_LEVELS,
    DetailLevel,
    Summary,
    summarize_transcript,
)
from tubeless.synthesis import Synthesis
from tubeless.transcript import Transcript, TranscriptSegment, fetch_transcript

__all__ = [
    "BACKENDS",
    "Channel",
    "ClaudeBackend",
    "ConfigError",
    "CorpusError",
    "DEFAULT_SCAN",
    "DETAIL_LEVELS",
    "DetailLevel",
    "Digest",
    "Entry",
    "FeedError",
    "FileStore",
    "GeminiBackend",
    "Importance",
    "ImportanceTier",
    "InvalidVideoURL",
    "LLMBackend",
    "LLMError",
    "OllamaBackend",
    "OpenAIBackend",
    "ScheduleError",
    "Skip",
    "Store",
    "SummarizedVideos",
    "Summary",
    "Synthesis",
    "Transcript",
    "TranscriptFetchBlocked",
    "TranscriptSegment",
    "TranscriptUnavailable",
    "TubelessError",
    "Video",
    "corpus_root",
    "curate_summaries",
    "extract_video_id",
    "fetch_recent_videos",
    "fetch_transcript",
    "fetch_video",
    "latest_per_video",
    "load_channels",
    "make_backend",
    "render_markdown",
    "summarize_transcript",
    "summarize_videos",
]
