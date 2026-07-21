"""Backend tests: key handling and vendor-error wrapping, no network.

The real SDK objects are faked so the tests exercise tubeless's own wiring --
the missing-key guard, the SDK-error -> LLMError translation, and the CLI's
per-vendor backend selection -- without an API call.
"""

import sys
import types

import pytest

from tubeless import config
from tubeless.cli import _make_backend
from tubeless.errors import LLMError
from tubeless.llm import AnthropicBackend, GeminiBackend, OllamaBackend, OpenAIBackend


def _no_keys_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blind the key resolver: an empty config file and no env variables, so a
    real ~/.tubeless/config.env on the test machine cannot satisfy the lookup."""
    monkeypatch.setattr(config, "read_config", lambda *a, **k: {})
    for name in ("OPENAI_SECRET_KEY", "OPENAI_API_KEY",
                 "ANTHROPIC_SECRET_KEY", "ANTHROPIC_API_KEY",
                 "GEMINI_SECRET_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


# --- missing key ----------------------------------------------------------
def test_openai_backend_without_a_key_raises(monkeypatch: pytest.MonkeyPatch):
    _no_keys_anywhere(monkeypatch)
    with pytest.raises(LLMError):
        OpenAIBackend()


def test_anthropic_backend_without_a_key_raises(monkeypatch: pytest.MonkeyPatch):
    _no_keys_anywhere(monkeypatch)
    with pytest.raises(LLMError):
        AnthropicBackend()


def test_gemini_backend_without_a_key_raises(monkeypatch: pytest.MonkeyPatch):
    _no_keys_anywhere(monkeypatch)
    with pytest.raises(LLMError):
        GeminiBackend()


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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_anthropic_backend_joins_text_blocks(monkeypatch: pytest.MonkeyPatch):
    def create(**kwargs):
        # system is passed as its own argument, never as a message role
        assert kwargs["system"] == "be terse"
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
        assert kwargs["max_tokens"] == 2048
        return types.SimpleNamespace(content=[_text_block("한 "), _text_block("줄")])

    _install_fake_anthropic(monkeypatch, create=create)
    assert AnthropicBackend().complete("hi", system="be terse") == "한 줄"


def test_anthropic_backend_wraps_sdk_errors(monkeypatch: pytest.MonkeyPatch):
    def create(**kwargs):
        raise _FakeAnthropicError("boom")

    _install_fake_anthropic(monkeypatch, create=create)
    with pytest.raises(LLMError):
        AnthropicBackend().complete("hi")


def test_anthropic_backend_rejects_an_empty_completion(monkeypatch: pytest.MonkeyPatch):
    def create(**kwargs):
        return types.SimpleNamespace(content=[_text_block("   ")])

    _install_fake_anthropic(monkeypatch, create=create)
    with pytest.raises(LLMError):
        AnthropicBackend().complete("hi")


# --- CLI backend selection ------------------------------------------------
def test_make_backend_defaults_the_model_per_vendor(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # openai client import is real but cheap; anthropic uses the fake if present.
    monkeypatch.setitem(sys.modules, "anthropic",
                        _fake_anthropic_module(create=lambda **k: None))

    assert _make_backend("openai", None).model == "gpt-4o-mini"
    assert _make_backend("anthropic", None).model == "claude-haiku-4-5-20251001"
    assert _make_backend("anthropic", "claude-sonnet-5").model == "claude-sonnet-5"


# --- Ollama backend (local, no key) ---------------------------------------
def test_ollama_backend_needs_no_key(monkeypatch: pytest.MonkeyPatch):
    # A local backend must construct with no API key set anywhere.
    for name in ("OPENAI_SECRET_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert OllamaBackend(model="llama3.1").model == "llama3.1"


def test_ollama_backend_targets_the_local_host_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    backend = OllamaBackend()
    assert str(backend._client.base_url).startswith("http://localhost:11434")


def test_make_backend_builds_ollama(monkeypatch: pytest.MonkeyPatch):
    assert _make_backend("ollama", None).model == "llama3.1"
    assert _make_backend("ollama", "qwen2.5").model == "qwen2.5"


# --- Gemini backend (OpenAI-compatible cloud endpoint) --------------------
def test_make_backend_builds_gemini(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert _make_backend("gemini", None).model == "gemini-flash-lite-latest"
    assert _make_backend("gemini", "gemini-2.5-pro").model == "gemini-2.5-pro"


def test_gemini_backend_targets_the_openai_compatible_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    backend = GeminiBackend()
    assert "generativelanguage.googleapis.com" in str(backend._client.base_url)
