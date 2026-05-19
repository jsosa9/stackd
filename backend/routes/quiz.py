import logging
import os
import string
from datetime import datetime, timedelta
from pathlib import Path
from logging.handlers import RotatingFileHandler
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv
import pytz

load_dotenv()

# Setup logging with rotating file handler for quiz operations
log_dir = Path(__file__).parent.parent.parent / "logs"
log_dir.mkdir(exist_ok=True)

quiz_logger = logging.getLogger("quiz")
quiz_logger.setLevel(logging.DEBUG)
quiz_handler = RotatingFileHandler(
    log_dir / "quiz.log",
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
)
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
quiz_handler.setFormatter(formatter)
quiz_logger.addHandler(quiz_handler)

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

class QuizPayload(BaseModel):
    user_id: str
    email: str
    goals: dict
    coach: dict
    about: dict
    boundaries: dict
    schedule: dict
    phone: str | None = None


# ---------------------------------------------------------------------------
# Helper function for background personality generation
# ---------------------------------------------------------------------------

async def trigger_personality_generation(user_id: str):
    """
    Background task: generate the coach system prompt then send the welcome SMS.
    For celebrity mode: fetch or create a persona via PersonaManager.
    For custom mode: fall back to build_coach_personality().
    """
    try:
        from routes.ai import build_coach_personality, generate_personality_id, HUMAN_BEHAVIOR_RULES, CONVICTION_RULES
        from routes.mock import send_welcome_message
        from routes.personas import persona_manager

        # Fetch the coach row that was just saved
        coach_res = (
            supabase.table("coach_settings")
            .select("coach_setup_type, sounds_like, coach_name, version")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        coach = coach_res.data[0] if coach_res.data else {}
        quiz_logger.info(f"trigger_personality_generation fetched coach row: {coach}")
        setup_type = (coach.get("coach_setup_type") or "").lower()
        sounds_like = (coach.get("sounds_like") or "").strip()

        if setup_type == "celebrity" and sounds_like:
            quiz_logger.info(f"Celebrity mode for user {user_id} — resolving persona '{sounds_like}'")

            persona = await persona_manager.fetch_persona_by_name(sounds_like)
            if persona is None:
                quiz_logger.info(f"Persona '{sounds_like}' not found — creating")
                persona = await persona_manager.create_persona(sounds_like)

            system_prompt = persona_manager.get_system_prompt(persona)
            full_prompt = f"{system_prompt}\n\n{HUMAN_BEHAVIOR_RULES}\n\n{CONVICTION_RULES}"

            # Update the existing active row — preserves the personality_id shown to the user
            supabase.table("coach_settings").update({
                "generated_system_prompt": full_prompt,
                "coach_name": sounds_like,
            }).eq("user_id", user_id).eq("is_active", True).execute()

            quiz_logger.info(f"Persona system prompt saved for user {user_id} (persona: {sounds_like})")
        else:
            quiz_logger.info(f"Custom mode for user {user_id} — running build_coach_personality")
            await build_coach_personality(user_id)

        quiz_logger.info(f"Personality generation done for user {user_id}")
        await send_welcome_message(user_id)
        quiz_logger.info(f"Welcome message sent for user {user_id}")
    except Exception as e:
        quiz_logger.error(f"Personality generation/welcome failed for user {user_id}: {str(e)}", exc_info=True)
        # Don't raise — this is a background task


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

@router.post("/complete-quiz")
async def complete_quiz(payload: QuizPayload, background_tasks: BackgroundTasks):
    """
    Save complete quiz data to Supabase and trigger coach personality generation.

    This endpoint processes all quiz data in a specific order:
    1. Upsert user profile
    2. Save goals
    3. Save coach settings
    4. Save schedule
    5. Save mindset and boundaries to user_context
    6. Trigger personality generation in background

    Args:
        payload: Complete quiz data including user info, goals, coach settings,
                 about info, boundaries, schedule, and phone number
        background_tasks: FastAPI BackgroundTasks for async personality generation

    Returns:
        Success response with user_id

    Raises:
        HTTPException: If any step fails with 500 status and clear error message
    """
    quiz_logger.info(f"Starting quiz completion for user {payload.user_id}")

    try:
        # Verify user exists in auth.users using service role client
        quiz_logger.info(f"Verifying user authentication for {payload.user_id}")
        try:
            auth_user = supabase.auth.admin.get_user_by_id(payload.user_id)
            if not auth_user or not auth_user.user:
                raise HTTPException(status_code=401, detail='User not authenticated')
            quiz_logger.info(f"User authentication verified for {payload.user_id}")
        except HTTPException:
            raise
        except Exception as e:
            quiz_logger.error(f"Authentication check failed for user {payload.user_id}: {str(e)}", exc_info=True)
            raise HTTPException(status_code=401, detail='User not authenticated')

        # Step 1 — Upsert user profile
        # Save basic user information to the users table
        quiz_logger.info(f"Step 1: Upserting user profile for {payload.user_id}")
        try:
            supabase.table('users').upsert({
                'id': payload.user_id,
                'email': payload.email,
                'phone': payload.phone,
                'name': payload.about.get('name'),
                'age': payload.about.get('age'),
                'occupation': payload.about.get('occupation')
            }, on_conflict='id').execute()
            quiz_logger.info(f"Step 1 completed: User profile upserted")
        except Exception as e:
            quiz_logger.error(f"Step 1 failed: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to save user profile: {str(e)}")

        # Step 2 — Save goals
        # Iterate over goals dictionary and save each activity
        quiz_logger.info(f"Step 2: Saving goals for {payload.user_id}")
        try:
            goals_to_insert = []
            for category_name, activities in payload.goals.items():
                if isinstance(activities, list):
                    for activity_data in activities:
                        if isinstance(activity_data, dict):
                            activity_name = activity_data.get('name', activity_data)
                            activity_schedule = activity_data.get('days', [])
                        else:
                            activity_name = activity_data
                            activity_schedule = []

                        goals_to_insert.append({
                            'user_id': payload.user_id,
                            'activity': activity_name,
                            'category': category_name,
                            'days': activity_schedule
                        })

            if goals_to_insert:
                supabase.table('goals').insert(goals_to_insert).execute()
                quiz_logger.info(f"Step 2 completed: {len(goals_to_insert)} goals saved")
            else:
                quiz_logger.warning(f"Step 2: No goals to save")
        except Exception as e:
            quiz_logger.error(f"Step 2 failed: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to save goals: {str(e)}")

        # Step 3 — Save coach settings
        # No unique constraint on user_id in live DB — deactivate old rows then insert fresh.
        quiz_logger.info(f"Step 3: Saving coach settings for {payload.user_id}")
        from routes.ai import generate_personality_id
        personality_id = generate_personality_id()
        setup_type = payload.coach.get('setup_type') or ''
        sounds_like = payload.coach.get('sounds_like') or ''
        quiz_logger.info(f"Step 3: setup_type={setup_type!r} sounds_like={sounds_like!r}")
        try:
            supabase.table('coach_settings').update({'is_active': False}).eq('user_id', payload.user_id).execute()
            supabase.table('coach_settings').insert({
                'user_id': payload.user_id,
                'coach_name': payload.coach.get('name') or 'Coach',
                'personality_preset': payload.coach.get('personality'),
                'coach_setup_type': setup_type or None,
                'sounds_like': sounds_like or None,
                'custom_build': payload.coach.get('custom_build', {}),
                'personality_id': personality_id,
                'is_active': True,
                'version': 1,
            }).execute()
            quiz_logger.info(f"Step 3 completed: Coach settings saved, personality_id={personality_id}")
        except Exception as e:
            quiz_logger.error(f"Step 3 failed: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to save coach settings: {str(e)}")

        # Step 4 — Save schedule
        # Save check-in time, timezone, and motivation settings
        quiz_logger.info(f"Step 4: Saving schedule for {payload.user_id}")
        try:
            checkin_hour = payload.schedule.get('checkin_hour', 8)
            checkin_minute = str(payload.schedule.get('checkin_minute', 0)).zfill(2)
            checkin_ampm = payload.schedule.get('checkin_ampm', 'AM')

            supabase.table('schedule').upsert({
                'user_id': payload.user_id,
                'checkin_time': f"{checkin_hour}:{checkin_minute} {checkin_ampm}",
                'timezone': payload.schedule.get('timezone'),
                'motivation_enabled': payload.schedule.get('motivation_enabled', False),
                'motivation_frequency': payload.schedule.get('motivation_frequency'),
                'motivation_window_start': payload.schedule.get('motivation_window_start'),
                'motivation_window_end': payload.schedule.get('motivation_window_end'),
                'motivation_styles': payload.schedule.get('motivation_styles', []),
                'motivation_from': payload.schedule.get('motivation_from')
            }).execute()
            quiz_logger.info(f"Step 4 completed: Schedule saved")
        except Exception as e:
            quiz_logger.error(f"Step 4 failed: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to save schedule: {str(e)}")

        # Step 5 — Save mindset and boundaries to user_context
        # Store obstacles, success vision, and boundaries as context entries
        quiz_logger.info(f"Step 5: Saving mindset and boundaries for {payload.user_id}")
        try:
            context_entries = [
                {
                    'user_id': payload.user_id,
                    'type': 'obstacle',
                    'description': payload.about.get('obstacle'),
                    'expires_at': None
                },
                {
                    'user_id': payload.user_id,
                    'type': 'success_vision',
                    'description': payload.about.get('success_vision'),
                    'expires_at': None
                },
                {
                    'user_id': payload.user_id,
                    'type': 'boundaries',
                    'description': str(payload.boundaries.get('off_limits', [])),
                    'expires_at': None
                }
            ]

            supabase.table('user_context').insert(context_entries).execute()
            quiz_logger.info(f"Step 5 completed: {len(context_entries)} context entries saved")
        except Exception as e:
            quiz_logger.error(f"Step 5 failed: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to save mindset and boundaries: {str(e)}")

        # Step 6 — Trigger personality generation in background
        # This runs asynchronously and does not block the response
        quiz_logger.info(f"Step 6: Triggering personality generation for {payload.user_id}")
        background_tasks.add_task(trigger_personality_generation, payload.user_id)
        quiz_logger.info(f"Step 6 completed: Personality generation triggered in background")

        # All steps completed successfully
        quiz_logger.info(f"Quiz completion successful for user {payload.user_id}")

        return {
            'success': True,
            'message': 'Quiz data saved successfully',
            'user_id': payload.user_id,
            'personality_id': personality_id,
            'sms_number': os.getenv('SENDBLUE_PHONE_NUMBER', ''),
        }

    except HTTPException:
        raise
    except Exception as e:
        quiz_logger.error(f"Unexpected error during quiz completion for user {payload.user_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


# ---------------------------------------------------------------------------
# Link token — phone number activation
# ---------------------------------------------------------------------------

class LinkTokenRequest(BaseModel):
    user_id: str


@router.post("/generate-link-token")
async def generate_link_token(req: LinkTokenRequest):
    chars = string.ascii_uppercase + string.digits
    token = "STK-" + "".join(random.choices(chars, k=4))
    expires_at = (datetime.now(pytz.UTC) + timedelta(hours=24)).isoformat()

    try:
        supabase.table("phone_link_tokens").insert({
            "user_id": req.user_id,
            "token": token,
            "used": False,
            "expires_at": expires_at,
        }).execute()
    except Exception as e:
        quiz_logger.error(f"Failed to insert link token for {req.user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create link token")

    return {
        "token": token,
        "sms_number": os.getenv("SENDBLUE_PHONE_NUMBER", ""),
    }