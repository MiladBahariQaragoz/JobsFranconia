# Deployment Guide

The bot runs as a **Docker container (`jobs-bot`) on the `german-bot` VM**, not on
Cloud Run. It was moved off Cloud Run on 2026-07-02 because a long-lived listener
there needs an always-on instance (min-instances=1, no CPU throttling), which cost
~€35/month; the VM already runs 24/7 for another bot, so co-hosting is ~free.

## Deployment Details
- **Host VM:** `german-bot` (project `learn-german-bot`, zone `us-central1-a`),
  reserved static IP `german-bot-ip`. The container runs `--restart=always`.
- **Container config:** env/secrets in `/etc/jobs-bot.env` on the VM (root, 600).
  Deploy helper: `/opt/jobs-bot-deploy.sh <image>` (pull + restart).
- **Image registry:** `europe-west3-docker.pkg.dev/jobs-franconia-bot-02/cloud-run-source-deploy/jobs-bot`
- **State bucket:** `gs://jobs-franconia-bot-02-state` (holds `last_seen.json` for catch-up)
- **CI/CD:** Cloud Build trigger on push to `master` runs [cloudbuild.yaml](cloudbuild.yaml).

> **History:** the original project `jobs-franconia-bot-01` was deleted (manually,
> via `gcloud projects delete`, 2026-06-17) and an undelete left its Cloud Run data
> plane permanently broken. The bot was migrated to `jobs-franconia-bot-02` on
> 2026-06-19, then moved from Cloud Run to the `german-bot` VM on 2026-07-02.
> **Never run `gcloud projects delete` on the live project.**

## How to make changes to the code

**Automatic (preferred):** push to `master`. The Cloud Build trigger runs
[cloudbuild.yaml](cloudbuild.yaml), which builds the image, pushes it to Artifact
Registry (tagged with the commit), then SSHes into the VM and pulls + restarts the
container. SSH uses a dedicated key whose private half is in Secret Manager
(`jobs-bot-deploy-ssh-key`; the build service account has `secretAccessor`) and
whose public half is in the VM's `authorized_keys`.

**Manual (fallback), from a machine with SSH access to the VM:**

```bash
# build + push an image, then deploy it on the VM
gcloud builds submit . --project jobs-franconia-bot-02 --region europe-west3 \
  --tag europe-west3-docker.pkg.dev/jobs-franconia-bot-02/cloud-run-source-deploy/jobs-bot:manual
gcloud compute ssh botadmin@german-bot --project learn-german-bot --zone us-central1-a \
  --command "sudo /opt/jobs-bot-deploy.sh europe-west3-docker.pkg.dev/jobs-franconia-bot-02/cloud-run-source-deploy/jobs-bot:manual"
```

*The sections below describe the previous Cloud Run setup and are kept for
historical context only — they no longer reflect how the bot is deployed.*

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