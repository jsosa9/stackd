"""
Mock messaging — testing without Twilio.

All functions write to the messages table and print to console.
Nothing here calls Twilio. Safe to run while API verification is pending.

Endpoints (mount at /mock):
    POST /mock/test-message/{user_id}   — sends a fixed test string
    POST /mock/welcome/{user_id}        — welcome message using coach name + goals
    POST /mock/daily-sim/{user_id}      — simulates today's check-ins via AI
"""

import logging
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
router = APIRouter()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ---------------------------------------------------------------------------
# Core mock send — reuse everywhere instead of calling Twilio
# ---------------------------------------------------------------------------

def send_message(user_id: str, message: str) -> None:
    """
    Persist an outbound message to the DB and print it to the console.
    Drop-in replacement for scheduler.send_sms() that skips Twilio entirely.
    When Twilio is live, callers can swap this for the real send_sms + log_message pair.
    """
    try:
        supabase.table("messages").insert({
            "user_id": user_id,
            "direction": "outbound",
            "body": message,
        }).execute()
    except Exception:
        logger.exception(f"[MOCK] Failed to save message for user {user_id}")

    print(f"[SMS MOCK] -> {user_id}: {message}")
    logger.info(f"[SMS MOCK] -> {user_id}: {message[:120]}")


# ---------------------------------------------------------------------------
# Welcome message
# ---------------------------------------------------------------------------

async def send_welcome_message(user_id: str) -> str:
    """
    Generate a personalized welcome SMS using the coach's AI personality,
    then mock-send it. Falls back to a static template if AI fails.
    """
    from routes.ai import generate_welcome_text

    try:
        msg = await generate_welcome_text(user_id)
    except Exception as e:
        logger.error(f"AI welcome failed for {user_id}, using fallback: {e}")
        coach_res = (
            supabase.table("coach_settings")
            .select("coach_name")
            .eq("user_id", user_id)
            .execute()
        )
        coach_name = coach_res.data[0]["coach_name"] if coach_res.data else "Coach"
        goals_res = (
            supabase.table("goals")
            .select("activity")
            .eq("user_id", user_id)
            .execute()
        )
        activities = [g["activity"] for g in (goals_res.data or [])]
        if activities:
            listed = ", ".join(activities[:3])
            if len(activities) > 3:
                listed += f" and {len(activities) - 3} more"
            msg = (
                f"Hey! 👋 {coach_name} here — your accountability coach. "
                f"I see you're working on: {listed}. "
                f"I'll be checking in with you every day. Let's get it! 💪"
            )
        else:
            msg = (
                f"Hey! 👋 {coach_name} here — your accountability coach. "
                f"I'll be checking in with you every day. Reply any time. Let's go! 💪"
            )

    send_message(user_id, msg)
    return msg


# ---------------------------------------------------------------------------
# Daily simulation
# ---------------------------------------------------------------------------

def run_daily_simulation(user_id: str) -> list[str]:
    """
    Match today's goals and mock-send an AI-generated check-in for each one.
    Reuses generate_checkin_text from ai.py — same logic the real scheduler uses,
    just without Twilio at the end.
    """
    from routes.ai import generate_checkin_text
    from routes.scheduler import run_async

    today = DAY_NAMES[datetime.now().weekday()]

    goals_res = (
        supabase.table("goals")
        .select("activity, days")
        .eq("user_id", user_id)
        .execute()
    )

    todays_goals = [
        g["activity"]
        for g in (goals_res.data or [])
        if today in (g.get("days") or [])
    ]

    if not todays_goals:
        logger.info(f"[MOCK] No goals for user {user_id} on {today}")
        return []

    sent: list[str] = []
    for goal in todays_goals:
        try:
            text = run_async(generate_checkin_text(user_id, goal))
            send_message(user_id, text)
            sent.append(text)
        except Exception as e:
            logger.error(f"[MOCK] Failed check-in for goal '{goal}': {e}")

    return sent


# ---------------------------------------------------------------------------
# HTTP endpoints — testable via /docs or curl
# ---------------------------------------------------------------------------

@router.post("/test-message/{user_id}")
async def test_message(user_id: str):
    """
    Quickest smoke test: sends a fixed string to confirm the DB write and
    console log pipeline works end-to-end.
    """
    msg = "Test message from your coach 👋"
    send_message(user_id, msg)
    return {"sent": msg}


@router.post("/welcome/{user_id}")
async def welcome(user_id: str):
    """Send a welcome message using the user's real coach name and goal list."""
    msg = await send_welcome_message(user_id)
    return {"sent": msg}


@router.post("/daily-sim/{user_id}")
async def daily_sim(user_id: str):
    """
    Run today's check-in simulation for a specific user without Twilio.
    Matches goals against today's day of week and generates an AI message per goal.
    """
    sent = run_daily_simulation(user_id)
    if not sent:
        today = DAY_NAMES[datetime.now().weekday()]
        return {"sent": [], "note": f"No goals matched {today}"}
    return {"sent": sent, "count": len(sent)}


class SeedRequest(BaseModel):
    name: str = "Jordan"
    age: str = "27"
    occupation: str = "working"
    coach_name: str = "Alex"
    personality_preset: str = "hype"
    coach_talk_style: list = ["Motivational", "Energetic"]
    coach_emoji_usage: str = "Lots"
    coach_message_length: str = "Balanced"
    coach_miss_behavior: str = "Tough love"
    coach_intensity: int = 5
    obstacles: list = ["Inconsistency", "Busy schedule", "No accountability"]
    success_vision: str = "Running a 5K without stopping and finally building a consistent gym habit"
    checkin_time: str = "7:00 AM"
    timezone: str = "America/Los_Angeles"


@router.post("/seed/{user_id}")
async def seed_db(user_id: str, req: SeedRequest = SeedRequest()):
    """
    Write test data to Supabase for a user and generate their coach personality.
    Called from the dev page when a user ID is set — lets you test the AI chat
    without completing the full quiz.
    """
    from routes.ai import build_coach_personality

    try:
        # Fetch email from auth so we satisfy the NOT NULL constraint
        try:
            auth_user = supabase.auth.admin.get_user_by_id(user_id)
            email = auth_user.user.email if auth_user.user else f"{user_id}@dev.local"
        except Exception:
            email = f"{user_id}@dev.local"

        # Upsert user profile (only columns that exist in the users table)
        supabase.table("users").upsert({
            "id": user_id,
            "email": email,
            "name": req.name,
            "age": int(req.age) if req.age.isdigit() else None,
            "occupation": req.occupation,
        }, on_conflict="id").execute()

        # Store obstacles + success_vision in user_context
        context_entries = []
        for obstacle in req.obstacles:
            context_entries.append({
                "user_id": user_id,
                "type": "obstacle",
                "description": obstacle,
                "expires_at": None,
            })
        if req.success_vision:
            context_entries.append({
                "user_id": user_id,
                "type": "success_vision",
                "description": req.success_vision,
                "expires_at": None,
            })
        if context_entries:
            supabase.table("user_context").delete().eq("user_id", user_id).in_("type", ["obstacle", "success_vision"]).execute()
            supabase.table("user_context").insert(context_entries).execute()

        # Upsert coach settings
        supabase.table("coach_settings").upsert({
            "user_id": user_id,
            "coach_name": req.coach_name,
            "personality_preset": req.personality_preset,
            "coach_personality": req.personality_preset,
            "coach_talk_style": req.coach_talk_style,
            "coach_emoji_usage": req.coach_emoji_usage,
            "coach_message_length": req.coach_message_length,
            "coach_miss_behavior": req.coach_miss_behavior,
            "coach_intensity": req.coach_intensity,
        }, on_conflict="user_id").execute()

        # Upsert schedule
        supabase.table("schedule").upsert({
            "user_id": user_id,
            "checkin_time": req.checkin_time,
            "timezone": req.timezone,
            "motivation_enabled": True,
            "motivation_frequency": "Once a day",
            "motivation_styles": ["Hype & pump-up 🎉", "Tough love 💪"],
        }, on_conflict="user_id").execute()

        # Delete existing goals then insert fresh ones
        supabase.table("goals").delete().eq("user_id", user_id).execute()
        goals = [
            {"user_id": user_id, "activity": "Running",             "category": "Fitness & Sports", "days": ["monday", "wednesday", "friday"]},
            {"user_id": user_id, "activity": "Gym / Weightlifting", "category": "Fitness & Sports", "days": ["tuesday", "thursday", "saturday"]},
            {"user_id": user_id, "activity": "Yoga",                "category": "Fitness & Sports", "days": ["monday", "wednesday", "sunday"]},
            {"user_id": user_id, "activity": "Reading",             "category": "Education",        "days": ["monday", "tuesday", "wednesday", "thursday", "friday"]},
            {"user_id": user_id, "activity": "Meditation",          "category": "Mind",             "days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]},
        ]
        supabase.table("goals").insert(goals).execute()

        # Generate coach personality
        await build_coach_personality(user_id)

        return {"status": "ok", "message": f"Seeded DB and generated personality for {user_id}"}

    except Exception as e:
        logger.exception(f"[MOCK SEED] Failed for {user_id}")
        raise HTTPException(status_code=500, detail=str(e))


class ChatRequest(BaseModel):
    message: str


@router.post("/chat/{user_id}")
async def mock_chat(user_id: str, req: ChatRequest):
    """
    Simulate a full SMS conversation turn without Twilio.
    Delegates to the shared process_inbound_sms pipeline so local testing
    is 100% representative of the live SMS webhook experience.
    """
    from services.message_router import process_inbound_sms

    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    try:
        # Fetch user row so process_inbound_sms has the same user_data shape as the SMS webhook
        user_res = (
            supabase.table("users")
            .select("*, schedule(*), coach_settings(*)")
            .eq("id", user_id)
            .execute()
        )
        if not user_res.data:
            raise HTTPException(status_code=404, detail="User not found")

        user_data = user_res.data[0]
        user_timezone = (user_data.get("schedule") or {}).get("timezone", "America/New_York")

        # Save inbound message
        try:
            supabase.table("messages").insert({
                "user_id": user_id,
                "direction": "inbound",
                "body": req.message,
            }).execute()
        except Exception as e:
            logger.warning(f"[MOCK CHAT] Failed to save inbound message: {e}")

        # Run through the same pipeline as the live SMS webhook
        reply = await process_inbound_sms(user_id, req.message, user_data, user_timezone)

        # Save outbound message
        send_message(user_id, reply)

        return {"reply": reply}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[MOCK CHAT] Unhandled error for {user_id}")
        return {"reply": f"Server error: {str(e)}", "error": str(e)}
