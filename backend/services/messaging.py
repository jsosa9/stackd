"""Shared outbound SMS helper — import this instead of calling Blooio directly."""

import logging
import os
import random
import time
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

BLOOIO_BASE = "https://backend.blooio.com/v2/api"


def send_reply(to_number: str, message: str) -> None:
    """Send a message via Blooio REST API. Single source of truth for all outbound SMS."""
    chat_id = quote(to_number, safe="")
    try:
        response = requests.post(
            f"{BLOOIO_BASE}/chats/{chat_id}/messages",
            headers={
                "Authorization": f"Bearer {os.getenv('BLOOIO_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={"text": message},
            timeout=10,
        )
        if not response.ok:
            logger.error(f"Blooio send failed: {response.status_code} {response.text}")
    except Exception:
        logger.exception(f"Blooio send raised exception for {to_number}")


def send_reply_with_delay(to_number: str, message: str) -> None:
    """Blocking send with a human-like typing delay (used from sync scheduler threads)."""
    base = 2.0
    length_bonus = min(len(message) / 200, 2.0)
    jitter = random.uniform(0.0, 0.6)
    time.sleep(base + length_bonus + jitter)
    send_reply(to_number, message)
