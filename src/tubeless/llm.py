"""LLM completion backends behind one structural interface.

``summarize()`` depends only on the ``LLMBackend`` protocol, so tests inject a
fake and a future backend (another vendor, a local model) plugs in without
touching the summary logic.
"""

from __future__ import annotations

import os
from typing import Protocol

from tubeless import config
from tubeless.errors import LLMError

__all__ = ["AnthropicBackend", "GeminiBackend", "LLMBackend", "OllamaBackend", "OpenAIBackend"]


class LLMBackend(Protocol):
    """Anything that can turn a prompt into a text completion."""

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


def _chat_complete(client, model: str, prompt: str, *, system: str | None, label: str) -> str:
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
        )
    except openai.OpenAIError as err:
        raise LLMError(f"{label} completion failed for model {model!r}: {err}") from err

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
                "no OpenAI API key: pass api_key=, set OPENAI_SECRET_KEY in the "
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

    def __init__(self, *, model: str = "gemini-2.5-flash", api_key: str | None = None) -> None:
        resolved_key = api_key if api_key is not None else config.api_key("gemini")
        if not resolved_key:
            raise LLMError(
                "no Gemini API key: pass api_key=, set GEMINI_SECRET_KEY in the "
                "environment, or add it to ~/.tubeless/config.env"
            )
        from openai import OpenAI  # lazy, like OpenAIBackend (see above)

        self.model   = model
        self._client = OpenAI(base_url=self._BASE_URL, api_key=resolved_key)

    def __repr__(self) -> str:
        return f"GeminiBackend(model={self.model!r})"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        return _chat_complete(self._client, self.model, prompt, system=system, label="Gemini")


class AnthropicBackend:
    """Messages backend over the Anthropic SDK: Claude tends to hedge unsupported
    specifics rather than invent them, which is the safer default when the
    transcript is a noisy auto-caption.

    A class for the same reason as :class:`OpenAIBackend` -- client and model are
    configured once and reused across a summary's map-reduce calls.

    ``max_tokens`` is required by this API (unlike OpenAI's), so it is a
    constructor knob; the default is generous enough for a summary reply.
    """

    def __init__(self, *, model: str = "claude-haiku-4-5-20251001",
                 api_key: str | None = None, max_tokens: int = 2048) -> None:
        resolved_key = api_key if api_key is not None else config.api_key("anthropic")
        if not resolved_key:
            raise LLMError(
                "no Anthropic API key: pass api_key=, set ANTHROPIC_SECRET_KEY in the "
                "environment, or add it to ~/.tubeless/config.env"
            )
        from anthropic import Anthropic  # lazy, like OpenAIBackend (see above)

        self.model      = model
        self.max_tokens = max_tokens
        self._client    = Anthropic(api_key=resolved_key)

    def __repr__(self) -> str:
        return f"AnthropicBackend(model={self.model!r})"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        import anthropic

        # Anthropic takes ``system`` as its own argument, not a message role.
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system is not None:
            kwargs["system"] = system

        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.AnthropicError as err:
            raise LLMError(f"Anthropic completion failed for model {self.model!r}: {err}") from err

        blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        text = "".join(blocks).strip()
        if not text:
            raise LLMError(f"Anthropic returned an empty completion for model {self.model!r}")
        return text
