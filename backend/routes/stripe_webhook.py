"""
stripe_webhook.py — Stripe webhook handler.

Environment variables required:
    STRIPE_SECRET_KEY      — Stripe secret key (sk_live_... or sk_test_...)
    STRIPE_WEBHOOK_SECRET  — Webhook signing secret from Stripe dashboard (whsec_...)
    STRIPE_PRICE_ID        — Stripe price ID for the subscription product
    FRONTEND_URL           — Base URL for checkout links in SMS messages

Mount this router at /stripe in main.py.

IMPORTANT: This route reads raw bytes from the request body for Stripe
signature verification. Do NOT wrap the endpoint body in a Pydantic model
or signature verification will break.
"""

import logging
import os
from datetime import datetime, timezone

import stripe
from dotenv import load_dotenv
from fastapi import APIRouter, Request, Response
from supabase import create_client

from services.billing import cancel_user, create_checkout_session
from services.messaging import send_reply

load_dotenv()

logger = logging.getLogger(__name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lookup_user_by_email(email: str) -> dict | None:
    try:
        res = (
            supabase.table("users")
            .select("id, phone, subscription_status, stripe_subscription_id")
            .eq("email", email)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception:
        logger.exception(f"[stripe_webhook] DB lookup failed for email={email}")
        return None


async def _coach_voice_message(user_id: str, prompt: str) -> str:
    """Generate a short in-character message using the user's active coach."""
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

        coach_res = (
            supabase.table("coach_settings")
            .select("generated_system_prompt")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        system_prompt = coach_res.data[0].get("generated_system_prompt", "") if coach_res.data else ""

        model = genai.GenerativeModel(
            "gemini-2.5-flash-lite",
            system_instruction=system_prompt,
        )
        return model.generate_content(prompt).text.strip()
    except Exception:
        logger.exception(f"[stripe_webhook] coach voice generation failed for user={user_id}")
        return prompt


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@router.post("/webhook")
async def stripe_webhook(request: Request) -> Response:
    """
    Stripe webhook handler. Reads raw body bytes for signature verification.
    Returns 200 for all known events (even if we skip processing) so Stripe
    doesn't retry. Returns 400 only for invalid signatures or malformed payloads.
    """
    raw_body = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe.Webhook.construct_event(raw_body, sig_header, webhook_secret)
    except stripe.errors.SignatureVerificationError:
        logger.warning("[stripe_webhook] invalid signature — rejecting")
        return Response(content="Invalid signature", status_code=400)
    except Exception:
        logger.exception("[stripe_webhook] failed to parse event")
        return Response(content="Bad payload", status_code=400)

    event_type = event["type"]
    logger.info(f"[stripe_webhook] received event type={event_type} id={event['id']}")

    try:
        if event_type == "checkout.session.completed":
            await _handle_checkout_completed(event["data"]["object"])

        elif event_type == "invoice.payment_failed":
            await _handle_payment_failed(event["data"]["object"])

        elif event_type == "customer.subscription.deleted":
            await _handle_subscription_deleted(event["data"]["object"])

        elif event_type == "invoice.payment_succeeded":
            await _handle_payment_succeeded(event["data"]["object"])

        else:
            logger.debug(f"[stripe_webhook] unhandled event type={event_type} — ignoring")

    except Exception:
        logger.exception(f"[stripe_webhook] handler error for event_type={event_type} id={event['id']}")

    return Response(content='{"ok":true}', media_type="application/json")


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

async def _handle_checkout_completed(session: dict) -> None:
    email = (session.get("customer_details") or {}).get("email", "")
    if not email:
        logger.warning("[stripe_webhook] checkout.session.completed — no customer email")
        return

    user = _lookup_user_by_email(email)
    if not user:
        logger.warning(f"[stripe_webhook] checkout.session.completed — no user found for email={email}")
        return

    user_id = user["id"]

    if user.get("subscription_status") == "active":
        logger.info(f"[stripe_webhook] user={user_id} already active — skipping duplicate processing")
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    supabase.table("users").update({
        "subscription_status": "active",
        "stripe_customer_id": session.get("customer"),
        "stripe_subscription_id": session.get("subscription"),
        "paid_at": now_iso,
    }).eq("id", user_id).execute()

    logger.info(f"[stripe_webhook] user={user_id} activated — subscription={session.get('subscription')}")

    phone = user.get("phone")
    if phone:
        msg = await _coach_voice_message(
            user_id,
            "The user just subscribed and paid. Welcome them and tell them their coach isn't going anywhere. "
            "Stay in character. One sentence. No emojis.",
        )
        send_reply(phone, msg)


async def _handle_payment_failed(invoice: dict) -> None:
    customer_id = invoice.get("customer")
    if not customer_id:
        return

    try:
        customer = stripe.Customer.retrieve(customer_id)
        email = customer.get("email", "")
    except Exception:
        logger.exception(f"[stripe_webhook] failed to retrieve customer {customer_id}")
        return

    user = _lookup_user_by_email(email)
    if not user:
        logger.warning(f"[stripe_webhook] invoice.payment_failed — no user for email={email}")
        return

    user_id = user["id"]
    supabase.table("users").update({"subscription_status": "past_due"}).eq("id", user_id).execute()
    logger.info(f"[stripe_webhook] user={user_id} marked past_due")

    phone = user.get("phone")
    if not phone:
        return

    try:
        checkout_url = await create_checkout_session(user_id)
    except Exception:
        logger.exception(f"[stripe_webhook] failed to create checkout for past_due user={user_id}")
        checkout_url = os.getenv("FRONTEND_URL", "")

    msg = await _coach_voice_message(
        user_id,
        f"The user's payment didn't go through and their account is on hold. "
        f"Tell them in your voice that their payment failed and they need to fix it to keep going. "
        f"Give them this link: {checkout_url}. Two sentences max. No emojis. Stay in character.",
    )
    send_reply(phone, msg)


async def _handle_subscription_deleted(subscription: dict) -> None:
    customer_id = subscription.get("customer")
    if not customer_id:
        return

    try:
        customer = stripe.Customer.retrieve(customer_id)
        email = customer.get("email", "")
    except Exception:
        logger.exception(f"[stripe_webhook] failed to retrieve customer {customer_id}")
        return

    user = _lookup_user_by_email(email)
    if not user:
        logger.warning(f"[stripe_webhook] customer.subscription.deleted — no user for email={email}")
        return

    user_id = user["id"]
    supabase.table("users").update({"subscription_status": "canceled"}).eq("id", user_id).execute()
    logger.info(f"[stripe_webhook] user={user_id} marked canceled")

    phone = user.get("phone")
    if phone:
        msg = await _coach_voice_message(
            user_id,
            "The user just canceled their subscription. Acknowledge they're leaving, stay in character, "
            "keep it brief, and leave the door open if they ever want to come back. No emojis.",
        )
        send_reply(phone, msg)


async def _handle_payment_succeeded(invoice: dict) -> None:
    customer_id = invoice.get("customer")
    if not customer_id:
        return

    try:
        customer = stripe.Customer.retrieve(customer_id)
        email = customer.get("email", "")
    except Exception:
        logger.exception(f"[stripe_webhook] failed to retrieve customer {customer_id}")
        return

    user = _lookup_user_by_email(email)
    if not user:
        return

    user_id = user["id"]
    if user.get("subscription_status") != "active":
        supabase.table("users").update({"subscription_status": "active"}).eq("id", user_id).execute()
        logger.info(f"[stripe_webhook] invoice.payment_succeeded — corrected status to active for user={user_id}")
    else:
        logger.info(f"[stripe_webhook] invoice.payment_succeeded — user={user_id} already active, no action needed")
