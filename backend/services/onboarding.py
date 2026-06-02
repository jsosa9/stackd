"""
onboarding.py — SMS-driven coach onboarding state machine.

State transitions (stored in users.onboarding_step):
  None/unknown → 0  : auto-create user, ask for coach name
  0 → 1             : start persona setup in background, ack
  1 → 2             : (background) persona ready — sends intro + goals preview + timezone ask
  2 → 3             : timezone received → store → ask for goals+schedule
  3 → 4             : extract goals+schedule → insert → send recap
  4 → 5             : recap confirmed → ask check-in preference
  5 → 6             : check-in preference received → finalize
  6+                : return None (fall through to process_inbound_sms)
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import google.generativeai as genai
from dotenv import load_dotenv
from services.messaging import send_reply

from routes.personas import persona_manager
from services.message_router import _generate_voice_reply
from routes.ai import HUMAN_BEHAVIOR_RULES

load_dotenv()

logger = logging.getLogger("onboarding")

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

_ALL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_DAY_ABBRS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_FULL_TO_ABBR = {d.lower(): _DAY_ABBRS[i] for i, d in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
)}

_TZ_MAP = {
    "eastern": "America/New_York",
    "est": "America/New_York",
    "edt": "America/New_York",
    "central": "America/Chicago",
    "cst": "America/Chicago",
    "cdt": "America/Chicago",
    "mountain": "America/Denver",
    "mst": "America/Denver",
    "mdt": "America/Denver",
    "pacific": "America/Los_Angeles",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "alaska": "America/Anchorage",
    "akst": "America/Anchorage",
    "hawaii": "Pacific/Honolulu",
    "hst": "Pacific/Honolulu",
    "gmt": "Europe/London",
    "utc": "UTC",
    "cet": "Europe/Paris",
    "ist": "Asia/Kolkata",
    "jst": "Asia/Tokyo",
    "aest": "Australia/Sydney",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_time(time_str: str) -> str | None:
    """Convert loose time strings like '8pm', '8:00 PM', 'morning' to HH:MM (24h).
    Returns None for vague strings that can't map to a clock time."""
    import re as _re
    s = time_str.strip().lower()
    natural = {
        "morning": "07:00", "afternoon": "14:00", "evening": "19:00",
        "night": "21:00", "noon": "12:00", "midnight": "00:00",
    }
    if s in natural:
        return natural[s]
    m = _re.search(r'(\d{1,2}):(\d{2})\s*(am|pm)?', s)
    if m:
        h, mn, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
        if ampm == "pm" and h != 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mn:02d}"
    m2 = _re.search(r'(\d{1,2})\s*(am|pm)', s)
    if m2:
        h, ampm = int(m2.group(1)), m2.group(2)
        if ampm == "pm" and h != 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return f"{h:02d}:00"
    return None


def _time_to_display(hhmm: str) -> str:
    """Convert '18:00' to '6pm', '09:30' to '9:30am'."""
    try:
        h, m = map(int, hhmm.split(":"))
        ampm = "am" if h < 12 else "pm"
        h12 = h % 12 or 12
        return f"{h12}:{m:02d}{ampm}" if m else f"{h12}{ampm}"
    except Exception:
        return hhmm


def _build_goal_payload(item: dict) -> dict:
    """Build a goals table update payload from an extracted {activity, days, time} item."""
    payload: dict = {}
    days = item.get("days") or []
    if days:
        days_lower = [d.lower() for d in days]
        payload["days"] = days_lower
        time_str = item.get("time")
        if time_str:
            times_map = {
                _FULL_TO_ABBR[d]: {"times": [time_str]}
                for d in days_lower if d in _FULL_TO_ABBR
            }
            payload["times_per_day"] = times_map
    elif item.get("time"):
        # time but no days — store nothing yet, will be filled from recap correction
        pass
    return payload


async def _parse_yes_no(message_body: str) -> bool | None:
    """Return True for yes, False for no, None if unclear."""
    prompt = (
        f"Does this message mean yes or no? Message: \"{message_body}\"\n"
        f"Reply with exactly one word: YES, NO, or UNCLEAR."
    )
    try:
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        raw = model.generate_content(prompt).text.strip().upper()
        if raw.startswith("YES"):
            return True
        if raw.startswith("NO"):
            return False
        return None
    except Exception:
        logger.exception("[onboarding] yes/no parse failed")
        return None


async def _parse_timezone(raw: str) -> str:
    """Convert a timezone name/abbreviation to an IANA string. Lookup table first, Gemini fallback."""
    key = raw.strip().lower()
    if key in _TZ_MAP:
        return _TZ_MAP[key]
    # Gemini fallback for anything not in the table (e.g. "Berlin time", "Mexico City")
    prompt = (
        f"What is the IANA timezone string for: {raw}? "
        f"Return only the timezone string, e.g. America/Los_Angeles or America/New_York. No explanation."
    )
    try:
        import pytz
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        result = model.generate_content(prompt).text.strip().strip('"').strip("'")
        pytz.timezone(result)  # validate
        return result
    except Exception:
        logger.warning(f"[onboarding] could not resolve timezone for '{raw}', defaulting to America/New_York")
        return "America/New_York"


async def _extract_goals_and_schedule(message_body: str) -> list[dict] | None:
    """
    Single Gemini call returning [{activity, days, time}].
    days is [] if not mentioned. time is None if not mentioned.
    Returns None on extraction failure.
    """
    all_days_str = str(_ALL_DAYS)
    prompt = (
        f"The user described their habits and schedule. Extract each activity with its days and time.\n"
        f"Message: \"{message_body}\"\n\n"
        f"Rules:\n"
        f"- activity: short name for the habit (use the user's own words)\n"
        f"- days: array of day names from {all_days_str}. Empty array [] if not mentioned.\n"
        f"  'every day', 'daily', 'every night', 'nightly' = all 7 days.\n"
        f"  'weekdays' = Monday through Friday.\n"
        f"- time: clock time string like '6:00 PM' or '18:00', or null if not mentioned.\n\n"
        f"Return only a JSON array. No markdown. Example:\n"
        f'[{{"activity": "gym", "days": ["Monday","Wednesday","Friday"], "time": "18:00"}}, '
        f'{{"activity": "reading", "days": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"], "time": null}}]'
    )
    try:
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        raw = model.generate_content(prompt).text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        parsed = json.loads(raw)
        if not isinstance(parsed, list) or not parsed:
            return None
        result = []
        for item in parsed:
            activity = (item.get("activity") or "").strip()
            if not activity:
                continue
            days = [d for d in (item.get("days") or []) if d in _ALL_DAYS]
            time_str = item.get("time")
            normalized = _normalize_time(time_str) if time_str else None
            result.append({"activity": activity, "days": days, "time": normalized})
        return result if result else None
    except Exception:
        logger.exception("[onboarding] combined goal+schedule extraction failed")
        return None


async def _coach_voice(system_prompt: str, instruction: str) -> str:
    """Generate a short in-character coach message for the given instruction."""
    try:
        model = genai.GenerativeModel(
            "gemini-2.5-flash-lite",
            system_instruction=(
                f"{system_prompt}\n\n{HUMAN_BEHAVIOR_RULES}\n\n"
                "Keep it to one SMS message. No em dashes. No bullet points. No markdown."
            ),
        )
        return model.generate_content(instruction).text.strip()
    except Exception:
        logger.exception("[onboarding] coach voice generation failed")
        return instruction


async def _build_recap(user_id: str, coach_prompt: str, supabase, pending_items: list[dict] | None = None) -> str:
    """Build and return an in-character recap confirmation message from current goals in DB.

    pending_items: extracted [{activity, days, time}] from the last parse pass, used to show
    times that were given but couldn't be stored (e.g. time with no days yet).
    """
    goals = (
        supabase.table("goals")
        .select("activity, days, times_per_day")
        .eq("user_id", user_id)
        .execute()
        .data or []
    )

    if not goals:
        return await _coach_voice(
            coach_prompt,
            "Something went wrong saving goals. Ask the user to list their habits and schedule again. One sentence."
        )

    # Build a map of pending times for goals where time was given but days were not
    pending_times: dict[str, str] = {}
    if pending_items:
        for item in pending_items:
            if item.get("time") and not item.get("days"):
                pending_times[item["activity"].lower()] = item["time"]

    lines = []
    for g in goals:
        activity = g["activity"]
        days = g.get("days") or []
        times_per_day = g.get("times_per_day") or {}

        days_str = " ".join(d.capitalize() for d in days) if days else "no days set"

        time_str = None
        for day_data in times_per_day.values():
            t = (day_data.get("times") or [None])[0]
            if t:
                time_str = t
                break

        # Overlay pending time if DB has no time yet
        if not time_str:
            time_str = pending_times.get(activity.lower())

        if time_str:
            lines.append(f"{activity} {days_str} at {_time_to_display(time_str)}")
        else:
            lines.append(f"{activity} {days_str}")

    goals_summary = ". ".join(lines)
    recap_prompt = (
        f"Confirm with the user what you have. Here is exactly what to confirm: {goals_summary}. "
        "Goals with no days set won't send reminders until days are added but they can say yes now and just tell you later to update it. "
        "Ask them to say yes if that is right or tell you what to fix. "
        "No em dashes, no bullets, no markdown. Stay in your voice. One SMS."
    )
    return await _coach_voice(coach_prompt, recap_prompt)


def _send_sms(to: str, body: str) -> None:
    send_reply(to, body)


def _expires_24h() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()


# ---------------------------------------------------------------------------
# Background task — persona setup + 3-message intro
# ---------------------------------------------------------------------------

async def setup_and_intro(user_id: str, to_number: str, name: str, supabase) -> None:
    """
    Run after webhook returns. Fetches or creates the persona, inserts
    coach_settings, sends 3 messages (intro, goals preview, city ask), advances step to 2.
    """
    try:
        persona = await persona_manager.fetch_persona_by_name(name)
        if persona is None:
            logger.info(f"[onboarding] creating new persona for '{name}'")
            persona = await persona_manager.create_persona(name)

        system_prompt = persona_manager.get_system_prompt(persona)

        supabase.table("coach_settings").update({"is_active": False}).eq("user_id", user_id).execute()
        coach_insert = supabase.table("coach_settings").insert({
            "user_id": user_id,
            "personality_id": persona.personality_id,
            "coach_name": persona.name,
            "sounds_like": persona.name,
            "generated_system_prompt": system_prompt,
            "is_active": True,
            "coach_setup_type": "celebrity",
        }).execute()
        logger.info(f"[onboarding] coach_settings created for user={user_id} coach={persona.name}")

        # Message 1: in-character intro (one sentence)
        intro = await _coach_voice(
            system_prompt,
            "Introduce yourself to your new athlete. One sentence only. Stay completely in character."
        )
        _send_sms(to_number, intro)

        # Message 2: goals preview — persona voice opener + hardcoded example
        goals_opener = await _coach_voice(
            system_prompt,
            "Ask what habits the user wants to track and when. One short sentence in your voice. Do not include an example."
        )
        goals_msg = f"{goals_opener} Something like gym Monday Wednesday Friday at 6pm or reading every night at 9pm."
        _send_sms(to_number, goals_msg)

        # Message 3: timezone ask — in character opener + hardcoded options
        tz_opener = await _coach_voice(
            system_prompt,
            "Ask what time zone they are in so you know when to text them. One short sentence in your voice. Do not list examples."
        )
        tz_msg = f"{tz_opener} Like Eastern, Central, Mountain, or Pacific."
        _send_sms(to_number, tz_msg)

        supabase.table("users").update({"onboarding_step": 2}).eq("id", user_id).execute()
        logger.info(f"[onboarding] user={user_id} advanced to step 2")

    except Exception:
        logger.exception(f"[onboarding] setup_and_intro failed for user={user_id}")
        _send_sms(to_number, "Something went wrong setting up your coach. Text us again to retry.")


# ---------------------------------------------------------------------------
# Onboarding finalization
# ---------------------------------------------------------------------------

async def _finalize_onboarding(
    user_id: str,
    to_number: str,
    coach_prompt: str,
    checkin_time: str | None,
    supabase,
) -> str:
    """Advance to step 6, start trial clock, create schedule row, return wrap-up message."""
    try:
        supabase.table("users").update({"onboarding_step": 6}).eq("id", user_id).execute()
    except Exception:
        logger.exception(f"[onboarding] failed to advance step to 6 for user={user_id}")

    try:
        supabase.rpc("set_trial_end", {"p_user_id": user_id}).execute()
    except Exception:
        try:
            trial_end = (datetime.now(timezone.utc) + timedelta(hours=120)).isoformat()
            supabase.table("users").update({
                "trial_ends_at": trial_end,
                "subscription_status": "trial",
            }).eq("id", user_id).execute()
            logger.info(f"[onboarding] trial clock started for user={user_id}, ends={trial_end}")
        except Exception:
            logger.exception(f"[onboarding] failed to set trial_ends_at for user={user_id}")

    # Read timezone stored during step 2
    tz_res = (
        supabase.table("user_context")
        .select("description")
        .eq("user_id", user_id)
        .eq("type", "onboarding_timezone")
        .limit(1)
        .execute()
    )
    tz = tz_res.data[0]["description"] if tz_res.data else "America/New_York"

    try:
        existing = supabase.table("schedule").select("user_id").eq("user_id", user_id).limit(1).execute()
        schedule_data = {
            "timezone": tz,
            "motivation_enabled": True,
            "motivation_frequency": "Once a day",
            "motivation_window_start": "08:00",
            "motivation_window_end": "21:00",
            "motivation_styles": ["Hype & pump-up"],
        }
        if checkin_time:
            schedule_data["checkin_time"] = checkin_time

        if not existing.data:
            schedule_data["user_id"] = user_id
            if "checkin_time" not in schedule_data:
                schedule_data["checkin_time"] = "08:00"
            supabase.table("schedule").insert(schedule_data).execute()
            logger.info(f"[onboarding] schedule row created for user={user_id} tz={tz} checkin={checkin_time}")
        else:
            supabase.table("schedule").update(schedule_data).eq("user_id", user_id).execute()
    except Exception:
        logger.exception(f"[onboarding] failed to create/update schedule row for user={user_id}")

    # Clean up onboarding context rows
    for ctx_type in ("onboarding_timezone", "onboarding_goals", "onboarding_checkin"):
        try:
            supabase.table("user_context").delete().eq("user_id", user_id).eq("type", ctx_type).execute()
        except Exception:
            pass

    # Rebuild system prompt with user's actual goals/schedule in background
    async def _rebuild():
        try:
            from routes.ai import build_coach_personality
            await build_coach_personality(user_id)
            logger.info(f"[onboarding] system prompt rebuilt with user goals for user={user_id}")
        except Exception:
            logger.warning(f"[onboarding] system prompt rebuild failed for user={user_id}", exc_info=True)

    import asyncio
    asyncio.create_task(_rebuild())

    wrap_up = await _coach_voice(
        coach_prompt,
        "All goals are scheduled. Tell the user you will be texting them on schedule and you are ready to get to work. One message, in character."
    )
    return wrap_up


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def handle_onboarding(
    from_number: str,
    message_body: str,
    background_tasks,
    supabase,
    user_data: dict | None,
) -> str | None:
    """
    Returns a reply string if this message was handled by onboarding.
    Returns None to fall through to the normal pipeline.
    """
    step = user_data.get("onboarding_step", 0) if user_data else None

    # ── Step 0 — unknown number: create user ──────────────────────────────
    if user_data is None:
        try:
            placeholder_email = f"sms_{from_number.lstrip('+').replace(' ', '')}@stackd.app"
            auth_res = supabase.auth.admin.create_user({
                "phone": from_number,
                "email": placeholder_email,
                "email_confirm": True,
                "phone_confirm": True,
            })
            auth_uid = auth_res.user.id
            now_iso = datetime.now(timezone.utc).isoformat()
            supabase.table("users").insert({
                "id": auth_uid,
                "email": placeholder_email,
                "phone": from_number,
                "onboarding_step": 0,
                "sms_consent_given_at": now_iso,
                "sms_consent_method": "sms_keyword",
            }).execute()
            logger.info(f"[onboarding] new user created for {from_number} uid={auth_uid}")
        except Exception:
            logger.exception(f"[onboarding] failed to create user for {from_number}")
            return "Something went wrong. Try again in a moment."
        ctia = (
            "stackd: You're signing up for daily AI coaching texts. ~20-30 msgs/month. "
            "Msg&Data rates may apply. Reply STOP to cancel anytime, HELP for info. "
            "stackd.app/help\n\n"
            "Who do you want as your coach? Text any celebrity name."
        )
        return ctia

    user_id = user_data["id"]

    # ── Step 0 — coach name received ──────────────────────────────────────
    if step == 0:
        name = message_body.strip()
        background_tasks.add_task(setup_and_intro, user_id, from_number, name, supabase)
        supabase.table("users").update({"onboarding_step": 1}).eq("id", user_id).execute()
        logger.info(f"[onboarding] user={user_id} chose coach '{name}', step→1")
        return f"On it. Setting up {name} as your coach..."

    # ── Step 1 — persona still being set up ───────────────────────────────
    if step == 1:
        coach_res = supabase.table("coach_settings").select("coach_name").eq("user_id", user_id).eq("is_active", True).limit(1).execute()
        if not coach_res.data:
            return "Still setting things up, give me one more second."
        return "Almost there — check your messages."

    # ── Step 2 — city received ────────────────────────────────────────────
    if step == 2:
        coach_res = supabase.table("coach_settings").select("generated_system_prompt").eq("user_id", user_id).eq("is_active", True).limit(1).execute()
        coach_prompt = coach_res.data[0].get("generated_system_prompt", "") if coach_res.data else ""

        tz_input = message_body.strip()
        tz = await _parse_timezone(tz_input)

        supabase.table("user_context").delete().eq("user_id", user_id).eq("type", "onboarding_timezone").execute()
        supabase.table("user_context").insert({
            "user_id": user_id,
            "type": "onboarding_timezone",
            "description": tz,
            "expires_at": _expires_24h(),
        }).execute()

        supabase.table("users").update({"onboarding_step": 3}).eq("id", user_id).execute()
        logger.info(f"[onboarding] user={user_id} tz_input='{tz_input}' tz='{tz}' step→3")

        # Ask for goals+schedule
        goals_ask = await _coach_voice(
            coach_prompt,
            "Tell the user you have their timezone and now ask what habits they want to work on and when. One sentence in your voice."
        )
        goals_msg = f"{goals_ask} Like gym Monday Wednesday Friday at 6pm or reading every night at 9pm."
        return goals_msg

    # ── Step 3 — goals+schedule received ──────────────────────────────────
    if step == 3:
        coach_res = supabase.table("coach_settings").select("generated_system_prompt").eq("user_id", user_id).eq("is_active", True).limit(1).execute()
        coach_prompt = coach_res.data[0].get("generated_system_prompt", "") if coach_res.data else ""

        # Check retry count
        ctx_res = (
            supabase.table("user_context")
            .select("id, metadata")
            .eq("user_id", user_id)
            .eq("type", "onboarding_goals")
            .limit(1)
            .execute()
        )
        retry_count = (ctx_res.data[0].get("metadata") or {}).get("retry_count", 0) if ctx_res.data else 0
        ctx_id = ctx_res.data[0]["id"] if ctx_res.data else None

        extracted = await _extract_goals_and_schedule(message_body)

        if extracted is None:
            # Extraction failed
            if retry_count >= 2:
                # Force advance to step 4 with whatever is in DB
                logger.warning(f"[onboarding] user={user_id} step 3 max retries hit, advancing to step 4")
                if ctx_id:
                    supabase.table("user_context").delete().eq("id", ctx_id).execute()
                supabase.table("users").update({"onboarding_step": 4}).eq("id", user_id).execute()
                return await _build_recap(user_id, coach_prompt, supabase, pending_items=None)

            new_retry = retry_count + 1
            if ctx_id:
                supabase.table("user_context").update({"metadata": {"retry_count": new_retry}}).eq("id", ctx_id).execute()
            else:
                supabase.table("user_context").insert({
                    "user_id": user_id,
                    "type": "onboarding_goals",
                    "description": "retry",
                    "metadata": {"retry_count": new_retry},
                    "expires_at": _expires_24h(),
                }).execute()

            return await _coach_voice(
                coach_prompt,
                "The user's reply was unclear. Ask them to list their activities and schedule again. Give the example: gym Monday Wednesday Friday at 6pm or reading every night at 9pm. One sentence in your voice."
            )

        # Insert goals into DB
        for item in extracted:
            try:
                # Check if this goal already exists for this user
                existing = (
                    supabase.table("goals")
                    .select("id")
                    .eq("user_id", user_id)
                    .ilike("activity", item["activity"])
                    .limit(1)
                    .execute()
                )
                payload = _build_goal_payload(item)
                if existing.data:
                    supabase.table("goals").update(payload).eq("id", existing.data[0]["id"]).execute()
                else:
                    supabase.table("goals").insert({
                        "user_id": user_id,
                        "activity": item["activity"],
                        "category": "fitness",
                        "days": payload.get("days", []),
                        "times_per_day": payload.get("times_per_day"),
                    }).execute()
                logger.info(f"[onboarding] user={user_id} goal '{item['activity']}' days={item['days']} time={item['time']}")
            except Exception:
                logger.exception(f"[onboarding] failed to insert/update goal '{item['activity']}' for user={user_id}")

        # Clean up retry context
        if ctx_id:
            supabase.table("user_context").delete().eq("id", ctx_id).execute()

        supabase.table("users").update({"onboarding_step": 4}).eq("id", user_id).execute()
        logger.info(f"[onboarding] user={user_id} goals inserted, step→4")

        return await _build_recap(user_id, coach_prompt, supabase, pending_items=extracted)

    # ── Step 4 — recap confirmation ───────────────────────────────────────
    if step == 4:
        coach_res = supabase.table("coach_settings").select("generated_system_prompt").eq("user_id", user_id).eq("is_active", True).limit(1).execute()
        coach_prompt = coach_res.data[0].get("generated_system_prompt", "") if coach_res.data else ""

        ctx_res = (
            supabase.table("user_context")
            .select("id, metadata")
            .eq("user_id", user_id)
            .eq("type", "onboarding_recap")
            .limit(1)
            .execute()
        )
        correction_count = (ctx_res.data[0].get("metadata") or {}).get("correction_count", 0) if ctx_res.data else 0
        ctx_id = ctx_res.data[0]["id"] if ctx_res.data else None

        answered_yes = await _parse_yes_no(message_body)

        def _advance_to_checkin():
            if ctx_id:
                supabase.table("user_context").delete().eq("id", ctx_id).execute()
            supabase.table("users").update({"onboarding_step": 5}).eq("id", user_id).execute()

        if answered_yes is True or correction_count >= 1:
            _advance_to_checkin()
            logger.info(f"[onboarding] user={user_id} recap confirmed, step→5")
            checkin_q = await _coach_voice(
                coach_prompt,
                "Ask the user if they want a daily check-in text from you. If yes, ask what time. Give an example like 8am or 9pm. Tell them to reply no to skip. No em dashes, no bullets, no markdown. One SMS."
            )
            return checkin_q

        # Try to parse as a correction
        corrected = await _extract_goals_and_schedule(message_body)
        if corrected:
            existing_goals = (
                supabase.table("goals")
                .select("id, activity, days")
                .eq("user_id", user_id)
                .execute()
                .data or []
            )
            for item in corrected:
                # Find matching goal by name (case-insensitive partial match)
                matched = None
                for g in existing_goals:
                    if (item["activity"].lower() in g["activity"].lower() or
                            g["activity"].lower() in item["activity"].lower()):
                        matched = g
                        break
                if not matched:
                    continue

                update_payload: dict = {}
                if item["days"]:
                    days_lower = [d.lower() for d in item["days"]]
                    update_payload["days"] = days_lower
                    if item["time"]:
                        times_map = {
                            _FULL_TO_ABBR[d]: {"times": [item["time"]]}
                            for d in days_lower if d in _FULL_TO_ABBR
                        }
                        update_payload["times_per_day"] = times_map
                elif item["time"]:
                    # Only time given — update times for already-stored days
                    existing_days = matched.get("days") or []
                    if existing_days:
                        times_map = {
                            _FULL_TO_ABBR[d]: {"times": [item["time"]]}
                            for d in existing_days if d in _FULL_TO_ABBR
                        }
                        update_payload["times_per_day"] = times_map

                if update_payload:
                    supabase.table("goals").update(update_payload).eq("id", matched["id"]).execute()
                    logger.info(f"[onboarding] step 4 correction applied to goal='{matched['activity']}' payload={update_payload}")

        # Store correction count and re-send recap
        new_count = correction_count + 1
        if ctx_id:
            supabase.table("user_context").update({"metadata": {"correction_count": new_count}}).eq("id", ctx_id).execute()
        else:
            supabase.table("user_context").insert({
                "user_id": user_id,
                "type": "onboarding_recap",
                "description": "awaiting_confirmation",
                "metadata": {"correction_count": new_count},
                "expires_at": _expires_24h(),
            }).execute()

        return await _build_recap(user_id, coach_prompt, supabase)

    # ── Step 5 — check-in preference ──────────────────────────────────────
    if step == 5:
        coach_res = supabase.table("coach_settings").select("generated_system_prompt").eq("user_id", user_id).eq("is_active", True).limit(1).execute()
        coach_prompt = coach_res.data[0].get("generated_system_prompt", "") if coach_res.data else ""

        ctx_res = (
            supabase.table("user_context")
            .select("id, description")
            .eq("user_id", user_id)
            .eq("type", "onboarding_checkin")
            .limit(1)
            .execute()
        )
        awaiting_time = ctx_res.data and ctx_res.data[0].get("description") == "awaiting_time"
        ctx_id = ctx_res.data[0]["id"] if ctx_res.data else None

        if awaiting_time:
            # Second pass — try to get the time from their reply
            time_raw = _normalize_time(message_body.strip())
            if ctx_id:
                supabase.table("user_context").delete().eq("id", ctx_id).execute()
            checkin_time = time_raw or "08:00"
            return await _finalize_onboarding(user_id, from_number, coach_prompt, checkin_time, supabase)

        answered_yes = await _parse_yes_no(message_body)

        if answered_yes is False:
            # No check-in
            if ctx_id:
                supabase.table("user_context").delete().eq("id", ctx_id).execute()
            return await _finalize_onboarding(user_id, from_number, coach_prompt, None, supabase)

        # Yes — try to parse a time from the same message
        time_raw = _normalize_time(message_body.strip())
        if time_raw:
            if ctx_id:
                supabase.table("user_context").delete().eq("id", ctx_id).execute()
            return await _finalize_onboarding(user_id, from_number, coach_prompt, time_raw, supabase)

        # Yes but no time — ask once more
        if ctx_id:
            supabase.table("user_context").update({"description": "awaiting_time"}).eq("id", ctx_id).execute()
        else:
            supabase.table("user_context").insert({
                "user_id": user_id,
                "type": "onboarding_checkin",
                "description": "awaiting_time",
                "expires_at": _expires_24h(),
            }).execute()

        return await _coach_voice(
            coach_prompt,
            "They want a check-in but did not give a time. Ask what time they want the daily check-in. Give an example like 8am or 9pm. One sentence in your voice."
        )

    # ── Step 6+ — fall through to normal pipeline ─────────────────────────
    return None
