"""Fetch a video's transcript as immutable, typed segments.

The vendor boundary lives here: youtube-transcript-api is imported and its
exceptions are translated into the tubeless hierarchy in exactly one place,
so no other module needs to know which library does the fetching.

Written against youtube-transcript-api >= 1.0: ``YouTubeTranscriptApi().list()``
returns a transcript list with ``find_transcript()``; each listed transcript
carries ``language_code`` / ``is_generated`` and ``fetch()`` yields snippets
with ``.text`` / ``.start`` / ``.duration``. The pre-1.0 module-level
``get_transcript()`` API is not supported.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests
from youtube_transcript_api import (
    CouldNotRetrieveTranscript,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    YouTubeRequestFailed,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.proxies import (
    GenericProxyConfig,
    ProxyConfig,
    WebshareProxyConfig,
)

from tubeless.credentials import secret
from tubeless.errors import TranscriptFetchBlocked, TranscriptUnavailable
from tubeless.source import Video

__all__ = ["TranscriptSegment", "Transcript", "fetch_transcript"]

_FETCH_TIMEOUT_SECONDS = 30.0

# Caption tracks to prefer, in order, when a video offers several. A video whose
# only captions are in an unlisted language is still summarized -- fetch_transcript
# falls back to whatever track exists, since the summary language is chosen
# separately (--lang) and need not match the caption's language.
_PREFERRED_LANGUAGES = ("en", "ko", "ja", "zh-Hans", "zh-Hant", "es", "fr", "de", "pt", "ru")


class _TimeoutSession(requests.Session):
    """A ``requests`` session with a default per-request timeout. The transcript
    API exposes no timeout of its own, so without this a wedged fetch would hang
    the digest's per-video loop -- the same bound the other network calls carry."""

    def request(self, *args: object, **kwargs: object) -> requests.Response:
        kwargs.setdefault("timeout", _FETCH_TIMEOUT_SECONDS)
        return super().request(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """One caption cue: its text, onset in seconds, and duration in seconds."""

    text:     str
    start:    float
    duration: float


@dataclass(frozen=True, slots=True)
class Transcript:
    """A whole transcript, kept segment-complete: downstream layers decide
    what to trim or chunk, the fetch layer never does.

    ``video`` is the video this transcript is of -- carried whole (not just the
    id) so the transcript is self-describing and every pipeline object (Video ->
    Transcript -> Summary) threads the same identity without re-fetching it."""

    video:             Video
    language:          str
    is_auto_generated: bool
    segments:          tuple[TranscriptSegment, ...]

    @property
    def text(self) -> str:
        """The full transcript as one space-joined string."""
        return " ".join(segment.text for segment in self.segments)


def _proxy_config() -> ProxyConfig | None:
    """Route the transcript fetch through a proxy when tubeless is configured for
    one, else fetch directly (return None -- the default).

    A proxy is the escape hatch for an IP block: YouTube rate-limits or blocks the
    anonymous transcript endpoint per source IP (busy residential ISPs and
    datacenter ranges alike), and this request carries no account, so the exit IP
    is the only thing that can change. Two config sources, checked in order:

      - Webshare rotating residential (``TUBELESS_WEBSHARE_USER`` / ``_PASS``):
        the vendor library's first-class support, which rotates the exit IP and
        retries on a block, so one throttled address does not stick.
      - a generic proxy (``TUBELESS_PROXY_HTTP`` / ``_HTTPS``): any other proxy the
        user runs; each url defaults to the other when only one is set.

    A proxy credential is a secret, so it comes from ``credentials`` (the
    environment or ``credentials.json``), the same file the API keys live in, and
    is never logged.
    """
    webshare_user = secret("TUBELESS_WEBSHARE_USER")
    webshare_pass = secret("TUBELESS_WEBSHARE_PASS")
    if webshare_user and webshare_pass:
        return WebshareProxyConfig(proxy_username=webshare_user, proxy_password=webshare_pass)
    http_url  = secret("TUBELESS_PROXY_HTTP")
    https_url = secret("TUBELESS_PROXY_HTTPS")
    if http_url or https_url:
        return GenericProxyConfig(http_url=http_url, https_url=https_url)
    return None


def fetch_transcript(
    video:      Video,
    *,
    languages:  tuple[str, ...] = _PREFERRED_LANGUAGES,
) -> Transcript:
    """Fetch the transcript, preferring the first requested language available.

    Takes a ``Video`` -- the same object every pipeline stage passes along --
    rather than a bare id, so the fetch-transcript call site is identical whether
    the video came from ``fetch_video`` (oembed) or ``discover`` (a channel feed).

    Within each language the vendor library prefers a manually created
    transcript over an auto-generated one; ``is_auto_generated`` records which
    kind was actually served, so the summarizer can hedge auto-caption
    mis-transcriptions.

    Args:
        video:     the video to fetch captions for (``video.video_id`` is a
                   validated 11-character id; see ``source.extract_video_id``).
        languages: language codes in preference order.

    Raises:
        TranscriptFetchBlocked: YouTube transiently rate-limited or IP-blocked
            this request (not a property of the video).
        TranscriptUnavailable: captions are permanently absent -- disabled, none
            of the requested languages exist, or the video does not exist.
    """
    video_id = video.video_id
    try:
        with _TimeoutSession() as http_client:
            api = YouTubeTranscriptApi(http_client=http_client, proxy_config=_proxy_config())
            listed  = api.list(video_id)
            chosen  = _choose_transcript(listed, languages)
            fetched = chosen.fetch()
    except (RequestBlocked, IpBlocked, YouTubeRequestFailed) as err:
        # Transient: the run's IP is blocked/throttled. Kept separate from the
        # permanent case so the digest aborts rather than marking the video
        # processed -- see TranscriptFetchBlocked. These subclass
        # CouldNotRetrieveTranscript, so this arm MUST come first.
        raise TranscriptFetchBlocked(
            f"transcript fetch blocked for video {video_id!r}: {err}"
        ) from err
    except CouldNotRetrieveTranscript as err:
        raise TranscriptUnavailable(
            f"no transcript for video {video_id!r} in languages {languages!r}: {err}"
        ) from err
    except requests.RequestException as err:
        # Our _TimeoutSession adds a timeout the vendor library has none of, so a
        # slow or dropped fetch surfaces as a raw requests.Timeout/ConnectionError
        # -- not a CouldNotRetrieveTranscript subclass, so it would otherwise
        # escape this boundary as a bare stack trace. A transport failure is
        # transient (retry later), so map it to TranscriptFetchBlocked, not the
        # permanent TranscriptUnavailable.
        raise TranscriptFetchBlocked(
            f"transcript fetch failed for video {video_id!r}: {err}"
        ) from err

    segments = tuple(
        TranscriptSegment(text=snippet.text, start=snippet.start, duration=snippet.duration)
        for snippet in fetched
    )
    return Transcript(
        video             = video,
        language          = chosen.language_code,
        is_auto_generated = chosen.is_generated,
        segments          = segments,
    )


def _choose_transcript(listed, languages: tuple[str, ...]):
    """Pick the best caption track: a preferred-language one if present, else any
    available track (a manually created one before an auto-generated one).

    Falling back to any language -- rather than failing -- means a video captioned
    only in a language outside ``languages`` still summarizes; the summary language
    is set separately (--lang), so the caption's own language need not match it.
    """
    try:
        return listed.find_transcript(list(languages))
    except NoTranscriptFound:
        available = list(listed)
        if not available:
            raise   # no captions at all -> caller maps it to TranscriptUnavailable
        return next((track for track in available if not track.is_generated), available[0])
