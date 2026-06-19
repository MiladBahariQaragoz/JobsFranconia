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
import state
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


_APPLY_MARKER = "👉"


def _extract_apply_url(message) -> str | None:
    """Return ONLY the job-ad apply URL, read from Telegram's message entities.

    Each source post carries two links: the apply link on the 👉 line, and a
    separate "report a problem / contact admin" link. We want only the ad link,
    so we don't just take the last URL — we take the first URL entity that falls
    on/after the 👉 marker, which is the apply link. The report link (before 👉,
    or a later admin/telegram link) is ignored.

    Reading entities (not the visible text) is what lets this survive a clickable
    label (MessageEntityTextUrl) or a scheme-less auto-linked domain like
    'www.joboo.de/x' (MessageEntityUrl), neither of which spells out 'https://'.

    Returns None when there is no 👉 line — better to drop the link than to risk
    publishing the admin/report link in its place.
    """
    raw = message.raw_text or ""
    marker = raw.find(_APPLY_MARKER)
    if marker < 0:
        return None

    # Telegram entity offsets are counted in UTF-16 code units, not Python chars.
    marker_offset = len(raw[:marker].encode("utf-16-le")) // 2

    try:
        pairs = message.get_entities_text()  # (entity, text), ordered by offset
    except Exception:
        return None

    for entity, entity_text in pairs:
        if getattr(entity, "offset", -1) < marker_offset:
            continue  # skip anything before the 👉 apply line (e.g. report link)
        explicit = getattr(entity, "url", None)  # MessageEntityTextUrl carries .url
        if explicit:
            return explicit
        if type(entity).__name__ == "MessageEntityUrl":  # bare/auto-linked URL
            return entity_text
    return None


# Maps a resolved source chat id (Telethon marked peer id) -> list of
# (lang, destination) targets. Populated at startup in main() from
# config.LANG_ROUTES, so each source can fan out to one post per language.
ROUTE_BY_ID: dict[int, list[tuple[str, object]]] = {}

# Resolved entity per source chat id, cached at startup so catch_up() can read
# message history reliably (by entity rather than a possibly-uncached id).
ENTITY_BY_ID: dict[int, object] = {}

# Message ids already handled in THIS process, per chat. Guards against the
# live handler and the startup catch_up() both processing the same message in
# their brief overlap window. Bounded to keep memory flat on a long-lived run.
_processed: dict[int, set] = {}


def _mark_processed(chat_id, message_id) -> bool:
    """Record a message id as handled. Return False if it was already handled."""
    seen = _processed.setdefault(chat_id, set())
    if message_id in seen:
        return False
    seen.add(message_id)
    if len(seen) > 2000:  # drop the oldest half to bound memory
        for old in sorted(seen)[:1000]:
            seen.discard(old)
    return True


async def process_message(message, chat_id, live: bool = False):
    """Filter, translate and repost a single source message.

    Shared by the live handler and the startup catch-up so missed posts are
    handled identically. Idempotent within a process via ``_mark_processed``;
    advances the persisted per-chat marker (``state``) so a future restart
    resumes after this message instead of replaying or dropping it.

    ``live=True`` marks a message arriving from the live listener (vs. a
    historical catch-up replay). Only live posts are eligible for the
    apply-link re-fetch wait below — a catch-up message is already fetched in
    its final, edited form, so waiting would gain nothing.
    """
    targets = ROUTE_BY_ID.get(chat_id)
    if targets is None:
        logger.debug("Ignoring message from non-source chat_id=%s", chat_id)
        return
    if not _mark_processed(chat_id, message.id):
        logger.debug("Skipping already-handled message id=%s (chat_id=%s)", message.id, chat_id)
        return

    loop = asyncio.get_event_loop()
    try:
        original_text = message.text or message.message
        if not original_text:
            logger.info("Skipping non-text message (id=%s)", message.id)
            return

        if not is_job_posting(original_text):
            logger.info("Filtered out non-job-posting message (id=%s, src=%s)", message.id, chat_id)
            return

        logger.info("Job posting received (id=%s, src=%s -> %d target(s)): %.80s…",
                    message.id, chat_id, len(targets), original_text)

        if config.DEBUG_MODE:
            print(f"\n{'='*60}\n[DEBUG] Job posting (id={message.id}, src={chat_id}):\n{original_text}\n{'='*60}\n")
            return

        # Authoritative apply URL from entities — survives clickable labels and
        # scheme-less auto-links that the visible text doesn't spell out.
        apply_url = _extract_apply_url(message)

        # The source often posts a skeleton first, then EDITS in the 👉 apply line
        # within a few minutes (consistent with the 🛡 "fully checked" status being
        # finalised on edit). The live listener only sees the original, link-less
        # version. So for a fresh post with no link, poll the message — re-fetching
        # every LINK_REFETCH_DELAY seconds up to LINK_REFETCH_MAX_WAIT total —
        # until the edit lands. Retries are logged at INFO/WARNING only (below the
        # admin-DM threshold) so waiting never spams the admin; the single alert is
        # sent only if the link never appears.
        if apply_url is None and live and config.LINK_REFETCH_DELAY > 0:
            entity = ENTITY_BY_ID.get(chat_id, chat_id)
            waited = 0
            while apply_url is None and waited < config.LINK_REFETCH_MAX_WAIT:
                delay = min(config.LINK_REFETCH_DELAY, config.LINK_REFETCH_MAX_WAIT - waited)
                logger.info("No apply link yet for id=%s; waiting %ds (%ds/%ds) for a source edit",
                            message.id, delay, waited, config.LINK_REFETCH_MAX_WAIT)
                await asyncio.sleep(delay)
                waited += delay
                try:
                    refetched = await client.get_messages(entity, ids=message.id)
                except Exception:
                    logger.warning("Re-fetch failed for id=%s (%ds elapsed); will retry", message.id, waited)
                    continue
                if refetched is None:
                    continue
                new_url = _extract_apply_url(refetched)
                if new_url is not None:
                    logger.info("Recovered apply link for id=%s after %ds from a later source edit",
                                message.id, waited)
                    message = refetched
                    original_text = refetched.text or refetched.message
                    apply_url = new_url

        # Still no apply link — after waiting for an edit (live) or outright
        # (catch-up). A job post without an apply link isn't useful, so skip it
        # entirely and send ONE alert to the admin to handle manually. This is the
        # only admin message on this path; the retries above stay silent.
        if apply_url is None:
            logger.error(
                "Skipping job post id=%s (src=%s): no apply link found%s:\n%s",
                message.id, chat_id,
                f" after waiting {config.LINK_REFETCH_MAX_WAIT}s" if live else "",
                original_text[:600],
            )
            return

        # Translate + post once per configured language (Persian, Azerbaijani, …).
        # Each language is independent: a failure on one must not stop the others.
        for lang, dest in targets:
            translated = await loop.run_in_executor(
                None, translate_uk, original_text, lang, apply_url
            )

            sent = await loop.run_in_executor(
                None, post_to_channel, translated, dest
            )

            # Success is logged at INFO only (visible in Cloud Run logs, no admin DM).
            # A failed delivery is an error -> admin_logger DMs the admin.
            if sent:
                logger.info("Successfully forwarded post id=%s (%s) to %s", message.id, lang, dest)
            else:
                logger.error("Post id=%s (%s) was NOT delivered to %s (see poster logs)", message.id, lang, dest)
    finally:
        # Advance the durable marker even for filtered/failed messages so a
        # restart doesn't re-examine (or re-post) them. set_last_seen only ever
        # moves forward, so live/backfill ordering can't rewind it.
        await loop.run_in_executor(None, state.set_last_seen, chat_id, message.id)


@client.on(events.NewMessage())
async def handle_new_post(event):
    # Listen to every chat the user account sees and match by the resolved chat
    # id (cached at startup). This is more robust than Telethon's username-based
    # chats= filter, which can fail to match channel updates.
    await process_message(event.message, event.chat_id, live=True)


async def catch_up():
    """Replay messages posted to each source while the bot was down.

    Uses the durable per-chat marker from ``state``: on a normal restart we fetch
    everything newer than the marker and run it through the same pipeline. On the
    very first run (no marker yet) we DON'T replay history — we just record the
    current latest id as a baseline, so only genuine future downtime is caught up.
    """
    loop = asyncio.get_event_loop()
    for chat_id in list(ROUTE_BY_ID):
        entity = ENTITY_BY_ID.get(chat_id, chat_id)
        try:
            last = await loop.run_in_executor(None, state.get_last_seen, chat_id)
            if not last:
                latest = await client.get_messages(entity, limit=1)
                if latest:
                    await loop.run_in_executor(None, state.set_last_seen, chat_id, latest[0].id)
                    logger.info("Catch-up: baseline set for chat_id=%s at id=%s (no history replay on first run)",
                                chat_id, latest[0].id)
                continue

            # Materialise the missed ids up front (oldest→newest) so the marker
            # advancing as we process can't shorten the list mid-iteration.
            missed = [m async for m in client.iter_messages(entity, min_id=last, reverse=True)]
            if not missed:
                continue
            logger.info("Catch-up: replaying %d missed message(s) for chat_id=%s (since id=%s)",
                        len(missed), chat_id, last)
            for msg in missed:
                await process_message(msg, chat_id)
        except Exception:
            logger.exception("Catch-up failed for chat_id=%s", chat_id)


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
            ENTITY_BY_ID[chat_id] = entity
            logger.info("Resolved source %s -> %s (chat_id=%s, title=%s)",
                        src, targets, chat_id, getattr(entity, "title", "?"))
            resolved += 1
        except Exception:
            logger.exception("Failed to resolve source channel '%s' — its updates may not arrive", src)

    if resolved == 0:
        logger.error("No source channels could be resolved — the bot will not forward anything")

    logger.info("Listening on %d/%d source channel(s)", resolved, len(config.SOURCE_CHANNELS))

    # Replay anything posted while the bot was down, then keep listening live.
    # Runs after the live handler is already attached, so the in-process dedup in
    # process_message covers the overlap. Never let a catch-up error stop startup.
    try:
        await catch_up()
    except Exception:
        logger.exception("Catch-up pass failed; continuing with live listening")

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
