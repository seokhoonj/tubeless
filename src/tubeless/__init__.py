"""tubeless: fetch a YouTube video's transcript and summarize it with an LLM.

The package only defines names at import time; all network and LLM work
happens inside the functions the caller invokes.
"""

from tubeless.errors import (
    InvalidVideoURL,
    LLMError,
    TranscriptUnavailable,
    TubelessError,
)
from tubeless.llm import AnthropicBackend, LLMBackend, OpenAIBackend
from tubeless.source import Video, fetch_video_meta, parse_video_id
from tubeless.summary import Summary, summarize
from tubeless.transcript import Transcript, TranscriptSegment, fetch_transcript

__all__ = [
    "AnthropicBackend",
    "InvalidVideoURL",
    "LLMBackend",
    "LLMError",
    "OpenAIBackend",
    "Summary",
    "Transcript",
    "TranscriptSegment",
    "TranscriptUnavailable",
    "TubelessError",
    "Video",
    "fetch_transcript",
    "fetch_video_meta",
    "parse_video_id",
    "summarize",
]
