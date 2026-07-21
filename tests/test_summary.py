import pytest

from tubeless import Transcript, TranscriptSegment, Video, summarize
from tubeless.summary import CHUNK_WORD_LIMIT

SAMPLE_VIDEO = Video(
    video_id = "dQw4w9WgXcQ",
    title    = "A talk about ducks",
    url      = "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    channel  = "Duck Channel",
)

CANNED_REPLY = (
    "TLDR: Ducks are covered end to end.\n"
    "- Ducks float.\n"
    "- Ducks quack.\n"
    "- Ducks migrate.\n"
)


class RecordingBackend:
    """Fake LLMBackend: records every prompt/system pair, returns canned text."""

    def __init__(self, reply: str = CANNED_REPLY) -> None:
        self.reply:   str = reply
        self.prompts: list[str] = []
        self.systems: list[str | None] = []

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        self.prompts.append(prompt)
        self.systems.append(system)
        return self.reply


def make_transcript(*, n_words: int, is_auto_generated: bool = False) -> Transcript:
    words = " ".join(f"word{word_index}" for word_index in range(n_words))
    return Transcript(
        video_id          = SAMPLE_VIDEO.video_id,
        language          = "en",
        is_auto_generated = is_auto_generated,
        segments          = (TranscriptSegment(text=words, start=0.0, duration=60.0),),
    )


def test_summarize_parses_tldr_and_points_from_the_reply() -> None:
    backend = RecordingBackend()

    summary = summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend)

    assert summary.tldr   == "Ducks are covered end to end."
    assert summary.points == ("Ducks float.", "Ducks quack.", "Ducks migrate.")
    assert summary.video  == SAMPLE_VIDEO
    assert summary.language == "ko"


def test_summarize_caps_points_at_max_points() -> None:
    reply_with_five_points = "TLDR: gist\n" + "\n".join(f"- point {i}" for i in range(5))
    backend = RecordingBackend(reply=reply_with_five_points)

    summary = summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend, max_points=2)

    assert summary.points == ("point 0", "point 1")


def test_summarize_parses_a_reply_without_a_tldr_label() -> None:
    backend = RecordingBackend(reply="Just the gist as plain prose.\n- one point\n")

    summary = summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend)

    assert summary.tldr   == "Just the gist as plain prose."
    assert summary.points == ("one point",)


def test_summarize_short_transcript_is_a_single_backend_call() -> None:
    backend = RecordingBackend()

    summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend)

    assert len(backend.prompts) == 1
    assert SAMPLE_VIDEO.title in backend.prompts[0]


def test_summarize_long_transcript_map_reduces_across_chunks() -> None:
    backend = RecordingBackend()
    three_chunks_of_words = make_transcript(n_words=CHUNK_WORD_LIMIT * 2 + 100)

    summarize(three_chunks_of_words, SAMPLE_VIDEO, backend)

    # Three map calls (one per chunk) plus one reduce call.
    assert len(backend.prompts) == 4
    combine_prompt = backend.prompts[-1]
    assert "[part 1]" in combine_prompt
    assert "[part 3]" in combine_prompt


def test_summarize_warns_about_auto_generated_captions_in_the_prompt() -> None:
    backend = RecordingBackend()

    summarize(
        make_transcript(n_words=50, is_auto_generated=True), SAMPLE_VIDEO, backend
    )

    assert "auto-generated" in backend.prompts[0]


def test_summarize_hedges_every_prompt_of_a_long_auto_generated_transcript() -> None:
    backend = RecordingBackend()

    summarize(
        make_transcript(n_words=CHUNK_WORD_LIMIT + 100, is_auto_generated=True),
        SAMPLE_VIDEO,
        backend,
    )

    assert all("auto-generated" in prompt for prompt in backend.prompts)


def test_summarize_manual_transcript_does_not_mention_auto_captions() -> None:
    backend = RecordingBackend()

    summarize(make_transcript(n_words=50, is_auto_generated=False), SAMPLE_VIDEO, backend)

    assert "auto-generated" not in backend.prompts[0]


def _reply_with_points(n: int) -> str:
    return "TLDR: gist\n" + "\n".join(f"- point {i}" for i in range(n))


def test_summarize_deep_detail_asks_for_fuller_points() -> None:
    backend = RecordingBackend()

    summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend, detail="deep")

    assert "two to four sentences" in backend.prompts[0]


def test_summarize_deep_detail_asks_to_preserve_every_figure() -> None:
    backend = RecordingBackend()

    summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend, detail="deep")

    assert "preserve EVERY specific figure" in backend.prompts[0]


def test_summarize_normal_detail_does_not_force_figure_preservation() -> None:
    backend = RecordingBackend()

    summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend, detail="normal")

    assert "preserve EVERY specific figure" not in backend.prompts[0]


def test_summarize_detail_sets_the_default_point_cap() -> None:
    backend = RecordingBackend(reply=_reply_with_points(20))

    brief = summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend, detail="brief")

    assert len(brief.points) == 5  # brief caps at 5 without an explicit --points


def test_summarize_explicit_max_points_overrides_the_detail_default() -> None:
    backend = RecordingBackend(reply=_reply_with_points(20))

    summary = summarize(
        make_transcript(n_words=50), SAMPLE_VIDEO, backend, detail="deep", max_points=3
    )

    assert len(summary.points) == 3


def test_summarize_rejects_an_unknown_detail_level() -> None:
    backend = RecordingBackend()

    with pytest.raises(ValueError):
        summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend, detail="huge")


def test_summarize_passes_the_target_language_into_the_prompt() -> None:
    backend = RecordingBackend()

    summary = summarize(
        make_transcript(n_words=50), SAMPLE_VIDEO, backend, target_language="en"
    )

    assert summary.language == "en"
    assert "Answer in en." in backend.prompts[0]
