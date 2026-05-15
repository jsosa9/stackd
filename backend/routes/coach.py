import logging
import os
import random  # noqa: F401 — kept for other usages
from pathlib import Path
from logging.handlers import RotatingFileHandler
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

log_dir = Path(__file__).parent.parent.parent / "logs"
log_dir.mkdir(exist_ok=True)

coach_logger = logging.getLogger("coach")
coach_logger.setLevel(logging.DEBUG)
coach_handler = RotatingFileHandler(
    log_dir / "coach.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
coach_handler.setFormatter(formatter)
coach_logger.addHandler(coach_handler)

router = APIRouter()

# ---------------------------------------------------------------------------
# Client setup
# ---------------------------------------------------------------------------

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class InsightPayload(BaseModel):
    user_id: str
    insight: str


class UpdateCoachPayload(BaseModel):
    user_id: str
    name: str | None = None
    personality: str | None = None
    sounds_like: str | None = None
    custom_build: dict | None = None


class UpdateSchedulePayload(BaseModel):
    user_id: str
    checkin_time: str | None = None
    timezone: str | None = None
    motivation_enabled: bool | None = None
    motivation_frequency: str | None = None
    motivation_styles: list | None = None


class PauseCoachPayload(BaseModel):
    user_id: str
    paused: bool


# ---------------------------------------------------------------------------
# Background helper
# ---------------------------------------------------------------------------

async def trigger_personality_generation(user_id: str):
    try:
        from routes.ai import build_coach_personality

        coach_logger.info(f"Starting personality generation for user {user_id}")
        await build_coach_personality(user_id)
        coach_logger.info(f"Personality generation completed for user {user_id}")
    except Exception as e:
        coach_logger.error(
            f"Personality generation failed for user {user_id}: {str(e)}", exc_info=True
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.put("/update-coach")
async def update_coach(payload: UpdateCoachPayload, background_tasks: BackgroundTasks):
    coach_logger.info(f"Updating coach settings for user {payload.user_id}")
    try:
        existing = supabase.table("coach_settings").select("version").eq("user_id", payload.user_id).execute()
        current_version = existing.data[0].get("version", 0) if existing.data else 0
        from routes.ai import generate_personality_id
        new_personality_id = generate_personality_id()

        supabase.table("coach_settings").upsert(
            {
                "user_id": payload.user_id,
                "coach_name": payload.name,
                "personality_preset": payload.personality,
                "sounds_like": payload.sounds_like,
                "custom_build": payload.custom_build,
                "personality_id": new_personality_id,
                "version": current_version + 1,
                "is_active": True,
            },
            on_conflict="user_id",
        ).execute()
        coach_logger.info(f"Coach settings upserted for user {payload.user_id}")

        background_tasks.add_task(trigger_personality_generation, payload.user_id)
        coach_logger.info(f"Personality generation queued for user {payload.user_id}")

        return {"success": True}
    except Exception as e:
        coach_logger.error(
            f"Failed to update coach settings for user {payload.user_id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to update coach settings: {str(e)}")


@router.put("/update-schedule")
async def update_schedule(payload: UpdateSchedulePayload):
    coach_logger.info(f"Updating schedule for user {payload.user_id}")
    try:
        supabase.table("schedule").upsert(
            {
                "user_id": payload.user_id,
                "checkin_time": payload.checkin_time,
                "timezone": payload.timezone,
                "motivation_enabled": payload.motivation_enabled,
                "motivation_frequency": payload.motivation_frequency,
                "motivation_styles": payload.motivation_styles,
            },
            on_conflict="user_id",
        ).execute()
        coach_logger.info(f"Schedule upserted for user {payload.user_id}")

        return {"success": True}
    except Exception as e:
        coach_logger.error(
            f"Failed to update schedule for user {payload.user_id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to update schedule: {str(e)}")


@router.put("/pause-coach")
async def pause_coach(payload: PauseCoachPayload):
    coach_logger.info(f"Setting paused={payload.paused} for user {payload.user_id}")
    try:
        supabase.table("users").update({"paused": payload.paused}).eq(
            "id", payload.user_id
        ).execute()
        coach_logger.info(f"Paused state updated for user {payload.user_id}")

        return {"success": True}
    except Exception as e:
        coach_logger.error(
            f"Failed to pause coach for user {payload.user_id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to update paused state: {str(e)}")


@router.post("/insight")
async def save_insight(payload: InsightPayload):
    """
    Persist a user-provided insight into user_context (type='coach_insight').
    Called by the frontend when the user explicitly shares a root-cause answer,
    or programmatically by the auto-save logic in message_router.
    """
    if not payload.insight.strip():
        raise HTTPException(status_code=400, detail="insight text is required")

    try:
        from services.coaching_service import save_coach_insight
        await save_coach_insight(payload.user_id, payload.insight)
        coach_logger.info(f"Saved coach insight for user {payload.user_id}: {payload.insight[:60]}")
        return {"saved": True}
    except Exception as e:
        coach_logger.error(f"Failed to save insight for {payload.user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save insight")


@router.get("/personality/{personality_id}")
async def get_personality_by_id(personality_id: str):
    """Fetch a coach personality by its 4-digit ID."""
    try:
        result = (
            supabase.table("coach_settings")
            .select("personality_id, coach_name, sounds_like, personality_preset, custom_build, generated_system_prompt")
            .eq("personality_id", personality_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        coach_logger.error(f"Failed to fetch personality {personality_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch personality")

    if not result.data:
        raise HTTPException(status_code=404, detail="Personality ID not found")

    return result.data[0]
