"""LLM completion backends behind one structural interface.

``summarize()`` depends only on the ``LLMBackend`` protocol, so tests inject a
fake and a future backend (another vendor, a local model) plugs in without
touching the summary logic.
"""

from __future__ import annotations

from typing import Protocol

from tubeless import config
from tubeless.errors import LLMError

__all__ = ["AnthropicBackend", "LLMBackend", "OpenAIBackend"]


class LLMBackend(Protocol):
    """Anything that can turn a prompt into a text completion."""

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        """Return the model's text reply for one prompt.

        Raises:
            LLMError: the backend could not produce a completion.
        """
        ...


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
        import openai

        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self._client.chat.completions.create(
                model    = self.model,
                messages = messages,
            )
        except openai.OpenAIError as err:
            raise LLMError(f"OpenAI completion failed for model {self.model!r}: {err}") from err

        text = response.choices[0].message.content
        if not text:
            raise LLMError(f"OpenAI returned an empty completion for model {self.model!r}")
        return text


class AnthropicBackend:
    """Messages backend over the Anthropic SDK, the other half of the two-vendor
    setup: Claude tends to hedge unsupported specifics rather than invent them,
    which is the safer default when the transcript is a noisy auto-caption.

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
