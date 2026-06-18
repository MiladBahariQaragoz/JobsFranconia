# Deployment Guide

This project is deployed on **Google Cloud Run**. 

## Deployment Details
- **Platform:** Google Cloud Run
- **Project ID:** `jobs-franconia-bot-01`
- **Region:** `europe-west3` (Frankfurt)
- **Service Name:** `jobs-bot`

## How to make changes to the code

If you edit the Python code (e.g., changing the filter logic in `filter.py` or modifying `main.py`) and want to push the updates to the live bot, run the following command in your terminal from the root folder of this project:

```bash
gcloud run deploy jobs-bot --source . --region europe-west3 --no-cpu-throttling --min-instances=1 --max-instances=1 --allow-unauthenticated
```
*Note: This command will package the code, build a Docker container automatically via Cloud Build, and deploy it. It usually takes 2-3 minutes to complete.*

> **Why `--min-instances=1` matters (the "deploys fine but stops working" bug):**
> This bot is a long-lived *listener*, not a request/response web service. It opens
> an outbound connection to Telegram and waits. Cloud Run, by default, scales an
> instance to **zero** when no inbound HTTP requests arrive — which is always, here,
> because nothing calls the service URL. When the instance is shut down the bot stops
> receiving and forwarding posts, even though the deploy "succeeded". `--min-instances=1`
> keeps exactly one instance alive 24/7; `--no-cpu-throttling` keeps its CPU running
> between requests; `--max-instances=1` prevents a second instance (which would create
> a duplicate Telegram listener and double-post). If you ever change env vars from the
> Console instead of redeploying, re-check that **Minimum number of instances = 1** under
> the revision's autoscaling settings.

## How to change Environment Variables (`.env` data)

For security reasons, `.env` files are not uploaded to Google Cloud. If you need to update a configuration value (like changing the destination channel, bot token, or admin ID), you must do it via the Google Cloud Console:

1. Open the [Google Cloud Run Console](https://console.cloud.google.com/run?project=jobs-franconia-bot-01).
2. Click on the **`jobs-bot`** service.
3. Click the **Edit & Deploy New Revision** button at the top.
4. Scroll down and click on the **Container(s), Volumes, Networking, Security** tab.
5. Go to the **Variables & Secrets** tab.
6. Under the **Environment variables** section, add, edit, or remove your variables.
7. Click the **Deploy** button at the bottom of the page.

Your bot will restart instantly with the new configuration.

### Enabling the Azerbaijani channel

The bot can post a parallel Azerbaijani version of every job alongside the Persian
one. It is **off by default**. To turn it on, add one environment variable (same
rules as the Persian destinations):

- `DEST_CHANNEL_AZ` — a single Azerbaijani channel that receives every source's posts, **or**
- `DEST_CHANNELS_AZ` — a comma-separated list paired 1:1 by position with `SOURCE_CHANNELS`.

The bot account must be an **admin** of each Azerbaijani destination, exactly like the
Persian channels. Leaving both unset keeps the bot Persian-only.

## Checking Logs
To view live logs from the bot (to see what it's processing or why it's failing):
1. Go to the **`jobs-bot`** service in the Cloud Run Console.
2. Click on the **Logs** tab.
3. You will see all `INFO` and `ERROR` outputs here in real-time.