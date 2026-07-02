import os
import re
from dotenv import load_dotenv

load_dotenv()

def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value

def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default)

def _split_list(raw: str) -> list[str]:
    """Split a comma-separated env value into clean tokens (empty entries dropped)."""
    return [c.strip() for c in raw.split(",") if c.strip()]

def _normalize_channel(token: str):
    """Numeric channel ids become ints (Telethon needs ints to match updates);
    @usernames / t.me links stay as strings."""
    t = token.strip()
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    return t

# Set DEBUG_MODE=true to read and filter messages locally without translating or posting.
DEBUG_MODE = _optional("DEBUG_MODE", "").lower() in ("1", "true", "yes")

TELEGRAM_API_ID      = int(_optional("TELEGRAM_API_ID", "0")) if DEBUG_MODE else int(_require("TELEGRAM_API_ID"))
TELEGRAM_API_HASH    = _optional("TELEGRAM_API_HASH") if DEBUG_MODE else _require("TELEGRAM_API_HASH")
TELEGRAM_SESSION_STR = _optional("TELEGRAM_SESSION_STRING") if DEBUG_MODE else _require("TELEGRAM_SESSION_STRING")

# ---------------------------------------------------------------------------
# Channels (multi-channel support)
#
# SOURCE_CHANNELS / DEST_CHANNELS are comma-separated and unlimited, e.g.
#   SOURCE_CHANNELS=@uka,@ukb,@ukc
#   DEST_CHANNELS=@faa,@fab,@fac        # paired 1:1 by position
# or a single shared destination:
#   DEST_CHANNEL=@my_persian_channel    # ALL sources post here
#
# The legacy single-value vars SOURCE_CHANNEL / DEST_CHANNEL still work.
# ---------------------------------------------------------------------------
_source_raw = _optional("SOURCE_CHANNELS") or _optional("SOURCE_CHANNEL", "dummy" if DEBUG_MODE else "")
if not _source_raw and not DEBUG_MODE:
    raise EnvironmentError("Missing required environment variable: SOURCE_CHANNELS (or SOURCE_CHANNEL)")
SOURCE_CHANNELS = [_normalize_channel(c) for c in _split_list(_source_raw)]

_dest_raw = _optional("DEST_CHANNELS") or _optional("DEST_CHANNEL")
if not _dest_raw and not DEBUG_MODE:
    raise EnvironmentError("Missing required environment variable: DEST_CHANNELS (or DEST_CHANNEL)")
DEST_CHANNELS = [_normalize_channel(c) for c in _split_list(_dest_raw)]


def _build_routes(sources: list, dests: list) -> dict:
    """Map each source channel to a destination.

    - 1 destination            -> every source posts there
    - N destinations (N=#srcs) -> paired by position (source[i] -> dest[i])
    - anything else            -> configuration error
    """
    if not dests:
        return {}  # debug mode: no posting
    if len(dests) == 1:
        return {s: dests[0] for s in sources}
    if len(dests) == len(sources):
        return dict(zip(sources, dests))
    raise EnvironmentError(
        f"DEST_CHANNELS must have exactly 1 entry or the same count as SOURCE_CHANNELS "
        f"(got {len(sources)} sources, {len(dests)} destinations)"
    )

# Maps each source channel token -> its Persian destination channel.
ROUTES = _build_routes(SOURCE_CHANNELS, DEST_CHANNELS)

# Per-language routing: target language code -> {source token -> dest channel}.
# main.py fans each job posting out to every language that has a destination for
# the originating source channel. Persian is the only language.
LANG_ROUTES = {"fa": ROUTES}

# Fallback destination used if a message arrives from an unrouted chat.
DEFAULT_DEST = DEST_CHANNELS[0] if DEST_CHANNELS else None

# Backwards-compatible single-value aliases (first entry of each list).
SOURCE_CHANNEL = SOURCE_CHANNELS[0] if SOURCE_CHANNELS else None
DEST_CHANNEL   = DEST_CHANNELS[0] if DEST_CHANNELS else None

# These are only required in production (not debug) mode.
TELEGRAM_BOT_TOKEN   = _optional("TELEGRAM_BOT_TOKEN")   if DEBUG_MODE else _require("TELEGRAM_BOT_TOKEN")
GOOGLE_CLOUD_PROJECT = _optional("GOOGLE_CLOUD_PROJECT")  if DEBUG_MODE else _require("GOOGLE_CLOUD_PROJECT")
ADMIN_ID             = _optional("ADMIN_ID") # Telegram User ID for admin dashboard and error logs

# Source posts are frequently published as a skeleton and then EDITED moments
# later to append the verified 👉 apply line — an edit the live listener never
# sees. When a fresh post arrives with no apply link, re-fetch the message every
# LINK_REFETCH_DELAY seconds (poll interval) until the edit lands, giving up
# after LINK_REFETCH_MAX_WAIT seconds total. On give-up the post is skipped and
# the admin gets a single alert. Set LINK_REFETCH_DELAY=0 to disable the wait.
LINK_REFETCH_DELAY    = int(_optional("LINK_REFETCH_DELAY_SECONDS", "45") or "45")
LINK_REFETCH_MAX_WAIT = int(_optional("LINK_REFETCH_MAX_WAIT_SECONDS", "300") or "300")
