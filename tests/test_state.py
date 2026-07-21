"""Seen-set persistence: roundtrip and graceful handling of a missing/corrupt file."""

import pytest

from tubeless.errors import ConfigError
from tubeless.state import read_seen, write_seen


def test_write_then_read_roundtrips(tmp_path):
    path = tmp_path / "state.json"
    write_seen({"a", "b", "c"}, path)

    assert read_seen(path) == {"a", "b", "c"}


def test_read_seen_missing_file_is_empty(tmp_path):
    assert read_seen(tmp_path / "nope.json") == set()


def test_read_seen_corrupt_file_is_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")

    assert read_seen(path) == set()


def test_write_seen_creates_the_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "state.json"
    write_seen({"x"}, path)

    assert read_seen(path) == {"x"}


def test_read_seen_raises_on_an_unreadable_state_file(tmp_path):
    # A present-but-unreadable file must not read as "no state" (which would then
    # be overwritten, wiping the seen-set and re-summarizing the backlog). Here a
    # directory at the path makes read_text raise OSError.
    unreadable = tmp_path / "state.json"
    unreadable.mkdir()

    with pytest.raises(ConfigError):
        read_seen(unreadable)
