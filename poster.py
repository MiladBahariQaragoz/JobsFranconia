import logging
import urllib.request
import urllib.parse
import json

import config

logger = logging.getLogger(__name__)

_BASE_URL = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def post_to_channel(text: str) -> None:
    """Send a text message to the destination channel via the Bot API."""
    if not text or not text.strip():
        logger.warning("Skipping empty message")
        return

    payload = json.dumps({
        "chat_id": config.DEST_CHANNEL,
        "text": text,
        "parse_mode": "HTML",
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

    logger.info("Posted to %s — message_id=%s", config.DEST_CHANNEL,
                body["result"]["message_id"])
