"""LLM completion backends behind one structural interface.

``summarize()`` depends only on the ``LLMBackend`` protocol, so tests inject a
fake and a future backend (another vendor, a local model) plugs in without
touching the summary logic.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol

from tubeless import config
from tubeless.errors import LLMError

if TYPE_CHECKING:
    from openai import OpenAI  # only for the type hint below; imported lazily at runtime

__all__ = [
    "BACKENDS",
    "ClaudeBackend",
    "GeminiBackend",
    "LLMBackend",
    "OllamaBackend",
    "OpenAIBackend",
    "make_backend",
]

# The LLM vendors tubeless ships, and the string that names each on the command
# line / in config. The roster lives here, beside the classes it maps to, so a
# consumer (the CLI, a scheduled job, another front end) asks the package for a
# backend by name instead of re-authoring the name->class map -- add a vendor
# here and every consumer picks it up.
BACKENDS = ("claude", "openai", "gemini", "ollama")


class LLMBackend(Protocol):
    """Anything that can turn a prompt into a text completion."""

    model: str
    """The model id the backend calls -- read by the CLI to print which model a
    run actually used (the settings header), so a small model mangling an
    unfamiliar name is visible rather than mysterious."""

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        """Return the model's text reply for one prompt.

        Raises:
            LLMError: the backend could not produce a completion.
        """
        ...


# Summaries are extraction, not creative writing: a low temperature keeps the
# model on the requested format and off invented preambles. It matters most for
# smaller local models, which drift into greetings and numbered prose at the
# vendor-default temperature.
_TEMPERATURE = 0.2

# An upper bound tubeless controls, instead of each SDK's multi-minute default:
# the digest calls a backend once per video in a loop, so one wedged request
# must not stall the whole run.
_LLM_TIMEOUT_SECONDS = 60.0


def _chat_complete(client: OpenAI, model: str, prompt: str, *, system: str | None, label: str) -> str:
    """Run one chat completion against any OpenAI-style ``/v1`` client.

    Shared by :class:`OpenAIBackend` and :class:`OllamaBackend`, which differ
    only in how the client is constructed. ``label`` names the vendor in errors.
    """
    import openai

    messages: list[dict[str, str]] = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=_TEMPERATURE,
            timeout=_LLM_TIMEOUT_SECONDS,
        )
    except openai.OpenAIError as err:
        raise LLMError(f"{label} completion failed for model {model!r}: {err}") from err

    if not response.choices:   # content-filter / gateway responses can be empty
        raise LLMError(f"{label} returned no choices for model {model!r}")
    text = response.choices[0].message.content
    if not text:
        raise LLMError(f"{label} returned an empty completion for model {model!r}")
    return text


class OpenAIBackend:
    """Chat-completions backend over the OpenAI SDK.

    A class rather than a function because the client and model choice are
    configured once and reused across the map-reduce calls of one summary
    (configure-now-apply-later, constitution 5.1).
    """

    def __init__(self, *, model: str = "gpt-4o-mini", api_key: str | None = None) -> None:
        resolved_key = api_key if api_key is not None else config.api_key("openai")
        if not resolved_key:
            raise LLMError(
                "no OpenAI API key: pass api_key=, set OPENAI_API_KEY in the "
                "environment, or add it to ~/.tubeless/config.env"
            )
        # Imported here, not at module top: constructing a backend is the first
        # moment the SDK is genuinely needed, and this keeps `import tubeless`
        # working (e.g. for parse_video_id) even where openai is not installed.
        from openai import OpenAI

        self.model   = model
        self._client = OpenAI(api_key=resolved_key)

    def __repr__(self) -> str:
        return f"OpenAIBackend(model={self.model!r})"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        return _chat_complete(self._client, self.model, prompt, system=system, label="OpenAI")


class OllamaBackend:
    """Chat-completions backend against a local Ollama server.

    Ollama exposes an OpenAI-compatible ``/v1`` endpoint, so this reuses the
    OpenAI SDK pointed at the local host -- no API key and no network egress, the
    model runs on your machine. This is the private/offline/free option; pick the
    model with ``model=`` (whatever you have ``ollama pull``-ed). The host comes
    from ``host=`` or the ``OLLAMA_HOST`` environment variable.
    """

    def __init__(self, *, model: str = "llama3.1",
                 host: str | None = None, api_key: str = "ollama") -> None:
        from openai import OpenAI  # lazy, like OpenAIBackend (see above)

        base_url     = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        self.model   = model
        # Ollama ignores the key but the OpenAI SDK requires a non-empty one.
        self._client = OpenAI(base_url=base_url, api_key=api_key)

    def __repr__(self) -> str:
        return f"OllamaBackend(model={self.model!r})"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        return _chat_complete(self._client, self.model, prompt, system=system, label="Ollama")


class GeminiBackend:
    """Chat-completions backend over Google's Gemini models.

    Gemini exposes an OpenAI-compatible ``/v1beta/openai`` endpoint, so this
    reuses the OpenAI SDK pointed at that host -- the same shape as
    :class:`OllamaBackend`, but Gemini is a cloud vendor and so needs a key.
    A class for the same reason as :class:`OpenAIBackend` -- client and model
    are configured once and reused across a summary's map-reduce calls.
    """

    _BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

    # A '-latest' alias, not a pinned version: Google gates pinned names (e.g.
    # gemini-2.5-flash) to 404 for newly created API keys, while the alias tracks
    # the current model and stays on the free tier. Override with model=.
    def __init__(self, *, model: str = "gemini-flash-lite-latest", api_key: str | None = None) -> None:
        resolved_key = api_key if api_key is not None else config.api_key("gemini")
        if not resolved_key:
            raise LLMError(
                "no Gemini API key: pass api_key=, set GEMINI_API_KEY in the "
                "environment, or add it to ~/.tubeless/config.env"
            )
        from openai import OpenAI  # lazy, like OpenAIBackend (see above)

        self.model   = model
        self._client = OpenAI(base_url=self._BASE_URL, api_key=resolved_key)

    def __repr__(self) -> str:
        return f"GeminiBackend(model={self.model!r})"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        return _chat_complete(self._client, self.model, prompt, system=system, label="Gemini")


class ClaudeBackend:
    """Messages backend for Claude, over Anthropic's ``anthropic`` SDK: Claude
    tends to hedge unsupported specifics rather than invent them, which is the
    safer default when the transcript is a noisy auto-caption.

    A class for the same reason as :class:`OpenAIBackend` -- client and model are
    configured once and reused across a summary's map-reduce calls.

    ``max_tokens`` is required by this API (unlike OpenAI's), so it is a
    constructor knob; the default is generous enough for a summary reply.
    """

    def __init__(self, *, model: str = "claude-haiku-4-5-20251001",
                 api_key: str | None = None, max_tokens: int = 2048) -> None:
        resolved_key = api_key if api_key is not None else config.api_key("claude")
        if not resolved_key:
            raise LLMError(
                "no Claude API key: pass api_key=, set CLAUDE_API_KEY in the "
                "environment, or add it to ~/.tubeless/config.env"
            )
        try:
            from anthropic import Anthropic  # lazy, like OpenAIBackend (see above)
        except ImportError as err:
            # anthropic is an optional extra; give a one-line fix, not a traceback.
            raise LLMError(
                "the Claude backend needs the 'anthropic' package: "
                "pip install 'tubeless[claude]'"
            ) from err

        self.model      = model
        self.max_tokens = max_tokens
        self._client    = Anthropic(api_key=resolved_key)

    def __repr__(self) -> str:
        return f"ClaudeBackend(model={self.model!r})"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        import anthropic

        # Anthropic takes ``system`` as its own argument, not a message role.
        kwargs: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": _LLM_TIMEOUT_SECONDS,
        }
        if system is not None:
            kwargs["system"] = system

        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.AnthropicError as err:
            raise LLMError(f"Anthropic completion failed for model {self.model!r}: {err}") from err

        blocks = [block.text for block in response.content if getattr(block, "type", None) == "text"]
        text = "".join(blocks).strip()
        if not text:
            raise LLMError(f"Anthropic returned an empty completion for model {self.model!r}")
        return text


def make_backend(name: str, *, model: str | None = None) -> LLMBackend:
    """Construct the backend named ``name`` (one of ``BACKENDS``); ``model``
    overrides the backend class's own small-tier default when given.

    Each class owns its default model (its constructor default), so a default
    lives in one place, and an unknown name is a loud ``KeyError`` rather than a
    silent fall-through to one vendor. The name->class map is built on call, not
    at import, so a test that monkeypatches e.g. ``OpenAIBackend`` still takes
    effect.

    Raises:
        LLMError: the chosen backend has no usable API key / SDK.
    """
    backend_class = {
        "claude": ClaudeBackend,
        "openai": OpenAIBackend,
        "gemini": GeminiBackend,
        "ollama": OllamaBackend,
    }[name]
    return backend_class() if model is None else backend_class(model=model)
