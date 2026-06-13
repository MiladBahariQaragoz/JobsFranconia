import os
from dotenv import load_dotenv

load_dotenv()

def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value

def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default)

# Set DEBUG_MODE=true to read and filter messages locally without translating or posting.
DEBUG_MODE = _optional("DEBUG_MODE", "").lower() in ("1", "true", "yes")

TELEGRAM_API_ID      = int(_require("TELEGRAM_API_ID"))
TELEGRAM_API_HASH    = _require("TELEGRAM_API_HASH")
TELEGRAM_SESSION_STR = _require("TELEGRAM_SESSION_STRING")
SOURCE_CHANNEL       = _require("SOURCE_CHANNEL")

# These are only required in production (not debug) mode.
TELEGRAM_BOT_TOKEN   = _optional("TELEGRAM_BOT_TOKEN")   if DEBUG_MODE else _require("TELEGRAM_BOT_TOKEN")
DEST_CHANNEL         = _optional("DEST_CHANNEL")          if DEBUG_MODE else _require("DEST_CHANNEL")
GOOGLE_CLOUD_PROJECT = _optional("GOOGLE_CLOUD_PROJECT")  if DEBUG_MODE else _require("GOOGLE_CLOUD_PROJECT")
