import logging
import urllib.request
import urllib.parse
import json

import config

logger = logging.getLogger(__name__)

_BASE_URL = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def post_to_channel(text: str, dest=None) -> None:
    """Send a text message to a destination channel via the Bot API.

    `dest` is the chat_id/@username to post to; defaults to config.DEST_CHANNEL
    for backwards compatibility.
    """
    if not text or not text.strip():
        logger.warning("Skipping empty message")
        return

    if dest is None:
        dest = config.DEST_CHANNEL

    # HTML parse mode: the post carries a labelled hyperlink (<a>) for the
    # application link. translator.py escapes every other dynamic value, so bare
    # '&' / '<' in company names or URLs can't break Telegram's entity parser.
    payload = json.dumps({
        "chat_id": dest,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{_BASE_URL}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())

    if not body.get("ok"):
        raise RuntimeError(f"Telegram API error: {body}")

    logger.info("Posted to %s — message_id=%s", dest,
                body["result"]["message_id"])
