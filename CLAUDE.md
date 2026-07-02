# CLAUDE.md

Guidance for working in this repository. Read this before making changes.

## Version control (required)

**Everything must be committed and pushed to GitHub.** The remote is
`https://github.com/MiladBahariQaragoz/JobsFranconia` (`master`). After any change
that affects the repo — code, config, docs, deploy notes — commit it and push to
`origin master`. Do not leave local-only edits; the GitHub repo must always reflect
the current local state. Never commit secrets (`.env`, `*.session`, real session
strings or tokens) — those are covered by `.gitignore`.

## What this is

A **Telegram channel translator bot**. It listens to a Ukrainian source channel,
keeps only structured job postings, translates them Ukrainian → Persian via Google
Cloud Translation (with a free MyMemory fallback when Google fails), and reposts
them to the destination channel. Runs 24/7 on Google Cloud Run.

Live deployment: project `jobs-franconia-bot-02`, region `europe-west3`, service
`jobs-bot`. (Migrated 2026-06-19 from the now-broken `jobs-franconia-bot-01` — see
deploy.md. Never run `gcloud projects delete` on the live project.) See
[deploy.md](deploy.md) for deploy/ops steps.

## Pipeline (the one flow that matters)

A single event handler in [main.py](main.py) drives everything:

```
new message in any SOURCE_CHANNELS
  → filter.is_job_posting()      # keep only structured job posts
  → filter.blocked_reason()      # drop posts with a Telegram/Facebook link or phone
                                 #   number in the poster's text or the apply link
  → translator.translate_uk(text, "fa")   # Google Translate (uk→fa), MyMemory fallback
  → poster.post_to_channel(text, dest)    # Telegram Bot API → routed DEST
```

Routing is many-to-many. `config.LANG_ROUTES` maps the language code `fa` to a
`{source → destination}` map (built paired 1:1, or all sources to one shared dest).
`main.py` resolves each source to its chat id at startup into `ROUTE_BY_ID`, where
each entry is a list of `(lang, dest)` targets, and the handler looks them up by the
incoming `event.chat_id`. The pipeline stays language-generic (a list of targets per
source) so another language could be re-added, but **Persian is the only one
configured** — the Azerbaijani route was removed.

Two Telethon clients run in one process:
- **user client** (`client`) — a real user account via `StringSession`, used to
  *read* the source channel. User accounts can join/read channels that bots cannot.
- **bot client** (`bot_client`) — a bot via `MemorySession`, used for `/status` and
  `/ping` admin commands. Posting to the destination uses the raw Bot API in
  [poster.py](poster.py), not this client.

## Module map

| File | Responsibility | Notes |
|------|----------------|-------|
| [main.py](main.py) | Entry point, event loop, both Telethon clients, admin commands, Cloud Run health server | The only place that wires modules together |
| [config.py](config.py) | Loads + validates env vars | `_require` fails fast in prod; `_optional` relaxes in `DEBUG_MODE` |
| [filter.py](filter.py) | `is_job_posting()` — emoji-marker heuristic; `blocked_reason()` / `has_blocked_content()` — off-channel-contact drop rule (Telegram/Facebook links, phone numbers) | Pure functions, no I/O. Easiest place to tune behavior. `blocked_reason` is deliberately broad (matches bare words `telegram`/`facebook` too) |
| [translator.py](translator.py) | `translate_uk(text, lang)` — Google Translate wrapper + pre/post-processing, with a **MyMemory backup** (`_mymemory_translate`) used only when Google fails | Per-language settings in `_LANGS` (only `fa`). `translate_uk_to_fa` is a back-compat alias. Top fields always go to German; the description + labels are language-specific. MyMemory reads `MYMEMORY_EMAIL` from the env and chunks text under its ~500-char/request cap |
| [poster.py](poster.py) | `post_to_channel()` — sends HTML message via Bot API | Uses stdlib `urllib`, no extra deps |
| [state.py](state.py) | Durable per-channel `last_seen` marker in GCS, for downtime catch-up | Fail-safe: storage errors are logged, never raised. Needs `STATE_BUCKET` |
| [admin_logger.py](admin_logger.py) | Logging handler that DMs ERROR/CRITICAL logs to the admin | Attached at ERROR level in main.py |
| [auth.py](auth.py) | One-time local generator for the Telethon session string | **Run locally only** — Telegram blocks cloud-IP logins |

Keep modules single-purpose: filtering logic in `filter.py`, translation in
`translator.py`, delivery in `poster.py`, wiring in `main.py`. New behavior should
extend the matching module rather than expanding `main.py`.

## Configuration

All config comes from environment variables, loaded by [config.py](config.py).
Locally via `.env` (copy from [.env.example](.env.example)); in production via Cloud
Run env vars / Secret Manager. Never commit `.env` or session strings.

| Var | Required in prod | Purpose |
|-----|------------------|---------|
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | yes | User-account API creds from my.telegram.org |
| `TELEGRAM_SESSION_STRING` | yes | Saved user session from `auth.py` |
| `SOURCE_CHANNELS` | yes | Comma-separated channels to read from (@username or id). `SOURCE_CHANNEL` (single) still accepted |
| `TELEGRAM_BOT_TOKEN` | yes | Bot token from @BotFather |
| `DEST_CHANNELS` / `DEST_CHANNEL` | yes | Persian destinations (bot must be admin). Either one shared `DEST_CHANNEL` for all sources, or `DEST_CHANNELS` paired 1:1 by position with `SOURCE_CHANNELS` |
| `GOOGLE_CLOUD_PROJECT` | yes | GCP project for Translation API |
| `MYMEMORY_EMAIL` | optional | Contact email for the MyMemory backup translator (used only if Google Translate fails); raises its free quota from ~5k to ~50k chars/day |
| `STATE_BUCKET` | recommended | GCS bucket holding `last_seen.json` for missed-message catch-up. Runtime SA needs `roles/storage.objectAdmin`. Unset → no cross-restart catch-up (still runs) |
| `LINK_REFETCH_DELAY_SECONDS` | optional | Poll interval (s) for re-fetching a fresh, link-less post to recover a 👉 apply link the source adds via a later edit (default `45`; `0` disables waiting) |
| `LINK_REFETCH_MAX_WAIT_SECONDS` | optional | Total time (s) to keep polling before giving up; on give-up the post is **skipped** and the admin gets one alert (default `300`) |
| `ADMIN_ID` | optional | Telegram user id for admin commands + error DMs |
| `DEBUG_MODE` | optional | `true` → read+filter+print only; no translate/post. Relaxes required vars |
| `PORT` | set by Cloud Run | Triggers the dummy HTTP health server |

`DEBUG_MODE=true` is the safe way to test filtering against the live source channel
without translating, posting, or needing GCP/bot credentials.

## Commands

```bash
pip install -r requirements.txt          # install deps
python auth.py                           # one-time: generate session string (local only)
cp .env.example .env                     # then fill in values
python main.py                           # run the bot locally

# tests (filter + translator rules). pytest only — the pure logic needs no bot deps.
python -m venv .venv && .venv/bin/pip install pytest python-dotenv
.venv/bin/pytest

# deploy to Cloud Run (builds container via Cloud Build, ~2-3 min)
gcloud run deploy jobs-bot --source . --region europe-west3 \
  --no-cpu-throttling --min-instances=1 --max-instances=1 --allow-unauthenticated
```

Tests live in `tests/` and cover `filter.py` and `translator.py` (pure logic — no
Telegram/Google deps needed) via `pytest` (config in `pytest.ini`). No linter or
formatter is configured. Prefer `pytest` for new tests and keep pure logic in
`filter.py`/`translator.py` so it stays testable.

## Conventions

- Python 3.12, standard library preferred for small jobs (see `poster.py` using
  `urllib` instead of `requests`).
- Use the module-level `logger = logging.getLogger(__name__)` pattern already in every
  module; do not `print` except behind `DEBUG_MODE`.
- Functions that can fail externally (translate, post) should fail safe and log via
  `logger.exception` rather than crashing the event loop — `translate_uk_to_fa`
  returns the original text on failure as the model.
- Blocking calls (Google Translate, HTTP posts) run via `run_in_executor` so they
  don't block the asyncio event loop. Keep new blocking I/O off the loop the same way.

## Traceability / maintainability / scalability notes

- **Traceability** — every stage logs with the source `message.id`. Errors are also
  DM'd to the admin via [admin_logger.py](admin_logger.py) and visible in Cloud Run
  logs. Preserve `message.id` in log lines when adding stages.
- **Maintainability** — one module per concern; `main.py` only orchestrates. The repo
  root contains scratch/iteration notes (`deploy*.txt`, `billing.txt`, `status.txt`,
  `session.txt`, `plan.md`, `system.md`) that are historical and not authoritative —
  prefer this file and [deploy.md](deploy.md). Don't add logic to those.
- **Scalability** — the bottleneck is the single-process event loop and the Translation
  API quota. If volume grows: batch/queue translations, add retry/backoff in
  `poster.py` and `translator.py`, and consider de-duplicating already-seen
  `message.id`s. The filter is a cheap pre-screen that keeps Translation API spend down.

## Gotchas

- `auth.py` must run on a local machine; Telegram blocks logins from cloud IPs.
- Posting uses `parse_mode=HTML`, so `translator.py` converts Markdown bold/links to
  HTML. Emit HTML, not Markdown, when changing post formatting.
- `filter.py` requires ≥3 of the emoji markers `🏢 💶 📍 📂 🛡`. Changing the source
  channel's post format will silently filter everything out — update `_JOB_MARKERS`.
- The user account must be **subscribed/joined** to every source channel — resolving
  the entity at startup is not enough to receive its updates.
- `main.py` listens with a bare `events.NewMessage()` and matches by **resolved
  `event.chat_id` against `ROUTE_BY_ID`**, NOT Telethon's `chats=` username filter.
  The `chats=@username` filter was observed to silently fail to match channel updates
  (messages arrive but the handler never fires). Don't reintroduce `chats=`.
- On Cloud Run, set `--no-cpu-throttling` **and `--min-instances=1`** (this is a
  long-lived listener, not a request/response service). Without `--min-instances=1`
  Cloud Run scales to zero when no inbound HTTP requests arrive — which is always —
  and the bot silently dies even though the deploy "succeeded". Use `--max-instances=1`
  too, so a second instance can't create a duplicate Telegram listener and double-post.
  The dummy HTTP server only exists to satisfy health checks.
