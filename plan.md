# Telegram Channel Translator Bot — Implementation Plan

## Overview

A service that monitors a Ukrainian Telegram channel, translates new posts to Persian using
Google Cloud Translation API, and forwards them to your own Persian channel.

**Stack:** Python · Telethon · Google Cloud Translation v2 · Cloud Run · Secret Manager

---

## Architecture

```
[Ukrainian Channel] ──read──▶ [Telethon user session]
                                      │
                                      ▼
                           [Google Translate API]
                           (Ukrainian → Persian)
                                      │
                                      ▼
                        [Telegram Bot API / Telethon]
                                      │
                                      ▼
                          [Your Persian Channel]
```

**Why Telethon (not Bot API) for reading:**
Telegram bots can only receive messages from channels they are admins of.
Since the source channel belongs to someone else, you need to act as a
regular Telegram user (MTProto protocol) via the Telethon library.

**Why Cloud Run with min_instances=1:**
Telethon maintains a persistent connection to Telegram servers. Cloud Run
with a minimum of 1 instance keeps the connection alive 24/7.

---

## Prerequisites You Need to Collect

### 1. Telegram API Credentials (from my.telegram.org)
- Log in at https://my.telegram.org with your phone number
- Go to "API development tools"
- Create an app → note your **API ID** and **API Hash**

### 2. Telegram Bot (for posting to your Persian channel)
- Open @BotFather on Telegram → `/newbot`
- Note the **Bot Token**
- Add this bot as **admin** to your destination (Persian) channel with "Post messages" permission

### 3. Channel Identifiers
- **Source channel username** (e.g. `@ukraine_news_channel`)
- **Destination channel username or ID** (e.g. `@my_persian_channel`)

### 4. Google Cloud Project
- A GCP project with billing enabled
- APIs to enable: **Cloud Translation API**, **Secret Manager API**, **Cloud Run API**, **Artifact Registry API**

---

## Project Structure

```
telegram-translator/
├── main.py              # Entry point: Telethon listener
├── translator.py        # Google Translate wrapper
├── poster.py            # Posts translated text to destination channel
├── config.py            # Loads config from env vars / Secret Manager
├── requirements.txt
├── Dockerfile
└── .env.example         # Template for local development
```

---

## Phase 1 — Local Development & Authentication

### Step 1.1 — Install dependencies locally

```bash
pip install telethon google-cloud-translate python-dotenv
```

### Step 1.2 — Authenticate Telethon locally (CRITICAL)

> Telegram blocks new logins from cloud server IPs.
> You MUST authenticate on your own machine first, then export the session string.

Run this one-time script on your local machine:

```python
# auth.py  —  run once locally, never on Cloud Run
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID = 123456          # your API ID
API_HASH = "your_hash"  # your API Hash

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print("SESSION STRING:")
    print(client.session.save())
```

This prints a long string. **Save it — this is your session.**

### Step 1.3 — Create `.env` for local testing

```
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_SESSION_STRING=your_session_string_from_step_1_2
TELEGRAM_BOT_TOKEN=your_bot_token
SOURCE_CHANNEL=@ukraine_channel_username
DEST_CHANNEL=@your_persian_channel
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
```

---

## Phase 2 — Application Code

### `config.py`
Loads all configuration from environment variables (works both locally and on Cloud Run).

### `translator.py`
Calls Google Cloud Translation API v2:
- Source language: `uk` (Ukrainian)
- Target language: `fa` (Persian/Farsi)
- Uses Application Default Credentials (ADC) on Cloud Run automatically

### `main.py`
- Creates a Telethon client using StringSession
- Registers an event handler on `NewMessage` from the source channel
- On each new message: translate text → post to destination via Bot API
- Runs `client.run_until_disconnected()` (keeps process alive)

### `poster.py`
Uses the Bot Token to call Telegram's Bot API (`sendMessage`) to post
the translated Persian text to the destination channel.

---

## Phase 3 — Google Cloud Setup

### Step 3.1 — Enable APIs

```bash
gcloud services enable \
  translate.googleapis.com \
  secretmanager.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com
```

### Step 3.2 — Store secrets in Secret Manager

```bash
# Session string (sensitive — never commit to git)
echo -n "YOUR_SESSION_STRING" | \
  gcloud secrets create TELEGRAM_SESSION_STRING --data-file=-

# API hash
echo -n "YOUR_API_HASH" | \
  gcloud secrets create TELEGRAM_API_HASH --data-file=-

# Bot token
echo -n "YOUR_BOT_TOKEN" | \
  gcloud secrets create TELEGRAM_BOT_TOKEN --data-file=-
```

### Step 3.3 — Create a service account for Cloud Run

```bash
gcloud iam service-accounts create telegram-translator \
  --display-name="Telegram Translator Bot"

# Grant access to Secret Manager
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:telegram-translator@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Grant access to Translation API
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:telegram-translator@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/cloudtranslate.user"
```

---

## Phase 4 — Containerization

### `Dockerfile`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

### `requirements.txt`

```
telethon==1.36.0
google-cloud-translate==3.15.0
python-dotenv==1.0.1
```

---

## Phase 5 — Deploy to Cloud Run

### Step 5.1 — Build and push container

```bash
export PROJECT_ID=your-gcp-project-id
export REGION=us-central1   # or europe-west1 for lower latency to Telegram

gcloud artifacts repositories create telegram-bot \
  --repository-format=docker \
  --location=$REGION

gcloud builds submit --tag $REGION-docker.pkg.dev/$PROJECT_ID/telegram-bot/translator
```

### Step 5.2 — Deploy

```bash
gcloud run deploy telegram-translator \
  --image $REGION-docker.pkg.dev/$PROJECT_ID/telegram-bot/translator \
  --region $REGION \
  --service-account telegram-translator@$PROJECT_ID.iam.gserviceaccount.com \
  --min-instances 1 \
  --max-instances 1 \
  --memory 512Mi \
  --no-allow-unauthenticated \
  --set-env-vars "SOURCE_CHANNEL=@ukraine_channel,DEST_CHANNEL=@persian_channel,TELEGRAM_API_ID=123456,GOOGLE_CLOUD_PROJECT=$PROJECT_ID" \
  --set-secrets "TELEGRAM_SESSION_STRING=TELEGRAM_SESSION_STRING:latest,TELEGRAM_API_HASH=TELEGRAM_API_HASH:latest,TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest"
```

> `--min-instances 1` keeps the container alive so Telethon stays connected.
> `--max-instances 1` prevents duplicate posts if Cloud Run scales.

---

## Phase 6 — Error Handling & Monitoring

- **Disconnection:** Telethon auto-reconnects; wrap the main loop in a retry
- **Translation failure:** Log and forward original Ukrainian text as fallback
- **Duplicate prevention:** Track last processed message ID in a Cloud Firestore
  document (optional, prevents re-posts on container restart)
- **Logs:** Cloud Run streams all `print()` / `logging` output to Cloud Logging
  automatically. View at: GCP Console → Cloud Run → Logs

---

## Cost Estimate (monthly, light traffic)

| Service | Cost |
|---|---|
| Cloud Run (min 1 instance, 512MB) | ~$10–15/mo |
| Cloud Translation (500k chars) | ~$10 |
| Secret Manager | ~$0.06 |
| **Total** | **~$20–25/mo** |

Translation is billed at $20 per 1M characters. A typical channel post is ~500 chars.

---

## What to Do Next

1. Collect all credentials from Prerequisites section
2. Clone/create project folder and run `auth.py` locally to get session string
3. Write and test the code locally with `.env`
4. Follow Phases 3–5 to deploy

Ready to start writing the actual code whenever you are.
