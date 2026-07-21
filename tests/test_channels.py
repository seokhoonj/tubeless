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
        'source = "@examplechannel"\n'
        'label  = "예시 채널"\n'
        'detail = "deep"\n'
        '\n'
        '[[channel]]\n'
        'source = "@anotherchannel"\n'
        'label  = "다른 채널"\n',
    )
    channels = load_channels(path)

    assert channels[0] == Channel(source="@examplechannel", label="예시 채널", detail="deep")
    assert channels[1].detail == "deep"  # default when omitted


def test_load_channels_reads_a_title_filter(tmp_path):
    path = _write(
        tmp_path,
        '[[channel]]\nsource = "PLxxxxxxxxxx"\nlabel = "S"\ntitle_includes = ["Alice", "News"]\n',
    )
    assert load_channels(path)[0].title_includes == ("Alice", "News")


def test_load_channels_rejects_a_non_string_non_list_title_filter(tmp_path):
    path = _write(
        tmp_path,
        '[[channel]]\nsource = "@x"\nlabel = "X"\ntitle_includes = 123\n',
    )
    with pytest.raises(ConfigError):
        load_channels(path)


def test_load_channels_wraps_a_bare_string_filter(tmp_path):
    path = _write(tmp_path, '[[channel]]\nsource = "@x"\ntitle_includes = "Alice"\n')

    assert load_channels(path)[0].title_includes == ("Alice",)


def test_load_channels_defaults_to_no_filter(tmp_path):
    path = _write(tmp_path, '[[channel]]\nsource = "@x"\nlabel = "X"\n')

    assert load_channels(path)[0].title_includes == ()


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
