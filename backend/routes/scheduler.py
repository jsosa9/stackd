import logging
import os
import asyncio
import json
import random
from datetime import datetime, timedelta, date
from fastapi import APIRouter
from supabase import create_client
from services.messaging import send_reply_with_delay
from services.billing import is_billable, generate_trial_warning_sms, generate_trial_upsell_sms, _get_checkout_url
from routes.ai import HUMAN_BEHAVIOR_RULES
from services.message_router import _strip_markdown
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
from dotenv import load_dotenv
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


# BackgroundScheduler runs in a separate thread — no async event loop required.
scheduler = BackgroundScheduler()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def send_sms(to: str, body: str) -> None:
    """Send an SMS via Blooio with a human-like typing delay scaled to message length."""
    send_reply_with_delay(to, _strip_markdown(body))
    logger.info(f"Sent SMS to {to}")


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
    Runs every minute. At each user's configured check-in time, sends one
    context-aware message based on where they are in their day:
      - before: forward-looking, primes them for what's ahead
      - during: references what's confirmed/missed, asks about pending
      - after:  wraps the day, closes with tomorrow question
      - no goals / no times: simple open check-in
    """
    from routes.ai import generate_contextual_checkin

    logger.debug("Running scheduled check-in job")

    schedules_res = supabase.table("schedule").select(
        "user_id, checkin_time, timezone, users(id, phone, name, paused, sms_consent_given_at)"
    ).execute()

    for sched in schedules_res.data or []:
        user = sched.get("users")
        if not user or not user.get("phone"):
            continue
        if user.get("paused"):
            continue
        if not run_async(is_billable(user.get("id", ""))):
            continue
        if not user.get("sms_consent_given_at"):
            logger.info(f"Skipping check-in — user {user.get('id')} has no consent record")
            continue

        user_id = user["id"]
        tz_name = sched.get("timezone", "America/New_York")

        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            logger.warning(f"Unknown timezone '{tz_name}' for user {user_id}")
            continue

        local_now = datetime.now(tz)

        checkin_time_str = sched.get("checkin_time", "08:00")
        try:
            checkin_hour, checkin_minute = map(int, checkin_time_str.split(":"))
        except Exception:
            logger.warning(f"Invalid checkin_time '{checkin_time_str}' for user {user_id}")
            continue

        if local_now.hour != checkin_hour or local_now.minute != checkin_minute:
            continue

        day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        today_lower = day_names[local_now.weekday()]
        today_abbr  = local_now.strftime("%a")   # "Mon", "Tue", …
        today_str   = local_now.strftime("%Y-%m-%d")
        checkin_minutes = checkin_hour * 60 + checkin_minute

        # ── Goals scheduled today ────────────────────────────────────────────
        goals_res = supabase.table("goals").select("id, activity, days, times_per_day").eq("user_id", user_id).execute()
        todays_goals = [
            g for g in (goals_res.data or [])
            if today_lower in (g.get("days") or [])
        ]

        if not todays_goals:
            logger.info(f"[checkin] no goals today for user={user_id} — sending open check-in")
            try:
                text = run_async(generate_contextual_checkin(
                    user_id, "no_goals", [], [], [],
                    checkin_time_display=local_now.strftime("%-I:%M %p"),
                ))
                send_sms(user["phone"], text)
                log_message(user_id, text)
            except Exception:
                logger.exception(f"[checkin] failed open check-in for user={user_id}")
            continue

        # ── Extract goal times for today ─────────────────────────────────────
        goal_times = []  # list of (activity, "HH:MM")
        for g in todays_goals:
            tpd = g.get("times_per_day") or {}
            day_sched = tpd.get(today_abbr) or {}
            for t in (day_sched.get("times") or []):
                goal_times.append((g["activity"], t))

        # ── Determine before / during / after state ──────────────────────────
        if not goal_times:
            checkin_state = "no_times"
        else:
            goal_minutes = [
                int(t.split(":")[0]) * 60 + int(t.split(":")[1])
                for _, t in goal_times
            ]
            if checkin_minutes < min(goal_minutes):
                checkin_state = "before"
            elif checkin_minutes >= max(goal_minutes):
                checkin_state = "after"
            else:
                checkin_state = "during"

        # ── Activity notification states for today ───────────────────────────
        notif_res = supabase.table("activity_notifications").select(
            "activity, state, scheduled_time"
        ).eq("user_id", user_id).gte("scheduled_time", today_str).lt(
            "scheduled_time", today_str + "T23:59:59"
        ).execute()
        notification_states = notif_res.data or []

        # ── Recent user_context (wins/struggles from today) ──────────────────
        since_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        ctx_res = supabase.table("user_context").select("type, description").eq("user_id", user_id).in_(
            "type", ["win", "struggle", "mood", "energy"]
        ).gte("created_at", since_midnight).order("created_at", desc=True).limit(5).execute()
        user_context_today = ctx_res.data or []

        logger.info(f"[checkin] user={user_id} state={checkin_state} goals={len(todays_goals)} notifs={len(notification_states)}")

        try:
            text = run_async(generate_contextual_checkin(
                user_id,
                checkin_state,
                goal_times,
                notification_states,
                user_context_today,
                checkin_time_display=local_now.strftime("%-I:%M %p"),
            ))
            send_sms(user["phone"], text)
            log_message(user_id, text)
        except Exception:
            logger.exception(f"[checkin] failed for user={user_id} state={checkin_state}")


# ---------------------------------------------------------------------------
# Scheduler job 2: Motivation messages (runs every 30 minutes)
# ---------------------------------------------------------------------------

def send_motivation_messages() -> None:
    """
    Runs every 30 minutes. For each user with motivation_enabled = true:
      - Checks if the current local time falls within their motivation_window
      - Checks if enough time has passed since their last motivation text
        based on motivation_frequency (e.g. "Once a day" = 22h gap minimum)
      - Calls deliver_motivation_text() if all checks pass (Quotable API + goal context + dedup)
      - Sends via Blooio and logs to messages table
    """
    from routes.ai import deliver_motivation_text

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
        "users(id, phone, name, paused, sms_consent_given_at)"
    ).eq("motivation_enabled", True).execute()

    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)

    for sched in schedules_res.data or []:
        user = sched.get("users")
        if not user or not user.get("phone"):
            continue
        if user.get("paused"):
            continue
        if not run_async(is_billable(user.get("id", ""))):
            continue
        if not user.get("sms_consent_given_at"):
            logger.info(f"Skipping motivation — user {user.get('id')} has no consent record")
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

        # Skip motivation if user has no goals scheduled today (rest day)
        today_name = local_now.strftime("%A").lower()
        goals_res = supabase.table("goals").select("days").eq("user_id", user_id).execute()
        active_today = any(
            today_name in (g.get("days") or [])
            for g in (goals_res.data or [])
        )
        if not active_today and goals_res.data:
            logger.info(f"[motivation] rest day for user={user_id} ({today_name}), skipping")
            continue

        logger.info(f"Sending motivation to {user.get('name', user_id)}")

        try:
            text = run_async(deliver_motivation_text(user_id))
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
    - Send reminder_message via Blooio
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
            .select("*, users(id, phone, name, paused, sms_consent_given_at)")
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
            if user.get("paused"):
                scheduler_logger.info(f"Skipping reminder {reminder_id} — user is paused")
                continue
            if not run_async(is_billable(user.get("id", ""))):
                scheduler_logger.info(f"Skipping reminder {reminder_id} — user not billable")
                continue
            if not user.get("sms_consent_given_at"):
                scheduler_logger.info(f"Skipping reminder {reminder_id} — user has no consent record")
                continue
            
            try:
                # Generate reminder in coach voice
                coach_res = (
                    supabase.table("coach_settings")
                    .select("generated_system_prompt")
                    .eq("user_id", user.get("id"))
                    .eq("is_active", True)
                    .limit(1)
                    .execute()
                )
                system_prompt = coach_res.data[0].get("generated_system_prompt") if coach_res.data else ""
                raw_reminder = reminder["reminder_message"] or reminder.get("description") or "reminder"
                try:
                    model = genai.GenerativeModel(
                        model_name="gemini-2.5-flash-lite",
                        system_instruction=f"{system_prompt}\n\n{HUMAN_BEHAVIOR_RULES}",
                    )
                    resp = model.generate_content(
                        f"Send a short reminder in your voice: {raw_reminder}. SMS only. 1-2 sentences max."
                    )
                    body = resp.text.strip()
                except Exception as ai_err:
                    scheduler_logger.warning(f"Reminder voice gen failed for {reminder_id}: {ai_err} — using raw")
                    body = raw_reminder

                send_sms(phone, body)

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
    - Send via Blooio
    - Log to scheduler.log
    
    Error handling: One deadline failing doesn't block others.
    """
    from routes.ai import get_active_context
    
    scheduler_logger.debug("Running deadline check-ins job")
    
    try:
        # Query all active deadlines
        deadlines_res = (
            supabase.table("deadlines")
            .select("*, users(id, phone, name, paused, sms_consent_given_at), coach_settings(generated_system_prompt)")
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
            if user.get("paused"):
                scheduler_logger.info(f"Skipping deadline check-in — user {user_id} is paused")
                continue
            if not run_async(is_billable(user_id)):
                scheduler_logger.info(f"Skipping deadline check-in — user {user_id} not billable")
                continue
            if not user.get("sms_consent_given_at"):
                scheduler_logger.info(f"Skipping deadline check-in — user {user_id} has no consent record")
                continue
            
            try:
                # Calculate days remaining
                if not deadline.get("deadline_date"):
                    continue
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
                    model_name="gemini-2.5-flash-lite",
                    system_instruction=f"{system_prompt}\n\n{HUMAN_BEHAVIOR_RULES}",
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
    patterns_logger.debug("Running pattern analysis job")

    try:
        # Fetch all users
        users_res = supabase.table("users").select("id").execute()

        scheduler_logger.info(f"Analyzing patterns for {len(users_res.data or [])} users")

        _pattern_model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")

        for user in (users_res.data or []):
            user_id = user["id"]
            if not run_async(is_billable(user_id)):
                continue

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
                
                # Ask Gemini to detect patterns
                _pattern_prompt = f"""{message_text}

Analyze this message history and detect behavioral patterns. Return a JSON object with this structure:
{{
    "quiet_days": ["Mon", "Fri"],
    "strong_days": ["Wed", "Thu"],
    "best_time": "afternoon",
    "pattern_notes": "user struggles on Mondays, strong Wednesday mornings"
}}

Only return JSON, no other text."""
                _pattern_resp = _pattern_model.generate_content(_pattern_prompt)

                try:
                    pattern_data = json.loads(_pattern_resp.text.strip())
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
            .select("*, users(id, phone, name, paused, sms_consent_given_at, schedule(timezone))")
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
            if user.get("paused"):
                scheduler_logger.info(f"Skipping proactive message — user {user_id} is paused")
                continue
            if not run_async(is_billable(user_id)):
                scheduler_logger.info(f"Skipping proactive message — user {user_id} not billable")
                continue
            if not user.get("sms_consent_given_at"):
                scheduler_logger.info(f"Skipping proactive message — user {user_id} has no consent record")
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
                    model_name="gemini-2.5-flash-lite",
                    system_instruction=f"{system_prompt}\n\n{HUMAN_BEHAVIOR_RULES}",
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
    - Send via Blooio
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
            .select("*, users(id, phone, name, paused, sms_consent_given_at), goals(activity), coach_settings(generated_system_prompt)")
            .execute()
        )

        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        for streak in (streaks_res.data or []):
            current = streak.get("current_streak", 0)

            # Streak momentum reminder: fire at milestone - 1 (e.g. day 6 before day 7 milestone)
            pre_milestones = {m - 1: m for m in milestones}
            if current in pre_milestones:
                upcoming = pre_milestones[current]
                user = streak.get("users", {})
                goal = streak.get("goals", {})
                user_id = user.get("id")
                phone = user.get("phone")
                if user_id and phone and not user.get("paused") and user.get("sms_consent_given_at"):
                    try:
                        already = supabase.table("user_context").select("id") \
                            .eq("user_id", user_id) \
                            .eq("type", f"streak_pre_{upcoming}_{streak.get('goal_id')}") \
                            .execute()
                        if not already.data:
                            coach = streak.get("coach_settings", {})
                            system_prompt = coach.get("generated_system_prompt", "")
                            model = genai.GenerativeModel(
                                model_name="gemini-2.5-flash-lite",
                                system_instruction=f"{system_prompt}\n\n{HUMAN_BEHAVIOR_RULES}",
                            )
                            goal_name = goal.get("activity", "your goal")
                            resp = model.generate_content(
                                f"The user is one day away from a {upcoming}-day streak on {goal_name}. "
                                f"Send a short push — build the anticipation, make them feel it. "
                                f"1-2 sentences max, in your voice."
                            )
                            msg = _strip_markdown(resp.text.strip())
                            supabase.table("user_context").insert({
                                "user_id": user_id,
                                "type": f"streak_pre_{upcoming}_{streak.get('goal_id')}",
                                "description": f"Pre-milestone reminder for {upcoming}-day streak",
                                "expires_at": (datetime.utcnow() + timedelta(days=7)).isoformat(),
                            }).execute()
                            send_sms(phone, msg)
                            log_message(user_id, msg)
                            streaks_logger.info(f"Sent pre-milestone reminder ({upcoming}-1) to {user.get('name', phone)}")
                    except Exception as e:
                        streaks_logger.error(f"Failed pre-milestone reminder for user {user_id}: {e}")

            # Check if this is a milestone
            if current not in milestones:
                continue
            
            user = streak.get("users", {})
            goal = streak.get("goals", {})
            user_id = user.get("id")
            phone = user.get("phone")

            if not user_id or not phone:
                continue
            if user.get("paused"):
                streaks_logger.info(f"Skipping milestone — user {user_id} is paused")
                continue
            if not run_async(is_billable(user_id)):
                streaks_logger.info(f"Skipping milestone — user {user_id} not billable")
                continue
            if not user.get("sms_consent_given_at"):
                streaks_logger.info(f"Skipping milestone — user {user_id} has no consent record")
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
                    model_name="gemini-2.5-flash-lite",
                    system_instruction=f"{system_prompt}\n\n{HUMAN_BEHAVIOR_RULES}",
                )
                
                goal_name = goal.get("activity", "your goal")
                
                prompt = (
                    f"The user just hit a {current}-day streak on {goal_name}! "
                    f"This is a BIG milestone. Send a special, celebratory message that feels different "
                    f"from a normal check-in. Make it more significant and congratulatory. "
                    f"Make it personal and real, not generic. 2-3 sentences max."
                )
                
                response = model.generate_content(prompt)
                message = _strip_markdown(response.text.strip())

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
            .eq("paused", False)
            .not_.is_("sms_consent_given_at", "null")
            .execute()
        )

        scheduler_logger.info(f"Sending Sunday reflections to {len(users_res.data or [])} users")

        for user in (users_res.data or []):
            user_id = user["id"]
            phone = user.get("phone")

            if not phone:
                continue
            if not run_async(is_billable(user_id)):
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
                    model_name="gemini-2.5-flash-lite",
                    system_instruction=f"{system_prompt}\n\n{HUMAN_BEHAVIOR_RULES}",
                )
                
                prompt = (
                    f"It's Sunday evening. Time for a weekly reflection. "
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
        users_res = supabase.table("users").select("id, phone, name, paused, sms_consent_given_at").execute()

        for user in (users_res.data or []):
            user_id = user["id"]
            phone = user.get("phone")

            if user.get("paused"):
                continue
            if not run_async(is_billable(user_id)):
                continue
            if not user.get("sms_consent_given_at"):
                continue
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
                
                # Determine escalation level based on hours silent
                # Each level fires exactly once (deduped via user_context)
                if hours_silent < 48:
                    continue
                elif hours_silent < 72:
                    escalation = "gentle"
                elif hours_silent < 96:
                    escalation = "direct"
                elif hours_silent < 120:
                    escalation = "nuclear"
                elif hours_silent < 168:
                    escalation = "day5"
                else:
                    escalation = "day7"

                # Dedup: only send each escalation level once per user
                already_sent = supabase.table("user_context").select("id") \
                    .eq("user_id", user_id) \
                    .eq("type", f"silent_{escalation}") \
                    .execute()
                if already_sent.data:
                    continue

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

                model = genai.GenerativeModel(
                    model_name="gemini-2.5-flash-lite",
                    system_instruction=f"{system_prompt}\n\n{HUMAN_BEHAVIOR_RULES}",
                )

                if escalation == "gentle":
                    prompt = "It's been a couple days. Check in on them. Short, in your voice."
                elif escalation == "direct":
                    prompt = "3 days of silence. Go direct. Call them out, ask what's going on with their goals. In your voice."
                elif escalation == "nuclear":
                    if nuclear_msg:
                        prompt = f"4+ days of silence. Use this as your starting point and say it in your voice: {nuclear_msg}"
                    else:
                        prompt = "Almost 4 days of silence. This is your last real attempt. Make it count. In your voice."
                elif escalation == "day5":
                    prompt = "5 days of silence. No pressure tone — just let them know you're still here if they want to come back. One sentence, in your voice."
                else:  # day7
                    prompt = "7 days of silence. Final message. Ask if they want to pause or if they're done. No guilt, just real. In your voice."

                response = model.generate_content(prompt)
                message = _strip_markdown(response.text.strip())

                # Record this escalation so it only fires once
                supabase.table("user_context").insert({
                    "user_id": user_id,
                    "type": f"silent_{escalation}",
                    "description": f"Sent {escalation} silence message at {int(hours_silent)}h",
                    "expires_at": (now_utc + timedelta(days=30)).isoformat(),
                }).execute()

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



def send_activity_notifications() -> None:
    """
    Runs every minute. For each user's goals with scheduled times today:
    - 30 min before activity: upsert activity_notifications row (SCHEDULED),
      send SMS, mark NOTIFIED
    - Skips if row already exists and state != SCHEDULED (dedup via state table)
    - Skips activities with empty times array
    """
    scheduler_logger.debug("Running activity notification job")

    try:
        schedules_res = supabase.table("schedule").select(
            "user_id, timezone, users(id, phone, name, paused, sms_consent_given_at)"
        ).execute()

        for sched in schedules_res.data or []:
            user = sched.get("users")
            if not user or not user.get("phone"):
                continue
            if user.get("paused"):
                continue
            if not run_async(is_billable(user.get("id", ""))):
                continue
            if not user.get("sms_consent_given_at"):
                continue

            user_id = user["id"]

            # Skip if user is actively texting (inbound message in last 5 minutes)
            try:
                five_min_ago = (datetime.now(pytz.UTC) - timedelta(minutes=5)).isoformat()
                recent_inbound = (
                    supabase.table("messages")
                    .select("id")
                    .eq("user_id", user_id)
                    .eq("direction", "inbound")
                    .gte("created_at", five_min_ago)
                    .limit(1)
                    .execute()
                )
                if recent_inbound.data:
                    scheduler_logger.debug(f"Skipping activity notification for {user_id} — active conversation")
                    continue
            except Exception as e:
                scheduler_logger.warning(f"Could not check recent inbound for {user_id}: {e}")

            tz_name = sched.get("timezone") or "America/Los_Angeles"

            try:
                tz = pytz.timezone(tz_name)
            except Exception:
                scheduler_logger.warning(f"Unknown timezone '{tz_name}' for user {user_id}, using LA")
                tz = pytz.timezone("America/Los_Angeles")

            local_now = datetime.now(tz)
            day_abbrs = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            today_abbr = day_abbrs[local_now.weekday()]
            today_date = local_now.strftime("%Y-%m-%d")

            try:
                goals_res = (
                    supabase.table("goals")
                    .select("activity, times_per_day")
                    .eq("user_id", user_id)
                    .execute()
                )
            except Exception as e:
                scheduler_logger.error(f"Failed to fetch goals for user {user_id}: {e}")
                continue

            for goal in goals_res.data or []:
                activity = goal["activity"]
                times_per_day = goal.get("times_per_day") or {}
                today_sched = times_per_day.get(today_abbr, {})
                times = today_sched.get("times", []) if isinstance(today_sched, dict) else []

                if not times:
                    continue

                for time_str in times:
                    try:
                        sched_h, sched_m = map(int, time_str.split(":"))
                    except (ValueError, AttributeError):
                        scheduler_logger.warning(f"Invalid time '{time_str}' for {activity}, user {user_id}")
                        continue

                    # ── 30-min pre-notification ───────────────────────────
                    trigger_total = (sched_h * 60 + sched_m - 30 + 1440) % 1440
                    trigger_h = trigger_total // 60
                    trigger_m = trigger_total % 60
                    is_30min = local_now.hour == trigger_h and local_now.minute == trigger_m
                    # ── T=0 start message ─────────────────────────────────
                    is_start = local_now.hour == sched_h and local_now.minute == sched_m

                    if not is_30min and not is_start:
                        continue

                    period = "AM" if sched_h < 12 else "PM"
                    hour_12 = sched_h % 12 or 12
                    time_12h = f"{hour_12}:{str(sched_m).zfill(2)} {period}"
                    name = user.get("name") or "there"
                    now_utc = datetime.now(pytz.UTC).isoformat()

                    try:
                        existing = (
                            supabase.table("activity_notifications")
                            .select("id, state")
                            .eq("user_id", user_id)
                            .eq("activity", activity)
                            .eq("scheduled_date", today_date)
                            .eq("scheduled_time", time_str)
                            .execute()
                        )
                        existing_state = existing.data[0]["state"] if existing.data else None
                        notif_id = existing.data[0]["id"] if existing.data else None

                        if is_30min:
                            # Skip if already past SCHEDULED state (dedup)
                            if existing_state and existing_state != "SCHEDULED":
                                scheduler_logger.debug(
                                    f"30-min already fired state={existing_state} "
                                    f"for {activity}, user {user_id} — skipping"
                                )
                            else:
                                if not notif_id:
                                    ins = supabase.table("activity_notifications").insert({
                                        "user_id": user_id,
                                        "activity": activity,
                                        "scheduled_date": today_date,
                                        "scheduled_time": time_str,
                                        "state": "SCHEDULED",
                                    }).execute()
                                    notif_id = ins.data[0]["id"]

                                try:
                                    from routes.ai import generate_activity_notification_text
                                    body = run_async(generate_activity_notification_text(user_id, activity, time_12h))
                                except Exception as ai_err:
                                    scheduler_logger.error(f"AI pre-notification failed for {user_id}/{activity}: {ai_err}")
                                    body = f"{activity} at {time_12h}. You in? Reply YES, NO, or RESCHEDULE."

                                send_sms(user["phone"], body)
                                log_message(user_id, body)
                                supabase.table("activity_notifications").update({
                                    "state": "NOTIFIED",
                                    "notified_at": now_utc,
                                    "updated_at": now_utc,
                                }).eq("id", notif_id).execute()
                                scheduler_logger.info(f"30-MIN NOTIFIED: {name} / {activity} at {time_12h}")

                        if is_start:
                            # Only send start message if user explicitly confirmed
                            if existing_state == "CONFIRMED":
                                try:
                                    from routes.ai import generate_activity_start_text
                                    start_body = run_async(generate_activity_start_text(user_id, activity))
                                except Exception:
                                    start_body = f"It's time. {activity} starts now. Let's go."

                                send_sms(user["phone"], start_body)
                                log_message(user_id, start_body)
                                scheduler_logger.info(f"START NOTIFIED: {name} / {activity} at {time_12h}")

                    except Exception as e:
                        scheduler_logger.error(
                            f"Error processing notification for {activity}, user {user_id}: {e}"
                        )

    except Exception as e:
        scheduler_logger.error(f"Critical error in activity notification job: {e}", exc_info=True)


def check_missed_notifications() -> None:
    """
    Runs every minute. Finds NOTIFIED notifications with no reply after 30 minutes.
    Marks state as MISSED and sends a personality-aware follow-up via Gemini.
    """
    scheduler_logger.debug("Running missed notification checker")

    try:
        from routes.ai import generate_notification_response

        now_utc = datetime.now(pytz.UTC)
        cutoff = (now_utc - timedelta(minutes=30)).isoformat()

        missed_res = (
            supabase.table("activity_notifications")
            .select(
                "id, activity, scheduled_time, user_id, "
                "users!inner(id, phone, name, paused, sms_consent_given_at)"
            )
            .eq("state", "NOTIFIED")
            .lte("notified_at", cutoff)
            .execute()
        )

        for notif in missed_res.data or []:
            user = notif.get("users", {})
            user_id = user.get("id") or notif.get("user_id")

            # Fetch coach separately — no FK between activity_notifications and coach_settings
            coach = {}
            if user_id:
                coach_res = (
                    supabase.table("coach_settings")
                    .select("coach_personality, coach_intensity, generated_system_prompt")
                    .eq("user_id", user_id)
                    .eq("is_active", True)
                    .limit(1)
                    .execute()
                )
                coach = coach_res.data[0] if coach_res.data else {}
            phone = user.get("phone")

            if not user_id or not phone:
                continue
            if user.get("paused"):
                continue
            if not run_async(is_billable(user_id)):
                continue
            if not user.get("sms_consent_given_at"):
                continue

            try:
                now_iso = now_utc.isoformat()

                supabase.table("activity_notifications").update({
                    "state": "MISSED",
                    "updated_at": now_iso,
                }).eq("id", notif["id"]).execute()

                response = run_async(generate_notification_response(
                    state="MISSED",
                    activity=notif["activity"],
                    user_name=user.get("name", "there"),
                    system_prompt=coach.get("generated_system_prompt", ""),
                    coach_personality=coach.get("coach_personality", "hype"),
                    coach_intensity=coach.get("coach_intensity", 3),
                ))

                send_sms(phone, response)
                log_message(user_id, response)

                scheduler_logger.info(
                    f"MISSED: {user.get('name', user_id)} / {notif['activity']}"
                )

            except Exception as e:
                scheduler_logger.error(f"Failed missed notification for user {user_id}: {e}")
                continue

    except Exception as e:
        scheduler_logger.error(f"Critical error in missed notification checker: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Scheduler job 12: Trial warning messages (runs every hour)
# ---------------------------------------------------------------------------

def send_trial_warnings() -> None:
    """
    Runs every hour. Day-based upsell cadence using created_at as reference:
      Days 1-3: no messages
      Day 4 morning (8-10am local): in-character coach upsell with Stripe link
      Day 5 morning (8-10am local): final in-character push if not converted
      Day 6+: plain cutoff message once, then is_billable() blocks access
    """
    scheduler_logger.debug("Running trial warning job")

    try:
        now_utc = datetime.now(pytz.UTC)

        res = (
            supabase.table("users")
            .select("id, phone, created_at, subscription_status, sms_consent_given_at, paused")
            .eq("subscription_status", "trial")
            .eq("paused", False)
            .not_.is_("sms_consent_given_at", "null")
            .execute()
        )

        for user in res.data or []:
            user_id = user["id"]
            phone = user.get("phone")
            if not phone:
                continue

            try:
                created_at = datetime.fromisoformat(user["created_at"].replace("Z", "+00:00"))
                trial_age_hours = (now_utc - created_at).total_seconds() / 3600
                trial_day = int(trial_age_hours // 24) + 1

                if trial_day < 4:
                    continue

                if trial_day >= 6:
                    existing = (
                        supabase.table("user_context")
                        .select("id")
                        .eq("user_id", user_id)
                        .eq("type", "trial_cutoff")
                        .limit(1)
                        .execute()
                    )
                    if existing.data:
                        continue
                    checkout_url = run_async(_get_checkout_url(user_id))
                    coach_res = (
                        supabase.table("coach_settings")
                        .select("generated_system_prompt")
                        .eq("user_id", user_id)
                        .eq("is_active", True)
                        .limit(1)
                        .execute()
                    )
                    system_prompt = coach_res.data[0].get("generated_system_prompt") if coach_res.data else ""
                    try:
                        cutoff_model = genai.GenerativeModel(
                            model_name="gemini-2.5-flash-lite",
                            system_instruction=f"{system_prompt}\n\n{HUMAN_BEHAVIOR_RULES}",
                        )
                        cutoff_resp = cutoff_model.generate_content(
                            f"The user's free trial has ended. Tell them in your voice that coaching stops here unless they sign up. "
                            f"Keep it short and in character. Include this link exactly: {checkout_url}"
                        )
                        msg = cutoff_resp.text.strip()
                    except Exception as ai_err:
                        scheduler_logger.warning(f"Trial cutoff voice gen failed for {user_id}: {ai_err} — using fallback")
                        msg = f"Your free trial has ended. To keep your coach, sign up here: {checkout_url}"
                    send_sms(phone, msg)
                    log_message(user_id, msg)
                    supabase.table("user_context").insert({
                        "user_id": user_id,
                        "type": "trial_cutoff",
                        "description": "sent",
                    }).execute()
                    scheduler_logger.info(f"Sent trial cutoff message to user={user_id}")
                    continue

                # Day 4 or 5 — only fire in morning window (8am-10am local time)
                schedule_res = supabase.table("schedule").select("timezone").eq("user_id", user_id).limit(1).execute()
                tz_str = schedule_res.data[0]["timezone"] if schedule_res.data else "America/New_York"
                try:
                    local_now = now_utc.astimezone(pytz.timezone(tz_str))
                except Exception:
                    local_now = now_utc
                if not (8 <= local_now.hour < 10):
                    continue

                dedup_type = f"trial_day{trial_day}_upsell"
                existing = (
                    supabase.table("user_context")
                    .select("id")
                    .eq("user_id", user_id)
                    .eq("type", dedup_type)
                    .limit(1)
                    .execute()
                )
                if existing.data:
                    continue

                days_active = min(trial_day - 1, 5)
                msg = run_async(generate_trial_upsell_sms(user_id, trial_day, days_active))
                send_sms(phone, msg)
                log_message(user_id, msg)
                supabase.table("user_context").insert({
                    "user_id": user_id,
                    "type": dedup_type,
                    "description": f"sent day {trial_day} upsell",
                }).execute()
                scheduler_logger.info(f"Sent trial day {trial_day} upsell to user={user_id}")

            except Exception as e:
                scheduler_logger.error(f"Trial warning failed for user={user_id}: {e}")
                continue

    except Exception as e:
        scheduler_logger.error(f"Critical error in trial warning job: {e}", exc_info=True)


def send_nightly_summaries() -> None:
    """
    Runs every minute. At 9pm in each user's local timezone, sends one nightly
    recap message in the coach's voice covering what they accomplished today.

    Skips users who had no logged activity or check-in responses today.
    Deduplicates via user_context row (type='nightly_summary_sent', description=YYYY-MM-DD).
    """
    from routes.ai import generate_nightly_summary

    scheduler_logger.debug("Running nightly summary job")

    schedules_res = supabase.table("schedule").select(
        "user_id, timezone, users(id, phone, name, paused, sms_consent_given_at)"
    ).execute()

    SUMMARY_HOUR = 21  # 9pm local

    for sched in schedules_res.data or []:
        user = sched.get("users")
        if not user or not user.get("phone"):
            continue
        if user.get("paused"):
            continue
        if not run_async(is_billable(user.get("id", ""))):
            continue
        if not user.get("sms_consent_given_at"):
            continue

        user_id = user["id"]
        tz_name = sched.get("timezone", "America/New_York")

        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            scheduler_logger.warning(f"[nightly] unknown tz '{tz_name}' for user={user_id}")
            continue

        local_now = datetime.now(tz)
        if local_now.hour != SUMMARY_HOUR or local_now.minute != 0:
            continue

        today_str = local_now.strftime("%Y-%m-%d")

        # Dedup: only one nightly summary per user per day
        already = supabase.table("user_context").select("id").eq("user_id", user_id).eq(
            "type", "nightly_summary_sent"
        ).eq("description", today_str).execute()
        if already.data:
            continue

        # Goal completions today
        completions_res = supabase.table("goal_completions").select(
            "goal_id, goals(activity)"
        ).eq("user_id", user_id).eq("completed_date", today_str).execute()
        completions = [
            {"activity": row["goals"]["activity"]}
            for row in (completions_res.data or [])
            if row.get("goals")
        ]

        # Missed notifications today
        notif_res = supabase.table("activity_notifications").select(
            "activity, state"
        ).eq("user_id", user_id).gte(
            "scheduled_time", today_str
        ).lt("scheduled_time", today_str + "T23:59:59").eq("state", "MISSED").execute()
        missed_goals = [{"activity": n["activity"]} for n in (notif_res.data or [])]

        # user_context from today (wins, struggles, mood, journal notes)
        since_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        ctx_res = supabase.table("user_context").select("type, description").eq(
            "user_id", user_id
        ).in_("type", ["win", "struggle", "mood", "energy", "personal"]).gte(
            "created_at", since_midnight
        ).order("created_at", desc=True).limit(5).execute()
        user_context_today = ctx_res.data or []

        # Skip silent days — nothing logged at all
        if not completions and not missed_goals and not user_context_today:
            scheduler_logger.info(f"[nightly] user={user_id} had no activity today — skipping")
            continue

        scheduler_logger.info(
            f"[nightly] user={user_id} done={len(completions)} missed={len(missed_goals)} ctx={len(user_context_today)}"
        )

        try:
            text = run_async(generate_nightly_summary(
                user_id, completions, missed_goals, user_context_today
            ))
            send_sms(user["phone"], text)
            log_message(user_id, text)
            # Mark sent for dedup
            supabase.table("user_context").insert({
                "user_id":    user_id,
                "type":       "nightly_summary_sent",
                "description": today_str,
                "expires_at": (local_now + timedelta(days=2)).isoformat(),
            }).execute()
        except Exception:
            scheduler_logger.exception(f"[nightly] failed for user={user_id}")


def start_scheduler() -> None:
    """
    Register jobs and start the background scheduler. Called from main.py startup.

    Jobs registered:
    1.  send_scheduled_checkins — every minute
    2.  send_motivation_messages — every 30 minutes
    3.  send_scheduled_reminders — every minute
    4.  send_deadline_checkins — every morning 6am UTC
    5.  analyze_message_patterns — every night midnight UTC
    6.  send_proactive_pattern_messages — every morning 7am UTC
    7.  send_milestone_celebrations — every night 9pm UTC
    8.  send_weekly_reflections — every Sunday 7pm UTC
    9.  detect_silent_users — every 6 hours
    10. send_activity_notifications — every minute (30-min pre-activity SMS)
    11. check_missed_notifications — every minute
    12. send_trial_warnings — every hour (12h and final warning before trial expires)
    13. send_nightly_summaries — every minute (fires at 9pm local per user)
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

    # Activity notifications: every minute (sends 30-min pre-activity SMS)
    scheduler.add_job(
        send_activity_notifications,
        CronTrigger(minute="*"),
        id="activity_notifications",
        replace_existing=True,
        misfire_grace_time=30,
    )

    # Missed notification checker: every minute (marks NOTIFIED→MISSED after 30m of no reply)
    scheduler.add_job(
        check_missed_notifications,
        CronTrigger(minute="*"),
        id="missed_notifications",
        replace_existing=True,
        misfire_grace_time=30,
    )

    # Trial warnings: every hour
    scheduler.add_job(
        send_trial_warnings,
        CronTrigger(minute="0"),
        id="trial_warnings",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Nightly summaries: every minute (fires at 9pm in each user's local timezone)
    scheduler.add_job(
        send_nightly_summaries,
        CronTrigger(minute="*"),
        id="nightly_summaries",
        replace_existing=True,
        misfire_grace_time=30,
    )

    scheduler.start()
    logger.info(
        "APScheduler started with 12 jobs: "
        "checkins (1m), motivation (30m), reminders (1m), "
        "deadlines (6am UTC), patterns (midnight UTC), "
        "proactive (7am UTC), milestones (9pm UTC), "
        "reflections (Sun 7pm UTC), silence (6h), "
        "activity_notifications (1m), missed_notifications (1m), "
        "trial_warnings (1h)"
    )
    scheduler_logger.info("Scheduler initialized with all 12 jobs")


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
    import asyncio
    await asyncio.to_thread(send_scheduled_checkins)
    return {"status": "check-ins triggered"}


@router.post("/trigger-motivation")
async def trigger_motivation():
    """Manually fire the motivation job right now (for testing)."""
    import asyncio
    await asyncio.to_thread(send_motivation_messages)
    return {"status": "motivation messages triggered"}


@router.post("/trigger-activity-notifications")
async def trigger_activity_notifications():
    """Manually fire the activity notification job right now (for testing)."""
    import asyncio
    await asyncio.to_thread(send_activity_notifications)
    return {"status": "activity notifications triggered"}
