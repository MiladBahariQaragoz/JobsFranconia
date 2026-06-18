import asyncio
import logging
import signal
import sys
import os
import threading
import http.server

from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession

import config
from filter import is_job_posting

if not config.DEBUG_MODE:
    from translator import translate_uk
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


def _extract_apply_url(message) -> str | None:
    """Return the application URL from the post, read from Telegram's message
    entities rather than the visible text.

    The source channel often hides the apply link behind a clickable label
    (MessageEntityTextUrl) or auto-links a scheme-less domain like
    'www.joboo.de/x' (MessageEntityUrl) — in both cases the visible text has no
    'https://...' to scrape. Entities are authoritative. We take the last URL,
    which in these posts is the trailing 👉 apply link.
    """
    try:
        pairs = message.get_entities_text()
    except Exception:
        return None
    url = None
    for entity, entity_text in pairs:
        explicit = getattr(entity, "url", None)  # MessageEntityTextUrl carries .url
        if explicit:
            url = explicit
        elif type(entity).__name__ == "MessageEntityUrl":  # bare/auto-linked URL
            url = entity_text
    return url


# Maps a resolved source chat id (Telethon marked peer id) -> list of
# (lang, destination) targets. Populated at startup in main() from
# config.LANG_ROUTES, so each source can fan out to one post per language.
ROUTE_BY_ID: dict[int, list[tuple[str, object]]] = {}


@client.on(events.NewMessage())
async def handle_new_post(event):
    # Listen to every chat the user account sees and match by the resolved chat
    # id (cached at startup). This is more robust than Telethon's username-based
    # chats= filter, which can fail to match channel updates.
    if event.chat_id not in ROUTE_BY_ID:
        logger.debug("Ignoring message from non-source chat_id=%s", event.chat_id)
        return

    targets = ROUTE_BY_ID[event.chat_id]
    message = event.message
    original_text = message.text or message.message
    if not original_text:
        logger.info("Skipping non-text message (id=%s)", message.id)
        return

    if not is_job_posting(original_text):
        logger.info("Filtered out non-job-posting message (id=%s, src=%s)", message.id, event.chat_id)
        return

    logger.info("Job posting received (id=%s, src=%s -> %d target(s)): %.80s…",
                message.id, event.chat_id, len(targets), original_text)

    if config.DEBUG_MODE:
        print(f"\n{'='*60}\n[DEBUG] Job posting (id={message.id}, src={event.chat_id}):\n{original_text}\n{'='*60}\n")
        return

    # Authoritative apply URL from entities — survives clickable labels and
    # scheme-less auto-links that the visible text doesn't spell out.
    apply_url = _extract_apply_url(message)

    # Translate + post once per configured language (Persian, Azerbaijani, …).
    # Each language is independent: a failure on one must not stop the others.
    for lang, dest in targets:
        translated = await asyncio.get_event_loop().run_in_executor(
            None, translate_uk, original_text, lang, apply_url
        )

        sent = await asyncio.get_event_loop().run_in_executor(
            None, post_to_channel, translated, dest
        )

        if sent:
            logger.info("Successfully forwarded post id=%s (%s) to %s", message.id, lang, dest)
        else:
            logger.error("Post id=%s (%s) was NOT delivered to %s (see poster logs)", message.id, lang, dest)

        # Debug feature: Send a copy to the admin, reflecting the real delivery status.
        if config.ADMIN_ID and config.TELEGRAM_BOT_TOKEN:
            status = "was forwarded ✅" if sent else "FAILED to send ❌"
            try:
                await bot_client.send_message(
                    int(config.ADMIN_ID),
                    f"🐛 DEBUG [{lang}]: Message passed filter and {status}\n\nOriginal:\n{original_text}\n\nTranslated:\n{translated}"
                )
            except Exception as e:
                logger.error("Failed to forward debug message to admin: %s", e)


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    """Minimal health endpoint for Cloud Run — always 200 OK.

    Avoids SimpleHTTPRequestHandler, which would serve the app's source files.
    """
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass  # silence per-request stderr noise


def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = http.server.HTTPServer(("0.0.0.0", port), _HealthHandler)
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

    # Resolve every source channel at startup so Telethon can match incoming
    # updates, and build the chat-id -> destination route map. One bad channel
    # must not stop the others.
    resolved = 0
    for src in config.SOURCE_CHANNELS:
        try:
            entity = await client.get_entity(src)
            chat_id = utils.get_peer_id(entity)
            # Build one (lang, dest) target per configured language for this source.
            targets = [
                (lang, routes[src])
                for lang, routes in config.LANG_ROUTES.items()
                if routes.get(src)
            ]
            if not targets and config.DEFAULT_DEST:
                targets = [("fa", config.DEFAULT_DEST)]
            ROUTE_BY_ID[chat_id] = targets
            logger.info("Resolved source %s -> %s (chat_id=%s, title=%s)",
                        src, targets, chat_id, getattr(entity, "title", "?"))
            resolved += 1
        except Exception:
            logger.exception("Failed to resolve source channel '%s' — its updates may not arrive", src)

    if resolved == 0:
        logger.error("No source channels could be resolved — the bot will not forward anything")

    logger.info("Listening on %d/%d source channel(s)", resolved, len(config.SOURCE_CHANNELS))

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
