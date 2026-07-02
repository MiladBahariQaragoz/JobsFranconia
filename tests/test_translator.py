"""Tests for translator.py: the MyMemory backup and the Persian-only setup.

translator.py imports only the stdlib at module load (the Google client is
imported lazily inside _get_client), so it can be exercised without the Google
or Telegram dependencies. The Google client and the MyMemory HTTP call are the
only things stubbed — everything else is the real code.
"""

import json

import pytest

import translator


class _FakeResp:
    """Minimal stand-in for the urllib response context manager."""
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- Azerbaijani removed ---------------------------------------------------

def test_azerbaijani_language_removed():
    assert "az" not in translator._LANGS
    assert "fa" in translator._LANGS


# --- MyMemory HTTP call parsing --------------------------------------------

def test_mymemory_get_returns_translated_text(monkeypatch):
    payload = {"responseData": {"translatedText": "سلام دنیا"}, "responseStatus": 200}
    monkeypatch.setattr(translator.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResp(payload))
    assert translator._mymemory_get("Привіт світ", "uk", "fa") == "سلام دنیا"


def test_mymemory_get_raises_on_error_status(monkeypatch):
    payload = {"responseData": {"translatedText": ""}, "responseStatus": 403,
               "responseDetails": "INVALID LANGUAGE PAIR"}
    monkeypatch.setattr(translator.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResp(payload))
    with pytest.raises(Exception):
        translator._mymemory_get("x", "uk", "fa")


# --- Chunking (MyMemory has a per-request length cap) ----------------------

def test_mymemory_translate_preserves_line_structure(monkeypatch):
    monkeypatch.setattr(translator, "_mymemory_get",
                        lambda text, s, t: text.upper())
    out = translator._mymemory_translate("line one\nline two\n\nlast", "uk", "fa")
    assert out == "LINE ONE\nLINE TWO\n\nLAST"


def test_mymemory_translate_splits_lines_over_the_limit(monkeypatch):
    limit = translator._MYMEMORY_LIMIT
    sent = []

    def fake_get(text, s, t):
        sent.append(text)
        assert len(text) <= limit  # never exceed the API's per-request cap
        return text

    monkeypatch.setattr(translator, "_mymemory_get", fake_get)
    long_line = " ".join(["wort"] * 400)  # ~2000 chars, one line
    out = translator._mymemory_translate(long_line, "uk", "fa")
    assert len(sent) > 1                    # actually split
    assert out.split() == long_line.split()  # every word preserved, in order


# --- Fallback: Google fails -> MyMemory ------------------------------------

def test_translate_falls_back_to_mymemory_when_google_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("google is down")
    monkeypatch.setattr(translator, "_get_client", boom)
    monkeypatch.setattr(translator, "_mymemory_translate",
                        lambda text, s, t: f"MM[{text}]")
    assert translator._translate("Привіт", "fa") == "MM[Привіт]"


def test_translate_batch_falls_back_per_item_when_google_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("google is down")
    monkeypatch.setattr(translator, "_get_client", boom)
    monkeypatch.setattr(translator, "_mymemory_translate",
                        lambda text, s, t: text.upper())
    assert translator._translate_batch(["ab", "cd"], "de") == ["AB", "CD"]
