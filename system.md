# System Documentation — Telegram Channel Translator

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Component Reference](#3-component-reference)
4. [Data Flow](#4-data-flow)
5. [Authentication & Security](#5-authentication--security)
6. [Configuration Reference](#6-configuration-reference)
7. [Phase 3 — Google Cloud Setup](#phase-3--google-cloud-setup)
8. [Phase 4 — Containerization](#phase-4--containerization)
9. [Phase 5 — Deployment](#phase-5--deployment)
10. [Error Handling & Resilience](#10-error-handling--resilience)
11. [Logging & Observability](#11-logging--observability)
12. [Cost Estimate](#cost-estimate)
13. [Known Limitations](#13-known-limitations)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. System Overview

This service bridges a Telegram channel written in Ukrainian with a Persian-speaking audience.
It runs as a persistent, containerized process on Google Cloud Run. It uses the Telegram MTProto
protocol (via Telethon) to receive channel posts in real time, the Google Cloud Translation API
to translate them, and the Telegram Bot API to publish the result.

### Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Reading source channel | Telethon (MTProto, user account) | Bot API cannot read channels the bot is not admin of |
| Writing to destination | Telegram Bot API | Clean separation; bot is easy to add as channel admin |
| Translation | Google Cloud Translation v2 | Simple, pay-per-use, 100+ languages, no model training needed |
| Runtime | Cloud Run (min 1 instance) | Persistent connection required; Cloud Run is cheaper than a VM |
| Secrets | Google Secret Manager | Avoids hardcoding credentials; integrates natively with Cloud Run |
| Session persistence | Telethon StringSession | Stateless — no file system needed; survives container restarts |

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Google Cloud Run                      │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │                   main.py                        │   │
│  │                                                  │   │
│  │   TelegramClient (Telethon + StringSession)      │   │
│  │          │                                       │   │
│  │          │  NewMessage event                     │   │
│  │          ▼                                       │   │
│  │   handle_new_post()                              │   │
│  │          │                                       │   │
│  │          ├──▶ translator.py                      │   │
│  │          │       └──▶ Google Translate API       │   │
│  │          │                                       │   │
│  │          └──▶ poster.py                          │   │
│  │                  └──▶ Telegram Bot API           │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│   Secret Manager ──▶ env vars (session, tokens, keys)   │
└─────────────────────────────────────────────────────────┘
         ▲                              │
         │ MTProto (port 443)           │ HTTPS (Bot API)
         │                              ▼
  [Ukrainian Channel]          [Persian Channel]
   (Telegram servers)          (Telegram servers)
```

### Protocol Comparison

| | Telethon (MTProto) | Telegram Bot API |
|---|---|---|
| Acts as | A regular Telegram user | A bot |
| Can read any channel | Yes, if subscribed | No — only channels where bot is admin |
| Can post to channel | Yes, if member/admin | Yes, if bot is admin |
| Connection type | Persistent TCP | HTTP polling or webhook |
| Used in this project for | Reading source channel | Posting to destination channel |

---

## 3. Component Reference

### `auth.py`

**Purpose:** One-time local script that authenticates with Telegram and outputs a `StringSession` string.

**Why run locally:** Telegram's anti-abuse systems flag and reject new logins originating from
cloud provider IP ranges (Google Cloud, AWS, etc.). Running auth locally and exporting the session
string allows Cloud Run to reuse an already-authenticated session from any IP.

**Run once. Never commit the output.**

---

### `config.py`

**Purpose:** Central configuration loader. Reads from environment variables (populated locally
from `.env` via `python-dotenv`, and on Cloud Run from `--set-env-vars` / `--set-secrets`).

Fails fast with a clear error if any required variable is missing — prevents the service from
starting in a misconfigured state.

**Variables loaded:**

| Variable | Type | Description |
|---|---|---|
| `TELEGRAM_API_ID` | int | Your Telegram app's API ID |
| `TELEGRAM_API_HASH` | str | Your Telegram app's API Hash |
| `TELEGRAM_SESSION_STRING` | str | Telethon StringSession (from auth.py) |
| `TELEGRAM_BOT_TOKEN` | str | Bot token from @BotFather |
| `SOURCE_CHANNEL` | str | Username or ID of the Ukrainian source channel |
| `DEST_CHANNEL` | str | Username or ID of your Persian destination channel |
| `GOOGLE_CLOUD_PROJECT` | str | GCP project ID (used for ADC scoping) |

---

### `translator.py`

**Purpose:** Thin wrapper around the Google Cloud Translation API v2 (Basic edition).

**Client initialization:** `translate.Client()` is instantiated once at module load time.
On Cloud Run, it uses **Application Default Credentials (ADC)** automatically — no key file needed
when the service account is attached to the Cloud Run service.

**Language codes:**
- Source: `uk` (Ukrainian)
- Target: `fa` (Persian / Farsi)

**Failure behavior:** On any exception (network error, quota exceeded, API error), logs the
exception and returns the original Ukrainian text. This ensures the post still reaches the
destination channel even if translation fails.

---

### `poster.py`

**Purpose:** Sends the translated text to the destination channel using the Telegram Bot API.

Uses only Python's built-in `urllib` — no extra HTTP library dependency.

**Endpoint used:** `POST https://api.telegram.org/bot{TOKEN}/sendMessage`

**Parameters sent:**

| Parameter | Value |
|---|---|
| `chat_id` | `DEST_CHANNEL` from config |
| `text` | Translated Persian text |
| `parse_mode` | `HTML` (allows basic formatting to survive translation) |

**Timeout:** 15 seconds per request. Raises `RuntimeError` if the Bot API returns `"ok": false`.

---

### `main.py`

**Purpose:** Entry point. Initializes the Telethon client and registers the event handler.

**Event handler:** `@client.on(events.NewMessage(chats=SOURCE_CHANNEL))`
- Fires for every new post in the source channel
- Runs `translate_uk_to_fa` and `post_to_channel` in a thread pool executor
  (keeps the async event loop unblocked during synchronous API calls)

**Shutdown handling:** Catches `SIGINT` and `SIGTERM` (the signal Cloud Run sends before
killing a container) and disconnects cleanly.

**Keep-alive:** `client.run_until_disconnected()` blocks indefinitely, maintaining the
MTProto connection to Telegram's servers.

---

### `Dockerfile`

```dockerfile
FROM python:3.12-slim     # minimal base, ~50MB
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1    # ensures logs appear in Cloud Logging immediately
CMD ["python", "main.py"]
```

`PYTHONUNBUFFERED=1` is critical — without it, Python buffers stdout and logs may not
appear in Cloud Logging for minutes.

---

## 4. Data Flow

```
1. User posts to Ukrainian channel
        │
        ▼
2. Telegram servers notify connected clients via MTProto
        │
        ▼
3. Telethon (on Cloud Run) receives NewMessage event
        │
        ▼
4. handle_new_post() extracts message.text
        │
   [text empty?] ──yes──▶ log "skipping" and return
        │ no
        ▼
5. translate_uk_to_fa(text) called in thread pool
        │
        ├──▶ Google Translate API: POST /language/translate/v2
        │         body: { q: text, source: "uk", target: "fa" }
        │         response: { translatedText: "..." }
        │
   [API error?] ──yes──▶ log exception, return original text
        │ no
        ▼
6. post_to_channel(translated_text) called in thread pool
        │
        ├──▶ Telegram Bot API: POST /sendMessage
        │         body: { chat_id: DEST_CHANNEL, text: translated }
        │
   [API error?] ──yes──▶ raise RuntimeError (logged by asyncio)
        │ no
        ▼
7. Log "Successfully forwarded post id=X"
```

---

## 5. Authentication & Security

### Telegram User Session (Telethon)

Telethon's `StringSession` serializes the full MTProto session state into a single string.
This string contains:
- The DC (data center) the account is connected to
- The authorization key negotiated during login

It does **not** contain your password. It acts like a long-lived access token.

**Security implications:**
- Anyone with this string can act as your Telegram account
- Store it only in Google Secret Manager, never in code or `.env` files committed to git
- If compromised, revoke it via Telegram Settings → Devices → Terminate session

### Google Cloud Authentication

On Cloud Run, the service account attached to the service automatically provides credentials
to all Google Cloud client libraries via **Application Default Credentials (ADC)**.
No service account key file is downloaded or stored.

### Secrets at Rest

All sensitive values are stored as versioned secrets in **Google Secret Manager** and injected
into the container as environment variables at startup. They are never written to disk inside
the container.

### Bot Token

The Bot Token has no account-level access — it can only interact with Telegram as the bot.
Its blast radius is limited to the destination channel where it is admin.

---

## 6. Configuration Reference

### Local (`.env`)

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=abc123...
TELEGRAM_SESSION_STRING=1BVtsOKABu...
TELEGRAM_BOT_TOKEN=7123456789:AAF...
SOURCE_CHANNEL=@ukraine_news
DEST_CHANNEL=@my_persian_channel
GOOGLE_CLOUD_PROJECT=my-gcp-project
```

### Cloud Run (injected at deploy time)

Non-sensitive values are passed via `--set-env-vars`.
Sensitive values are passed via `--set-secrets` and read from Secret Manager.

```
--set-env-vars  SOURCE_CHANNEL,DEST_CHANNEL,TELEGRAM_API_ID,GOOGLE_CLOUD_PROJECT
--set-secrets   TELEGRAM_SESSION_STRING,TELEGRAM_API_HASH,TELEGRAM_BOT_TOKEN
```

---

## Phase 3 — Google Cloud Setup

### Step 3.1 — Enable required APIs

```bash
gcloud services enable \
  translate.googleapis.com \
  secretmanager.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com
```

### Step 3.2 — Store secrets in Secret Manager

```bash
# Replace each placeholder with your real value

echo -n "YOUR_SESSION_STRING" | \
  gcloud secrets create TELEGRAM_SESSION_STRING --data-file=-

echo -n "YOUR_API_HASH" | \
  gcloud secrets create TELEGRAM_API_HASH --data-file=-

echo -n "YOUR_BOT_TOKEN" | \
  gcloud secrets create TELEGRAM_BOT_TOKEN --data-file=-
```

To update a secret later:

```bash
echo -n "NEW_VALUE" | \
  gcloud secrets versions add TELEGRAM_SESSION_STRING --data-file=-
```

### Step 3.3 — Create and configure the service account

```bash
export PROJECT_ID=$(gcloud config get-value project)

gcloud iam service-accounts create telegram-translator \
  --display-name="Telegram Translator Bot"

SA="telegram-translator@${PROJECT_ID}.iam.gserviceaccount.com"

# Read secrets
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA" \
  --role="roles/secretmanager.secretAccessor"

# Call Translation API
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA" \
  --role="roles/cloudtranslate.user"
```

---

## Phase 4 — Containerization

### Step 4.1 — Create Artifact Registry repository

```bash
export REGION=europe-west1   # pick a region close to you

gcloud artifacts repositories create telegram-bot \
  --repository-format=docker \
  --location=$REGION \
  --description="Telegram translator bot images"
```

### Step 4.2 — Configure Docker authentication

```bash
gcloud auth configure-docker ${REGION}-docker.pkg.dev
```

### Step 4.3 — Build and push the image

```bash
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/telegram-bot/translator"

gcloud builds submit --tag $IMAGE
```

This uses Cloud Build to build the image remotely — no local Docker required.

---

## Phase 5 — Deployment

### Step 5.1 — Deploy to Cloud Run

```bash
gcloud run deploy telegram-translator \
  --image $IMAGE \
  --region $REGION \
  --service-account $SA \
  --min-instances 1 \
  --max-instances 1 \
  --memory 512Mi \
  --cpu 1 \
  --timeout 3600 \
  --no-allow-unauthenticated \
  --set-env-vars "SOURCE_CHANNEL=@your_source,DEST_CHANNEL=@your_dest,TELEGRAM_API_ID=123456,GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
  --set-secrets "TELEGRAM_SESSION_STRING=TELEGRAM_SESSION_STRING:latest,TELEGRAM_API_HASH=TELEGRAM_API_HASH:latest,TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest"
```

**Flag explanations:**

| Flag | Value | Reason |
|---|---|---|
| `--min-instances 1` | 1 | Keeps Telethon connection alive; prevents missed posts |
| `--max-instances 1` | 1 | Prevents two instances from posting the same message twice |
| `--timeout 3600` | 3600s | Cloud Run's max; the container is long-lived, not request-based |
| `--no-allow-unauthenticated` | — | No HTTP endpoint is exposed; service is not web-accessible |
| `--set-secrets` | `NAME=SECRET:latest` | Pulls secret latest version and injects as env var |

### Step 5.2 — Verify the deployment

```bash
# Check service status
gcloud run services describe telegram-translator --region $REGION

# Stream live logs
gcloud run services logs tail telegram-translator --region $REGION
```

You should see: `Logged in as <your username>` and `Listening on channel: @source_channel`.

### Step 5.3 — Redeploy after code changes

```bash
gcloud builds submit --tag $IMAGE && \
gcloud run deploy telegram-translator --image $IMAGE --region $REGION
```

---

## 10. Error Handling & Resilience

### Translation errors

`translator.py` catches all exceptions and falls back to the original Ukrainian text.
The post still reaches the destination channel; the failure is logged as `ERROR` level.

### Telegram Bot API errors

`poster.py` raises `RuntimeError` on a non-OK API response. The exception propagates
to the event handler in `main.py` and is logged by Python's asyncio exception handler.
The bot continues running and will process the next post normally.

### Telethon disconnection

Telethon has built-in automatic reconnection with exponential backoff. If Telegram's servers
restart or drop the connection, Telethon reconnects and resumes the event stream.

### Container restarts

If Cloud Run restarts the container (OOM, deploy, etc.), posts that arrived during the downtime
window are **not** replayed — Telethon's event stream only delivers messages received while
connected.

**Optional mitigation:** On startup, compare the latest message ID in the source channel against
a last-seen ID stored in a Cloud Firestore document, and replay any missed messages.
This is not implemented by default but is a straightforward addition.

### Duplicate post prevention

`--max-instances 1` guarantees only one container runs at a time, preventing duplicate posts
during scaling events.

---

## 11. Logging & Observability

All output goes to **stdout** (enforced by `PYTHONUNBUFFERED=1`), which Cloud Run forwards
to **Cloud Logging** automatically.

### Log levels used

| Level | Examples |
|---|---|
| `INFO` | New post received, successfully forwarded, logged in |
| `WARNING` | Empty message skipped |
| `ERROR` | Translation failed, Telegram API returned error |

### Viewing logs

```bash
# Live tail
gcloud run services logs tail telegram-translator --region $REGION

# GCP Console
# Cloud Run → telegram-translator → Logs tab

# Filter for errors only
gcloud logging read \
  'resource.type="cloud_run_revision" severity>=ERROR' \
  --project $PROJECT_ID \
  --limit 50
```

### Setting up alerts (optional)

In GCP Console → Monitoring → Alerting → Create Policy:
- Metric: `logging/user/ERROR` log count
- Condition: count > 5 in 10 minutes
- Notification: email / PagerDuty

---

## Cost Estimate

Prices as of 2025. Assumes a moderately active channel (~100 posts/day, avg 500 chars/post).

| Service | Usage | Monthly Cost |
|---|---|---|
| Cloud Run (1 vCPU, 512MB, always-on) | ~730 hrs/mo | ~$12 |
| Cloud Translation API | ~1.5M chars/mo | ~$30 |
| Secret Manager | 3 secrets, ~30 access/day | ~$0.10 |
| Artifact Registry | ~200MB image storage | ~$0.04 |
| Cloud Build | ~5 builds/mo | Free tier |
| Cloud Logging | Default ingestion | Free tier (first 50GB) |
| **Total** | | **~$42/mo** |

Translation is the dominant cost at $20 per 1M characters. For a low-volume channel
(<10 posts/day) expect ~$15–20/mo total.

---

## 13. Known Limitations

- **Text only.** Photos, videos, polls, stickers, and other media are silently skipped.
  The event handler checks `message.text` and returns early if empty.

- **No missed-message recovery.** Posts published while the container is offline are lost.
  See the optional Firestore backfill approach in section 10.

- **Single source channel.** The listener is wired to one `SOURCE_CHANNEL`. To support
  multiple sources, add them as a comma-separated env var and split into a list for
  `events.NewMessage(chats=[...])`.

- **No formatting preservation.** Telegram's bold/italic/links in the original post are
  translated as plain text. The Google Translate API strips entities.

- **Session tied to a phone number.** If the phone number associated with the session is
  banned or the session is revoked via Telegram Settings, you must re-run `auth.py` locally
  and update the secret.

---

## 14. Troubleshooting

### "Missing required environment variable: X"
The service started without a required env var. Check `--set-env-vars` and `--set-secrets`
in your deploy command. Run `gcloud run services describe telegram-translator` to inspect
the current env configuration.

### "Telegram authorization does not work on a cloud platform"
This means `auth.py` was run on Cloud Run or another cloud IP. Run it on your local machine,
copy the session string, and update the `TELEGRAM_SESSION_STRING` secret.

### Bot posts to channel fail with 400 Bad Request
The bot is not an admin of the destination channel, or the `DEST_CHANNEL` value is wrong
(must be `@username` or a numeric channel ID like `-1001234567890`).

### Translation returns garbled text
The source text may not actually be Ukrainian. The API always translates even if the
source language detection is wrong. You can remove `source_language="uk"` to let the API
auto-detect, but this increases cost slightly.

### Container keeps restarting (OOM)
Increase memory: `gcloud run services update telegram-translator --memory 1Gi --region $REGION`.
512MB is sufficient for typical load; OOM usually means a burst of very large posts.

### Logs not appearing in Cloud Logging
Ensure `PYTHONUNBUFFERED=1` is set in the Dockerfile or deploy command. Without it,
Python buffers output and logs may be delayed or lost on crash.
