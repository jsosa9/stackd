import logging
import os
import asyncio
from datetime import datetime, timedelta
from fastapi import APIRouter
from supabase import create_client
from twilio.rest import Client as TwilioClient
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
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

twilio_client = TwilioClient(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN"),
)

TWILIO_FROM = os.getenv("TWILIO_PHONE_NUMBER")

# BackgroundScheduler runs in a separate thread — no async event loop required.
scheduler = BackgroundScheduler()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def send_sms(to: str, body: str) -> None:
    """Send an SMS via Twilio and log the result."""
    try:
        msg = twilio_client.messages.create(body=body, from_=TWILIO_FROM, to=to)
        logger.info(f"Sent SMS to {to}: sid={msg.sid}")
    except Exception:
        logger.exception(f"Failed to send SMS to {to}")


def log_message(user_id: str, body: str) -> None:
    """Save an outbound scheduled message to the messages table."""
    try:
        supabase.table("messages").insert({
            "user_id": user_id,
            "direction": "outbound",
            "body": body,
        }).execute()
    except Exception:
        logger.exception(f"Failed to log outbound message for user {user_id}")


def run_async(coro):
    """Run an async coroutine from synchronous (scheduler thread) context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Scheduler job 1: Daily check-ins (runs every minute)
# ---------------------------------------------------------------------------

def send_scheduled_checkins() -> None:
    """
    Runs every minute. Queries all users and checks if the current local time
    matches their configured check-in time. For each match:
      - Fetches the user's active goals for today's day of week
      - Calls generate_checkin_text() for each goal
      - Sends via Twilio
      - Logs to messages table

    Uses the user's own timezone to compare times accurately.
    """
    from routes.ai import generate_checkin_text

    logger.debug("Running scheduled check-in job")

    # Fetch all users with their schedule preferences joined
    schedules_res = supabase.table("schedule").select(
        "user_id, checkin_time, timezone, users(id, phone, name)"
    ).execute()

    now_utc = datetime.utcnow()

    for sched in schedules_res.data or []:
        user = sched.get("users")
        if not user or not user.get("phone"):
            continue

        user_id = user["id"]
        tz_name = sched.get("timezone", "America/New_York")

        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            logger.warning(f"Unknown timezone '{tz_name}' for user {user_id}")
            continue

        local_now = datetime.now(tz)

        # Parse checkin_time in HH:MM format (e.g., "08:00", "14:30")
        checkin_time_str = sched.get("checkin_time", "08:00")
        try:
            checkin_hour, checkin_minute = map(int, checkin_time_str.split(":"))
        except Exception:
            logger.warning(f"Invalid checkin_time '{checkin_time_str}' for user {user_id}")
            continue

        if local_now.hour != checkin_hour or local_now.minute != checkin_minute:
            continue  # Not their check-in time yet

        # Get goals scheduled for today's day of week (0=Mon … 6=Sun)
        day_abbrevs = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        today = day_abbrevs[local_now.weekday()]

        goals_res = supabase.table("goals").select("activity, days").eq("user_id", user_id).execute()
        todays_goals = [
            g["activity"]
            for g in (goals_res.data or [])
            if today in (g.get("days") or [])
        ]

        if not todays_goals:
            logger.info(f"No goals for {user.get('name', user_id)} on {today} — skipping check-in")
            continue

        logger.info(f"Sending check-in to {user.get('name', user_id)} for {len(todays_goals)} goal(s)")

        for goal in todays_goals:
            try:
                text = run_async(generate_checkin_text(user_id, goal))
                send_sms(user["phone"], text)
                log_message(user_id, text)
            except Exception:
                logger.exception(f"Failed check-in for user {user_id}, goal '{goal}'")


# ---------------------------------------------------------------------------
# Scheduler job 2: Motivation messages (runs every 30 minutes)
# ---------------------------------------------------------------------------

def send_motivation_messages() -> None:
    """
    Runs every 30 minutes. For each user with motivation_enabled = true:
      - Checks if the current local time falls within their motivation_window
      - Checks if enough time has passed since their last motivation text
        based on motivation_frequency (e.g. "Once a day" = 22h gap minimum)
      - Calls generate_motivation_text() if all checks pass
      - Sends via Twilio and logs to messages table
    """
    from routes.ai import generate_motivation_text

    logger.debug("Running motivation message job")

    # Minimum gap between motivation texts by frequency setting
    frequency_gap: dict[str, int] = {
        "Once a day":    22,
        "2x a day":       10,
        "3x a day":        6,
        "Weekdays only": 22,
        "Weekends only": 22,
    }

    schedules_res = supabase.table("schedule").select(
        "user_id, motivation_enabled, motivation_frequency, "
        "motivation_window_start, motivation_window_end, timezone, "
        "users(id, phone, name)"
    ).eq("motivation_enabled", True).execute()

    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)

    for sched in schedules_res.data or []:
        user = sched.get("users")
        if not user or not user.get("phone"):
            continue

        user_id = user["id"]
        tz_name = sched.get("timezone", "America/New_York")

        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            logger.warning(f"Unknown timezone '{tz_name}' for user {user_id}")
            continue

        local_now = datetime.now(tz)

        # Parse the motivation window times in HH:MM format (e.g., "09:00", "20:00")
        def parse_time_str(time_str: str) -> int:
            """Parse 'HH:MM' format and return hour (0-23)."""
            try:
                hour, _ = map(int, time_str.split(":"))
                return hour
            except Exception:
                return 9  # Default to 9 AM

        window_start = parse_time_str(sched.get("motivation_window_start", "09:00"))
        window_end = parse_time_str(sched.get("motivation_window_end", "20:00"))

        if not (window_start <= local_now.hour < window_end):
            continue  # Outside their motivation window

        # Check weekday filter if applicable
        frequency = sched.get("motivation_frequency", "Once a day")
        if frequency == "Weekdays only" and local_now.weekday() >= 5:
            continue
        if frequency == "Weekends only" and local_now.weekday() < 5:
            continue

        # Check minimum gap since last motivation text
        min_gap_hours = frequency_gap.get(frequency, 22)
        last_msg_res = (
            supabase.table("messages")
            .select("created_at")
            .eq("user_id", user_id)
            .eq("direction", "outbound")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if last_msg_res.data:
            last_sent_str = last_msg_res.data[0]["created_at"]
            # Supabase returns ISO 8601 strings
            last_sent = datetime.fromisoformat(last_sent_str.replace("Z", "+00:00"))
            if (now_utc - last_sent).total_seconds() < min_gap_hours * 3600:
                continue  # Too soon to send another

        logger.info(f"Sending motivation to {user.get('name', user_id)}")

        try:
            text = run_async(generate_motivation_text(user_id))
            send_sms(user["phone"], text)
            log_message(user_id, text)
        except Exception:
            logger.exception(f"Failed motivation message for user {user_id}")


# ---------------------------------------------------------------------------
# Scheduler startup / shutdown
# ---------------------------------------------------------------------------

def start_scheduler() -> None:
    """Register jobs and start the background scheduler. Called from main.py startup."""
    if scheduler.running:
        logger.info("Scheduler already running — skipping start")
        return

    # Check-in job: every minute
    scheduler.add_job(
        send_scheduled_checkins,
        CronTrigger(minute="*"),
        id="checkins",
        replace_existing=True,
        misfire_grace_time=30,
    )

    # Motivation job: every 30 minutes
    scheduler.add_job(
        send_motivation_messages,
        CronTrigger(minute="0,30"),
        id="motivation",
        replace_existing=True,
        misfire_grace_time=60,
    )

    scheduler.start()
    logger.info("APScheduler started — check-ins (every 1m), motivation (every 30m)")


def stop_scheduler() -> None:
    """Gracefully stop the scheduler. Called from main.py shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")


# ---------------------------------------------------------------------------
# Manual trigger endpoints (useful for testing without waiting for the clock)
# ---------------------------------------------------------------------------

@router.post("/trigger-checkins")
async def trigger_checkins():
    """Manually fire the check-in job right now (for testing)."""
    send_scheduled_checkins()
    return {"status": "check-ins triggered"}


@router.post("/trigger-motivation")
async def trigger_motivation():
    """Manually fire the motivation job right now (for testing)."""
    send_motivation_messages()
    return {"status": "motivation messages triggered"}
