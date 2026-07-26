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
        'detail = "deep"\n'
        '\n'
        '[[channel]]\n'
        'source = "@anotherchannel"\n',
    )
    channels = load_channels(path)

    assert channels[0] == Channel(source="@examplechannel", detail="deep")
    assert channels[1].detail == "deep"  # default when omitted


def test_load_channels_reads_a_title_filter(tmp_path):
    path = _write(
        tmp_path,
        '[[channel]]\nsource = "PLxxxxxxxxxx"\ntitle_includes = ["Alice", "News"]\n',
    )
    assert load_channels(path)[0].includes == ("Alice", "News")


def test_load_channels_reads_a_title_exclude_filter(tmp_path):
    path = _write(
        tmp_path,
        '[[channel]]\nsource = "@x"\ntitle_excludes = ["LIVE"]\n',
    )
    channel = load_channels(path)[0]
    assert channel.excludes == ("LIVE",)
    assert channel.includes == ()


def test_load_channels_accepts_legacy_includes_and_excludes_keys(tmp_path):
    # The pre-title_ config spelling still resolves, so an older channels.toml
    # keeps working after the key rename.
    path = _write(
        tmp_path,
        '[[channel]]\nsource = "@x"\nincludes = ["Alice"]\nexcludes = ["LIVE"]\n',
    )
    channel = load_channels(path)[0]
    assert channel.includes == ("Alice",)
    assert channel.excludes == ("LIVE",)


def test_load_channels_prefers_canonical_keys_over_legacy_aliases(tmp_path):
    # When both spellings are present, the title_* keys win, so an old key left in
    # the file cannot silently override the intended filter.
    path = _write(
        tmp_path,
        '[[channel]]\nsource = "@x"\n'
        'title_includes = ["canonical"]\nincludes = ["legacy"]\n'
        'title_excludes = ["skip-canonical"]\nexcludes = ["skip-legacy"]\n',
    )
    channel = load_channels(path)[0]
    assert channel.includes == ("canonical",)
    assert channel.excludes == ("skip-canonical",)


@pytest.mark.parametrize(
    ("canonical", "legacy"),
    [("title_includes", "includes"), ("title_excludes", "excludes")],
)
def test_load_channels_rejects_a_malformed_canonical_key_over_a_valid_legacy(
    tmp_path, canonical, legacy,
):
    # A present-but-malformed canonical key must be rejected (and named), never
    # silently masked by a valid legacy alias sitting in the same entry.
    path = _write(
        tmp_path,
        f'[[channel]]\nsource = "@x"\n{canonical} = 123\n{legacy} = ["ok"]\n',
    )
    with pytest.raises(ConfigError, match=rf"{canonical} must be a string or a list, got int"):
        load_channels(path)


@pytest.mark.parametrize("key", ["title_includes", "includes"])
def test_load_channels_rejects_a_non_string_non_list_include_filter(tmp_path, key):
    # The error must name the key the user actually wrote, not the canonical spelling.
    path = _write(tmp_path, f'[[channel]]\nsource = "@x"\n{key} = 123\n')
    with pytest.raises(ConfigError, match=rf"{key} must be a string or a list, got int"):
        load_channels(path)


@pytest.mark.parametrize("key", ["title_excludes", "excludes"])
def test_load_channels_rejects_a_non_string_non_list_exclude_filter(tmp_path, key):
    path = _write(tmp_path, f'[[channel]]\nsource = "@x"\n{key} = 123\n')
    with pytest.raises(ConfigError, match=rf"{key} must be a string or a list, got int"):
        load_channels(path)


@pytest.mark.parametrize("key", ["title_includes", "includes"])
def test_load_channels_wraps_a_bare_string_filter(tmp_path, key):
    path = _write(tmp_path, f'[[channel]]\nsource = "@x"\n{key} = "Alice"\n')

    assert load_channels(path)[0].includes == ("Alice",)


@pytest.mark.parametrize("key", ["title_excludes", "excludes"])
def test_load_channels_wraps_a_bare_string_exclude(tmp_path, key):
    path = _write(tmp_path, f'[[channel]]\nsource = "@x"\n{key} = "LIVE"\n')

    assert load_channels(path)[0].excludes == ("LIVE",)


def test_load_channels_defaults_to_no_filter(tmp_path):
    path = _write(tmp_path, '[[channel]]\nsource = "@x"\n')

    channel = load_channels(path)[0]
    assert channel.includes == ()
    assert channel.excludes == ()


def test_load_channels_accepts_a_legacy_handle_key(tmp_path):
    path = _write(tmp_path, '[[channel]]\nhandle = "@x"\n')

    assert load_channels(path)[0].source == "@x"


def test_load_channels_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_channels(tmp_path / "nope.toml")


def test_load_channels_no_entries_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_channels(_write(tmp_path, "# empty\n"))


def test_load_channels_missing_source_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_channels(_write(tmp_path, '[[channel]]\ndetail = "deep"\n'))


def test_load_channels_bad_detail_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_channels(_write(tmp_path, '[[channel]]\nsource = "@x"\ndetail = "huge"\n'))


def test_load_channels_default_path_is_channels_path(monkeypatch, tmp_path):
    # The no-path default replaced the old CHANNELS_PATH constant; verify it routes
    # through channels_path() (a lazy resolve).
    import tubeless.channels as channels_module
    target = tmp_path / "channels.toml"
    target.write_text('[[channel]]\nsource = "@x"\n', encoding="utf-8")
    monkeypatch.setattr(channels_module, "channels_path", lambda: target)
    assert load_channels()[0].source == "@x"   # no path -> default channels_path()


def test_channels_path_hangs_off_the_config_dir(monkeypatch, tmp_path):
    import tubeless.channels as channels_module
    from tubeless.channels import channels_path
    monkeypatch.setattr(channels_module, "config_dir", lambda: tmp_path)
    assert channels_path() == tmp_path / "channels.toml"
