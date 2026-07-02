# Deployment Guide

This project is deployed on **Google Cloud Run**. 

## Deployment Details
- **Platform:** Google Cloud Run
- **Project ID:** `jobs-franconia-bot-02`
- **Region:** `europe-west3` (Frankfurt)
- **Service Name:** `jobs-bot`
- **State bucket:** `gs://jobs-franconia-bot-02-state` (holds `last_seen.json` for catch-up)

> **History:** the original project `jobs-franconia-bot-01` was deleted (manually,
> via `gcloud projects delete`, 2026-06-17) and an undelete left its Cloud Run data
> plane permanently broken — all writes failed `"Project has been deleted"` even though
> the control plane reported ACTIVE. The bot was migrated to `jobs-franconia-bot-02` on
> 2026-06-19. **Never run `gcloud projects delete` on the live project.**

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

1. Open the [Google Cloud Run Console](https://console.cloud.google.com/run?project=jobs-franconia-bot-02).
2. Click on the **`jobs-bot`** service.
3. Click the **Edit & Deploy New Revision** button at the top.
4. Scroll down and click on the **Container(s), Volumes, Networking, Security** tab.
5. Go to the **Variables & Secrets** tab.
6. Under the **Environment variables** section, add, edit, or remove your variables.
7. Click the **Deploy** button at the bottom of the page.

Your bot will restart instantly with the new configuration.

### Missed-message catch-up (downtime recovery)

If the bot is offline for a while, it must not silently drop posts made meanwhile.
On startup it reads a durable per-channel marker (`last_seen.json` in the state
bucket) and replays every source message newer than the marker through the normal
filter → translate → post pipeline, then resumes live. The marker advances as
messages are handled (see `state.py` + `catch_up()` in `main.py`).

- Requires env var `STATE_BUCKET` (the GCS bucket name) and that the service's
  runtime service account has `roles/storage.objectAdmin` on that bucket.
- **First run only** records a baseline (current latest id) and does **not** replay
  history — so pointing the bot at a channel won't translate its entire backlog.
- If `STATE_BUCKET` is unset or the bucket is unreachable, the bot still runs; it
  just can't catch up across that restart (fail-safe, never crashes).

## Checking Logs
To view live logs from the bot (to see what it's processing or why it's failing):
1. Go to the **`jobs-bot`** service in the Cloud Run Console.
2. Click on the **Logs** tab.
3. You will see all `INFO` and `ERROR` outputs here in real-time.