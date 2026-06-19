"""Durable per-channel progress marker, used for missed-message catch-up.

Cloud Run has an ephemeral filesystem and the Telethon user client uses an
in-memory session, so on every restart the bot would otherwise forget how far it
had read and silently drop anything posted during downtime. This module persists
a small JSON map ``{chat_id: last_seen_message_id}`` to a GCS object so a restart
can resume from where it left off.

Design notes:
- Fail-safe: any storage error is logged, never raised. If the bucket is unset or
  unreachable the marker just lives in memory for the process lifetime — the bot
  keeps running, it simply can't catch up across that particular restart.
- ``set_last_seen`` only ever advances the marker (stores a max), so out-of-order
  calls from the live handler vs. the backfill can't move it backwards.
- Calls do blocking GCS I/O; main.py invokes them via ``run_in_executor`` to keep
  them off the asyncio event loop (same convention as translate/post).
"""

import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

_BUCKET = os.environ.get("STATE_BUCKET", "")
_OBJECT = os.environ.get("STATE_OBJECT", "last_seen.json")

_lock = threading.Lock()
_cache: dict[str, int] = {}
_loaded = False
_client = None


def _bucket():
    """Return the GCS bucket handle, or None if unconfigured/unavailable."""
    global _client
    if not _BUCKET:
        return None
    try:
        from google.cloud import storage
        if _client is None:
            _client = storage.Client()
        return _client.bucket(_BUCKET)
    except Exception:
        logger.exception("State: could not initialise GCS client for bucket %s", _BUCKET)
        return None


def load() -> dict:
    """Load the marker map once (cached). Safe to call repeatedly."""
    global _loaded
    with _lock:
        if _loaded:
            return dict(_cache)
    bucket = _bucket()
    data: dict[str, int] = {}
    if bucket is not None:
        try:
            blob = bucket.blob(_OBJECT)
            if blob.exists():
                raw = json.loads(blob.download_as_text())
                data = {str(k): int(v) for k, v in raw.items()}
        except Exception:
            logger.exception("State: failed to load gs://%s/%s", _BUCKET, _OBJECT)
    with _lock:
        # Merge rather than overwrite, in case set_last_seen ran before load.
        for k, v in data.items():
            if v > _cache.get(k, 0):
                _cache[k] = v
        _loaded = True
        return dict(_cache)


def get_last_seen(chat_id) -> int:
    """Last message id we have already examined for this chat (0 if unknown)."""
    load()
    with _lock:
        return _cache.get(str(chat_id), 0)


def set_last_seen(chat_id, message_id: int) -> None:
    """Advance and persist the marker for this chat (no-op if not newer)."""
    load()
    key = str(chat_id)
    with _lock:
        if int(message_id) <= _cache.get(key, 0):
            return
        _cache[key] = int(message_id)
        snapshot = dict(_cache)
    bucket = _bucket()
    if bucket is None:
        return
    try:
        bucket.blob(_OBJECT).upload_from_string(
            json.dumps(snapshot), content_type="application/json"
        )
    except Exception:
        logger.exception("State: failed to persist gs://%s/%s", _BUCKET, _OBJECT)
