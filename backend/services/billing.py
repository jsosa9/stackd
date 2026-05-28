"""
billing.py — Single source of truth for all billing decisions.

Nothing else in the codebase makes billing decisions directly.
All functions are async. Fail open on infrastructure errors — never
cut off a user because Supabase or Stripe had a blip.

Environment variables required:
    STRIPE_SECRET_KEY   — Stripe secret key (sk_live_... or sk_test_...)
    STRIPE_PRICE_ID     — Stripe price ID for the subscription product
    STRIPE_WEBHOOK_SECRET — Stripe webhook signing secret (whsec_...)
    FRONTEND_URL        — Base URL for checkout success/cancel redirects
"""

import logging
import os
from datetime import datetime, timezone

import stripe
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

logger = logging.getLogger(__name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)


# ---------------------------------------------------------------------------
# Core eligibility gate
# ---------------------------------------------------------------------------

async def is_billable(user_id: str) -> bool:
    """
    Return True if the user is allowed to receive coaching messages.

    Rules:
      - subscription_status = 'active'  → always billable
      - subscription_status = 'trial' AND trial_ends_at > NOW()  → billable
      - anything else → not billable

    Fails open: if Supabase is unreachable, return True so users are
    never cut off due to infrastructure failure.
    """
    try:
        res = (
            supabase.table("users")
            .select("subscription_status, trial_ends_at")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            logger.warning(f"[billing] is_billable: user {user_id} not found — failing open")
            return True

        user = res.data[0]
        status = user.get("subscription_status", "trial")

        if status == "active":
            return True

        if status == "trial":
            trial_ends_at = user.get("trial_ends_at")
            if not trial_ends_at:
                logger.info(f"[billing] user={user_id} trial with no end date — failing open")
                return True
            ends = datetime.fromisoformat(trial_ends_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if ends > now:
                return True
            logger.info(f"[billing] user={user_id} trial expired at {trial_ends_at} — blocking")
            return False

        logger.info(f"[billing] user={user_id} status={status} — blocking")
        return False

    except Exception:
        logger.exception(f"[billing] is_billable check failed for user={user_id} — failing open")
        return True


# ---------------------------------------------------------------------------
# Stripe customer management
# ---------------------------------------------------------------------------

async def get_or_create_stripe_customer(user_id: str) -> str:
    """
    Return the Stripe customer ID for this user, creating one if needed.
    Never creates duplicates — checks DB first before calling Stripe.
    """
    res = (
        supabase.table("users")
        .select("stripe_customer_id, email, phone")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise ValueError(f"User {user_id} not found")

    user = res.data[0]

    if user.get("stripe_customer_id"):
        return user["stripe_customer_id"]

    customer = stripe.Customer.create(
        email=user["email"],
        metadata={"user_id": user_id, "phone": user.get("phone", "")},
    )
    customer_id = customer.id

    supabase.table("users").update({"stripe_customer_id": customer_id}).eq("id", user_id).execute()
    logger.info(f"[billing] created Stripe customer {customer_id} for user={user_id}")
    return customer_id


async def create_checkout_session(user_id: str) -> str:
    """
    Create a Stripe Checkout session and return the URL.
    Always calls get_or_create_stripe_customer first to avoid duplicates.
    """
    customer_id = await get_or_create_stripe_customer(user_id)
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": os.getenv("STRIPE_PRICE_ID"), "quantity": 1}],
        success_url=f"{frontend_url}?paid=true",
        cancel_url=frontend_url,
    )
    logger.info(f"[billing] checkout session created for user={user_id}")
    return session.url


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

async def cancel_user(user_id: str) -> None:
    """
    Mark user canceled in Supabase and immediately cancel in Stripe.
    Does not delete any user data.
    """
    try:
        res = (
            supabase.table("users")
            .select("stripe_subscription_id")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        sub_id = res.data[0].get("stripe_subscription_id") if res.data else None

        supabase.table("users").update({"subscription_status": "canceled"}).eq("id", user_id).execute()
        logger.info(f"[billing] user={user_id} marked canceled in Supabase")

        if sub_id:
            stripe.Subscription.cancel(sub_id)
            logger.info(f"[billing] Stripe subscription {sub_id} canceled for user={user_id}")

    except Exception:
        logger.exception(f"[billing] cancel_user failed for user={user_id}")


# ---------------------------------------------------------------------------
# Trial warning SMS
# ---------------------------------------------------------------------------

async def _get_checkout_url(user_id: str) -> str:
    try:
        return await create_checkout_session(user_id)
    except Exception:
        logger.exception(f"[billing] failed to create checkout session for user={user_id}")
        return os.getenv("FRONTEND_URL", "http://localhost:3000")


async def generate_trial_upsell_sms(user_id: str, trial_day: int, days_active: int) -> str:
    """
    Generate an in-character coach upsell SMS for day 4 or day 5 of the trial.
    Uses the user's actual coach persona from coach_settings.
    """
    checkout_url = await _get_checkout_url(user_id)

    try:
        coach_res = (
            supabase.table("coach_settings")
            .select("generated_system_prompt")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        system_prompt = coach_res.data[0].get("generated_system_prompt", "") if coach_res.data else ""
    except Exception:
        system_prompt = ""

    no_markdown = "No asterisks, no bold, no italics, no markdown of any kind. Plain text SMS only."

    if trial_day == 4:
        user_prompt = (
            f"The user has been showing up for {days_active} days. Their free trial ends tomorrow. "
            f"Tell them in your voice — reference that they've been putting in the work and ask if they're continuing. "
            f"Include this link naturally: {checkout_url}. One to two sentences. No payment jargon. No emojis. {no_markdown}"
        )
    else:
        user_prompt = (
            f"This is the last day of the user's free trial. They haven't signed up yet. "
            f"One final push in your voice — direct, in character. Include: {checkout_url}. One sentence. No emojis. {no_markdown}"
        )

    try:
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-2.5-flash-lite", system_instruction=system_prompt)
        response = model.generate_content(user_prompt)
        return response.text.strip()
    except Exception:
        logger.exception(f"[billing] Gemini upsell generation failed for user={user_id}")
        return f"Trial ends soon. Stay in it: {checkout_url}"


async def generate_trial_warning_sms(user_id: str, hours_remaining: int) -> str:
    """
    Generate an in-character trial warning SMS with a checkout link.
    Creates Stripe customer and checkout session if they don't exist yet.
    """
    try:
        checkout_url = await create_checkout_session(user_id)
    except Exception:
        logger.exception(f"[billing] failed to create checkout session for trial warning user={user_id}")
        checkout_url = os.getenv("FRONTEND_URL", "http://localhost:3000")

    try:
        coach_res = (
            supabase.table("coach_settings")
            .select("generated_system_prompt")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        system_prompt = coach_res.data[0].get("generated_system_prompt", "") if coach_res.data else ""
    except Exception:
        system_prompt = ""

    try:
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            "gemini-2.5-flash-lite",
            system_instruction=system_prompt,
        )
        prompt = (
            f"The user's free trial ends in {hours_remaining} hours. "
            f"Tell them in your voice that their time is almost up and they need to commit. "
            f"Give them this link: {checkout_url}. "
            f"One to two sentences max. No emojis. No asterisks, no bold, no italics, no markdown. Plain text SMS only. Make it feel urgent but stay completely in character."
        )
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception:
        logger.exception(f"[billing] Gemini trial warning generation failed for user={user_id}")
        return f"Your free trial ends in {hours_remaining} hours. Lock in your spot: {checkout_url}"
