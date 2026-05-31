import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, BackgroundTasks, Request, Response, HTTPException
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv

from routes.ai import generate_notification_response
from services.message_router import process_inbound_sms
from services.messaging import send_reply
from services.onboarding import handle_onboarding

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory OTP store: { normalized_phone: { code, expires_at, attempts } }
# For a multi-instance deployment swap this for Redis.
_otp_store: dict[str, dict] = {}

OTP_TTL_SECONDS = 300   # code expires after 5 minutes
OTP_MAX_ATTEMPTS = 5    # lock out after 5 wrong guesses

# In-memory rate limiter: { phone: { count, window_start } }
_rate_store: dict[str, dict] = {}

# Adaptive message aggregation window
# _pending_token: phone → latest token (reset on each new message)
# _message_buffer: phone → ordered list of message bodies
_pending_token: dict[str, str] = {}
_message_buffer: dict[str, list[str]] = {}
_RATE_PER_MINUTE = 5  # >5 msgs/min = bot/spam; real split-texters won't hit this

# ---------------------------------------------------------------------------
# Client setup
# ---------------------------------------------------------------------------

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_link_token(body: str) -> str | None:
    cleaned = body.strip().upper()
    return cleaned if re.match(r'^STK-[A-Z0-9]{4}$', cleaned) else None


async def _typing_delay(text: str) -> None:
    pass


def _is_rate_limited(phone: str) -> bool:
    now = datetime.now(timezone.utc)
    entry = _rate_store.get(phone)
    if entry is None:
        _rate_store[phone] = {"count": 1, "window_start": now}
        return False
    if (now - entry["window_start"]).total_seconds() > 60:
        entry["count"] = 1
        entry["window_start"] = now
        return False
    entry["count"] += 1
    return entry["count"] > _RATE_PER_MINUTE


def _verify_sendblue_signature(raw_body: bytes, signature_header: str) -> bool:
    """Verify Sendblue webhook signature using HMAC-SHA256."""
    try:
        secret = os.getenv("SENDBLUE_WEBHOOK_SECRET", "").encode()
        expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header)
    except Exception:
        return False


def _send_reply(to_number: str, message: str) -> Response:
    """Send message via Blooio and return a plain 200 JSON response."""
    send_reply(to_number, message)
    return Response(content='{"ok":true}', media_type="application/json")


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
    r"\b(reschedule|tomorrow|later|move|shift|delay|another time|push|soon|instead|different time|change it)\b"
    r"|\b(\d{1,2}:\d{2})\b"          # time like "3:30"
    r"|\b(\d{1,2}\s*(am|pm))\b"      # time like "3pm"
    r"|can\s+we\s+do\s+\d",          # "can we do 8" / "can we do 8:30"
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
# Background inbound processor
# ---------------------------------------------------------------------------

async def _resolve_message_intent(messages: list[str]) -> str:
    """Collapse multiple rapid-fire messages into one resolved intent using Gemini."""
    import google.generativeai as genai
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    combined = "\n".join(f"- {m}" for m in messages)
    prompt = (
        f"A user sent these messages in rapid succession:\n{combined}\n\n"
        f"Some may be corrections to earlier ones, or they may be adding context. "
        f"Return a single plain-text string that captures the user's true combined intent. "
        f"No explanation. Just the resolved message."
    )
    try:
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        return model.generate_content(prompt).text.strip()
    except Exception:
        logger.exception("[sms] intent resolution failed, using last message")
        return messages[-1]


async def _gemini_classify_notification_reply(message_body: str, activity: str) -> str | None:
    """
    Gemini fallback for ambiguous notification replies that regex couldn't classify.
    Returns 'CONFIRMED', 'DECLINED', 'RESCHEDULED', or None (truly unclear).
    """
    import google.generativeai as genai
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    try:
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        prompt = (
            f"The user has a scheduled activity: {activity}.\n"
            f"They replied: \"{message_body}\"\n\n"
            f"Classify their reply as exactly one of:\n"
            f"CONFIRMED: they are doing the activity\n"
            f"DECLINED: they are skipping it\n"
            f"RESCHEDULED: they want to move it to a different time\n"
            f"UNCLEAR: cannot determine intent from this message\n\n"
            f"Reply with exactly one word."
        )
        result = model.generate_content(prompt).text.strip().upper()
        return result if result in ("CONFIRMED", "DECLINED", "RESCHEDULED") else None
    except Exception:
        logger.exception("[sms] Gemini notification classifier failed")
        return None


async def _ask_notification_clarification(user_id: str, activity: str) -> str:
    """Generate a natural in-voice clarification when intent cannot be parsed."""
    import google.generativeai as genai
    from routes.ai import HUMAN_BEHAVIOR_RULES
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    try:
        coach_res = (
            supabase.table("coach_settings")
            .select("generated_system_prompt")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        system_prompt = coach_res.data[0].get("generated_system_prompt") if coach_res.data else ""
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash-lite",
            system_instruction=f"{system_prompt}\n\n{HUMAN_BEHAVIOR_RULES}",
        )
        resp = model.generate_content(
            f"The user replied to a reminder about '{activity}' but you couldn't tell if they're doing it, "
            f"skipping it, or rescheduling. Ask them directly, in your voice, whether they're doing it, "
            f"not doing it, or need to move it. The user must understand they need to give you a clear answer. "
            f"One short sentence. Do not be vague."
        )
        return resp.text.strip()
    except Exception:
        logger.exception("[sms] notification clarification generation failed")
        return "Were you saying you're doing it or skipping it?"


async def _process_inbound(from_number: str, token: str, background_tasks: BackgroundTasks) -> None:
    """All inbound SMS logic runs here in the background so the webhook returns 200 instantly."""
    # Adaptive window: wait 5s, skip if a newer message has arrived
    await asyncio.sleep(5)
    if _pending_token.get(from_number) != token:
        logger.info(f"[sms] adaptive window: newer message pending for {from_number}, skipping")
        return

    messages = _message_buffer.pop(from_number, [])
    _pending_token.pop(from_number, None)

    if not messages:
        return

    message_body = messages[0] if len(messages) == 1 else await _resolve_message_intent(messages)

    try:
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
                        supabase.table("users").update({
                            "sms_consent_given_at": datetime.now(timezone.utc).isoformat(),
                            "sms_consent_method": "stk_token",
                        }).eq("id", uid).execute()
                        ctia_welcome = (
                            "stackd: You're now set up for daily AI coaching texts. ~20-30 msgs/month. "
                            "Msg&Data rates may apply. Reply STOP to cancel anytime, HELP for info. "
                            "stackd.app/help"
                        )
                        _save_message(uid, "outbound", ctia_welcome)
                        from routes.mock import send_welcome_message
                        welcome = await send_welcome_message(uid)
                        full_msg = f"{ctia_welcome}\n\n{welcome}"
                        await _typing_delay(full_msg)
                        send_reply(from_number, full_msg)
                        return
            except Exception:
                logger.exception(f"Link token activation failed for token {link_token}")
            send_reply(from_number, "That code wasn't recognized. Try again or visit the app.")
            return

        _normalized = message_body.strip().lower()
        _STOP_WORDS  = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
        _HELP_WORDS  = {"help", "info"}
        _START_WORDS = {"start", "unstop"}  # "yes" excluded — needed during onboarding step 4

        # Check onboarding step before keyword matching so "yes/no" aren't swallowed during step 4
        _onb_check = supabase.table("users").select("onboarding_step").eq("phone", from_number).limit(1).execute()
        _onb_step = _onb_check.data[0].get("onboarding_step") if _onb_check.data else None

        if _normalized in _STOP_WORDS:
            try:
                _stop_res = supabase.table("users").select("id").eq("phone", from_number).execute()
                if _stop_res.data:
                    supabase.table("users").update({"paused": True}).eq("phone", from_number).execute()
                    logger.info(f"User {from_number} opted out via STOP")
            except Exception:
                logger.exception(f"Failed to pause user on STOP from {from_number}")
            send_reply(from_number, "You've been unsubscribed from stackd. No further messages will be sent. Reply START to resubscribe.")
            return

        if _normalized in _HELP_WORDS:
            send_reply(from_number, "stackd AI coaching app. ~20-30 msgs/month. Msg&Data rates may apply. Reply STOP to cancel anytime. Support: help@stackd.app stackd.app/help")
            return

        if _normalized in _START_WORDS and _onb_step not in (3, 4):
            try:
                _start_res = supabase.table("users").select("id").eq("phone", from_number).execute()
                if _start_res.data:
                    _uid = _start_res.data[0]["id"]
                    supabase.table("users").update({"paused": False}).eq("id", _uid).execute()
                    logger.info(f"User {from_number} resubscribed via START")
                    _start_welcome = (
                        "Welcome back to stackd! You're resubscribed for daily coaching texts. "
                        "~20-30 msgs/month. Reply STOP anytime to cancel."
                    )
                    _save_message(_uid, "outbound", _start_welcome)
                    send_reply(from_number, _start_welcome)
                    return
            except Exception:
                logger.exception(f"Failed to unpause user {from_number} on START")
            send_reply(from_number, "Welcome back to stackd! You're resubscribed for daily coaching texts. Reply STOP anytime to cancel.")
            return

        if _is_rate_limited(from_number):
            logger.warning(f"Rate limit hit for {from_number} — dropping message")
            return

        user_res = (
            supabase.table("users")
            .select("*, schedule(*), coach_settings(*)")
            .eq("phone", from_number)
            .execute()
        )
        user_data = user_res.data[0] if user_res.data else None

        if user_data is None or user_data.get("onboarding_step", 5) < 5:
            onboarding_reply = await handle_onboarding(
                from_number, message_body, background_tasks, supabase, user_data
            )
            if onboarding_reply is not None:
                await _typing_delay(onboarding_reply)
                send_reply(from_number, onboarding_reply)
                return
            if user_data is None:
                user_res2 = supabase.table("users").select("*, schedule(*), coach_settings(*)").eq("phone", from_number).execute()
                user_data = user_res2.data[0] if user_res2.data else None
            if user_data is None:
                send_reply(from_number, "Something went wrong. Try again.")
                return

        user_id = user_data["id"]
        user_timezone = (user_data.get("schedule") or {}).get("timezone", "America/New_York")

        _save_message(user_id, "inbound", message_body)

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
                notif_row = pending_res.data[0]
                notif_state = parse_notification_reply(message_body)

                if notif_state is None:
                    notif_state = await _gemini_classify_notification_reply(message_body, notif_row["activity"])

                if notif_state is not None:
                    response_text = await handle_notification_reply(
                        user_id, user_data, notif_row, notif_state, message_body
                    )
                    _save_message(user_id, "outbound", response_text)
                    logger.info(f"Notification reply {notif_state} for {notif_row['activity']} from {from_number}: {response_text[:80]}")
                    await _typing_delay(response_text)
                    send_reply(from_number, response_text)
                    return
                else:
                    # Truly unclear — coach asks for clarification in their voice
                    clarification = await _ask_notification_clarification(user_id, notif_row["activity"])
                    _save_message(user_id, "outbound", clarification)
                    logger.info(f"Notification clarification sent for {notif_row['activity']} to {from_number}: {clarification[:80]}")
                    await _typing_delay(clarification)
                    send_reply(from_number, clarification)
                    return
        except Exception:
            logger.exception(f"Notification reply intercept failed for user {user_id} — falling through to Gemini")

        try:
            response_text = await process_inbound_sms(user_id, message_body, user_timezone=user_timezone)
        except Exception:
            logger.exception(f"process_inbound_sms failed for user {user_id}")
            response_text = (
                f"Hey {user_data.get('name', 'there')}! Got your message, "
                "I'm having a quick moment but I'll be right back with you. 💪"
            )

        _save_message(user_id, "outbound", response_text)
        logger.info(f"Outbound SMS to {from_number}: {response_text[:80]}")
        await _typing_delay(response_text)
        send_reply(from_number, response_text)

    except Exception:
        logger.exception(f"_process_inbound failed for {from_number}")


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@router.post("/incoming")
async def sms_incoming(request: Request, background_tasks: BackgroundTasks):
    """
    Blooio webhook — validates signature, parses payload, returns 200 immediately.
    All processing delegated to _process_inbound() via BackgroundTasks.
    """
    raw_body = await request.body()
    signature_header = request.headers.get("X-Sendblue-Signature", "")

    # Validate Sendblue signature in production
    if os.getenv("ENV", "development") != "development" and os.getenv("SENDBLUE_WEBHOOK_SECRET"):
        if not _verify_sendblue_signature(raw_body, signature_header):
            logger.warning("Invalid Sendblue signature — rejecting request")
            raise HTTPException(status_code=403, detail="Invalid Sendblue signature")

    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Sendblue sends all webhook types to the same URL — only process inbound
    # status field is "RECEIVED" for inbound messages
    status = payload.get("status", "")
    if status != "RECEIVED":
        return Response(content='{"ok":true}', media_type="application/json")

    from_number = (payload.get("from_number") or "").strip()
    message_body = (payload.get("content") or "").strip()

    logger.info(f"Inbound message from {from_number}: {message_body[:80]}")

    # Buffer message and reset adaptive window token
    import uuid as _uuid
    token = str(_uuid.uuid4())
    _pending_token[from_number] = token
    _message_buffer.setdefault(from_number, []).append(message_body)

    # Return 200 immediately, process after window closes
    background_tasks.add_task(_process_inbound, from_number, token, background_tasks)
    return Response(content='{"ok":true}', media_type="application/json")


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
    Generate a 6-digit OTP, store it with a 5-minute TTL, and send it via Blooio.
    Called from quiz step 6 before sign-in. No user_id needed yet.
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
        send_reply(phone, body)
        logger.info(f"Sent OTP to {phone}")
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
