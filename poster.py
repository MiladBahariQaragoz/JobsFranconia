import logging
import re
import html
import urllib.request
import urllib.error
import urllib.parse
import json

import config

logger = logging.getLogger(__name__)

_BASE_URL = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def _send(dest, text: str, parse_mode: str | None) -> tuple[bool, str]:
    """Low-level sendMessage. Returns (ok, detail) and never raises for an API
    rejection — the detail carries Telegram's error description for logging."""
    msg = {
        "chat_id": dest,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        msg["parse_mode"] = parse_mode
    payload = json.dumps(msg).encode("utf-8")

    req = urllib.request.Request(
        f"{_BASE_URL}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # Telegram returns a JSON body with the real reason (e.g. "can't parse
        # entities: ...") even on a 400 — surface it instead of a bare HTTPError.
        detail = e.read().decode("utf-8", "replace")
        return False, f"HTTP {e.code}: {detail}"
    except urllib.error.URLError as e:
        return False, f"network error: {e.reason}"

    if not body.get("ok"):
        return False, str(body)
    return True, str(body["result"]["message_id"])


def _html_to_plain(text: str) -> str:
    """Degrade an HTML post to plain text: turn <a href="u">label</a> into
    'label: u', strip any other tags, and unescape entities. Used as a fallback
    so a message still gets delivered if Telegram rejects the HTML."""
    text = re.sub(r'<a href="([^"]*)">(.*?)</a>', r"\2: \1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def post_to_channel(text: str, dest=None) -> None:
    """Send a message to a destination channel via the Bot API.

    `dest` is the chat_id/@username to post to; defaults to config.DEST_CHANNEL
    for backwards compatibility. Sent as HTML (translator.py emits a labelled
    <a> link and escapes everything else). Fails safe: a Telegram rejection is
    logged, and the post is retried as plain text so it is not silently lost.
    """
    if not text or not text.strip():
        logger.warning("Skipping empty message")
        return

    if dest is None:
        dest = config.DEST_CHANNEL

    ok, detail = _send(dest, text, parse_mode="HTML")
    if ok:
        logger.info("Posted to %s — message_id=%s", dest, detail)
        return

    # HTML was rejected (most often a bad entity). Log the real reason (visible
    # in Cloud Run logs), then retry as plain text so the post still lands (link
    # shown as a raw URL). Only a failed retry below is escalated to ERROR.
    logger.warning("HTML send to %s rejected, retrying as plain text — %s", dest, detail)
    plain = _html_to_plain(text)
    ok2, detail2 = _send(dest, plain, parse_mode=None)
    if ok2:
        logger.info("Posted to %s as plain text — message_id=%s", dest, detail2)
    else:
        logger.error("Plain-text retry to %s also failed — %s", dest, detail2)
