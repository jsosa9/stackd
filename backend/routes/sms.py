import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, BackgroundTasks, Request, Response, HTTPException
from pydantic import BaseModel
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
from twilio.rest import Client as TwilioClient
from supabase import create_client
from dotenv import load_dotenv

from routes.ai import generate_notification_response
from services.message_router import process_inbound_sms
from services.onboarding import handle_onboarding

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory OTP store: { normalized_phone: { code, expires_at, attempts } }
# For a multi-instance deployment swap this for Redis.
_otp_store: dict[str, dict] = {}

OTP_TTL_SECONDS = 300   # code expires after 5 minutes
OTP_MAX_ATTEMPTS = 5    # lock out after 5 wrong guesses

# ---------------------------------------------------------------------------
# Client setup
# ---------------------------------------------------------------------------

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)

twilio_client = TwilioClient(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN"),
)

validator = RequestValidator(os.getenv("TWILIO_AUTH_TOKEN"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_link_token(body: str) -> str | None:
    cleaned = body.strip().upper()
    return cleaned if re.match(r'^STK-[A-Z0-9]{4}$', cleaned) else None


def _twiml_reply(message: str) -> Response:
    """Return a TwiML XML response that sends an SMS back to the user."""
    twiml = MessagingResponse()
    twiml.message(message)
    return Response(content=str(twiml), media_type="application/xml")


def _save_message(user_id: str, direction: str, body: str) -> None:
    """Persist an inbound or outbound message to the messages table."""
    try:
        supabase.table("messages").insert({
            "user_id": user_id,
            "direction": direction,  # "inbound" | "outbound"
            "body": body,
        }).execute()
    except Exception:
        logger.exception(f"Failed to save {direction} message for user {user_id}")


# ---------------------------------------------------------------------------
# Activity notification reply helpers
# ---------------------------------------------------------------------------

_CONFIRMED_PATTERNS = re.compile(
    r"\b(yes|yeah|yep|yup|sure|definitely|absolutely|im in|i'm in|let'?s go|letsgo|ok|okay|yea|yass)\b",
    re.IGNORECASE,
)
_DECLINED_PATTERNS = re.compile(
    r"\b(no|nope|nah|can'?t|cannot|skip|not today|not gonna|won'?t|wont|pass|bail)\b",
    re.IGNORECASE,
)
_RESCHEDULE_PATTERNS = re.compile(
    r"\b(reschedule|tomorrow|later|move|shift|delay|another time|push|soon)\b"
    r"|\b(\d{1,2}:\d{2})\b"   # time like "3:30"
    r"|\b(\d{1,2}\s*(am|pm))\b",  # time like "3pm"
    re.IGNORECASE,
)

def parse_notification_reply(body: str) -> str | None:
    """
    Parse an SMS body into a notification state.
    Returns 'CONFIRMED', 'DECLINED', 'RESCHEDULED', or None if unrecognised.
    Reschedule is checked last so "yes, reschedule me to 3pm" → RESCHEDULED.
    """
    text = body.strip()
    if _RESCHEDULE_PATTERNS.search(text):
        return "RESCHEDULED"
    if _CONFIRMED_PATTERNS.search(text):
        return "CONFIRMED"
    if _DECLINED_PATTERNS.search(text):
        return "DECLINED"
    return None


def _extract_reschedule_time(body: str) -> str:
    """Pull a time string from the reply text, or return empty string."""
    # Look for HH:MM
    m = re.search(r"\b(\d{1,2}:\d{2})\s*(am|pm)?\b", body, re.IGNORECASE)
    if m:
        t = m.group(1)
        ampm = m.group(2) or ""
        return f"{t} {ampm}".strip()
    # Look for bare "3pm"
    m2 = re.search(r"\b(\d{1,2})\s*(am|pm)\b", body, re.IGNORECASE)
    if m2:
        return f"{m2.group(1)} {m2.group(2)}"
    # Natural language
    for word in ("tomorrow", "later", "tonight", "morning", "afternoon", "evening"):
        if word.lower() in body.lower():
            return word.capitalize()
    return ""


async def handle_notification_reply(
    user_id: str,
    user_data: dict,
    notif: dict,
    state: str,
    reply_text: str,
) -> str:
    """
    Update the activity_notifications row to the new state, then generate
    and return a personality-aware coach response.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    rescheduled_to = _extract_reschedule_time(reply_text) if state == "RESCHEDULED" else ""

    update_payload: dict = {
        "state": state,
        "replied_at": now_iso,
        "reply_text": reply_text,
        "updated_at": now_iso,
    }
    if rescheduled_to:
        update_payload["rescheduled_to"] = rescheduled_to

    try:
        supabase.table("activity_notifications").update(update_payload).eq("id", notif["id"]).execute()
    except Exception:
        logger.exception(f"Failed to update activity_notifications row {notif['id']}")

    # Fetch coach settings for personality
    coach_res = (
        supabase.table("coach_settings")
        .select("generated_system_prompt, coach_personality, coach_intensity")
        .eq("user_id", user_id)
        .execute()
    )
    coach = coach_res.data[0] if coach_res.data else {}

    # Format scheduled time as 12h for context
    time_str = notif.get("scheduled_time", "")
    scheduled_time_12h = ""
    if time_str:
        try:
            h, m = map(int, time_str.split(":"))
            period = "AM" if h < 12 else "PM"
            h12 = h % 12 or 12
            scheduled_time_12h = f"{h12}:{str(m).zfill(2)} {period}"
        except Exception:
            pass

    return await generate_notification_response(
        state=state,
        activity=notif["activity"],
        user_name=user_data.get("name") or "there",
        system_prompt=coach.get("generated_system_prompt", ""),
        coach_personality=coach.get("coach_personality") or "hype",
        coach_intensity=coach.get("coach_intensity") or 3,
        scheduled_time_12h=scheduled_time_12h,
        rescheduled_to=rescheduled_to,
    )


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@router.post("/incoming")
async def sms_incoming(request: Request, background_tasks: BackgroundTasks):
    """
    Twilio webhook — thin wrapper around process_inbound_sms().

    Responsibilities here:
      1. Twilio signature validation
      2. Parse From number and Body
      3. Link token intercept (STK-XXXX activation)
      4. User lookup by phone number
      5. Unknown number handling
      6. Save inbound message
      7. Notification reply intercept (CONFIRMED/DECLINED/RESCHEDULED)
      8. Delegate to process_inbound_sms() for all routing/classification/voice
      9. Save outbound message
     10. Return TwiML reply
    """
    form_data = await request.form()
    url = str(request.url)
    signature = request.headers.get("X-Twilio-Signature", "")

    # Validate Twilio signature in production
    if os.getenv("ENV", "development") != "development":
        if not validator.validate(url, dict(form_data), signature):
            logger.warning("Invalid Twilio signature — rejecting request")
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    from_number = form_data.get("From", "").strip()
    message_body = form_data.get("Body", "").strip()

    logger.info(f"Inbound SMS from {from_number}: {message_body[:80]}")

    # Link token intercept — handle STK-XXXX activation before normal user lookup
    link_token = _extract_link_token(message_body)
    if link_token:
        try:
            row = (
                supabase.table("phone_link_tokens")
                .select("user_id, used, expires_at")
                .eq("token", link_token)
                .eq("used", False)
                .execute()
            )
            if row.data:
                entry = row.data[0]
                if entry["expires_at"] > datetime.now(timezone.utc).isoformat():
                    uid = entry["user_id"]
                    supabase.table("users").update({"phone": from_number}).eq("id", uid).execute()
                    supabase.table("phone_link_tokens").update({"used": True}).eq("token", link_token).execute()
                    from routes.mock import send_welcome_message
                    welcome = await send_welcome_message(uid)
                    return _twiml_reply(welcome)
        except Exception:
            logger.exception(f"Link token activation failed for token {link_token}")
        return _twiml_reply("That code wasn't recognized. Try again or visit the app.")

    # STOP / START keyword intercept — must run before user lookup and onboarding
    _msg_upper = message_body.strip().upper()

    if _msg_upper == "STOP":
        try:
            _stop_res = supabase.table("users").select("id").eq("phone", from_number).execute()
            if _stop_res.data:
                uid = _stop_res.data[0]["id"]
                token = "STK-" + "".join(
                    secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(4)
                )
                expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
                supabase.table("phone_link_tokens").insert({
                    "user_id": uid,
                    "token": token,
                    "used": False,
                    "expires_at": expires_at,
                }).execute()
                logger.info(f"STOP token {token} created for {from_number}")
                return _twiml_reply(
                    f"To unsubscribe tap this link. "
                    f"It expires in 24 hours.\n"
                    f"stackd.chat/unsubscribe?token={token}"
                )
        except Exception:
            logger.exception(f"Failed to create unsubscribe token for {from_number}")
        return _twiml_reply(
            "To unsubscribe email support@stackd.chat with your phone number."
        )

    if _msg_upper in ("START", "UNSTOP"):
        try:
            _start_res = supabase.table("users").select("id").eq("phone", from_number).execute()
            if _start_res.data:
                supabase.table("users").update({"paused": False}).eq("phone", from_number).execute()
                logger.info(f"User {from_number} resubscribed via {_msg_upper}")
        except Exception:
            logger.exception(f"Failed to unpause user {from_number} on {_msg_upper}")
        return _twiml_reply(
            "You have been resubscribed to stackd. "
            "Your coach will resume texting you. "
            "Text STOP at any time to unsubscribe."
        )

    if _msg_upper == "HELP":
        return _twiml_reply("stackd Help\nstackd.chat/help")

    # Look up user by phone number with schedule and coach settings
    user_res = (
        supabase.table("users")
        .select("*, schedule(*), coach_settings(*)")
        .eq("phone", from_number)
        .execute()
    )

    user_data = user_res.data[0] if user_res.data else None

    # Onboarding intercept — handles new users and users mid-onboarding (step < 5)
    if user_data is None or user_data.get("onboarding_step", 5) < 5:
        onboarding_reply = await handle_onboarding(
            from_number, message_body, background_tasks, supabase, user_data
        )
        if onboarding_reply is not None:
            return _twiml_reply(onboarding_reply)
        # If None returned, onboarding is complete — fall through to normal pipeline
        # Re-fetch user_data in case it was just created
        if user_data is None:
            user_res2 = supabase.table("users").select("*, schedule(*), coach_settings(*)").eq("phone", from_number).execute()
            user_data = user_res2.data[0] if user_res2.data else None
        if user_data is None:
            return _twiml_reply("Something went wrong. Try again.")

    user_id = user_data["id"]
    user_timezone = (user_data.get("schedule") or {}).get("timezone", "America/New_York")

    # Save inbound message
    _save_message(user_id, "inbound", message_body)

    # ── Notification reply intercept ────────────────────────────────────────
    # Check if there's an open NOTIFIED activity_notifications row for this user.
    # If the reply parses to a known state, handle it and short-circuit Gemini.
    try:
        pending_res = (
            supabase.table("activity_notifications")
            .select("id, activity, scheduled_time")
            .eq("user_id", user_id)
            .eq("state", "NOTIFIED")
            .order("notified_at", desc=True)
            .limit(1)
            .execute()
        )
        if pending_res.data:
            notif_state = parse_notification_reply(message_body)
            if notif_state:
                notif_row = pending_res.data[0]
                response_text = await handle_notification_reply(
                    user_id, user_data, notif_row, notif_state, message_body
                )
                _save_message(user_id, "outbound", response_text)
                logger.info(
                    f"Notification reply {notif_state} for {notif_row['activity']} "
                    f"from {from_number}: {response_text[:80]}"
                )
                return _twiml_reply(response_text)
    except Exception:
        logger.exception(f"Notification reply intercept failed for user {user_id} — falling through to Gemini")

    # Delegate to the shared pipeline — classification, routing, handlers, voice all live there
    try:
        response_text = await process_inbound_sms(user_id, message_body, user_timezone=user_timezone)
    except Exception:
        logger.exception(f"process_inbound_sms failed for user {user_id}")
        response_text = (
            f"Hey {user_data.get('name', 'there')}! Got your message — "
            "I'm having a quick moment but I'll be right back with you. 💪"
        )

    _save_message(user_id, "outbound", response_text)
    logger.info(f"Outbound SMS to {from_number}: {response_text[:80]}")
    return _twiml_reply(response_text)


# ---------------------------------------------------------------------------
# OTP — phone verification (used during quiz step 6, before sign-in)
# ---------------------------------------------------------------------------

class SendOtpRequest(BaseModel):
    phone: str  # E.164 format, e.g. "+15550001234"

class VerifyOtpRequest(BaseModel):
    phone: str
    code: str


def _normalize_phone(phone: str) -> str:
    """Strip spaces/dashes and ensure E.164 format."""
    digits = "".join(c for c in phone if c.isdigit() or c == "+")
    if not digits.startswith("+"):
        digits = "+1" + digits  # default to US
    return digits


@router.post("/send-otp")
async def send_otp(req: SendOtpRequest):
    """
    Generate a 6-digit OTP, store it with a 5-minute TTL, and send it to
    the given phone number via Twilio. Called from quiz step 6 before sign-in.
    The user doesn't have an account yet — no user_id is needed.
    """
    phone = _normalize_phone(req.phone)

    # Generate a cryptographically random 6-digit code
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=OTP_TTL_SECONDS)

    _otp_store[phone] = {
        "code": code,
        "expires_at": expires_at,
        "attempts": 0,
    }

    body = f"Your stackd verification code is: {code}\n\nExpires in 5 minutes. Don't share this."

    try:
        msg = twilio_client.messages.create(body=body, from_=os.getenv("TWILIO_PHONE_NUMBER"), to=phone)
        logger.info(f"Sent OTP to {phone}, SID: {msg.sid}")
    except Exception as e:
        logger.exception(f"Failed to send OTP to {phone}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to send verification code: {str(e)}")

    return {"status": "sent", "expires_in": OTP_TTL_SECONDS}


@router.post("/verify-otp")
async def verify_otp(req: VerifyOtpRequest):
    """
    Verify a 6-digit OTP for a phone number. Enforces TTL and max-attempt lockout.
    On success returns { verified: true } — the frontend stores this in the quiz
    localStorage and the phone gets saved to the users table during onboarding.
    """
    phone = _normalize_phone(req.phone)
    entry = _otp_store.get(phone)

    if not entry:
        raise HTTPException(status_code=400, detail="No code was sent to this number. Request a new one.")

    if datetime.now(timezone.utc) > entry["expires_at"]:
        del _otp_store[phone]
        raise HTTPException(status_code=400, detail="Code expired. Request a new one.")

    if entry["attempts"] >= OTP_MAX_ATTEMPTS:
        del _otp_store[phone]
        raise HTTPException(status_code=429, detail="Too many attempts. Request a new code.")

    entry["attempts"] += 1

    if req.code != entry["code"]:
        remaining = OTP_MAX_ATTEMPTS - entry["attempts"]
        raise HTTPException(
            status_code=400,
            detail=f"Incorrect code. {remaining} attempt{'s' if remaining != 1 else ''} left.",
        )

    # Correct — clean up and confirm
    del _otp_store[phone]
    logger.info(f"Phone {phone} verified successfully")
    return {"verified": True, "phone": phone}
