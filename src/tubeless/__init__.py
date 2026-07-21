"""tubeless: fetch a YouTube video's transcript and summarize it with an LLM.

The package only defines names at import time; all network and LLM work
happens inside the functions the caller invokes.
"""

from tubeless.channels import Channel, load_channels
from tubeless.digest import Digest, DigestEntry, build_digest
from tubeless.errors import (
    ConfigError,
    FeedError,
    InvalidVideoURL,
    LLMError,
    TranscriptUnavailable,
    TubelessError,
)
from tubeless.feed import Upload, fetch_channel_uploads, resolve_channel_id
from tubeless.importance import Importance, score_importance
from tubeless.llm import AnthropicBackend, LLMBackend, OpenAIBackend
from tubeless.render import to_markdown
from tubeless.source import Video, fetch_video_meta, parse_video_id
from tubeless.summary import Summary, summarize
from tubeless.transcript import Transcript, TranscriptSegment, fetch_transcript

__all__ = [
    "AnthropicBackend",
    "Channel",
    "ConfigError",
    "Digest",
    "DigestEntry",
    "FeedError",
    "Importance",
    "InvalidVideoURL",
    "LLMBackend",
    "LLMError",
    "OpenAIBackend",
    "Summary",
    "Transcript",
    "TranscriptSegment",
    "TranscriptUnavailable",
    "TubelessError",
    "Upload",
    "Video",
    "build_digest",
    "fetch_channel_uploads",
    "fetch_transcript",
    "fetch_video_meta",
    "load_channels",
    "parse_video_id",
    "resolve_channel_id",
    "score_importance",
    "summarize",
    "to_markdown",
]
