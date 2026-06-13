import logging

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        from google.cloud import translate_v2 as translate
        _client = translate.Client()
    return _client


def translate_uk_to_fa(text: str) -> str:
    """Translate Ukrainian text to Persian. Returns original text on failure."""
    if not text or not text.strip():
        return text
    try:
        result = _get_client().translate(text, source_language="uk", target_language="fa")
        return result["translatedText"]
    except Exception:
        logger.exception("Translation failed — forwarding original text")
        return text
