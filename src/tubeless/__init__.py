"""tubeless: fetch a YouTube video's transcript and summarize it with an LLM.

The package only defines names at import time; all network and LLM work
happens inside the functions the caller invokes.
"""

from tubeless.channels import Channel, load_channels
from tubeless.digest import (
    Digest,
    DigestRun,
    Entry,
    Skip,
    SummarizeVideosResult,
    curate,
    recompute,
    run_digest,
    summarize_videos,
)
from tubeless.discover import DEFAULT_SCAN, discover
from tubeless.errors import (
    ConfigError,
    CorpusError,
    FeedError,
    InvalidVideoURL,
    LLMError,
    TranscriptFetchBlocked,
    TranscriptUnavailable,
    TubelessError,
)
from tubeless.feed import (
    Upload,
    fetch_channel_uploads,
    fetch_playlist_uploads,
    fetch_uploads,
    resolve_channel_id,
)
from tubeless.importance import Importance, ImportanceTier, score
from tubeless.llm import (
    BACKENDS,
    ClaudeBackend,
    GeminiBackend,
    LLMBackend,
    OllamaBackend,
    OpenAIBackend,
    make_backend,
)
from tubeless.render import to_markdown
from tubeless.source import Video, fetch_video, parse_video_id
from tubeless.store import CORPUS_ROOT, FileStore, Store
from tubeless.summary import (
    DETAIL_LEVELS,
    DetailLevel,
    Summary,
    summarize,
    summarize_transcript,
)
from tubeless.synthesis import Synthesis, synthesize
from tubeless.transcript import Transcript, TranscriptSegment, fetch_transcript

__all__ = [
    "BACKENDS",
    "CORPUS_ROOT",
    "Channel",
    "ClaudeBackend",
    "ConfigError",
    "CorpusError",
    "DEFAULT_SCAN",
    "DETAIL_LEVELS",
    "DetailLevel",
    "Digest",
    "DigestRun",
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
    "Skip",
    "Store",
    "Summary",
    "SummarizeVideosResult",
    "Synthesis",
    "Transcript",
    "TranscriptFetchBlocked",
    "TranscriptSegment",
    "TranscriptUnavailable",
    "TubelessError",
    "Upload",
    "Video",
    "curate",
    "discover",
    "fetch_channel_uploads",
    "fetch_playlist_uploads",
    "fetch_uploads",
    "fetch_transcript",
    "fetch_video",
    "load_channels",
    "make_backend",
    "parse_video_id",
    "recompute",
    "resolve_channel_id",
    "run_digest",
    "score",
    "summarize",
    "summarize_transcript",
    "summarize_videos",
    "synthesize",
    "to_markdown",
]
