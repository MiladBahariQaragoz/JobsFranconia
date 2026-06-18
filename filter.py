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
