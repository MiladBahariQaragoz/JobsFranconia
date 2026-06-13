import logging

logger = logging.getLogger(__name__)

# Emoji markers that identify a structured job posting from the source channel.
# A message must contain at least MIN_MARKERS of these to be processed.
_JOB_MARKERS = ["🏢", "💶", "📍", "📂", "🛡"]
_MIN_MARKERS = 3


def is_job_posting(text: str) -> bool:
    count = sum(1 for m in _JOB_MARKERS if m in text)
    if count < _MIN_MARKERS:
        logger.debug("Message skipped: only %d/%d job markers found", count, len(_JOB_MARKERS))
        return False
    return True
