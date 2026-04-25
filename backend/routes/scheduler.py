import logging
import os
import asyncio
import json
from datetime import datetime, timedelta, date
from fastapi import APIRouter
from supabase import create_client
from twilio.rest import Client as TwilioClient
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
from dotenv import load_dotenv
from anthropic import Anthropic
import google.generativeai as genai

load_dotenv()

logger = logging.getLogger(__name__)

# Import logging modules for specialized loggers
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Set up specialized loggers if not already configured
log_dir = Path(__file__).parent.parent.parent / "logs"
log_dir.mkdir(exist_ok=True)

scheduler_logger = logging.getLogger("scheduler")
if not scheduler_logger.handlers:
    scheduler_logger.setLevel(logging.DEBUG)
    scheduler_handler = RotatingFileHandler(
        log_dir / "scheduler.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
    )
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    scheduler_handler.setFormatter(formatter)
    scheduler_logger.addHandler(scheduler_handler)

patterns_logger = logging.getLogger("patterns")
if not patterns_logger.handlers:
    patterns_logger.setLevel(logging.DEBUG)
    patterns_handler = RotatingFileHandler(
        log_dir / "patterns.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
    )
    patterns_handler.setFormatter(formatter)
    patterns_logger.addHandler(patterns_handler)

streaks_logger = logging.getLogger("streaks")
if not streaks_logger.handlers:
    streaks_logger.setLevel(logging.DEBUG)
    streaks_handler = RotatingFileHandler(
        log_dir / "streaks.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
    )
    streaks_handler.setFormatter(formatter)
    streaks_logger.addHandler(streaks_handler)

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
# Scheduler job 3: Reminders checker (runs every minute)
# ---------------------------------------------------------------------------

def send_scheduled_reminders() -> None:
    """
    Runs every minute. Queries reminders table for rows where:
    - sent = false
    - scheduled_for <= now (in UTC)
    
    For each unsent reminder:
    - Fetch user's phone number
    - Send reminder_message via Twilio
    - Update sent = true
    - Log to scheduler.log
    
    Error handling: One reminder failing doesn't block others.
    """
    scheduler_logger.debug("Running reminder checker job")
    
    try:
        now_utc = datetime.now(pytz.UTC).isoformat()
        
        # Query unsent reminders scheduled for now or earlier
        reminders_res = (
            supabase.table("reminders")
            .select("*, users(phone, name)")
            .eq("sent", False)
            .lte("scheduled_for", now_utc)
            .execute()
        )
        
        if not reminders_res.data:
            return
        
        scheduler_logger.info(f"Found {len(reminders_res.data)} unsent reminders to deliver")
        
        for reminder in reminders_res.data:
            user = reminder.get("users", {})
            phone = user.get("phone")
            reminder_id = reminder["id"]
            
            if not phone:
                scheduler_logger.warning(f"Reminder {reminder_id} has no phone number — skipping")
                continue
            
            try:
                # Send the reminder message
                send_sms(phone, reminder["reminder_message"])
                
                # Mark as sent
                supabase.table("reminders").update({"sent": True}).eq("id", reminder_id).execute()
                
                scheduler_logger.info(
                    f"Sent reminder to {user.get('name', phone)}: {reminder['description']}"
                )
            except Exception as e:
                scheduler_logger.error(f"Failed to send reminder {reminder_id}: {str(e)}")
                continue
    
    except Exception as e:
        scheduler_logger.error(f"Critical error in reminder checker: {str(e)}", exc_info=True)


# ---------------------------------------------------------------------------
# Scheduler job 4: Deadline daily check-ins (runs every morning 6am UTC)
# ---------------------------------------------------------------------------

def send_deadline_checkins() -> None:
    """
    Runs every morning at 6am UTC. For each active deadline:
    - Calculate days remaining until deadline_date
    - Fetch user's coach system prompt
    - Generate a deadline-specific check-in message using Gemini
    - Send via Twilio
    - Log to scheduler.log
    
    Error handling: One deadline failing doesn't block others.
    """
    from routes.ai import get_active_context
    
    scheduler_logger.debug("Running deadline check-ins job")
    
    try:
        # Query all active deadlines
        deadlines_res = (
            supabase.table("deadlines")
            .select("*, users(id, phone, name), coach_settings(generated_system_prompt)")
            .eq("active", True)
            .eq("daily_checkin", True)
            .execute()
        )
        
        if not deadlines_res.data:
            return
        
        scheduler_logger.info(f"Sending deadline check-ins to {len(deadlines_res.data)} users")
        
        for deadline in deadlines_res.data:
            user = deadline.get("users", {})
            phone = user.get("phone")
            user_id = user.get("id")
            
            if not phone or not user_id:
                continue
            
            try:
                # Calculate days remaining
                deadline_date = datetime.strptime(deadline["deadline_date"], "%Y-%m-%d").date()
                days_remaining = (deadline_date - date.today()).days
                
                if days_remaining < 0:
                    continue  # Deadline already passed
                
                # Get system prompt
                coach = deadline.get("coach_settings", {})
                system_prompt = coach.get("generated_system_prompt", "")
                
                # Get active context
                active_context = run_async(get_active_context(user_id))
                if active_context:
                    system_prompt = f"{system_prompt}\n\n{active_context}"
                
                # Generate deadline check-in with Gemini
                model = genai.GenerativeModel(
                    model_name="gemini-1.5-flash",
                    system_instruction=system_prompt,
                )
                
                prompt = (
                    f"Generate a brief check-in about this deadline: {deadline['description']}. "
                    f"There are {days_remaining} days left. "
                    f"Ask about progress or what they need. Keep it 1-2 sentences, SMS-friendly."
                )
                
                response = model.generate_content(prompt)
                message = response.text.strip()
                
                # Send and log
                send_sms(phone, message)
                log_message(user_id, message)
                
                scheduler_logger.info(
                    f"Sent deadline check-in to {user.get('name', phone)}: "
                    f"{deadline['description']} ({days_remaining} days)"
                )
            except Exception as e:
                scheduler_logger.error(
                    f"Failed deadline check-in for user {user_id}: {str(e)}"
                )
                continue
    
    except Exception as e:
        scheduler_logger.error(f"Critical error in deadline check-ins: {str(e)}", exc_info=True)


# ---------------------------------------------------------------------------
# Scheduler job 5: Pattern analyzer (runs every night at midnight UTC)
# ---------------------------------------------------------------------------

def analyze_message_patterns() -> None:
    """
    Runs every night at midnight UTC. For each user:
    - Fetch last 30 days of message history
    - Analyze with Claude Haiku to detect patterns:
      * Days of week where they go quiet (no inbound messages)
      * Times of day where they reply most
      * Days where they report wins vs struggles
    - Insert or update habit_patterns table
    - Increment confidence if pattern already exists
    - Log to patterns.log
    
    Error handling: One user failing doesn't block others.
    """
    from anthropic import Anthropic
    
    patterns_logger.debug("Running pattern analysis job")
    
    try:
        # Fetch all users
        users_res = supabase.table("users").select("id").execute()
        
        scheduler_logger.info(f"Analyzing patterns for {len(users_res.data or [])} users")
        
        anthropic = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        
        for user in (users_res.data or []):
            user_id = user["id"]
            
            try:
                # Fetch last 30 days of messages
                thirty_days_ago = (datetime.now(pytz.UTC) - timedelta(days=30)).isoformat()
                
                messages_res = (
                    supabase.table("messages")
                    .select("direction, body, created_at")
                    .eq("user_id", user_id)
                    .gte("created_at", thirty_days_ago)
                    .order("created_at", desc=False)
                    .execute()
                )
                
                if not messages_res.data:
                    continue
                
                # Prepare message summary for Claude
                message_text = "Message history (last 30 days):\n"
                for msg in messages_res.data:
                    direction = "USER" if msg["direction"] == "inbound" else "COACH"
                    message_text += f"{direction}: {msg['body']}\n"
                
                # Ask Claude to detect patterns
                response = anthropic.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=500,
                    messages=[{
                        "role": "user",
                        "content": f"""{message_text}

Analyze this message history and detect behavioral patterns. Return a JSON object with this structure:
{{
    "quiet_days": ["Mon", "Fri"],  // Days of week with no inbound messages
    "strong_days": ["Wed", "Thu"],  // Days with strong engagement
    "best_time": "afternoon",  // Time of day they reply most
    "pattern_notes": "user struggles on Mondays, strong Wednesday mornings"
}}

Only return JSON, no other text."""
                    }]
                )
                
                try:
                    pattern_data = json.loads(response.content[0].text)
                except json.JSONDecodeError:
                    patterns_logger.warning(f"Failed to parse pattern data for user {user_id}")
                    continue
                
                # Day name to number mapping
                day_map = {
                    "Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3,
                    "Fri": 4, "Sat": 5, "Sun": 6
                }
                
                # Insert or update quiet days
                for day_name in pattern_data.get("quiet_days", []):
                    day_num = day_map.get(day_name)
                    if day_num is not None:
                        # Check if pattern already exists
                        existing = (
                            supabase.table("habit_patterns")
                            .select("*")
                            .eq("user_id", user_id)
                            .eq("pattern_type", "quiet_day")
                            .eq("day_of_week", day_num)
                            .execute()
                        )
                        
                        if existing.data:
                            # Update: increment confidence
                            pattern = existing.data[0]
                            supabase.table("habit_patterns").update({
                                "confidence": pattern.get("confidence", 1) + 1,
                                "updated_at": datetime.now(pytz.UTC).isoformat()
                            }).eq("id", pattern["id"]).execute()
                            patterns_logger.debug(f"Updated quiet_day pattern for {user_id} on {day_name}")
                        else:
                            # Insert new pattern
                            supabase.table("habit_patterns").insert({
                                "user_id": user_id,
                                "pattern_type": "quiet_day",
                                "day_of_week": day_num,
                                "description": f"User goes quiet on {day_name}s",
                                "confidence": 1,
                                "active": True,
                            }).execute()
                            patterns_logger.info(f"Detected quiet_day pattern for {user_id} on {day_name}")
                
                # Insert or update strong days
                for day_name in pattern_data.get("strong_days", []):
                    day_num = day_map.get(day_name)
                    if day_num is not None:
                        existing = (
                            supabase.table("habit_patterns")
                            .select("*")
                            .eq("user_id", user_id)
                            .eq("pattern_type", "strong_day")
                            .eq("day_of_week", day_num)
                            .execute()
                        )
                        
                        if existing.data:
                            pattern = existing.data[0]
                            supabase.table("habit_patterns").update({
                                "confidence": pattern.get("confidence", 1) + 1,
                                "updated_at": datetime.now(pytz.UTC).isoformat()
                            }).eq("id", pattern["id"]).execute()
                            patterns_logger.debug(f"Updated strong_day pattern for {user_id} on {day_name}")
                        else:
                            supabase.table("habit_patterns").insert({
                                "user_id": user_id,
                                "pattern_type": "strong_day",
                                "day_of_week": day_num,
                                "description": f"User is strong and engaged on {day_name}s",
                                "confidence": 1,
                                "active": True,
                            }).execute()
                            patterns_logger.info(f"Detected strong_day pattern for {user_id} on {day_name}")
            
            except Exception as e:
                patterns_logger.error(f"Failed to analyze patterns for user {user_id}: {str(e)}")
                continue
    
    except Exception as e:
        patterns_logger.error(f"Critical error in pattern analyzer: {str(e)}", exc_info=True)


# ---------------------------------------------------------------------------
# Scheduler job 6: Proactive pattern-based messages (runs every morning 7am UTC)
# ---------------------------------------------------------------------------

def send_proactive_pattern_messages() -> None:
    """
    Runs every morning at 7am UTC. For each habit_pattern with confidence >= 3:
    - Check if today matches the pattern (day of week, time of day)
    - If matched and no proactive message sent today, generate and send one
    - For quiet days: send preemptive check-in
    - For post-lunch crash: send motivation at 12:30pm
    - Log to scheduler.log
    
    Error handling: One pattern failing doesn't block others.
    """
    from routes.ai import get_active_context
    
    scheduler_logger.debug("Running proactive pattern messages job")
    
    try:
        # Query high-confidence patterns
        patterns_res = (
            supabase.table("habit_patterns")
            .select("*, users(id, phone, name, schedule(timezone))")
            .eq("active", True)
            .gte("confidence", 3)
            .execute()
        )
        
        if not patterns_res.data:
            return
        
        scheduler_logger.info(f"Checking {len(patterns_res.data)} high-confidence patterns")
        
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        today_name = day_names[datetime.utcnow().weekday()]
        
        for pattern in patterns_res.data:
            user = pattern.get("users", {})
            user_id = user.get("id")
            phone = user.get("phone")
            
            if not user_id or not phone:
                continue
            
            try:
                # Check if pattern matches today
                pattern_day = pattern.get("day_of_week")
                if pattern_day is not None and pattern_day != datetime.utcnow().weekday():
                    continue  # Pattern doesn't match today
                
                # Check if we already sent a proactive message today
                today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                today_messages = (
                    supabase.table("messages")
                    .select("*")
                    .eq("user_id", user_id)
                    .eq("direction", "outbound")
                    .gte("created_at", today_start)
                    .execute()
                )
                
                if len(today_messages.data or []) > 0:
                    continue  # Already sent a message today
                
                # Get active context and system prompt
                coach_res = (
                    supabase.table("coach_settings")
                    .select("generated_system_prompt")
                    .eq("user_id", user_id)
                    .execute()
                )
                
                system_prompt = coach_res.data[0]["generated_system_prompt"] if coach_res.data else ""
                active_context = run_async(get_active_context(user_id))
                
                if active_context:
                    system_prompt = f"{system_prompt}\n\n{active_context}"
                
                # Generate proactive message based on pattern type
                model = genai.GenerativeModel(
                    model_name="gemini-1.5-flash",
                    system_instruction=system_prompt,
                )
                
                if pattern["pattern_type"] == "quiet_day":
                    prompt = (
                        f"The user tends to go quiet on {today_name}s. "
                        f"Send a preemptive, gentle check-in to engage them. "
                        f"Keep it 1-2 sentences, friendly and casual."
                    )
                elif pattern["pattern_type"] == "strong_day":
                    prompt = (
                        f"The user is typically strong and engaged on {today_name}s. "
                        f"Send a message that energizes them and sets up for a great day. "
                        f"Keep it 1-2 sentences."
                    )
                else:
                    continue  # Unknown pattern type
                
                response = model.generate_content(prompt)
                message = response.text.strip()
                
                # Send and log
                send_sms(phone, message)
                log_message(user_id, message)
                
                scheduler_logger.info(
                    f"Sent proactive message to {user.get('name', phone)} "
                    f"(pattern: {pattern['pattern_type']})"
                )
            
            except Exception as e:
                scheduler_logger.error(
                    f"Failed proactive message for user {user_id}: {str(e)}"
                )
                continue
    
    except Exception as e:
        scheduler_logger.error(f"Critical error in proactive messages: {str(e)}", exc_info=True)


# ---------------------------------------------------------------------------
# Scheduler job 7: Streak milestone checker (runs every night 9pm UTC)
# ---------------------------------------------------------------------------

def send_milestone_celebrations() -> None:
    """
    Runs every night at 9pm UTC. Query streaks table for rows where
    current_streak matches a milestone number (3, 7, 14, 30, 60, 100).
    
    For each milestone hit:
    - Check if milestone celebration already sent today
    - Generate special milestone celebration message using Gemini
    - Send via Twilio
    - Log to streaks.log
    
    Error handling: One milestone failing doesn't block others.
    """
    from routes.ai import get_active_context
    
    streaks_logger.debug("Running milestone celebration job")
    
    try:
        milestones = [3, 7, 14, 30, 60, 100]
        
        # Fetch all streaks that hit milestones
        streaks_res = (
            supabase.table("streaks")
            .select("*, users(id, phone, name), goals(activity), coach_settings(generated_system_prompt)")
            .execute()
        )
        
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        
        for streak in (streaks_res.data or []):
            current = streak.get("current_streak", 0)
            
            # Check if this is a milestone
            if current not in milestones:
                continue
            
            user = streak.get("users", {})
            goal = streak.get("goals", {})
            user_id = user.get("id")
            phone = user.get("phone")
            
            if not user_id or not phone:
                continue
            
            try:
                # Check if we already celebrated this milestone today
                today_msgs = (
                    supabase.table("messages")
                    .select("*")
                    .eq("user_id", user_id)
                    .eq("direction", "outbound")
                    .gte("created_at", today_start)
                    .execute()
                )
                
                # Simple heuristic: if we sent a message today with "milestone" or the streak number, skip
                if any("milestone" in m.get("body", "").lower() or str(current) in m.get("body", "")
                       for m in (today_msgs.data or [])):
                    continue
                
                # Get active context and system prompt
                coach = streak.get("coach_settings", {})
                system_prompt = coach.get("generated_system_prompt", "")
                active_context = run_async(get_active_context(user_id))
                
                if active_context:
                    system_prompt = f"{system_prompt}\n\n{active_context}"
                
                # Generate special milestone message
                model = genai.GenerativeModel(
                    model_name="gemini-1.5-flash",
                    system_instruction=system_prompt,
                )
                
                goal_name = goal.get("activity", "your goal")
                
                prompt = (
                    f"The user just hit a {current}-day streak on {goal_name}! "
                    f"This is a BIG milestone. Send a special, celebratory message that feels different "
                    f"from a normal check-in — more significant and congratulatory. "
                    f"Make it personal and real, not generic. 2-3 sentences max."
                )
                
                response = model.generate_content(prompt)
                message = response.text.strip()
                
                # Send and log
                send_sms(phone, message)
                log_message(user_id, message)
                
                streaks_logger.info(
                    f"Sent {current}-day milestone celebration to {user.get('name', phone)} "
                    f"for goal: {goal_name}"
                )
            
            except Exception as e:
                streaks_logger.error(
                    f"Failed milestone celebration for user {user_id}: {str(e)}"
                )
                continue
    
    except Exception as e:
        streaks_logger.error(f"Critical error in milestone celebrations: {str(e)}", exc_info=True)


# ---------------------------------------------------------------------------
# Scheduler job 8: Sunday intention setter (runs every Sunday 7pm UTC)
# ---------------------------------------------------------------------------

def send_weekly_reflections() -> None:
    """
    Runs every Sunday at 7pm UTC. For all active users generate a Sunday
    evening reflection message that:
    - Summarizes accomplishments this week based on message history
    - Sets intentions for next week
    - References upcoming deadlines or commitments
    - Feels like a real conversation, not a report
    
    Error handling: One user failing doesn't block others.
    """
    from routes.ai import get_active_context, get_message_history
    
    scheduler_logger.debug("Running Sunday reflection job")
    
    try:
        # Fetch all users with their coach settings
        users_res = (
            supabase.table("users")
            .select("*, coach_settings(generated_system_prompt), schedule(timezone)")
            .execute()
        )
        
        scheduler_logger.info(f"Sending Sunday reflections to {len(users_res.data or [])} users")
        
        for user in (users_res.data or []):
            user_id = user["id"]
            phone = user.get("phone")
            
            if not phone:
                continue
            
            try:
                # Get this week's messages (last 7 days)
                week_ago = (datetime.now(pytz.UTC) - timedelta(days=7)).isoformat()
                week_msgs = run_async(get_message_history(user_id, limit=50))
                
                # Get upcoming deadlines
                deadlines_res = (
                    supabase.table("deadlines")
                    .select("*")
                    .eq("user_id", user_id)
                    .eq("active", True)
                    .execute()
                )
                
                # Get active context
                coach = user.get("coach_settings", {})
                system_prompt = coach.get("generated_system_prompt", "")
                active_context = run_async(get_active_context(user_id))
                
                if active_context:
                    system_prompt = f"{system_prompt}\n\n{active_context}"
                
                # Build prompt with week context
                deadline_list = ""
                if deadlines_res.data:
                    deadline_list = "Upcoming: " + ", ".join(
                        d["description"] for d in deadlines_res.data[:3]
                    )
                
                # Generate reflection
                model = genai.GenerativeModel(
                    model_name="gemini-1.5-flash",
                    system_instruction=system_prompt,
                )
                
                prompt = (
                    f"It's Sunday evening — time for a weekly reflection. "
                    f"Look back at this week's conversations and summarize what the user accomplished. "
                    f"Ask them what went well and what they want to focus on next week. "
                    f"{f'Reference these upcoming commitments: {deadline_list}. ' if deadline_list else ''}"
                    f"Keep it conversational and warm, 2-3 sentences. Feel like a real coach wrapping up the week."
                )
                
                response = model.generate_content(prompt)
                message = response.text.strip()
                
                # Send and log
                send_sms(phone, message)
                log_message(user_id, message)
                
                scheduler_logger.info(f"Sent weekly reflection to {user.get('name', phone)}")
            
            except Exception as e:
                scheduler_logger.error(f"Failed weekly reflection for user {user_id}: {str(e)}")
                continue
    
    except Exception as e:
        scheduler_logger.error(f"Critical error in weekly reflections: {str(e)}", exc_info=True)


# ---------------------------------------------------------------------------
# Scheduler job 9: Silence detector (runs every 6 hours)
# ---------------------------------------------------------------------------

def detect_silent_users() -> None:
    """
    Runs every 6 hours. Finds users with no inbound messages for 48+ hours.
    Escalates based on silence duration:
    
    - 48+ hours: Send gentle check-in in coach voice
    - 72+ hours: Send more direct message, reference their goals
    - 96+ hours: Send nuclear option message from coach_settings if set,
      otherwise send direct "are you okay?" message
    
    Error handling: One user failing doesn't block others.
    """
    from routes.ai import get_active_context
    
    scheduler_logger.debug("Running silence detector job")
    
    try:
        now_utc = datetime.now(pytz.UTC)
        
        # Fetch all users
        users_res = supabase.table("users").select("id, phone, name").execute()
        
        for user in (users_res.data or []):
            user_id = user["id"]
            phone = user.get("phone")
            
            if not phone:
                continue
            
            try:
                # Get last inbound message
                last_msg = (
                    supabase.table("messages")
                    .select("created_at")
                    .eq("user_id", user_id)
                    .eq("direction", "inbound")
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                
                if not last_msg.data:
                    continue  # No messages yet
                
                last_msg_time = datetime.fromisoformat(
                    last_msg.data[0]["created_at"].replace('Z', '+00:00')
                )
                hours_silent = (now_utc - last_msg_time).total_seconds() / 3600
                
                # Determine escalation level
                if hours_silent < 48:
                    continue  # Not silent enough
                elif hours_silent < 72:
                    escalation = "gentle"
                elif hours_silent < 96:
                    escalation = "direct"
                else:
                    escalation = "nuclear"
                
                # Get system prompt and context
                coach_res = (
                    supabase.table("coach_settings")
                    .select("generated_system_prompt, custom_coach_nuclear_option")
                    .eq("user_id", user_id)
                    .execute()
                )
                
                system_prompt = coach_res.data[0]["generated_system_prompt"] if coach_res.data else ""
                nuclear_msg = coach_res.data[0].get("custom_coach_nuclear_option") if coach_res.data else None
                
                active_context = run_async(get_active_context(user_id))
                if active_context:
                    system_prompt = f"{system_prompt}\n\n{active_context}"
                
                # Generate appropriate message
                model = genai.GenerativeModel(
                    model_name="gemini-1.5-flash",
                    system_instruction=system_prompt,
                )
                
                if escalation == "nuclear" and nuclear_msg:
                    message = nuclear_msg
                else:
                    if escalation == "gentle":
                        prompt = "It's been a couple days — gentle check in. How are things?"
                    elif escalation == "direct":
                        prompt = "Haven't heard from you in 3 days. What's going on? How are your goals?"
                    else:  # nuclear without custom message
                        prompt = "Hey. It's been almost 4 days. Are you okay? I'm here if you need anything."
                    
                    response = model.generate_content(prompt)
                    message = response.text.strip()
                
                # Send and log
                send_sms(phone, message)
                log_message(user_id, message)
                
                scheduler_logger.info(
                    f"Sent {escalation} silence message to {user.get('name', phone)} "
                    f"({int(hours_silent)}h silent)"
                )
            
            except Exception as e:
                scheduler_logger.error(f"Failed silence detector for user {user_id}: {str(e)}")
                continue
    
    except Exception as e:
        scheduler_logger.error(f"Critical error in silence detector: {str(e)}", exc_info=True)



def start_scheduler() -> None:
    """
    Register jobs and start the background scheduler. Called from main.py startup.
    
    Jobs registered:
    1. send_scheduled_checkins — every minute
    2. send_motivation_messages — every 30 minutes
    3. send_scheduled_reminders — every minute
    4. send_deadline_checkins — every morning 6am UTC
    5. analyze_message_patterns — every night midnight UTC
    6. send_proactive_pattern_messages — every morning 7am UTC
    7. send_milestone_celebrations — every night 9pm UTC
    8. send_weekly_reflections — every Sunday 7pm UTC
    9. detect_silent_users — every 6 hours
    """
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

    # Reminders checker: every minute
    scheduler.add_job(
        send_scheduled_reminders,
        CronTrigger(minute="*"),
        id="reminders",
        replace_existing=True,
        misfire_grace_time=30,
    )

    # Deadline check-ins: every morning 6am UTC
    scheduler.add_job(
        send_deadline_checkins,
        CronTrigger(hour="6", minute="0", timezone="UTC"),
        id="deadline_checkins",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Pattern analyzer: every night midnight UTC
    scheduler.add_job(
        analyze_message_patterns,
        CronTrigger(hour="0", minute="0", timezone="UTC"),
        id="pattern_analyzer",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Proactive pattern messages: every morning 7am UTC
    scheduler.add_job(
        send_proactive_pattern_messages,
        CronTrigger(hour="7", minute="0", timezone="UTC"),
        id="proactive_patterns",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Milestone celebrations: every night 9pm UTC
    scheduler.add_job(
        send_milestone_celebrations,
        CronTrigger(hour="21", minute="0", timezone="UTC"),
        id="milestones",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Weekly reflections: every Sunday 7pm UTC
    scheduler.add_job(
        send_weekly_reflections,
        CronTrigger(day_of_week="6", hour="19", minute="0", timezone="UTC"),
        id="weekly_reflections",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Silence detector: every 6 hours
    scheduler.add_job(
        detect_silent_users,
        CronTrigger(hour="0,6,12,18", minute="0", timezone="UTC"),
        id="silence_detector",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.start()
    logger.info(
        "APScheduler started with 9 jobs: "
        "checkins (1m), motivation (30m), reminders (1m), "
        "deadlines (6am UTC), patterns (midnight UTC), "
        "proactive (7am UTC), milestones (9pm UTC), "
        "reflections (Sun 7pm UTC), silence (6h)"
    )
    scheduler_logger.info("Scheduler initialized with all 9 jobs")


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
