import logging
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

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

class UpdateSchedulePayload(BaseModel):
    user_id: str
    checkin_time: str | None = None
    timezone: str | None = None
    motivation_enabled: bool | None = None
    motivation_frequency: str | None = None


class PauseCoachPayload(BaseModel):
    user_id: str
    paused: bool = True


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@router.put("/update-schedule")
async def update_schedule(payload: UpdateSchedulePayload):
    """
    Update user's schedule and motivation settings.

    Args:
        payload: UpdateSchedulePayload with user_id and optional schedule fields
    """
    try:
        # Build update dictionary with only non-None fields
        update_data = {}
        if payload.checkin_time is not None:
            update_data["checkin_time"] = payload.checkin_time
        if payload.timezone is not None:
            update_data["timezone"] = payload.timezone
        if payload.motivation_enabled is not None:
            update_data["motivation_enabled"] = payload.motivation_enabled
        if payload.motivation_frequency is not None:
            update_data["motivation_frequency"] = payload.motivation_frequency

        if not update_data:
            raise HTTPException(
                status_code=400,
                detail="No fields to update"
            )

        result = supabase.table("schedule").update(update_data).eq(
            "user_id", payload.user_id
        ).execute()

        if not result.data:
            raise HTTPException(
                status_code=404,
                detail="Schedule not found for this user"
            )

        logger.info(f"Updated schedule for user {payload.user_id}")

        return {
            "status": "success",
            "message": "Schedule updated successfully",
            "data": result.data[0] if result.data else None,
        }

    except Exception as e:
        logger.error(f"Error updating schedule: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update schedule: {str(e)}"
        )


@router.put("/pause-coach")
async def pause_coach(payload: PauseCoachPayload):
    """
    Pause or resume coach for a user.

    Args:
        payload: PauseCoachPayload with user_id and paused boolean
    """
    try:
        # Update user's paused status
        result = supabase.table("users").update(
            {
                "paused": payload.paused,
            }
        ).eq("id", payload.user_id).execute()

        if not result.data:
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )

        action = "paused" if payload.paused else "resumed"
        logger.info(f"Coach {action} for user {payload.user_id}")

        return {
            "status": "success",
            "message": f"Coach {action} successfully",
            "data": result.data[0] if result.data else None,
        }

    except Exception as e:
        logger.error(f"Error pausing/resuming coach: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to pause/resume coach: {str(e)}"
        )
