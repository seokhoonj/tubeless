"""Transcribe a video's audio locally when it has no captions.

The whisper vendor boundary lives here: yt-dlp (audio download) and
faster-whisper (speech-to-text) are imported and used in exactly one place, so
no other module -- transcript.py's caption path included -- depends on either
being installed. Both are optional (``tubeless[whisper]``) and imported lazily
inside the functions below, so the base install carries neither.

This is the fallback ``transcript.fetch_transcript`` reaches for when a video's
captions are permanently absent: download the audio, run whisper on it, and
return the same ``Transcript`` the caption path yields -- so every downstream
stage (summary, importance) is identical whether the text came from YouTube's
captions or from whisper.
"""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from tubeless.errors import TranscriptUnavailable
from tubeless.source import Video, watch_url
from tubeless.transcript_types import Transcript, TranscriptSegment

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

__all__ = ["transcribe_audio"]

# faster-whisper on CPU: int8 is the fast, low-memory compute type, and the box
# that runs the digest cron is a CPU machine. The device/compute choice stays
# here -- the one place that knows about the vendor -- while the model size is the
# operator's (TUBELESS_WHISPER_MODEL); a GPU user can still name a larger model.
_DEVICE       = "cpu"
_COMPUTE_TYPE = "int8"

_MISSING_EXTRA_MESSAGE = (
    "the whisper fallback needs the 'whisper' extra: pip install tubeless[whisper]"
)


def transcribe_audio(video: Video, *, model_name: str) -> Transcript:
    """Download ``video``'s audio and transcribe it locally with whisper.

    Returns the same ``Transcript`` the caption path produces, with
    ``is_auto_generated=True`` (whisper output is machine-transcribed) and
    ``language`` the language whisper detected -- or ``"und"`` when it detected none.

    Args:
        video:      the video to transcribe (``video.video_id`` is validated
                    upstream by ``source.extract_video_id``).
        model_name: a faster-whisper model size (e.g. ``"small"``) -- the value of
                    ``TUBELESS_WHISPER_MODEL``.

    Raises:
        TranscriptUnavailable: the 'whisper' extra is not installed, the model
            could not be loaded, the audio could not be downloaded or transcribed,
            or it held no speech -- a permanent miss for this video, so the digest
            skips it exactly as it would a caption-less video with the fallback off.
    """
    # The audio only has to survive long enough to be transcribed, so it lives in
    # a temporary directory removed on the way out -- faster-whisper materialises
    # its segments before we leave the block (see _transcribe_file).
    with tempfile.TemporaryDirectory(prefix="tubeless-whisper-") as workdir:
        audio_path                    = _download_audio(video, Path(workdir))
        language, transcript_segments = _transcribe_file(audio_path, model_name)
    if not transcript_segments:
        raise TranscriptUnavailable(f"whisper found no speech in video {video.video_id!r}")
    return Transcript(
        video             = video,
        language          = language,
        is_auto_generated = True,
        segments          = transcript_segments,
    )


def _download_audio(video: Video, workdir: Path) -> Path:
    """Download the video's audio-only stream into ``workdir`` and return its path.

    yt-dlp picks the best audio format; the container (m4a/webm/...) does not
    matter, since faster-whisper decodes whatever it is. Imported lazily so the
    base install does not need yt-dlp.

    Raises:
        TranscriptUnavailable: the 'whisper' extra (yt-dlp) is missing, the
            download failed, or it produced no file.
    """
    try:
        import yt_dlp
    except ImportError as err:
        raise TranscriptUnavailable(f"{_MISSING_EXTRA_MESSAGE} (video {video.video_id!r})") from err

    options = {
        "format":      "bestaudio/best",
        "outtmpl":     str(workdir / "audio.%(ext)s"),
        "quiet":       True,
        "no_warnings": True,
        "noprogress":  True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([watch_url(video.video_id)])
    except Exception as err:
        # yt-dlp raises a wide, unstable error surface (DownloadError plus wrapped
        # network/ffmpeg failures). Any of them means the audio is not in hand, so
        # at this vendor boundary they all map to one permanent miss for the video
        # -- a fallback hiccup must never crash the digest with a bare traceback.
        raise TranscriptUnavailable(
            f"could not download audio for video {video.video_id!r}: {err}"
        ) from err

    # Pick the largest real file: yt-dlp renames .part -> final on success, but a
    # leftover .part (or a dotfile) must never be chosen over the finished audio.
    audio_paths = [
        path for path in workdir.iterdir() if path.is_file() and path.suffix != ".part"
    ]
    if not audio_paths:
        raise TranscriptUnavailable(
            f"audio download produced no file for video {video.video_id!r}"
        )
    return max(audio_paths, key=lambda path: path.stat().st_size)


def _transcribe_file(audio_path: Path, model_name: str) -> tuple[str, tuple[TranscriptSegment, ...]]:
    """Run whisper on ``audio_path`` and return (detected language, segments).

    faster-whisper yields its segments lazily; they are materialised here, while
    the audio file still exists, into the same ``TranscriptSegment`` shape the
    caption path uses. ``duration`` is ``end - start`` clamped to ``0.0`` (whisper
    can report ``end <= start``; the caption path never yields a negative duration) --
    whisper reports an end time where the caption API reports a duration, and
    ``Transcript`` stores a duration.

    Raises:
        TranscriptUnavailable: the 'whisper' extra (faster-whisper) is missing,
            the model could not be loaded, or whisper failed to decode the audio.
    """
    try:
        whisper_model = _load_model(model_name)
    except ImportError as err:
        raise TranscriptUnavailable(_MISSING_EXTRA_MESSAGE) from err
    except Exception as err:
        # Loading a model constructs a WhisperModel, which validates the name and
        # (on first use) downloads its weights -- either can fail with a non-import
        # error (a bad TUBELESS_WHISPER_MODEL, an OSError, a network failure). Map
        # it too, so an unloadable model skips the video rather than crashing the run.
        raise TranscriptUnavailable(
            f"could not load the whisper model {model_name!r}: {err}"
        ) from err

    try:
        whisper_segments, transcription_info = whisper_model.transcribe(str(audio_path))
        # "und" is the ISO 639-2 code for an undetermined language -- keeps the str
        # contract of Transcript.language when whisper reports no detected language.
        # Read inside the guard so a payload missing .language maps to a per-video
        # miss too, rather than escaping this boundary as a bare AttributeError.
        language            = transcription_info.language or "und"
        transcript_segments = tuple(
            TranscriptSegment(
                text     = segment.text.strip(),
                start    = segment.start,
                # whisper can report end <= start; clamp so duration is never
                # negative (the caption path never yields a negative duration).
                duration = max(0.0, segment.end - segment.start),
            )
            for segment in whisper_segments
        )
    except Exception as err:
        # whisper decoding runs C/PyAV under the hood and can fail on a truncated
        # or unusual audio file. Like the download step, any such failure is a
        # permanent miss for this one video, mapped here so it never escapes the
        # fallback as a bare traceback and crashes the whole digest run.
        raise TranscriptUnavailable(
            f"whisper failed to transcribe the audio ({audio_path.name}): {err}"
        ) from err
    return language, transcript_segments


@lru_cache(maxsize=2)
def _load_model(model_name: str) -> WhisperModel:
    """Load and cache a faster-whisper model by size.

    Loading reads hundreds of MB off disk, so one digest run with several
    caption-less videos reuses the same instance instead of reloading per video.
    Imported lazily -- the base install does not need faster-whisper.
    """
    from faster_whisper import WhisperModel

    return WhisperModel(model_name, device=_DEVICE, compute_type=_COMPUTE_TYPE)
