import asyncio
import logging
import signal
import sys
import os
import threading
import http.server

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

from admin_logger import TelegramAdminHandler
admin_handler = TelegramAdminHandler()
admin_handler.setLevel(logging.ERROR)
admin_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
logging.getLogger().addHandler(admin_handler)

if config.DEBUG_MODE:
    logger.info("=== DEBUG MODE ENABLED — messages will be filtered and printed only ===")

from telethon.sessions import MemorySession

client = TelegramClient(
    StringSession(config.TELEGRAM_SESSION_STR),
    config.TELEGRAM_API_ID,
    config.TELEGRAM_API_HASH,
)

bot_client = TelegramClient(
    MemorySession(),
    config.TELEGRAM_API_ID,
    config.TELEGRAM_API_HASH,
)

@bot_client.on(events.NewMessage(pattern="/status"))
async def handle_status_cmd(event):
    if config.ADMIN_ID and str(event.sender_id) != str(config.ADMIN_ID):
        return
    await event.reply("✅ **Jobs Franconia Bot** is running and listening for new posts!")

@bot_client.on(events.NewMessage(pattern="/ping"))
async def handle_ping_cmd(event):
    if config.ADMIN_ID and str(event.sender_id) != str(config.ADMIN_ID):
        return
    await event.reply("Pong! 🏓")


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
    
    # Debug feature: Send a copy to the admin
    if config.ADMIN_ID and config.TELEGRAM_BOT_TOKEN:
        try:
            await bot_client.send_message(
                int(config.ADMIN_ID), 
                f"🐛 **DEBUG: Message passed filter and was forwarded!**\n\n**Original:**\n{original_text}\n\n**Translated:**\n{translated}"
            )
        except Exception as e:
            logger.error("Failed to forward debug message to admin: %s", e)


def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = http.server.HTTPServer(("0.0.0.0", port), http.server.SimpleHTTPRequestHandler)
    server.serve_forever()

async def main():
    if "PORT" in os.environ:
        threading.Thread(target=run_dummy_server, daemon=True).start()
        logger.info("Dummy HTTP server started for Cloud Run.")

    try:
        await client.start()
        me = await client.get_me()
        logger.info("Logged in as %s (id=%s)", me.username or me.first_name, me.id)
        
        if config.TELEGRAM_BOT_TOKEN:
            await bot_client.start(bot_token=config.TELEGRAM_BOT_TOKEN)
            bot_me = await bot_client.get_me()
            logger.info("Bot Admin Dashboard started as %s", bot_me.username)
            if config.ADMIN_ID:
                await bot_client.send_message(int(config.ADMIN_ID), "🚀 Bot successfully started and deployed!")
    except Exception as e:
        logger.error("Failed to start Telegram Clients: %s", e)
        if "PORT" in os.environ:
            logger.info("Keeping process alive for Cloud Run health checks...")
            while True:
                await asyncio.sleep(3600)
        return

    # Resolve the channel entity at startup so Telethon can match incoming updates.
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
    if config.TELEGRAM_BOT_TOKEN:
        await bot_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
