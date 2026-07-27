"""LLM completion backends, delegated to the thinchat library.

thinchat speaks to every vendor (claude, openai, gemini, ollama) behind one
``complete(prompt, *, system) -> str`` interface, so tubeless no longer hand-rolls
a client per vendor. What stays here is the tubeless-specific wiring:

  - the roster name each backend answers to on the command line (``BACKENDS``),
  - key resolution from tubeless's own credentials store (the environment or
    ``~/.config/tubeless/credentials.json``), handed to thinchat explicitly since
    thinchat otherwise reads its own environment variables,
  - the extraction-tuned request settings (a low temperature, a per-request
    timeout) that summarizing wants but generic chat does not, and
  - the vendor-error -> ``LLMError`` translation, so the rest of tubeless catches
    one exception type.

``summarize_transcript()`` and the digest depend only on the ``LLMBackend``
protocol, so the thinchat client -- wrapped in ``_Backend`` so its errors stay in
tubeless's hierarchy -- plugs in without touching the summary logic.
"""

from __future__ import annotations

from typing import Protocol, cast

from thinchat import Client, ThinchatError, make_client

from tubeless import credentials
from tubeless.errors import LLMError

__all__ = ["BACKENDS", "LLMBackend", "make_backend"]

# The LLM vendors tubeless offers, named as they appear on the command line / in
# config. Deliberately tubeless's own roster, NOT derived from thinchat's PROVIDERS:
# a backend is offered only where tubeless can resolve its key (credentials.Vendor)
# or it needs none (ollama), so a provider thinchat adds is not auto-exposed before
# tubeless can authenticate it. The reverse drift -- thinchat dropping a provider
# tubeless still lists -- needs no guard: make_client then raises UnknownProviderError,
# which make_backend already maps to LLMError below.
BACKENDS = ("claude", "openai", "gemini", "ollama")

# Summaries are extraction, not creative writing: a low temperature keeps the
# model on the requested format and off invented preambles. It matters most for
# smaller local models, which drift into greetings and numbered prose at the
# vendor-default temperature.
_LLM_TEMPERATURE = 0.2

# An upper bound tubeless controls, instead of each SDK's multi-minute default:
# the digest calls a backend once per video in a loop, so one wedged request
# must not stall the whole run.
_LLM_TIMEOUT_SECONDS = 60.0


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


class _Backend:
    """Adapts a thinchat client to the tubeless ``LLMBackend`` contract: it forwards
    ``complete`` to the client but translates a thinchat error raised *at call time*
    -- a rate limit, a dropped connection, an empty reply -- into
    ``tubeless.errors.LLMError``.

    Without this the vendor error (a ``thinchat.errors.LLMError``, which is not a
    ``TubelessError``) would escape the CLI's ``except TubelessError`` as a raw
    traceback instead of the one-line ``tubeless:`` failure. ``make_backend`` already
    maps construction-time errors; this extends the same guarantee to every request,
    so a transient API failure mid-digest aborts cleanly rather than crashing.
    """

    def __init__(self, client: Client) -> None:
        self._client = client
        self.model   = client.model

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        try:
            return self._client.complete(prompt, system=system)
        except ThinchatError as err:
            raise LLMError(f"the {self.model} backend failed: {err}") from err


def make_backend(name: str, *, model: str | None = None) -> LLMBackend:
    """Construct the backend named ``name`` (one of ``BACKENDS``); ``model``
    overrides thinchat's per-vendor default when given.

    The key is resolved from tubeless's credentials store (the environment or
    ``~/.config/tubeless/credentials.json``) and handed to thinchat explicitly;
    "ollama" is local and needs none. The extraction-tuned temperature and the
    per-request timeout are applied here so every backend a summary or digest
    builds carries them. The client is wrapped so that a thinchat failure at
    request time surfaces as a tubeless ``LLMError`` too (see ``_Backend``).

    Raises:
        LLMError: ``name`` is not one of ``BACKENDS``, the chosen vendor has no
            usable API key, or thinchat could not construct its client.
    """
    if name not in BACKENDS:
        raise LLMError(f"unknown backend {name!r}; choose one of {BACKENDS}")

    # ollama runs locally with no key; the cloud vendors resolve one from the
    # tubeless store. credentials.api_key accepts only the keyed vendors (its Vendor
    # type), so ollama is kept out of that lookup and the cast is sound in the else
    # branch -- name is one of BACKENDS minus "ollama" there.
    api_key = None if name == "ollama" else credentials.api_key(cast(credentials.Vendor, name))
    if name != "ollama" and not api_key:
        raise LLMError(
            f"no {name} API key: set {name.upper()}_API_KEY in the environment "
            "or add it to ~/.config/tubeless/credentials.json"
            + credentials.legacy_config_note()
        )

    try:
        client = make_client(
            name,
            model       = model,
            api_key     = api_key,
            temperature = _LLM_TEMPERATURE,
            timeout     = _LLM_TIMEOUT_SECONDS,
        )
    except ThinchatError as err:
        raise LLMError(f"could not start the {name} backend: {err}") from err
    return _Backend(client)
