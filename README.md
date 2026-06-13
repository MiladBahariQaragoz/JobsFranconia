# Telegram Channel Translator Bot

Monitors a Ukrainian Telegram channel, translates new posts to Persian using Google Cloud Translation API, and forwards them to your own channel — automatically, in real time.

## How It Works

1. A new post appears in the source (Ukrainian) channel
2. Telethon (running as your Telegram user account) receives it instantly
3. Google Cloud Translation API translates the text from Ukrainian (`uk`) to Persian (`fa`)
4. A Telegram bot posts the translated text to your destination channel

## Prerequisites

- A Telegram account + API credentials from [my.telegram.org](https://my.telegram.org)
- A Telegram bot created via [@BotFather](https://t.me/BotFather), added as admin to your destination channel
- A Google Cloud project with billing enabled
- Python 3.12+ (for local setup)
- Google Cloud CLI (`gcloud`)

## Quick Start

### 1. Install dependencies locally

```bash
pip install -r requirements.txt
```

### 2. Generate your Telegram session string (run once, on your machine)

```bash
python auth.py
```

Save the printed session string — you'll need it for the next step.

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in all values
```

### 4. Run locally to test

```bash
python main.py
```

Post something in the source channel — you should see it translated and forwarded within seconds.

### 5. Deploy to Google Cloud Run

Follow the deployment steps in [system.md](system.md#phase-3--google-cloud-setup).

## Project Structure

```
├── auth.py           # One-time local session generator
├── config.py         # Environment variable loader
├── translator.py     # Google Translate wrapper (uk → fa)
├── poster.py         # Telegram Bot API poster
├── main.py           # Entry point — Telethon event listener
├── requirements.txt
├── Dockerfile
├── .env.example
├── plan.md           # Architecture and implementation plan
└── system.md         # Detailed technical reference
```

## Cost Estimate

~$20–25/month (Cloud Run always-on + Translation API at moderate volume).
See [system.md](system.md#cost-estimate) for a full breakdown.
