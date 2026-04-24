import logging
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv
from typing import Optional, List, Dict, Any

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)


# ---------------------------------------------------------------------------
# Request model — mirrors the QuizData shape from the frontend
# ---------------------------------------------------------------------------

class OnboardPayload(BaseModel):
    user_id: str
    email: str

    # Step 1a/1b — activities + schedules
    selectedActivities: Optional[Dict[str, List[str]]] = None
    activitySchedules: Optional[Dict[str, Any]] = None

    # Step 2 — coach
    coachName: Optional[str] = "Alex"
    coachAvatar: Optional[str] = "🦁"
    coachSetupMode: Optional[str] = "quick"
    coachPersonality: Optional[str] = None
    coachTalkStyle: Optional[List[str]] = None
    coachEmojiUsage: Optional[str] = None
    coachMessageLength: Optional[str] = None
    coachMissBehavior: Optional[str] = None
    coachOpenerStyle: Optional[str] = None
    coachIntensity: Optional[int] = 3
    customCoachSoundsLike: Optional[str] = None
    customCoachPersonalityDesc: Optional[str] = None
    customCoachCelebrationStyle: Optional[str] = None
    customCoachMissedDayResponse: Optional[str] = None
    customCoachFavoritePhrase: Optional[str] = None
    customCoachAvoidPhrases: Optional[str] = None
    customCoachMotivationStyle: Optional[str] = None
    customCoachTone: Optional[str] = None
    customCoachSpecialRules: Optional[str] = None

    # Step 3 — about you
    name: Optional[str] = None
    age: Optional[str] = None
    occupation: Optional[str] = None
    obstacles: Optional[List[str]] = None
    experience: Optional[str] = None
    successVision: Optional[str] = None

    # Step 4 — boundaries + check-in time
    avoidTopics: Optional[List[str]] = None
    restDayBehavior: Optional[str] = None
    directnessLevel: Optional[int] = 3
    multiTextAllowed: Optional[bool] = True
    checkinHour: Optional[int] = 8
    checkinMinute: Optional[int] = 0
    checkinAmPm: Optional[str] = "AM"
    timezone: Optional[str] = "America/New_York"

    # Step 5 — motivation
    motivationEnabled: Optional[bool] = False
    motivationFrequency: Optional[str] = "Once a day"
    motivationWindowStart: Optional[str] = "9"
    motivationWindowEnd: Optional[str] = "8"
    motivationWindowStartAmPm: Optional[str] = "AM"
    motivationWindowEndAmPm: Optional[str] = "PM"
    motivationStyles: Optional[List[str]] = None
    motivationPullFrom: Optional[str] = None
    morningKickstartEnabled: Optional[bool] = False
    morningKickstartTime: Optional[str] = None
    morningKickstartAmPm: Optional[str] = None
    eveningReflectionEnabled: Optional[bool] = False
    eveningReflectionTime: Optional[str] = None
    eveningReflectionAmPm: Optional[str] = None

    # Step 6 — phone
    phone: Optional[str] = None
    phoneVerified: Optional[bool] = False


# ---------------------------------------------------------------------------
# Onboard endpoint
# ---------------------------------------------------------------------------

@router.post("/onboard")
async def onboard_user(payload: OnboardPayload):
    """
    Called after the user completes the quiz and signs in with Google.
    Saves all quiz data to the appropriate Supabase tables, then triggers
    Claude Haiku to generate the coach's personalized system prompt.
    """
    user_id = payload.user_id
    logger.info(f"Onboarding user {user_id} ({payload.email})")

    # 1. Upsert user profile
    supabase.table("users").upsert({
        "id": user_id,
        "email": payload.email,
        "name": payload.name,
        "phone": payload.phone,
        "age": int(payload.age) if payload.age and payload.age.isdigit() else None,
        "occupation": payload.occupation,
        "obstacles": payload.obstacles or [],
        "experience": payload.experience,
        "success_vision": payload.successVision,
        "phone_verified": payload.phoneVerified or False,
    }).execute()

    # 2. Insert goals — one row per activity across all categories
    if payload.selectedActivities:
        for category, activities in payload.selectedActivities.items():
            for activity in activities:
                # Get the schedule for this activity (days with frequencies)
                activity_sched = (payload.activitySchedules or {}).get(activity, {})
                days_with_freq = activity_sched.get("days", {})
                # days is a list of day abbreviations where freq > 0
                days_list = list(days_with_freq.keys())

                supabase.table("goals").insert({
                    "user_id": user_id,
                    "activity": activity,
                    "category": category,
                    "days": days_list,
                    "schedule": days_with_freq,  # { "Mon": 1, "Wed": 2 }
                }).execute()

    # 3. Upsert coach settings
    supabase.table("coach_settings").upsert({
        "user_id": user_id,
        "coach_name": payload.coachName,
        "coach_avatar": payload.coachAvatar,
        "coach_setup_mode": payload.coachSetupMode,
        "coach_personality": payload.coachPersonality,
        "coach_talk_style": payload.coachTalkStyle or [],
        "coach_emoji_usage": payload.coachEmojiUsage,
        "coach_message_length": payload.coachMessageLength,
        "coach_miss_behavior": payload.coachMissBehavior,
        "coach_opener_style": payload.coachOpenerStyle,
        "coach_intensity": payload.coachIntensity,
        "custom_coach_sounds_like": payload.customCoachSoundsLike,
        "custom_coach_personality_desc": payload.customCoachPersonalityDesc,
        "custom_coach_celebration_style": payload.customCoachCelebrationStyle,
        "custom_coach_missed_day_response": payload.customCoachMissedDayResponse,
        "custom_coach_favorite_phrase": payload.customCoachFavoritePhrase,
        "custom_coach_avoid_phrases": payload.customCoachAvoidPhrases,
        "custom_coach_motivation_style": payload.customCoachMotivationStyle,
        "custom_coach_tone": payload.customCoachTone,
        "custom_coach_special_rules": payload.customCoachSpecialRules,
    }).execute()

    # 4. Upsert schedule + motivation prefs
    supabase.table("schedule").upsert({
        "user_id": user_id,
        "checkin_hour": payload.checkinHour,
        "checkin_minute": payload.checkinMinute,
        "checkin_ampm": payload.checkinAmPm,
        "timezone": payload.timezone,
        "rest_day_behavior": payload.restDayBehavior,
        "directness_level": payload.directnessLevel,
        "multi_text_allowed": payload.multiTextAllowed,
        "avoid_topics": payload.avoidTopics or [],
        "motivation_enabled": payload.motivationEnabled,
        "motivation_frequency": payload.motivationFrequency,
        "motivation_window_start": payload.motivationWindowStart,
        "motivation_window_end": payload.motivationWindowEnd,
        "motivation_window_start_ampm": payload.motivationWindowStartAmPm,
        "motivation_window_end_ampm": payload.motivationWindowEndAmPm,
        "motivation_styles": payload.motivationStyles or [],
        "motivation_pull_from": payload.motivationPullFrom,
        "morning_kickstart_enabled": payload.morningKickstartEnabled,
        "morning_kickstart_time": payload.morningKickstartTime,
        "morning_kickstart_ampm": payload.morningKickstartAmPm,
        "evening_reflection_enabled": payload.eveningReflectionEnabled,
        "evening_reflection_time": payload.eveningReflectionTime,
        "evening_reflection_ampm": payload.eveningReflectionAmPm,
    }).execute()

    # 5. Trigger Claude Haiku to generate the personalized coach system prompt
    # Run in the background so onboard returns quickly — Haiku call takes ~2-3s
    try:
        from routes.ai import build_coach_personality
        await build_coach_personality(user_id)
        logger.info(f"Coach personality built for {user_id}")
    except Exception:
        # Non-fatal — the scheduler will retry on first text send
        logger.exception(f"Failed to build coach personality for {user_id} during onboard")

    return {"status": "ok", "user_id": user_id}


@router.get("/{user_id}/profile")
async def get_user_profile(user_id: str):
    """Fetch a user's full profile including coach settings and schedule."""
    user_res = supabase.table("users").select("*").eq("id", user_id).execute()
    if not user_res.data:
        raise HTTPException(status_code=404, detail="User not found")

    coach_res = supabase.table("coach_settings").select("*").eq("user_id", user_id).execute()
    sched_res = supabase.table("schedule").select("*").eq("user_id", user_id).execute()
    goals_res = supabase.table("goals").select("*").eq("user_id", user_id).execute()

    return {
        "user": user_res.data[0],
        "coach": coach_res.data[0] if coach_res.data else None,
        "schedule": sched_res.data[0] if sched_res.data else None,
        "goals": goals_res.data or [],
    }
