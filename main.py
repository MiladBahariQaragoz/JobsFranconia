import asyncio
import logging
import signal
import sys

from telethon import TelegramClient, events
from telethon.sessions import StringSession

import config
from filter import is_job_posting

if not config.DEBUG_MODE:
    from translator import translate_uk_to_fa
    from poster import post_to_channel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

if config.DEBUG_MODE:
    logger.info("=== DEBUG MODE ENABLED — messages will be filtered and printed only ===")

client = TelegramClient(
    StringSession(config.TELEGRAM_SESSION_STR),
    config.TELEGRAM_API_ID,
    config.TELEGRAM_API_HASH,
)



@client.on(events.NewMessage(chats=config.SOURCE_CHANNEL))
async def handle_new_post(event):
    message = event.message
    original_text = message.text or message.message
    if not original_text:
        logger.info("Skipping non-text message (id=%s)", message.id)
        return

    if not is_job_posting(original_text):
        logger.info("Filtered out non-job-posting message (id=%s)", message.id)
        return

    logger.info("Job posting received (id=%s): %.80s…", message.id, original_text)

    if config.DEBUG_MODE:
        print(f"\n{'='*60}\n[DEBUG] Job posting (id={message.id}):\n{original_text}\n{'='*60}\n")
        return

    translated = await asyncio.get_event_loop().run_in_executor(
        None, translate_uk_to_fa, original_text
    )

    await asyncio.get_event_loop().run_in_executor(
        None, post_to_channel, translated
    )

    logger.info("Successfully forwarded post id=%s", message.id)


async def main():
    await client.start()
    me = await client.get_me()
    logger.info("Logged in as %s (id=%s)", me.username or me.first_name, me.id)

    # Resolve the channel entity at startup so Telethon can match incoming updates.
    try:
        entity = await client.get_entity(config.SOURCE_CHANNEL)
        logger.info("Resolved source channel: %s (id=%s)", getattr(entity, 'title', config.SOURCE_CHANNEL), entity.id)
    except Exception:
        logger.exception("Failed to resolve source channel '%s' — updates may not arrive", config.SOURCE_CHANNEL)

    logger.info("Listening on channel: %s", config.SOURCE_CHANNEL)

    # add_signal_handler is Unix-only; on Windows fall back to KeyboardInterrupt
    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(shutdown()))

    await client.run_until_disconnected()


async def shutdown():
    logger.info("Shutting down...")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
