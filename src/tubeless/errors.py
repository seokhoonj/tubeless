"""Domain exception hierarchy for tubeless.

Every error tubeless raises on purpose derives from ``TubelessError``, so a
caller can handle this package's failures with one ``except`` without catching
unrelated bugs.
"""

__all__ = [
    "TubelessError",
    "TranscriptUnavailable",
    "TranscriptFetchBlocked",
    "LLMError",
    "InvalidVideoURL",
    "FeedError",
    "ConfigError",
]


class TubelessError(Exception):
    """Base for every error tubeless raises deliberately."""


class TranscriptUnavailable(TubelessError):
    """The video has no usable transcript (captions disabled, none in the
    requested languages, or the video does not exist).

    This is a *permanent* condition for that video: the digest records it as
    processed so it is not retried every run."""


class TranscriptFetchBlocked(TubelessError):
    """The transcript could not be fetched because of a *transient* block --
    YouTube rate-limited or IP-blocked this run, not a property of the video.

    Kept distinct from ``TranscriptUnavailable`` on purpose: the digest must not
    mark an affected video processed, or a momentary block would drop it
    forever. This error aborts the run instead, so the video is retried once the
    block clears."""


class LLMError(TubelessError):
    """The LLM backend failed: missing credentials, API error, empty reply."""


class InvalidVideoURL(TubelessError):
    """The input could not be parsed into a YouTube video id."""


class FeedError(TubelessError):
    """A channel feed could not be fetched or parsed, or a handle could not be
    resolved to a channel id."""


class ConfigError(TubelessError):
    """A tubeless config file (channels list) is missing or malformed."""
