"""Shared fixtures for tubeless tests."""

from __future__ import annotations

import pytest

from tubeless import credentials


@pytest.fixture(autouse=True)
def _reset_credbox_binding(monkeypatch):
    """Reset the cached credbox facade and the store-binding env vars around every test,
    so a binding one test sets (or a dev's shell) cannot leak into another."""
    monkeypatch.delenv("TUBELESS_STORE_APP", raising=False)
    monkeypatch.delenv("TUBELESS_NAMESPACE", raising=False)
    credentials._get_credentials.cache_clear()
    yield
    credentials._get_credentials.cache_clear()
