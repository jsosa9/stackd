"""
message_router.py — Inbound SMS processing pipeline.

Both the live SMS webhook (routes/sms.py) and the dev chat simulator
(routes/mock.py) call process_inbound_sms() so local testing is always
100% representative of production.

Pipeline:
  0.  Personality swap  — regex check, no Gemini, instant return if matched
  1.  Gatekeeper        — abort if no active coach persona
  2.  Classifier        — one Gemini call → category string
  3.  Handler           — category-specific DB writes, returns execution_result
  4.  Voice generator   — Gemini chat reply using persona system prompt

Public API:
    process_inbound_sms(user_id, message_body, user_timezone) -> str
"""

import json
import logging
import os
import re
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from logging.handlers import RotatingFileHandler

import google.generativeai as genai
from dotenv import load_dotenv
from supabase import create_client
from routes.personas import persona_manager
from routes.ai import HUMAN_BEHAVIOR_RULES, CONVICTION_RULES

load_dotenv()

# ---------------------------------------------------------------------------
# Logging — rotating file handler, mirrors routes/ai.py pattern
# ---------------------------------------------------------------------------

log_dir = Path(__file__).parent.parent.parent / "logs"
log_dir.mkdir(exist_ok=True)

router_logger = logging.getLogger("message_router")
router_logger.setLevel(logging.DEBUG)
_handler = RotatingFileHandler(
    log_dir / "message_router.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
router_logger.addHandler(_handler)

logger = router_logger

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PERSONALITY_SWAP_RE = re.compile(r'^[A-Z]{4}[0-9]{4}$')

VALID_CATEGORIES = (
    "GOAL", "CREATE_GOAL", "MODIFY_GOAL", "DELETE_GOAL",
    "STATS_QUERY", "MOTIVATION_REQUEST",
    "NUTRITION", "TASK", "JOURNAL", "BET", "GENERAL",
)

_CLASSIFIER_SYSTEM = (
    "You are classifying an SMS message for a coaching app.\n"
    "Return only one word. The category name, nothing else.\n\n"
    "CREATE_GOAL: user is explicitly asking to register, add, or track a NEW habit/goal in the app as an ongoing commitment. "
    "REQUIRED: the message must contain at least one of these exact trigger words or phrases: 'add', 'track', 'create', 'register', 'set up', 'start tracking', 'can we track', 'log this as', 'make this a goal', 'add as a goal'. "
    "Examples that ARE CREATE_GOAL: 'I want to add a goal', 'can we track my running', 'add journaling as a habit to track', 'track this for me'. "
    "Examples that are NOT CREATE_GOAL (classify as GENERAL): 'I want to start meditating', 'I want to begin reading', 'I want to write about my day', 'I want to reflect on things', 'maybe I should meditate', 'I want to do yoga', 'I want to wake up at 5am'. "
    "The phrase 'I want to [verb] [activity]' without track/add/register is NEVER CREATE_GOAL — it is GENERAL.\n"
    "MODIFY_GOAL: user wants to change an existing goal — update its days, time, or activity name. "
    "REQUIRED: must reference an existing habit they already track and use words like 'change', 'update', 'move', 'switch', 'edit'. "
    "Examples: 'change my gym to Monday and Wednesday', 'update reading to 9pm', 'move my run to Tuesdays', 'switch gym to MWF at 7am'\n"
    "DELETE_GOAL: user wants to remove, delete, or stop tracking a goal. "
    "Examples: 'remove my running goal', 'delete my gym habit', 'I want to stop tracking meditation'\n"
    "GOAL: user is checking in on or reporting progress on an existing goal. "
    "Examples: completed a workout, ran 3 miles, did their habit, just finished the gym\n"
    "STATS_QUERY: user wants to see their goals, streaks, or progress. "
    "Examples: 'what are my goals', 'show my streak', 'how am I doing', 'what goals do I have'\n"
    "MOTIVATION_REQUEST: user explicitly wants motivation, a quote, or to be hyped up. "
    "Examples: 'I need motivation', 'send me a quote', 'hype me up', 'I need a push'\n"
    "NUTRITION: any mention of food, eating, drinking, calories, meals, "
    "a burger, coffee, anything consumed\n"
    "TASK: user is explicitly requesting a reminder or scheduling action — 'remind me at 9am', 'set a reminder', 'schedule this for tomorrow'. "
    "A message that simply mentions a time or speculation ('maybe I should meditate at 5am', 'I wake up at 5am') is NOT TASK — classify those as GENERAL.\n"
    "JOURNAL: any mention of feelings, tiredness, wanting to quit, "
    "emotions, mood, energy, stress, or mental state\n"
    "BET: any mention of a challenge, bet, or competing with someone\n"
    "GENERAL: only if it truly fits none of the above"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_json_fences(text: str) -> str:
    """Remove ```json ... ``` markdown fences from a Gemini response."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1].lstrip("json").strip() if len(parts) > 1 else text
    return text


def _strip_markdown(text: str) -> str:
    """Remove markdown bold/italic markers so they never reach SMS."""
    text = re.sub(r'\*{1,2}([^*\n]+)\*{1,2}', r'\1', text)
    text = re.sub(r'_{1,2}([^_\n]+)_{1,2}', r'\1', text)
    return text


def _strip_emojis(text: str) -> str:
    """Remove all emoji characters from a string."""
    return re.sub(
        "[\U00002600-\U000027BF"
        "\U0001F300-\U0001F9FF"
        "\U0001FA00-\U0001FA9F"
        "\U0001FAA0-\U0001FAFF"
        "\U00002702-\U000027B0"
        "\U0000FE00-\U0000FE0F"
        "\U0001F1E0-\U0001F1FF]+",
        "",
        text,
    ).strip()


def _hours_from_now(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _today_str() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Step 0 — Personality swap
# ---------------------------------------------------------------------------

async def _maybe_personality_swap(user_id: str, message_body: str) -> str | None:
    """
    If message_body looks like a personality ID (AAAA9999), attempt to swap.
    Returns the confirmation string on success, None if the ID isn't found
    (caller should continue normal pipeline).
    """
    if not _PERSONALITY_SWAP_RE.fullmatch(message_body.strip()):
        return None

    personality_id = message_body.strip()
    logger.info(f"[swap] user={user_id} attempting swap to personality_id={personality_id}")

    try:
        match_res = (
            supabase.table("coach_settings")
            .select("id, coach_name, user_id, generated_system_prompt")
            .eq("personality_id", personality_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not match_res.data:
            logger.info(f"[swap] personality_id={personality_id} not found for user={user_id} — treating as regular message")
            return None

        coach_row = match_res.data[0]
        coach_name = coach_row.get("coach_name", "your coach")

        # Deactivate all rows for this user, then activate the matched one
        supabase.table("coach_settings").update({"is_active": False}).eq("user_id", user_id).execute()
        supabase.table("coach_settings").update({"is_active": True}).eq("personality_id", personality_id).eq("user_id", user_id).execute()

        logger.info(f"[swap] user={user_id} switched to {coach_name} ({personality_id})")
        return await _generate_voice_reply(
            user_id,
            "I just activated you. Introduce yourself in one sentence, in character.",
            coach_row,
            "",
        )

    except Exception:
        logger.exception(f"[swap] failed for user={user_id} personality_id={personality_id}")
        return None


# ---------------------------------------------------------------------------
# Step 1 — Gatekeeper
# ---------------------------------------------------------------------------

async def _get_active_coach(user_id: str) -> dict | None:
    """
    Return the active coach_settings row for this user, or None.
    Falls back to most recent row by created_at if no is_active=True row exists.
    """
    try:
        res = (
            supabase.table("coach_settings")
            .select("personality_id, generated_system_prompt, coach_name, sounds_like")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .execute()
        )
        if res.data:
            return res.data[0]

        # Fallback: most recent row regardless of is_active
        fallback = (
            supabase.table("coach_settings")
            .select("personality_id, generated_system_prompt, coach_name, sounds_like")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return fallback.data[0] if fallback.data else None
    except Exception:
        logger.exception(f"[gatekeeper] DB error fetching coach for user={user_id}")
        return None


async def _handle_no_persona(user_id: str, message_body: str) -> str:
    """Log the inbound message and return a neutral ack — no Gemini, no engine."""
    try:
        supabase.table("messages").insert({
            "user_id":   user_id,
            "direction": "inbound",
            "body":      message_body,
        }).execute()
    except Exception:
        logger.exception(f"[gatekeeper] failed to log message for user={user_id}")
    logger.info(f"[gatekeeper] no active persona for user={user_id} — minimal ack")
    return "Message received."


# ---------------------------------------------------------------------------
# Step 2 — Classifier
# ---------------------------------------------------------------------------

async def classify(message_body: str) -> str:
    """One Gemini call → one of VALID_CATEGORIES. Defaults to GENERAL on any error."""
    try:
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash-lite",
            system_instruction=_CLASSIFIER_SYSTEM,
        )
        response = model.generate_content(f"Message: {message_body}")
        category = response.text.strip().upper()
        return category if category in VALID_CATEGORIES else "GENERAL"
    except Exception:
        logger.exception("[classifier] Gemini call failed — defaulting to GENERAL")
        return "GENERAL"


# ---------------------------------------------------------------------------
# Step 3 — Handlers
# ---------------------------------------------------------------------------

async def handle_goal(user_id: str, message_body: str, user_timezone: str) -> str:
    """
    Extract which goal was checked in and whether it was completed.
    Updates streak on completion. Writes win/struggle to user_context.
    """
    try:
        goals_res = (
            supabase.table("goals")
            .select("id, activity")
            .eq("user_id", user_id)
            .execute()
        )
        goals = goals_res.data or []
        if not goals:
            logger.info(f"[goal] no goals found for user={user_id}")
            return ""

        goals_list = ", ".join(f"{g['id']}:{g['activity']}" for g in goals)
        valid_ids  = {g["id"] for g in goals}

        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(
            f"From this message identify which goal is being checked in and if it was completed. "
            f"Goals: {goals_list}. Message: {message_body}\n"
            f'Return JSON: {{"goal_id": "str", "completed": true/false, "metric": "str"}}'
        )
        try:
            data = json.loads(_strip_json_fences(response.text))
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"[goal] bad JSON from Gemini for user={user_id}: {response.text[:100]}")
            data = {}

        goal_id   = data.get("goal_id", "")
        completed = bool(data.get("completed", False))
        metric    = data.get("metric") or message_body[:200]

        # Only use goal_id if it actually belongs to this user
        if goal_id not in valid_ids:
            goal_id = ""

        if completed and goal_id:
            try:
                from routes.ai import update_streak
                await update_streak(user_id, goal_id)
                logger.info(f"[goal] streak updated for user={user_id} goal={goal_id}")
            except Exception:
                logger.exception(f"[goal] update_streak failed for user={user_id} goal={goal_id}")
            try:
                supabase.table("goal_completions").upsert({
                    "user_id": user_id,
                    "goal_id": goal_id,
                    "completed_date": date.today().isoformat(),
                }, on_conflict="user_id,goal_id,completed_date").execute()
            except Exception:
                logger.exception(f"[goal] goal_completions upsert failed for user={user_id} goal={goal_id}")

        ctx_type = "win" if completed else "struggle"
        supabase.table("user_context").insert({
            "user_id":     user_id,
            "type":        ctx_type,
            "description": metric[:500],
            "expires_at":  _hours_from_now(72),
        }).execute()

        logger.info(f"[goal] logged {ctx_type} for user={user_id}: {metric[:60]}")
        return f"Goal logged: {metric}"

    except Exception:
        logger.exception(f"[goal] handler failed for user={user_id}")
        return ""


async def handle_create_goal(user_id: str, message_body: str, user_timezone: str = "") -> str:
    """Extract goal details from the user's message and insert a new goal into the DB."""
    _all_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    _day_abbrs = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    _full_to_abbr = {d.lower(): _day_abbrs[i] for i, d in enumerate(
        ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    )}
    try:
        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(
            f"The user wants to create a new goal. Extract the details.\n"
            f"Message: \"{message_body}\"\n\n"
            f"Return JSON with these keys:\n"
            f"activity: short action phrase for the goal (e.g. journal, run, meditate, read)\n"
            f"category: one of fitness, health, learning, personal, productivity\n"
            f"days: array of full day names they want to do it — empty array [] if not mentioned. "
            f"'every day', 'daily', 'every night' = all 7 days.\n"
            f"time: clock time string like '6:00 PM' or '18:00', or null if not mentioned.\n\n"
            f"Example: {{\"activity\": \"journal\", \"category\": \"personal\", "
            f"\"days\": [\"Monday\",\"Tuesday\",\"Wednesday\",\"Thursday\",\"Friday\",\"Saturday\",\"Sunday\"], "
            f"\"time\": \"21:00\"}}\n\n"
            f"Return only valid JSON, nothing else."
        )
        try:
            data = json.loads(_strip_json_fences(response.text))
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"[create_goal] bad JSON from Gemini for user={user_id}: {response.text[:100]}")
            data = {}

        activity = data.get("activity") or "new goal"
        category = data.get("category") or "personal"
        days = data.get("days") or []
        if not isinstance(days, list):
            days = []
        days = [d for d in days if d in _all_days]

        # Build times_per_day if time given
        time_raw = data.get("time")
        times_per_day: dict = {}
        time_display: str | None = None
        if time_raw and days:
            from services.onboarding import _normalize_time
            normalized = _normalize_time(str(time_raw))
            if normalized:
                days_lower = [d.lower() for d in days]
                times_per_day = {_full_to_abbr[d]: {"times": [normalized]} for d in days_lower if d in _full_to_abbr}
                h, m = map(int, normalized.split(":"))
                ampm = "am" if h < 12 else "pm"
                h12 = h % 12 or 12
                time_display = f"{h12}:{m:02d}{ampm}" if m else f"{h12}{ampm}"

        supabase.table("goals").insert({
            "user_id": user_id,
            "activity": activity,
            "category": category,
            "days": [d.lower() for d in days],
            "times_per_day": times_per_day,
        }).execute()

        day_str = "every day" if len(days) >= 7 else (", ".join(days) if days else "no days set")
        time_str = f" at {time_display}" if time_display else ""
        result = f"Goal added: {activity} {day_str}{time_str}"
        logger.info(f"[create_goal] {result} for user={user_id}")
        return result
    except Exception:
        logger.exception(f"[create_goal] failed for user={user_id}")
        return ""


async def handle_nutrition(user_id: str, message_body: str, user_timezone: str) -> str:
    """
    Extract food and calories from the message.
    Writes to nutrition_logs and user_context.
    """
    try:
        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(
            f"Extract food and estimated calories from this message: {message_body}\n"
            f'Return JSON: {{"food_description": "str", "estimated_calories": int, "confidence": float}}'
        )
        try:
            data = json.loads(_strip_json_fences(response.text))
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"[nutrition] bad JSON from Gemini for user={user_id}: {response.text[:100]}")
            data = {}

        food_desc = data.get("food_description") or message_body[:200]
        calories  = int(data.get("estimated_calories") or 0)

        supabase.table("nutrition_logs").insert({
            "user_id":            user_id,
            "food_description":   food_desc,
            "estimated_calories": calories,
            "gemini_analysis":    data,
            "reporting_date":     _today_str(),
        }).execute()

        description = f"Logged {calories} calories: {food_desc}"
        supabase.table("user_context").insert({
            "user_id":     user_id,
            "type":        "nutrition",
            "description": description[:500],
            "expires_at":  _hours_from_now(24),
        }).execute()

        logger.info(f"[nutrition] {description[:80]} for user={user_id}")
        return description

    except Exception:
        logger.exception(f"[nutrition] handler failed for user={user_id}")
        return ""


async def handle_task(user_id: str, message_body: str, user_timezone: str) -> str:
    """
    Extract task and time. Inserts into reminders (specific time) or
    deadlines (deadline-only, no specific time).
    """
    try:
        import pytz
        user_tz          = pytz.timezone(user_timezone)
        current_time_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(
            f"Extract the task and scheduled time from this message.\n"
            f"User timezone: {user_timezone}. Current time: {current_time_utc}.\n"
            f"Message: {message_body}\n"
            f'Return JSON: {{"description": "str", "scheduled_for_iso": "ISO 8601 UTC or null", '
            f'"reminder_message": "str"}}'
        )
        try:
            data = json.loads(_strip_json_fences(response.text))
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"[task] bad JSON from Gemini for user={user_id}: {response.text[:100]}")
            data = {}

        description      = data.get("description") or message_body[:200]
        scheduled_for    = data.get("scheduled_for_iso")
        reminder_message = data.get("reminder_message", "")

        if scheduled_for:
            # Validate it parses as a datetime before inserting
            try:
                datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))
                supabase.table("reminders").insert({
                    "user_id":          user_id,
                    "description":      description,
                    "scheduled_for":    scheduled_for,
                    "reminder_message": reminder_message,
                    "sent":             False,
                }).execute()
                logger.info(f"[task] reminder set for user={user_id}: {description[:60]}")
            except ValueError:
                logger.warning(f"[task] invalid scheduled_for ISO: {scheduled_for!r}")
                scheduled_for = None

        if not scheduled_for:
            # No specific time — treat as a deadline with daily check-in
            supabase.table("deadlines").insert({
                "user_id":        user_id,
                "description":    description,
                "deadline_date":  None,
                "daily_checkin":  True,
                "active":         True,
            }).execute()
            logger.info(f"[task] deadline set for user={user_id}: {description[:60]}")

        return f"Task set: {description}"

    except Exception:
        logger.exception(f"[task] handler failed for user={user_id}")
        return ""


async def handle_journal(user_id: str, message_body: str, user_timezone: str) -> str:
    """
    Extract mood and energy. Writes a structured entry to user_context.
    """
    try:
        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(
            f"Extract mood and energy from this message: {message_body}\n"
            f'Return JSON: {{'
            f'"mood": "great/good/neutral/low/struggling", '
            f'"energy": "high/normal/low", '
            f'"type": "mood/energy/struggle/win/personal", '
            f'"description": "one sentence summary as a fact about the user"'
            f'}}'
        )
        try:
            data = json.loads(_strip_json_fences(response.text))
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"[journal] bad JSON from Gemini for user={user_id}: {response.text[:100]}")
            data = {}

        ctx_type    = data.get("type", "personal")
        description = data.get("description") or message_body[:200]

        supabase.table("user_context").insert({
            "user_id":     user_id,
            "type":        ctx_type,
            "description": description[:500],
            "expires_at":  _hours_from_now(24),
        }).execute()

        logger.info(f"[journal] logged type={ctx_type} for user={user_id}: {description[:60]}")
        return f"Journal noted: {description}"

    except Exception:
        logger.exception(f"[journal] handler failed for user={user_id}")
        return ""


async def handle_bet(user_id: str, message_body: str, user_timezone: str) -> str:
    """
    Extract social bet details. Writes to social_bets table.
    """
    try:
        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(
            f"Extract the social bet or accountability challenge from this message: {message_body}\n"
            f'Return JSON: {{"description": "str", "target": "str", "deadline_iso": "ISO 8601 UTC or null"}}'
        )
        try:
            data = json.loads(_strip_json_fences(response.text))
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"[bet] bad JSON from Gemini for user={user_id}: {response.text[:100]}")
            data = {}

        description  = data.get("description") or message_body[:200]
        target       = data.get("target", "")
        deadline_iso = data.get("deadline_iso")

        supabase.table("social_bets").insert({
            "user_id":     user_id,
            "description": description,
            "target":      target,
            "deadline":    deadline_iso,
            "completed":   False,
        }).execute()

        logger.info(f"[bet] logged for user={user_id}: {description[:60]}")
        return f"Bet logged: {description}"

    except Exception:
        logger.exception(f"[bet] handler failed for user={user_id}")
        return ""


async def handle_delete_goal(user_id: str, message_body: str, user_timezone: str = "") -> str:
    """Identify which goal the user wants to delete and remove it from the DB."""
    try:
        goals_res = (
            supabase.table("goals")
            .select("id, activity")
            .eq("user_id", user_id)
            .execute()
        )
        goals = goals_res.data or []
        if not goals:
            return "no goals to delete"

        goals_list = ", ".join(f"{g['id']}:{g['activity']}" for g in goals)
        valid_ids  = {g["id"] for g in goals}

        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(
            f"The user wants to delete a goal. Identify which one.\n"
            f"Goals: {goals_list}. Message: {message_body}\n"
            f'Return JSON: {{"goal_id": "str", "activity": "str"}}'
        )
        try:
            data = json.loads(_strip_json_fences(response.text))
        except (json.JSONDecodeError, ValueError):
            data = {}

        goal_id  = data.get("goal_id", "")
        activity = data.get("activity", "that goal")

        if goal_id not in valid_ids:
            return "could not identify which goal to delete"

        supabase.table("goals").delete().eq("id", goal_id).eq("user_id", user_id).execute()
        logger.info(f"[delete_goal] deleted goal='{activity}' ({goal_id}) for user={user_id}")
        return f"Goal deleted: {activity}"

    except Exception:
        logger.exception(f"[delete_goal] failed for user={user_id}")
        return ""


async def handle_modify_goal(user_id: str, message_body: str, user_timezone: str = "") -> str:
    """Parse the user's update request and apply changes to the matching goal in DB."""
    _all_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    _day_abbrs = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    _full_to_abbr = {d.lower(): _day_abbrs[i] for i, d in enumerate(
        ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    )}
    try:
        goals_res = supabase.table("goals").select("id, activity, days, times_per_day").eq("user_id", user_id).execute()
        goals = goals_res.data or []
        if not goals:
            return "user has no goals to modify"

        goals_context = ", ".join(f"{g['id']}:{g['activity']}" for g in goals)
        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(
            f"The user wants to modify an existing goal.\n"
            f"Their goals: {goals_context}\n"
            f"Message: \"{message_body}\"\n\n"
            f"Return JSON with these keys:\n"
            f"goal_id: the id of the goal to modify\n"
            f"activity: the activity name (for confirmation)\n"
            f"days: new array of full day names, or null to keep existing\n"
            f"  'every day', 'daily', 'every night' = all 7 days\n"
            f"time: new clock time string like '18:00', or null to keep existing\n\n"
            f"Return only valid JSON, nothing else."
        )
        try:
            data = json.loads(_strip_json_fences(response.text))
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"[modify_goal] bad JSON from Gemini for user={user_id}: {response.text[:100]}")
            return "could not understand which goal to update"

        goal_id = data.get("goal_id", "")
        activity = data.get("activity", "that goal")
        valid_ids = {g["id"] for g in goals}

        if goal_id not in valid_ids:
            return "could not identify which goal to update"

        update_payload: dict = {}
        new_days = data.get("days")
        new_time = data.get("time")

        if new_days is not None:
            if not isinstance(new_days, list):
                new_days = []
            new_days = [d for d in new_days if d in _all_days]
            days_lower = [d.lower() for d in new_days]
            update_payload["days"] = days_lower

        # Rebuild times_per_day if either days or time changed
        existing_goal = next((g for g in goals if g["id"] == goal_id), {})
        working_days = update_payload.get("days") or existing_goal.get("days") or []

        if new_time is not None:
            from services.onboarding import _normalize_time
            normalized = _normalize_time(str(new_time))
            if normalized and working_days:
                times_map = {_full_to_abbr[d]: {"times": [normalized]} for d in working_days if d in _full_to_abbr}
                update_payload["times_per_day"] = times_map
        elif "days" in update_payload and working_days:
            # Days changed but time not — preserve existing time across new days
            existing_tpd = existing_goal.get("times_per_day") or {}
            existing_time = None
            for dv in existing_tpd.values():
                t = (dv.get("times") or [None])[0]
                if t:
                    existing_time = t
                    break
            if existing_time:
                times_map = {_full_to_abbr[d]: {"times": [existing_time]} for d in working_days if d in _full_to_abbr}
                update_payload["times_per_day"] = times_map

        if not update_payload:
            return "no changes detected"

        supabase.table("goals").update(update_payload).eq("id", goal_id).eq("user_id", user_id).execute()

        day_str = ("every day" if len(working_days) >= 7 else ", ".join(d.capitalize() for d in working_days)) if working_days else "days unchanged"
        logger.info(f"[modify_goal] updated goal='{activity}' for user={user_id} payload={update_payload}")
        return f"Goal updated: {activity} is now {day_str}"

    except Exception:
        logger.exception(f"[modify_goal] failed for user={user_id}")
        return ""


async def handle_stats_query(user_id: str, message_body: str, user_timezone: str = "") -> str:
    """Fetch goals and streaks and return a summary for the voice generator."""
    try:
        goals_res = (
            supabase.table("goals")
            .select("id, activity, category, days")
            .eq("user_id", user_id)
            .execute()
        )
        goals = goals_res.data or []

        if not goals:
            return "user has no goals set yet"

        streaks_res = (
            supabase.table("streaks")
            .select("goal_id, current_streak, longest_streak")
            .eq("user_id", user_id)
            .execute()
        )
        streak_map = {s["goal_id"]: s for s in (streaks_res.data or [])}

        lines = []
        for g in goals:
            s = streak_map.get(g["id"], {})
            current = s.get("current_streak", 0)
            longest = s.get("longest_streak", 0)
            lines.append(
                f"- {g['activity']} ({g.get('category', '')}): "
                f"{current} day streak (best: {longest})"
            )

        summary = "User's goals and streaks:\n" + "\n".join(lines)
        logger.info(f"[stats_query] fetched {len(goals)} goals for user={user_id}")
        return summary

    except Exception:
        logger.exception(f"[stats_query] failed for user={user_id}")
        return ""


async def handle_motivation_request(user_id: str, message_body: str, user_timezone: str = "") -> str:
    """Signal to the voice generator that the user wants on-demand motivation."""
    logger.info(f"[motivation_request] user={user_id} requested motivation")
    return "user is explicitly asking for motivation and a push right now"


_HANDLER_MAP = {
    "CREATE_GOAL":        handle_create_goal,
    "MODIFY_GOAL":        handle_modify_goal,
    "DELETE_GOAL":        handle_delete_goal,
    "STATS_QUERY":        handle_stats_query,
    "MOTIVATION_REQUEST": handle_motivation_request,
    "GOAL":               handle_goal,
    "NUTRITION":          handle_nutrition,
    "TASK":               handle_task,
    "JOURNAL":            handle_journal,
    "BET":                handle_bet,
    "GENERAL":     None,  # no DB write; voice generator handles it
}


# ---------------------------------------------------------------------------
# Step 4 — Voice generator
# ---------------------------------------------------------------------------

async def _generate_voice_reply(
    user_id:          str,
    message_body:     str,
    coach:            dict,
    execution_result: str,
) -> str:
    """
    Build and send the coach reply using the persona's system prompt
    and the last 10 messages as chat history.
    """
    system_prompt = coach.get("generated_system_prompt") or ""

    # Augment system_prompt with few-shot examples (without duplicating the identity header)
    try:
        from routes.ai import get_persona_examples_block
        examples_block = await get_persona_examples_block(coach)
        if examples_block and examples_block not in system_prompt:
            system_prompt = system_prompt + "\n\nReinforcement examples:\n" + examples_block
    except Exception:
        logger.exception("[voice] persona augmentation failed — continuing with base prompt")

    try:
        msgs_res = (
            supabase.table("messages")
            .select("direction, body")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        history = list(reversed(msgs_res.data or []))
    except Exception:
        logger.exception(f"[voice] failed to fetch message history for user={user_id}")
        history = []

    gemini_history = [
        {
            "role":  "user" if m["direction"] == "inbound" else "model",
            "parts": [m["body"]],
        }
        for m in history
    ]

    try:
        user_res = supabase.table("users").select("name").eq("id", user_id).limit(1).execute()
        user_name = user_res.data[0].get("name", "") if user_res.data else ""
    except Exception:
        logger.exception(f"[voice] failed to fetch user name for user={user_id}")
        user_name = ""

    try:
        streaks_res = (
            supabase.table("streaks")
            .select("goal_id, current_streak, longest_streak")
            .eq("user_id", user_id)
            .execute()
        )
        streak_map = {s["goal_id"]: s["current_streak"] for s in (streaks_res.data or [])}
    except Exception:
        logger.exception(f"[voice] failed to fetch streaks for user={user_id}")
        streak_map = {}

    try:
        goals_res = (
            supabase.table("goals")
            .select("id, activity, days, times_per_day")
            .eq("user_id", user_id)
            .execute()
        )
        goals_list = goals_res.data or []
        goals_context = (
            "\n".join(
                f"- {g['activity']} (streak: {streak_map.get(g['id'], 0)} days)"
                for g in goals_list
            )
            if goals_list else "none set"
        )
        # Streak coaching hints: tell the AI how to use each streak
        streak_hints = []
        for g in goals_list:
            s = streak_map.get(g["id"], 0)
            if s >= 7:
                streak_hints.append(f"{g['activity']}: {s}-day streak — this is serious momentum, make them feel what it would cost to break it")
            elif s >= 3:
                streak_hints.append(f"{g['activity']}: {s}-day streak — acknowledge the build, make it feel real")
            elif s == 0:
                streak_hints.append(f"{g['activity']}: no current streak — ask one question about what got in the way, do not lecture")
        streak_coaching = "\n".join(streak_hints) if streak_hints else ""
    except Exception:
        logger.exception(f"[voice] failed to fetch goals for user={user_id}")
        goals_context = "unknown"
        streak_coaching = ""

    try:
        ctx_res = (
            supabase.table("user_context")
            .select("type, description")
            .eq("user_id", user_id)
            .in_("type", ["mood", "struggle", "win", "personal", "energy"])
            .order("created_at", desc=True)
            .limit(3)
            .execute()
        )
        user_context_lines = [f"- {r['type']}: {r['description']}" for r in (ctx_res.data or [])]
        user_context_block = "\n".join(user_context_lines) if user_context_lines else "none"
    except Exception:
        logger.exception(f"[voice] failed to fetch user_context for user={user_id}")
        user_context_block = "unknown"

    streak_section = f"\nStreak coaching context:\n{streak_coaching}\n" if streak_coaching else ""
    user_prompt = (
        f"User's name: {user_name}\n"
        f"User's active goals and streaks:\n{goals_context}\n"
        f"{streak_section}\n"
        f"Recent user context (mood, wins, struggles):\n{user_context_block}\n\n"
        f"The user sent: {message_body}\n"
        f"Actions taken: {execution_result or 'none'}\n"
        "Reply as the coach. SMS only. Stay in character. "
        "Only reference activities that exist in the user's goal list above. "
        "Never assume the user is committing to an activity or will do something — ask first. "
        "Never tell the user you will remind them of something or that an activity starts now unless it was confirmed. "
        "If the user checked in on a goal, ask one specific follow-up question about quality or depth — how far, how long, how hard, what surprised them. "
        "Probe — but only about real registered goals. Never ask more than one question."
    )

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-lite",
        system_instruction=f"{system_prompt}\n\n{HUMAN_BEHAVIOR_RULES}\n\n{CONVICTION_RULES}",
    )
    chat     = model.start_chat(history=gemini_history)
    response = chat.send_message(user_prompt)
    reply    = _strip_markdown(_strip_emojis(response.text.strip()))
    logger.info(f"[voice] reply generated for user={user_id} ({len(reply)} chars)")
    return reply


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def process_inbound_sms(
    user_id:       str,
    message_body:  str,
    user_data:     dict = None,
    user_timezone: str  = "America/New_York",
) -> str:
    """
    Full inbound SMS pipeline:

      0. Personality swap  — instant if message matches AAAA9999 and ID is found
      1. Gatekeeper        — return minimal ack if no coach persona exists
      2. Classify          — one Gemini call → GOAL/NUTRITION/TASK/JOURNAL/BET/GENERAL
      3. Handle            — category-specific DB writes → execution_result string
      4. Voice             — Gemini chat reply in the coach's persona voice
    """
    message_body = (message_body or "").strip()
    logger.info(f"[pipeline] start user={user_id} len={len(message_body)}")

    # 0. Personality swap — no Gemini, regex only
    swap_reply = await _maybe_personality_swap(user_id, message_body)
    if swap_reply is not None:
        return swap_reply

    # 1. Gatekeeper — must have an active (or any) coach row
    coach = await _get_active_coach(user_id)
    if coach is None:
        return await _handle_no_persona(user_id, message_body)

    # 2. Classify
    category = await classify(message_body)
    logger.info(f"[pipeline] user={user_id} category={category}")

    # 3. Route to handler
    handler          = _HANDLER_MAP.get(category)
    execution_result = ""
    if handler is not None:
        execution_result = await handler(user_id, message_body, user_timezone)
        logger.info(f"[pipeline] user={user_id} handler result: {execution_result[:80]}")

    # 4. Voice generator
    try:
        reply = await _generate_voice_reply(user_id, message_body, coach, execution_result)
    except Exception:
        logger.exception(f"[pipeline] voice generator failed for user={user_id}")
        reply = "Got you. Let's keep going."
    return reply
