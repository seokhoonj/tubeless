import pytest

from tubeless import Transcript, TranscriptSegment, Video, summarize
from tubeless.summary import CHUNK_WORD_LIMIT, language_name

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


def test_language_name_maps_codes_and_passes_names_through() -> None:
    assert language_name("ko") == "Korean"
    assert language_name("EN") == "English"
    assert language_name("Korean") == "Korean"   # already a name -> unchanged
    assert language_name("xx") == "xx"            # unknown code -> unchanged


def test_summarize_prompt_uses_the_language_name_not_the_code() -> None:
    backend = RecordingBackend()
    summary = summarize(make_transcript(n_words=10), SAMPLE_VIDEO, backend, target_language="ko")

    # the model is told "Korean", not the bare "ko" it tends to ignore
    assert "Answer in Korean" in backend.prompts[0]
    assert "Answer in ko." not in backend.prompts[0]
    # the stored Summary keeps the original code, not the name
    assert summary.language == "ko"


def test_summarize_parses_tldr_and_points_from_the_reply() -> None:
    backend = RecordingBackend()

    summary = summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend)

    assert summary.tldr   == "Ducks are covered end to end."
    assert summary.points == ("Ducks float.", "Ducks quack.", "Ducks migrate.")
    assert summary.video  == SAMPLE_VIDEO
    assert summary.language == "en"


def test_summarize_caps_points_at_max_points() -> None:
    reply_with_five_points = "TLDR: gist\n" + "\n".join(f"- point {i}" for i in range(5))
    backend = RecordingBackend(reply=reply_with_five_points)

    summary = summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend, max_points=2)

    assert summary.points == ("point 0", "point 1")


def test_summarize_parses_a_numbered_list_as_points() -> None:
    # Smaller models often answer with "1. 2. 3." instead of "- " bullets.
    backend = RecordingBackend(reply="TLDR: gist\n1. first\n2) second\n3. third\n")

    summary = summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend)

    assert summary.tldr   == "gist"
    assert summary.points == ("first", "second", "third")


def test_summarize_parses_the_canonical_tldr_label() -> None:
    # tubeless now emits "TL;DR:"; the parser must read the canonical form as well
    # as the plain "TLDR:" it still tolerates, and accept an em-dash separator.
    backend = RecordingBackend(reply="TL;DR — the gist\n- a point\n")

    summary = summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend)

    assert summary.tldr   == "the gist"
    assert summary.points == ("a point",)


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


def test_summarize_deep_detail_preserves_figures_in_every_map_chunk() -> None:
    # Regression: the figure-preservation instruction reached only the combine
    # step, so long (map-reduced) videos dropped numbers the chunks had already
    # discarded. Every chunk prompt must carry it too.
    backend = RecordingBackend()

    summarize(
        make_transcript(n_words=CHUNK_WORD_LIMIT + 100), SAMPLE_VIDEO, backend, detail="deep"
    )

    assert len(backend.prompts) > 1  # map-reduced
    assert all("preserve EVERY specific figure" in prompt for prompt in backend.prompts)


def test_summarize_normal_detail_does_not_force_figure_preservation() -> None:
    backend = RecordingBackend()

    summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend, detail="normal")

    assert "preserve EVERY specific figure" not in backend.prompts[0]


@pytest.mark.parametrize("detail, cap", [("brief", 5), ("normal", 8), ("deep", 14)])
def test_summarize_detail_sets_the_default_point_cap(detail: str, cap: int) -> None:
    # Each detail level caps the points at its own default when --max-points is
    # not given; a regression to any of the three would silently change every
    # summary's length.
    backend = RecordingBackend(reply=_reply_with_points(20))

    summary = summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend, detail=detail)

    assert len(summary.points) == cap


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


def test_summarize_strips_bold_markers_from_the_tldr_label() -> None:
    # A common drift: the model bolds the label as "**TLDR:** ...".
    backend = RecordingBackend(reply="**TLDR:** the bolded gist\n- a point\n")

    summary = summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend)

    assert summary.tldr   == "the bolded gist"
    assert summary.points == ("a point",)


def test_summarize_does_not_mistake_a_leading_year_for_a_numbered_point() -> None:
    # "2026. ..." is four digits, so it must fall through as prose (the TLDR),
    # not be parsed as list item "2026".
    backend = RecordingBackend(reply="2026. The year in review\n- one point\n")

    summary = summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend)

    assert summary.tldr   == "2026. The year in review"
    assert summary.points == ("one point",)


def test_summarize_with_max_points_zero_is_rejected() -> None:
    backend = RecordingBackend(reply=_reply_with_points(5))

    with pytest.raises(ValueError):
        summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend, max_points=0)


def test_summarize_with_negative_max_points_is_rejected() -> None:
    # Guards the negative-slice trap: points[:-2] would drop trailing points.
    backend = RecordingBackend(reply=_reply_with_points(5))

    with pytest.raises(ValueError):
        summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend, max_points=-2)


def test_summarize_rejoins_a_point_wrapped_onto_a_second_line() -> None:
    # A long point the model wrapped onto a second physical line: the continuation
    # is neither a bullet nor a label, so it must attach to the last point instead
    # of being dropped, which truncated the point to a half-sentence.
    backend = RecordingBackend(
        reply="TLDR: gist\n- A long point that runs\non into a second line.\n- Second point\n"
    )

    summary = summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend)

    assert summary.points == (
        "A long point that runs on into a second line.",
        "Second point",
    )


def test_summarize_does_not_glue_a_trailing_remark_onto_a_complete_point() -> None:
    # A trailing remark after the bullets (against the "no other text" instruction)
    # must NOT be attached to the last point, which already ends a sentence --
    # otherwise the rejoin that saves a wrapped point would corrupt a complete one.
    backend = RecordingBackend(
        reply="TLDR: gist\n- Point one.\n- Point two.\nIn summary, watch the Fed.\n"
    )

    summary = summarize(make_transcript(n_words=50), SAMPLE_VIDEO, backend)

    assert summary.points == ("Point one.", "Point two.")


def test_summarize_passes_the_target_language_into_the_prompt() -> None:
    backend = RecordingBackend()

    summary = summarize(
        make_transcript(n_words=50), SAMPLE_VIDEO, backend, target_language="en"
    )

    assert summary.language == "en"
    assert "Answer in English." in backend.prompts[0]
