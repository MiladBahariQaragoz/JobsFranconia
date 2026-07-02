"""Tests for the contact-blocking rules in filter.py.

These cover `blocked_reason`, which decides whether a post must be dropped
because it routes applicants off-channel via a Telegram link, a Facebook link,
or a phone number. `has_blocked_content` is the boolean convenience wrapper.
"""

import pytest

from filter import blocked_reason, has_blocked_content


# --- Telegram links --------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Schreib mir: t.me/hans",
    "https://t.me/joinchat/AAAAA",
    "join telegram.me/foo",
    "telegram.dog/bar",
    "tg://resolve?domain=hans",
    "Kontakt: T.ME/Hans",             # case-insensitive
    "Bewerbung über Telegram möglich",  # bare word
])
def test_detects_telegram(text):
    assert blocked_reason(text) == "telegram"


# --- Facebook links --------------------------------------------------------

@pytest.mark.parametrize("text", [
    "facebook.com/jobs",
    "https://www.facebook.com/company/x",
    "m.facebook.com/x",
    "fb.me/abc",
    "fb.com/x",
    "Folge uns auf Facebook",         # bare word
])
def test_detects_facebook(text):
    assert blocked_reason(text) == "facebook"


# --- Phone numbers ---------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Ruf an: 0176 1234567",
    "+49 176 1234567",
    "0049 176 1234567",
    "Tel. 0911/1234567",
    "(0176) 123 4567",
    "0176-123-4567",
    "01761234567",
])
def test_detects_phone(text):
    assert blocked_reason(text) == "phone"


# --- Must NOT match (false-positive guards) --------------------------------

@pytest.mark.parametrize("text", [
    "🏢 DEMIR GmbH\n📍 90449 Nürnberg\n💶 2500€",  # postal code + salary
    "Standort: 01067 Dresden",                       # postal code starting 0
    "Arbeitszeit 8:00-17:00 Uhr",                    # time
    "Beginn ab 01.07.2026",                          # date
    "Gehalt 2500-3000 €",                            # salary range
    "Team von 5-10 Personen",                        # small range
    "Bewirb dich über joboo.de/stelle",              # allowed domain
    "Kontakt über unsere Webseite",                  # no link/phone
    "info@firma.de",                                 # email, not tg/fb/phone
    "",                                              # empty
])
def test_allows_clean_posts(text):
    assert blocked_reason(text) is None


def test_has_blocked_content_wrapper():
    assert has_blocked_content("t.me/hans") is True
    assert has_blocked_content("clean job post") is False


def test_first_matching_reason_is_returned():
    # A post can trip several rules; any non-None reason is enough to drop it.
    assert blocked_reason("t.me/x and 0176 1234567") in ("telegram", "phone")
