import logging
import re

logger = logging.getLogger(__name__)

# Emoji markers that identify a structured job posting from the source channel.
# A message must contain at least MIN_MARKERS of these to be processed.
_JOB_MARKERS = ["🏢", "💶", "📍", "📂", "🛡"]
_MIN_MARKERS = 3


def _location_in_franconia(text: str) -> bool:
    """Region gate based on the 📍 location line.

    Franconia/Bavaria postal codes all start with 9. Rule: if the location value
    has a number (a postal code), that number must start with '9' — otherwise the
    posting is for another region and the whole message is dropped. A location
    with no number imposes no constraint (kept).
    """
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("📍"):
            value = s.split(":", 1)[1] if ":" in s else s[1:]
            num = re.search(r"\d+", value)
            if num and not num.group(0).startswith("9"):
                return False
            return True
    return True  # no location line — let the marker check decide


def is_job_posting(text: str) -> bool:
    count = sum(1 for m in _JOB_MARKERS if m in text)
    if count < _MIN_MARKERS:
        logger.debug("Message skipped: only %d/%d job markers found", count, len(_JOB_MARKERS))
        return False
    if not _location_in_franconia(text):
        logger.info("Message dropped: location postal code is outside Franconia (not 9xxxx)")
        return False
    return True


# ---------------------------------------------------------------------------
# Off-channel contact rules
#
# A post is DROPPED (see main.process_message) when it tries to route
# applicants off the channel via a Telegram link, a Facebook link, or a phone
# number — anywhere in the post, INCLUDING the apply link. These patterns are
# deliberately broad (they also match the bare words "telegram"/"facebook");
# tune them here — this is the single place that owns the rule.
# ---------------------------------------------------------------------------

_TELEGRAM_RE = re.compile(
    r"\btelegram\b"                             # bare word "telegram"
    r"|(?:https?://)?t\.me\b"                   # t.me links (scheme optional)
    r"|(?:https?://)?telegram\.(?:me|dog|org)\b"
    r"|tg://",                                  # tg:// deep link
    re.IGNORECASE,
)

_FACEBOOK_RE = re.compile(
    r"\bfacebook\b"                             # bare word "facebook" (covers *.facebook.com)
    r"|(?:https?://)?fb\.(?:com|me|watch|gg)\b"  # fb.com / fb.me / fb.watch / fb.gg
    r"|fb://",
    re.IGNORECASE,
)

# Candidate phone sequences start at a '+' or '0' — the hallmark of an
# international or German national number — and run through digits and the usual
# separators (space, parentheses, slash, dash). Franconian postal codes start
# with 9, salaries/years don't start with 0/+, and dates (dot-separated) and
# times (colon-separated) use separators kept out of the character class, so
# none of them form a candidate. A candidate counts as a phone number only once
# it carries at least _MIN_PHONE_DIGITS real digits.
_PHONE_CANDIDATE_RE = re.compile(r"[+0][\d()/\s\-]{5,}\d")
_MIN_PHONE_DIGITS = 7


def _has_phone(text: str) -> bool:
    for m in _PHONE_CANDIDATE_RE.finditer(text):
        if len(re.sub(r"\D", "", m.group(0))) >= _MIN_PHONE_DIGITS:
            return True
    return False


def blocked_reason(text: str) -> str | None:
    """Return why a post must be dropped, or None if it is clean.

    One of ``"telegram"``, ``"facebook"``, ``"phone"`` — the first off-channel
    contact method found. Pure function over the post text; the caller passes
    the visible text plus any link URLs it has unpacked from Telegram entities.
    """
    if not text:
        return None
    if _TELEGRAM_RE.search(text):
        return "telegram"
    if _FACEBOOK_RE.search(text):
        return "facebook"
    if _has_phone(text):
        return "phone"
    return None


def has_blocked_content(text: str) -> bool:
    """True when ``text`` carries a Telegram/Facebook link or a phone number."""
    return blocked_reason(text) is not None
