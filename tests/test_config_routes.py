"""The Azerbaijani routing is gone: even a leftover DEST_CHANNEL_AZ in the
environment must not produce an 'az' language route."""

import importlib


def test_lang_routes_are_persian_only_even_with_az_env(monkeypatch):
    monkeypatch.setenv("DEBUG_MODE", "true")   # relaxes the required prod vars
    monkeypatch.setenv("SOURCE_CHANNEL", "@src")
    monkeypatch.setenv("DEST_CHANNEL", "@fa_dest")
    monkeypatch.setenv("DEST_CHANNEL_AZ", "@az_dest")  # stale env must be ignored

    import config
    importlib.reload(config)

    assert list(config.LANG_ROUTES.keys()) == ["fa"]
    assert not hasattr(config, "ROUTES_AZ")
    assert not hasattr(config, "DEST_CHANNELS_AZ")
