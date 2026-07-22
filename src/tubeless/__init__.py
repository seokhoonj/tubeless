"""tubeless: fetch a YouTube video's transcript and summarize it with an LLM.

The package only defines names at import time; all network and LLM work
happens inside the functions the caller invokes.
"""

from tubeless.channels import Channel, load_channels
from tubeless.corpus import (
    CorpusEntry,
    append_entry,
    archive_transcript,
    load_summaries,
    load_transcript,
    record_entry,
)
from tubeless.digest import Digest, DigestEntry, build_digest
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
from tubeless.importance import Importance, ImportanceTier, score_importance
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
from tubeless.source import Video, fetch_video_meta, parse_video_id
from tubeless.summary import DETAIL_LEVELS, DetailLevel, Summary, summarize
from tubeless.synthesis import DailySynthesis, synthesize
from tubeless.transcript import Transcript, TranscriptSegment, fetch_transcript

__all__ = [
    "BACKENDS",
    "Channel",
    "ClaudeBackend",
    "ConfigError",
    "CorpusEntry",
    "CorpusError",
    "DailySynthesis",
    "DETAIL_LEVELS",
    "DetailLevel",
    "Digest",
    "DigestEntry",
    "FeedError",
    "GeminiBackend",
    "Importance",
    "ImportanceTier",
    "InvalidVideoURL",
    "LLMBackend",
    "LLMError",
    "OllamaBackend",
    "OpenAIBackend",
    "Summary",
    "Transcript",
    "TranscriptFetchBlocked",
    "TranscriptSegment",
    "TranscriptUnavailable",
    "TubelessError",
    "Upload",
    "Video",
    "append_entry",
    "archive_transcript",
    "build_digest",
    "fetch_channel_uploads",
    "fetch_playlist_uploads",
    "fetch_uploads",
    "fetch_transcript",
    "fetch_video_meta",
    "load_channels",
    "load_summaries",
    "load_transcript",
    "make_backend",
    "parse_video_id",
    "record_entry",
    "resolve_channel_id",
    "score_importance",
    "summarize",
    "synthesize",
    "to_markdown",
]
