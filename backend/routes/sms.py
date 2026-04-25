import logging
import os
import secrets
import asyncio
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
from twilio.rest import Client as TwilioClient
from supabase import create_client
from dotenv import load_dotenv

from routes.ai import (
    generate_checkin_text,
    extract_intents,
    process_intents,
    get_active_context,
    get_message_history,
    generate_gemini_response,
)

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


async def _generate_reply(user_id: str, inbound_text: str) -> str:
    """
    Build a reply using the user's generated system prompt and the last 10 messages
    as conversation history, then call Gemini Flash via generate_checkin_text.
    We re-use generate_checkin_text with the inbound message as the "goal" context
    because it already handles history + system prompt correctly.
    """
    import google.generativeai as genai

    # Fetch the saved system prompt
    coach_res = (
        supabase.table("coach_settings")
        .select("generated_system_prompt")
        .eq("user_id", user_id)
        .execute()
    )
    if not coach_res.data or not coach_res.data[0].get("generated_system_prompt"):
        # Fallback: build it on the fly (should not normally happen)
        from routes.ai import build_coach_personality
        system_prompt = await build_coach_personality(user_id)
    else:
        system_prompt = coach_res.data[0]["generated_system_prompt"]

    # Fetch last 10 messages (oldest first for chronological context)
    msgs_res = (
        supabase.table("messages")
        .select("direction, body")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    history = list(reversed(msgs_res.data or []))

    # Build Gemini history — inbound = "user", outbound = "model"
    gemini_history = [
        {
            "role": "user" if m["direction"] == "inbound" else "model",
            "parts": [m["body"]],
        }
        for m in history
    ]

    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=system_prompt,
    )
    chat = model.start_chat(history=gemini_history)
    response = chat.send_message(inbound_text)
    return response.text.strip()


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@router.post("/incoming")
async def sms_incoming(request: Request):
    """
    Twilio webhook: called every time a user replies to a text.
    
    NEW FLOW (with intent detection):
    1. Validate request came from Twilio (skipped in development)
    2. Parse From number and message body
    3. Look up user by phone, including coach_settings and schedule
    4. Save inbound message to messages table
    5. Launch intent extraction as a background task (doesn't block response)
    6. Fetch generated_system_prompt, active context, and message history
    7. Call Gemini Flash 1.5 with full context to generate response
    8. Save outbound response to messages table
    9. Send response via Twilio
    10. Wait for intent extraction to complete and process results
    11. Return 200 to Twilio
    
    Key: Intent extraction runs concurrently with response generation for zero latency impact.
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

    # Look up user by phone number with schedule and coach settings
    user_res = (
        supabase.table("users")
        .select("*, schedule(*), coach_settings(*)")
        .eq("phone", from_number)
        .execute()
    )

    if not user_res.data:
        logger.info(f"Unknown number {from_number} — sending registration prompt")
        return _twiml_reply(
            "Hey! 👋 Looks like this number isn't linked to a stackd account yet. "
            "Head to our app to get started and set up your AI coach!"
        )

    user_data = user_res.data[0]
    user_id = user_data["id"]
    user_timezone = user_data.get("schedule", {}).get("timezone", "America/New_York")

    # Save inbound message
    _save_message(user_id, "inbound", message_body)

    # Launch intent extraction as a concurrent task (don't await yet)
    intents_task = asyncio.create_task(
        extract_intents(user_id, message_body, user_timezone)
    )

    # Get active context and message history while intent extraction runs
    try:
        active_context, message_history = await asyncio.gather(
            get_active_context(user_id),
            get_message_history(user_id, limit=20)
        )
    except Exception as e:
        logger.warning(f"Failed to fetch context/history for user {user_id}: {str(e)}")
        active_context = ""
        message_history = []

    # Fetch generated system prompt
    coach_res = (
        supabase.table("coach_settings")
        .select("generated_system_prompt")
        .eq("user_id", user_id)
        .execute()
    )
    
    if not coach_res.data or not coach_res.data[0].get("generated_system_prompt"):
        # Fallback: build it on the fly (should be rare)
        from routes.ai import build_coach_personality
        system_prompt = await build_coach_personality(user_id)
    else:
        system_prompt = coach_res.data[0]["generated_system_prompt"]

    # Inject active context into system prompt
    from routes.ai import HUMAN_BEHAVIOR_RULES
    if active_context:
        system_prompt = f"{system_prompt}\n\n{active_context}\n\n{HUMAN_BEHAVIOR_RULES}"

    # Generate response using Gemini with full context awareness
    try:
        response_text = await generate_gemini_response(
            system_prompt=system_prompt,
            message_history=message_history,
            new_message=message_body
        )
    except Exception as e:
        logger.exception(f"Failed to generate reply for user {user_id}")
        response_text = (
            f"Hey {user_data.get('name', 'there')}! Got your message — "
            "I'm having a quick moment but I'll be right back with you. 💪"
        )

    # Save outbound message
    _save_message(user_id, "outbound", response_text)

    logger.info(f"Outbound SMS to {from_number}: {response_text[:80]}")

    # Prepare TwiML response to send back to Twilio immediately
    twiml_response = _twiml_reply(response_text)

    # Now wait for intent extraction to complete and process results
    try:
        intents = await intents_task
        if intents.get("has_actionable_content"):
            await process_intents(user_id, intents, user_timezone)
            logger.info(f"Processed intents for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to process intents for user {user_id}: {str(e)}", exc_info=True)
        # Don't crash the SMS response — intents are background processing

    return twiml_response


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
