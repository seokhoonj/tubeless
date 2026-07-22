"""Backend tests: key handling and vendor-error wrapping, no network.

The real SDK objects are faked so the tests exercise tubeless's own wiring --
the missing-key guard, the SDK-error -> LLMError translation, and the CLI's
per-vendor backend selection -- without an API call.
"""

import sys
import types

import pytest

from tubeless import config
from tubeless.errors import LLMError
from tubeless.llm import (
    _LLM_TIMEOUT_SECONDS,
    ClaudeBackend,
    GeminiBackend,
    OllamaBackend,
    OpenAIBackend,
    _chat_complete,
    make_backend,
)


def _no_keys_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blind the key resolver: an empty config file and no env variables, so a
    real ~/.tubeless/config.env on the test machine cannot satisfy the lookup."""
    monkeypatch.setattr(config, "read_config", lambda *a, **k: {})
    for name in ("OPENAI_API_KEY", "CLAUDE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


# --- missing key ----------------------------------------------------------
def test_openai_backend_without_a_key_raises(monkeypatch: pytest.MonkeyPatch):
    _no_keys_anywhere(monkeypatch)
    with pytest.raises(LLMError):
        OpenAIBackend()


def test_claude_backend_without_a_key_raises(monkeypatch: pytest.MonkeyPatch):
    _no_keys_anywhere(monkeypatch)
    with pytest.raises(LLMError):
        ClaudeBackend()


def test_gemini_backend_without_a_key_raises(monkeypatch: pytest.MonkeyPatch):
    _no_keys_anywhere(monkeypatch)
    with pytest.raises(LLMError):
        GeminiBackend()


def test_claude_backend_without_the_package_raises_a_helpful_error(monkeypatch: pytest.MonkeyPatch):
    # anthropic is an optional extra; a missing install must surface as a one-line
    # LLMError telling the user how to fix it, not a raw ModuleNotFoundError.
    monkeypatch.setenv("CLAUDE_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "anthropic", None)  # makes `from anthropic import ...` fail
    with pytest.raises(LLMError) as raised:
        ClaudeBackend()
    assert "tubeless[claude]" in str(raised.value)


# --- Anthropic error wrapping + response parsing --------------------------
class _FakeAnthropicError(Exception):
    pass


def _fake_anthropic_module(*, create):
    """A stand-in ``anthropic`` module: an ``Anthropic`` client whose
    ``messages.create`` calls ``create``, plus the ``AnthropicError`` base."""
    module = types.ModuleType("anthropic")
    module.AnthropicError = _FakeAnthropicError

    class _Messages:
        def create(self, **kwargs):
            return create(**kwargs)

    class _Client:
        def __init__(self, *, api_key):
            self.messages = _Messages()

    module.Anthropic = _Client
    return module


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


def _install_fake_anthropic(monkeypatch, *, create):
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic_module(create=create))
    monkeypatch.setenv("CLAUDE_API_KEY", "test-key")


def test_claude_backend_joins_text_blocks(monkeypatch: pytest.MonkeyPatch):
    def create(**kwargs):
        # system is passed as its own argument, never as a message role
        assert kwargs["system"] == "be terse"
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
        assert kwargs["max_tokens"] == 2048
        return types.SimpleNamespace(content=[_text_block("one "), _text_block("line")])

    _install_fake_anthropic(monkeypatch, create=create)
    assert ClaudeBackend().complete("hi", system="be terse") == "one line"


def test_claude_backend_wraps_sdk_errors(monkeypatch: pytest.MonkeyPatch):
    def create(**kwargs):
        raise _FakeAnthropicError("boom")

    _install_fake_anthropic(monkeypatch, create=create)
    with pytest.raises(LLMError):
        ClaudeBackend().complete("hi")


def test_claude_backend_rejects_an_empty_completion(monkeypatch: pytest.MonkeyPatch):
    def create(**kwargs):
        return types.SimpleNamespace(content=[_text_block("   ")])

    _install_fake_anthropic(monkeypatch, create=create)
    with pytest.raises(LLMError):
        ClaudeBackend().complete("hi")


# --- backend selection (make_backend) -------------------------------------
def test_make_backend_defaults_the_model_per_vendor(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CLAUDE_API_KEY", "test-key")
    # openai client import is real but cheap; anthropic uses the fake if present.
    monkeypatch.setitem(sys.modules, "anthropic",
                        _fake_anthropic_module(create=lambda **k: None))

    assert make_backend("openai").model == "gpt-4o-mini"
    assert make_backend("claude").model == "claude-haiku-4-5-20251001"
    assert make_backend("claude", model="claude-sonnet-5").model == "claude-sonnet-5"


# --- Ollama backend (local, no key) ---------------------------------------
def test_ollama_backend_needs_no_key(monkeypatch: pytest.MonkeyPatch):
    # A local backend must construct with no API key set anywhere.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert OllamaBackend(model="llama3.1").model == "llama3.1"


def test_ollama_backend_targets_the_local_host_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    backend = OllamaBackend()
    assert str(backend._client.base_url).startswith("http://localhost:11434")


def test_make_backend_builds_ollama(monkeypatch: pytest.MonkeyPatch):
    assert make_backend("ollama").model == "llama3.1"
    assert make_backend("ollama", model="qwen2.5").model == "qwen2.5"


# --- Gemini backend (OpenAI-compatible cloud endpoint) --------------------
def test_make_backend_builds_gemini(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert make_backend("gemini").model == "gemini-flash-lite-latest"
    assert make_backend("gemini", model="gemini-2.5-pro").model == "gemini-2.5-pro"


def test_gemini_backend_targets_the_openai_compatible_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    backend = GeminiBackend()
    assert "generativelanguage.googleapis.com" in str(backend._client.base_url)


# --- shared OpenAI-style completion path -----------------------------------
def test_chat_complete_rejects_a_response_with_no_choices():
    # A content-filter/gateway response can carry an empty choices list; indexing
    # it would raise IndexError outside the LLMError hierarchy.
    def create(**kwargs):
        return types.SimpleNamespace(choices=[])

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )

    with pytest.raises(LLMError):
        _chat_complete(client, "gpt-x", "hi", system=None, label="OpenAI")


def test_chat_complete_wraps_an_sdk_error_as_llm_error():
    # An openai.OpenAIError must surface as LLMError for all three OpenAI-style
    # backends (OpenAI/Ollama/Gemini route through _chat_complete), so the CLI's
    # one-line failure holds instead of a raw SDK traceback.
    import openai

    def create(**kwargs):
        raise openai.OpenAIError("boom")

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )

    with pytest.raises(LLMError):
        _chat_complete(client, "gpt-x", "hi", system=None, label="OpenAI")


def test_chat_complete_rejects_an_empty_completion():
    # choices present but the content string is empty -> LLMError, never an empty
    # summary silently passed downstream.
    def create(**kwargs):
        message = types.SimpleNamespace(content="")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )

    with pytest.raises(LLMError):
        _chat_complete(client, "gpt-x", "hi", system=None, label="OpenAI")


def test_chat_complete_returns_text_and_passes_a_timeout():
    # Happy path: the reply text comes back, the system prompt is sent as its own
    # message, and a timeout is always passed so one wedged call can't hang a run.
    seen: dict[str, object] = {}

    def create(**kwargs):
        seen.update(kwargs)
        message = types.SimpleNamespace(content="the summary")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )

    text = _chat_complete(client, "gpt-x", "hi", system="be terse", label="OpenAI")

    assert text == "the summary"
    assert seen["timeout"] == _LLM_TIMEOUT_SECONDS
    assert seen["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
    ]
