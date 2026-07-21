"""Domain exception hierarchy for tubeless.

Every error tubeless raises on purpose derives from ``TubelessError``, so a
caller can handle this package's failures with one ``except`` without catching
unrelated bugs.
"""

__all__ = [
    "TubelessError",
    "TranscriptUnavailable",
    "LLMError",
    "InvalidVideoURL",
    "FeedError",
    "ConfigError",
]


class TubelessError(Exception):
    """Base for every error tubeless raises deliberately."""


class TranscriptUnavailable(TubelessError):
    """The video has no usable transcript (captions disabled, none in the
    requested languages, or the video does not exist)."""


class LLMError(TubelessError):
    """The LLM backend failed: missing credentials, API error, empty reply."""


class InvalidVideoURL(TubelessError):
    """The input could not be parsed into a YouTube video id."""


class FeedError(TubelessError):
    """A channel feed could not be fetched or parsed, or a handle could not be
    resolved to a channel id."""


class ConfigError(TubelessError):
    """A tubeless config file (channels list) is missing or malformed."""
