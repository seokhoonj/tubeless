"""Backend wiring tests: name validation, key resolution, request settings, and
vendor-error wrapping -- no network.

The per-vendor SDK calls live in the thinchat library (with their own tests);
these exercise tubeless's thin boundary over it -- the roster guard, the
missing-key guard, the extraction-tuned temperature/timeout, the model
passthrough, and the ThinchatError -> LLMError translation.
"""

import types

import pytest

from tubeless import credentials, llm
from tubeless.errors import LLMError
from tubeless.llm import make_backend


def _no_keys_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blind the key resolver: no stored secret and no env variables, so a real
    ~/.config/tubeless/credentials.json on the test machine cannot satisfy the lookup."""
    monkeypatch.setattr(credentials, "secret", lambda name: None)
    for name in ("OPENAI_API_KEY", "CLAUDE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _capture_make_client(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace thinchat's make_client with a stub that records its kwargs and
    returns a stand-in client, so the tubeless -> thinchat call can be inspected
    without constructing a real SDK client."""
    seen: dict = {}

    def fake_make_client(provider: str, **kwargs: object):
        seen["provider"] = provider
        seen.update(kwargs)
        return types.SimpleNamespace(model=kwargs.get("model") or "default-model")

    monkeypatch.setattr(llm, "make_client", fake_make_client)
    return seen


# --- roster guard ---------------------------------------------------------
def test_backends_is_the_deliberate_closed_roster():
    # BACKENDS is tubeless's own closed set -- a backend is offered only where tubeless
    # can resolve its key or it needs none -- NOT derived from thinchat's providers. Pin
    # the exact membership so an accidental addition (or a switch to deriving it) is caught,
    # not just the removal of a supported one.
    assert set(llm.BACKENDS) == {"claude", "openai", "gemini", "ollama"}


def test_make_backend_rejects_an_unknown_vendor():
    # An out-of-roster name must raise a domain LLMError (catchable via the
    # documented TubelessError hierarchy), not a bare, uncatchable error.
    with pytest.raises(LLMError):
        make_backend("gpt")


# --- missing key ----------------------------------------------------------
@pytest.mark.parametrize("name", ["openai", "claude", "gemini"])
def test_make_backend_without_a_key_raises(monkeypatch: pytest.MonkeyPatch, name: str):
    _no_keys_anywhere(monkeypatch)
    with pytest.raises(LLMError) as raised:
        make_backend(name)
    # the message names the env var so the user knows which one to set
    assert f"{name.upper()}_API_KEY" in str(raised.value)


@pytest.mark.parametrize("stored_key", [None, ""])
def test_make_backend_rejects_a_missing_or_empty_key(monkeypatch: pytest.MonkeyPatch, stored_key):
    # The guard is `not api_key`, so an empty string is rejected the same as None -- an
    # empty credential must never reach thinchat (guarding against a `is None` regression).
    monkeypatch.setattr(credentials, "api_key", lambda vendor: stored_key)
    for name in ("OPENAI_API_KEY", "CLAUDE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(LLMError):
        make_backend("openai")


# --- request settings handed to thinchat ----------------------------------
def test_make_backend_applies_the_extraction_temperature_and_timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen = _capture_make_client(monkeypatch)
    make_backend("openai")
    # Hardcoded, not compared to the module constants -- the test pins the actual
    # extraction-tuned values (a low temperature, a 60s bound), so a change to either
    # constant is caught rather than moving both sides of the assertion together.
    assert seen["temperature"] == 0.2
    assert seen["timeout"] == 60.0


def test_make_backend_resolves_the_key_from_the_store(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "resolved-key")
    seen = _capture_make_client(monkeypatch)
    make_backend("gemini")
    assert seen["provider"] == "gemini"
    assert seen["api_key"] == "resolved-key"


def test_make_backend_passes_a_model_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen = _capture_make_client(monkeypatch)
    make_backend("openai", model="gpt-4o")
    assert seen["model"] == "gpt-4o"


def test_make_backend_leaves_the_default_model_to_thinchat(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen = _capture_make_client(monkeypatch)
    make_backend("openai")
    assert seen["model"] is None   # None -> thinchat fills the vendor default


# --- ollama: local, no key ------------------------------------------------
def test_make_backend_builds_ollama_without_a_key(monkeypatch: pytest.MonkeyPatch):
    _no_keys_anywhere(monkeypatch)
    seen = _capture_make_client(monkeypatch)
    make_backend("ollama")
    assert seen["provider"] == "ollama"
    assert seen["api_key"] is None   # a local backend must construct with no key


# --- vendor-error wrapping ------------------------------------------------
def test_make_backend_wraps_a_thinchat_error_as_llm_error(monkeypatch: pytest.MonkeyPatch):
    # A failure constructing the client must surface as LLMError (catchable via the
    # TubelessError hierarchy), carrying the vendor name and chaining the original.
    from thinchat import ThinchatError

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    original = ThinchatError("bad client")

    def raise_thinchat_error(provider: str, **kwargs: object):
        raise original

    monkeypatch.setattr(llm, "make_client", raise_thinchat_error)
    with pytest.raises(LLMError) as raised:
        make_backend("openai")
    assert "openai" in str(raised.value)
    assert raised.value.__cause__ is original   # chained, not swallowed


def test_make_backend_does_not_wrap_a_non_thinchat_construction_error(monkeypatch: pytest.MonkeyPatch):
    # make_backend wraps only ThinchatError at construction; a genuine bug (not a vendor
    # failure) must propagate unchanged -- the construction-time twin of the runtime
    # narrowness test, guarding `except ThinchatError` from being widened to Exception.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    original = RuntimeError("a construction bug")

    def raise_runtime_error(provider: str, **kwargs: object):
        raise original

    monkeypatch.setattr(llm, "make_client", raise_runtime_error)
    with pytest.raises(RuntimeError) as raised:
        make_backend("openai")
    assert raised.value is original   # propagated unchanged, not wrapped in LLMError


def test_backend_translates_a_runtime_thinchat_error(monkeypatch: pytest.MonkeyPatch):
    # A thinchat error raised at .complete() time (a rate limit, a dropped connection)
    # must surface as a tubeless LLMError -- not a raw thinchat error, which is not a
    # TubelessError and would escape the CLI's `except TubelessError` as a traceback.
    from thinchat import LLMError as ThinchatLLMError

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    original = ThinchatLLMError("429 rate limited")

    class _FailingClient:
        model = "gpt-4o-mini"

        def complete(self, prompt: str, *, system: str | None = None) -> str:
            raise original

    monkeypatch.setattr(llm, "make_client", lambda *a, **k: _FailingClient())

    backend = make_backend("openai")
    with pytest.raises(LLMError) as raised:
        backend.complete("hi")
    assert "gpt-4o-mini" in str(raised.value)   # the message names the model that failed
    assert raised.value.__cause__ is original    # the original is chained, not swallowed


def test_backend_does_not_translate_a_non_thinchat_error(monkeypatch: pytest.MonkeyPatch):
    # _Backend.complete catches only ThinchatError, so a genuine programming error (a bug,
    # not a vendor failure) propagates unchanged rather than being masked as a backend
    # failure -- this guards `except ThinchatError` from being widened to `except Exception`.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    original = RuntimeError("a bug, not a vendor failure")

    class _BuggyClient:
        model = "gpt-4o-mini"

        def complete(self, prompt: str, *, system: str | None = None) -> str:
            raise original

    monkeypatch.setattr(llm, "make_client", lambda *a, **k: _BuggyClient())

    backend = make_backend("openai")
    with pytest.raises(RuntimeError) as raised:
        backend.complete("hi")
    assert raised.value is original   # propagated unchanged, not wrapped in LLMError


def test_backend_forwards_the_prompt_system_and_reply(monkeypatch: pytest.MonkeyPatch):
    # _Backend.complete forwards the prompt and the keyword-only system to the client and
    # returns its reply unchanged -- the adapter's happy path, not just its error paths.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen: dict = {}

    class _RecordingClient:
        model = "gpt-4o-mini"

        def complete(self, prompt: str, *, system: str | None = None) -> str:
            seen["prompt"] = prompt
            seen["system"] = system
            return "the reply"

    monkeypatch.setattr(llm, "make_client", lambda *a, **k: _RecordingClient())

    result = make_backend("openai").complete("a prompt", system="be terse")
    assert result == "the reply"
    assert seen == {"prompt": "a prompt", "system": "be terse"}


# --- end-to-end wiring (real thinchat construction, offline) ---
# Constructing a client is offline (no request is made yet). This confirms the whole
# tubeless -> thinchat wiring holds for every vendor -- a client is built and exposes a
# model. It deliberately does NOT assert the exact default string: which model a vendor
# defaults to is thinchat's to own and to change, so pinning it here would couple
# tubeless's suite to thinchat's choices. The delegation contract (tubeless passes
# model=None) is covered by test_make_backend_leaves_the_default_model_to_thinchat.
@pytest.mark.parametrize("name", ["openai", "gemini", "claude", "ollama"])
def test_make_backend_exposes_a_model_per_vendor(monkeypatch: pytest.MonkeyPatch, name: str):
    if name != "ollama":
        monkeypatch.setenv(f"{name.upper()}_API_KEY", "test-key")
    model = make_backend(name).model
    assert isinstance(model, str) and model
