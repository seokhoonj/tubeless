"""Channel-config parsing: valid entries, defaults, and rejected input."""

import pytest

from tubeless.channels import Channel, load_channels
from tubeless.errors import ConfigError


def _write(tmp_path, text):
    path = tmp_path / "channels.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_channels_reads_entries_with_defaults(tmp_path):
    path = _write(
        tmp_path,
        '[[channel]]\n'
        'source = "@superstocktv"\n'
        'label  = "수페TV"\n'
        'detail = "deep"\n'
        '\n'
        '[[channel]]\n'
        'source = "@lecture"\n'
        'label  = "강의"\n',
    )
    channels = load_channels(path)

    assert channels[0] == Channel(source="@superstocktv", label="수페TV", detail="deep")
    assert channels[1].detail == "deep"  # default when omitted


def test_load_channels_accepts_a_legacy_handle_key(tmp_path):
    path = _write(tmp_path, '[[channel]]\nhandle = "@x"\nlabel = "X"\n')

    assert load_channels(path)[0].source == "@x"


def test_load_channels_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_channels(tmp_path / "nope.toml")


def test_load_channels_no_entries_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_channels(_write(tmp_path, "# empty\n"))


def test_load_channels_missing_source_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_channels(_write(tmp_path, '[[channel]]\nlabel = "no source"\n'))


def test_load_channels_bad_detail_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_channels(_write(tmp_path, '[[channel]]\nsource = "@x"\ndetail = "huge"\n'))
