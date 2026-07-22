"""Cross-source synthesis: reply parsing and prompt assembly, no network."""

from tubeless import DailySynthesis
from tubeless.source import Video
from tubeless.summary import Summary
from tubeless.synthesis import _parse_synthesis, synthesize

_REPLY = (
    "TONE: cautious bearish -- chips and FX\n"
    "OVERVIEW: A corrective day led by semiconductors.\n"
    "It stayed orderly throughout.\n"
    "AGREEMENT:\n"
    "- Chip rebound failed\n"
    "- Foreign selling continued\n"
    "DISAGREEMENT:\n"
    "- A says the correction is enough; B still distrusts the rally\n"
)


def _summary(title: str, tldr: str, points: tuple[str, ...]) -> Summary:
    video = Video(video_id="vid00000001", title=title, url="https://x", channel="c")
    return Summary(video=video, tldr=tldr, points=points, language="en")


class OneReplyBackend:
    """Fake backend: records the prompt, returns one canned reply."""

    def __init__(self, reply: str) -> None:
        self.reply:  str = reply
        self.prompt: str | None = None

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        self.prompt = prompt
        return self.reply


def test_parse_synthesis_reads_all_four_fields():
    got = _parse_synthesis(_REPLY)

    assert got.tone == "cautious bearish -- chips and FX"
    # the wrapped OVERVIEW is re-joined into one line
    assert got.overview == "A corrective day led by semiconductors. It stayed orderly throughout."
    assert got.agreements == ("Chip rebound failed", "Foreign selling continued")
    assert got.disagreements == ("A says the correction is enough; B still distrusts the rally",)


def test_parse_synthesis_drops_a_none_disagreement():
    got = _parse_synthesis("TONE: flat\nOVERVIEW: a quiet day\nDISAGREEMENT:\n- (none)")

    assert got.disagreements == ()
    assert got.agreements == ()


def test_synthesize_feeds_every_source_into_the_prompt():
    backend   = OneReplyBackend(_REPLY)
    summaries = [
        ("Channel A", _summary("Video A", "gist A", ("a1", "a2"))),
        ("Channel B", _summary("Video B", "gist B", ("b1",))),
    ]

    got = synthesize(summaries, backend, language="en")

    assert isinstance(got, DailySynthesis)
    # each source's label, title, and tldr reached the model
    for token in ("Channel A", "Video A", "gist A", "a1", "Channel B", "Video B", "gist B"):
        assert token in backend.prompt
