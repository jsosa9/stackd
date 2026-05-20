"""Shared outbound SMS/iMessage helper — import this instead of calling Sendblue directly."""

import logging
import os
import random
import time

import requests

logger = logging.getLogger(__name__)

SENDBLUE_BASE = "https://api.sendblue.co/api"


def send_reply(to_number: str, message: str) -> None:
    """Send a message via Sendblue REST API. Single source of truth for all outbound messages."""
    try:
        response = requests.post(
            f"{SENDBLUE_BASE}/send-message",
            headers={
                "sb-api-key-id": os.getenv("SENDBLUE_API_KEY", ""),
                "sb-api-secret-key": os.getenv("SENDBLUE_API_SECRET", ""),
                "Content-Type": "application/json",
            },
            json={
                "number": to_number,
                "from_number": os.getenv("SENDBLUE_PHONE_NUMBER", ""),
                "content": message,
            },
            timeout=10,
        )
        if not response.ok:
            logger.error(f"Sendblue send failed: {response.status_code} {response.text}")
    except Exception:
        logger.exception(f"Sendblue send raised exception for {to_number}")


def send_reply_with_delay(to_number: str, message: str) -> None:
    """Blocking send with a human-like typing delay (used from sync scheduler threads)."""
    base = 2.0
    length_bonus = min(len(message) / 200, 2.0)
    jitter = random.uniform(0.0, 0.6)
    time.sleep(base + length_bonus + jitter)
    send_reply(to_number, message)
