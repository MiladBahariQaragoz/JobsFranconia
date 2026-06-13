"""
Run this ONCE on your local machine to generate a Telethon session string.
Never run this on Cloud Run — Telegram blocks logins from cloud IPs.

Usage:
    python auth.py

Copy the printed session string and store it in Google Secret Manager as
TELEGRAM_SESSION_STRING.
"""

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID = int(input("Enter your API ID: "))
API_HASH = input("Enter your API Hash: ").strip()

print("\nYou will receive a Telegram login code on your phone.\n")

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    session_string = client.session.save()

print("\n" + "=" * 60)
print("YOUR SESSION STRING (store this in Secret Manager):")
print("=" * 60)
print(session_string)
print("=" * 60 + "\n")
