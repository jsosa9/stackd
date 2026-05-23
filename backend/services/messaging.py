"""Shared outbound SMS/iMessage helper — import this instead of calling Sendblue directly."""

import logging
import os

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
    send_reply(to_number, message)
