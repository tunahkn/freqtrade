"""Telegram bildirimi (opsiyonel). Token/chat_id yoksa sessizce atlanır."""

from __future__ import annotations

import json
import logging
import os
import urllib.request


log = logging.getLogger(__name__)


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN") or os.environ.get("FREQTRADE__TELEGRAM__TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("FREQTRADE__TELEGRAM__CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text[:4000]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).close()  # noqa: S310
        return True
    except Exception:
        log.warning("Telegram mesajı gönderilemedi", exc_info=True)
        return False
