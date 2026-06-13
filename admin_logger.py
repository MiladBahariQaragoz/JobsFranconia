import logging
import urllib.request
import json
import config

class TelegramAdminHandler(logging.Handler):
    """Sends ERROR and CRITICAL logs to the Telegram ADMIN_ID."""
    
    def emit(self, record):
        if not config.ADMIN_ID or not config.TELEGRAM_BOT_TOKEN:
            return
            
        try:
            msg = self.format(record)
            text = f"⚠️ <b>BOT ERROR</b>\n<pre>{msg}</pre>"
            
            payload = json.dumps({
                "chat_id": config.ADMIN_ID,
                "text": text,
                "parse_mode": "HTML",
            }).encode("utf-8")
            
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass # Silently fail to avoid infinite logging loops
