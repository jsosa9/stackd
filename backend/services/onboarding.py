"""
onboarding.py — SMS-driven coach onboarding state machine.

State transitions (stored in users.onboarding_step):
  None/unknown → 0  : auto-create user, ask for coach name
  0 → 1             : start persona setup in background, ack
  1 → 2             : (background) persona ready → in-character intro + ask goals
  2 → 3             : extract goals via Gemini, start schedule loop
  3 loop            : collect days per goal
  3 → 4             : all goals scheduled, send confirmation
  4 → 5             : final handoff message, pipeline takes over
  5+                : return None (fall through to process_inbound_sms)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _extract_days_gemini(text: str, goal_name: str) -> list[str]:
    """Call 1: extract days from natural language reply."""
    prompt = (
        f"The user's message may mention schedules for multiple activities. "
        f"Focus ONLY on the schedule for '{goal_name}'. "
        f"Their message: \"{text}\"\n"
        f"Extract the days of the week specifically for '{goal_name}'. "
        f"IMPORTANT: phrases like 'every day', 'every night', 'each night', 'every morning', 'before bed', 'nightly', 'daily' mean ALL 7 days including weekends — never just weekdays. "
        f"Do not use days mentioned for other activities in the message. "
        f"Return a JSON array of full day names from: {_ALL_DAYS}. No markdown. No explanation."
    )
    try:
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        raw = model.generate_content(prompt).text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        days = json.loads(raw)
        return [d for d in days if d in _ALL_DAYS]
    except Exception:
        logger.exception("[onboarding] day extraction failed")
        return []


async def _score_day_extraction(original_text: str, goal_name: str, extracted_days: list[str]) -> int:
    """Call 2: independently score extraction accuracy 0-100."""
    prompt = (
        f"The user's message may cover schedules for multiple activities. "
        f"Focus ONLY on what they said about '{goal_name}'. "
        f"Their full message: \"{original_text}\"\n"
        f"An AI extracted these days specifically for '{goal_name}': {extracted_days}\n"
        f"Score 0-100 how accurately {extracted_days} matches what the user intends for '{goal_name}'. "
        f"Rules: "
        f"If the user clearly said specific days for '{goal_name}' (e.g. 'monday through friday', 'tuesday and thursday') and those days were extracted, score 90+. "
        f"If the user said 'every day', 'every night', 'every morning', 'before bed', 'daily' for '{goal_name}' and all 7 days were extracted, score 95+. "
        f"Only score below 50 if the days are genuinely wrong or fabricated for this goal. "
        f"Return a single integer only. No explanation."
    )
    try:
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        raw = model.generate_content(prompt).text.strip()
        match = re.search(r'\d+', raw)
        return int(match.group()) if match else 0
    except Exception:
        logger.exception("[onboarding] day scoring failed")
        return 0


_EVERYDAY_PHRASES = [
    "every day", "everyday", "every night", "each night", "every morning",
    "each morning", "before bed", "nightly", "daily", "each day",
]

async def _parse_days_smart(text: str, goal_name: str) -> list[str]:
    """Extract days then independently score — only return if confidence >= 70.
    Bypasses scoring when all 7 days are extracted and an obvious everyday phrase is present."""
    days = await _extract_days_gemini(text, goal_name)
    if not days:
        return []
    text_lower = text.lower()
    if set(days) == set(_ALL_DAYS) and any(p in text_lower for p in _EVERYDAY_PHRASES):
        logger.info(f"[onboarding] everyday phrase detected for '{goal_name}', skipping score days={days}")
        return days
    score = await _score_day_extraction(text, goal_name, days)
    logger.info(f"[onboarding] day extraction score={score} days={days}")
    return days if score >= 70 else []


async def _goals_mentioned_gemini(text: str, goal_names: list[str]) -> list[str]:
    """Return which goal names from the list the user mentioned scheduling in their message."""
    if not goal_names:
        return []
    prompt = (
        f"Message: \"{text}\"\n"
        f"Which of these goals did the user mention they want to schedule? {goal_names}\n"
        f"Return a JSON array of matching goal names exactly as listed. Return [] if none. No markdown."
    )
    try:
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        raw = model.generate_content(prompt).text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        mentioned = json.loads(raw)
        return [g for g in mentioned if g in goal_names]
    except Exception:
        logger.exception("[onboarding] goals mentioned extraction failed")
        return []


async def _extract_time_gemini(text: str, goal_name: str) -> str | None:
    """Extract a time of day from user reply, or None if not mentioned."""
    prompt = (
        f"The user was asked what time they want to do '{goal_name}'. "
        f"Their reply: \"{text}\"\n"
        f"Extract the time if mentioned. Return a short string like '7:00 AM', '6:30 PM', 'morning', or null if no time given. "
        f"Return ONLY the value, no JSON, no explanation."
    )
    try:
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        raw = model.generate_content(prompt).text.strip()
        if raw.lower() in ("null", "none", ""):
            return None
        return raw
    except Exception:
        logger.exception("[onboarding] time extraction failed")
        return None


async def _coach_voice(system_prompt: str, instruction: str) -> str:
    """Generate a short in-character coach message for the given instruction."""
    try:
        model = genai.GenerativeModel(
            "gemini-2.5-flash-lite",
            system_instruction=f"{system_prompt}\n\n{HUMAN_BEHAVIOR_RULES}\n\nKeep it to one SMS message. No hyphens.",
        )
        return model.generate_content(instruction).text.strip()
    except Exception:
        logger.exception("[onboarding] coach voice generation failed")
        return instruction


def _send_sms(to: str, body: str) -> None:
    send_reply(to, body)


def _expires_24h() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()


# ---------------------------------------------------------------------------
# Background task — persona setup + intro
# ---------------------------------------------------------------------------

async def setup_and_intro(user_id: str, to_number: str, name: str, supabase) -> None:
    """
    Run after webhook returns. Fetches or creates the persona, inserts
    coach_settings, sends the in-character intro, then advances step to 2.
    """
    try:
        persona = await persona_manager.fetch_persona_by_name(name)
        if persona is None:
            logger.info(f"[onboarding] creating new persona for '{name}'")
            persona = await persona_manager.create_persona(name)

        system_prompt = persona_manager.get_system_prompt(persona)

        # Insert coach_settings — deactivate any existing rows first, then insert
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
        coach_row = coach_insert.data[0] if coach_insert.data else {
            "user_id": user_id,
            "personality_id": persona.personality_id,
            "coach_name": persona.name,
            "generated_system_prompt": system_prompt,
            "is_active": True,
        }
        logger.info(f"[onboarding] coach_settings created for user={user_id} coach={persona.name}")

        # Generate in-character intro using existing voice function
        intro = await _generate_voice_reply(
            user_id,
            "Introduce yourself to your new athlete. One SMS message only. Be completely in character.",
            coach_row,
            "",
        )

        _send_sms(to_number, intro)
        await asyncio.sleep(2)
        goals_ask = await _coach_voice(system_prompt, "Ask the user what specific activities they want to work on. Tell them to name the actual activity, not the outcome. One message.")
        _send_sms(to_number, goals_ask)

        supabase.table("users").update({"onboarding_step": 2}).eq("id", user_id).execute()
        logger.info(f"[onboarding] user={user_id} advanced to step 2")

    except Exception:
        logger.exception(f"[onboarding] setup_and_intro failed for user={user_id}")
        _send_sms(to_number, "Something went wrong setting up your coach. Text us again to retry.")


# ---------------------------------------------------------------------------
# Goal extraction
# ---------------------------------------------------------------------------

async def _extract_goals(message_body: str) -> list[str]:
    prompt = (
        f"Extract the specific activities or habits the user wants to do from this message. "
        f"Use the user's exact words — do not paraphrase or rename. "
        f"Only include schedulable activities (e.g. 'weight lifting', 'jogging', 'meditation'), not outcomes (e.g. 'build muscle', 'lose weight'). "
        f"Message: {message_body}\n"
        f"Return a valid JSON array of short activity name strings only. No markdown."
    )
    try:
        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1].lstrip("json").strip() if len(parts) > 1 else text
        return json.loads(text)
    except Exception:
        logger.exception("[onboarding] goal extraction failed")
        return []


async def _score_goals(message_body: str, extracted_goals: list[str]) -> int:
    """Independently score 0-100 whether extracted_goals are accurate schedulable activities from message_body."""
    prompt = (
        f"A user listed their goals. Their message: \"{message_body}\"\n"
        f"An AI extracted these as schedulable activities: {extracted_goals}\n"
        f"Score how accurately and completely these represent the specific activities the user wants to schedule. "
        f"100 = exact match, correct activity names, nothing missing or renamed. "
        f"Penalize heavily if: outcomes are included instead of activities (e.g. 'build muscle' instead of 'weight lifting'), "
        f"user's exact words were changed, or real activities were missed. "
        f"Return a single integer 0-100 only. No explanation."
    )
    try:
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        raw = model.generate_content(prompt).text.strip()
        match = re.search(r'\d+', raw)
        return int(match.group()) if match else 0
    except Exception:
        logger.exception("[onboarding] goal scoring failed")
        return 0


async def _extract_goals_smart(message_body: str) -> list[str] | None:
    """Extract goals then independently score — only return if confidence >= 80."""
    goals = await _extract_goals(message_body)
    if not goals:
        return None
    score = await _score_goals(message_body, goals)
    logger.info(f"[onboarding] goal extraction score={score} goals={goals}")
    return goals if score >= 80 else None


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
            # Create an auth.users entry first (required by FK constraint)
            placeholder_email = f"sms_{from_number.lstrip('+').replace(' ', '')}@stackd.app"
            auth_res = supabase.auth.admin.create_user({
                "phone": from_number,
                "email": placeholder_email,
                "email_confirm": True,
                "phone_confirm": True,
            })
            auth_uid = auth_res.user.id
            now_iso = datetime.now(timezone.utc).isoformat()
            # Insert public users row with matching id — record TCPA consent at first contact
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
        # CTIA-required disclosure must be first message sent
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
        # Check if background task already finished (coach_settings exists)
        coach_res = supabase.table("coach_settings").select("coach_name").eq("user_id", user_id).eq("is_active", True).limit(1).execute()
        if not coach_res.data:
            return "Still setting things up, give me one more second."
        # Persona is ready — the background task already sent intro and set step=2
        # If user texted during the window, just nudge them
        return "Almost there — I just sent you your coach's first message."

    # ── Step 2 — goals text received ──────────────────────────────────────
    if step == 2:
        coach_res_2 = supabase.table("coach_settings").select("generated_system_prompt").eq("user_id", user_id).eq("is_active", True).limit(1).execute()
        coach_prompt_2 = coach_res_2.data[0].get("generated_system_prompt", "") if coach_res_2.data else ""

        goal_names = await _extract_goals_smart(message_body)
        if goal_names is None:
            clarify = await _coach_voice(
                coach_prompt_2,
                "The user's goals were unclear or stated as outcomes, not activities. Ask them to name the specific activities they plan to do, not the results they want. Be direct. One message."
            )
            return clarify

        inserted_goals = []
        for goal_name in goal_names:
            try:
                res = supabase.table("goals").insert({
                    "user_id": user_id,
                    "activity": goal_name,
                    "category": "fitness",
                    "days": [],
                }).execute()
                if res.data:
                    inserted_goals.append({"id": res.data[0]["id"], "name": goal_name})
            except Exception:
                logger.exception(f"[onboarding] failed to insert goal '{goal_name}' for user={user_id}")

        if not inserted_goals:
            return "Couldn't save your goals. Try again."

        # Store loop state in user_context using remaining list (order-independent, partial-reply safe)
        remaining_ids = [g["id"] for g in inserted_goals]
        context_payload = json.dumps({"goals": inserted_goals, "remaining": remaining_ids})
        supabase.table("user_context").delete().eq("user_id", user_id).eq("type", "onboarding_goals").execute()
        supabase.table("user_context").insert({
            "user_id": user_id,
            "type": "onboarding_goals",
            "description": context_payload,
            "expires_at": _expires_24h(),
        }).execute()

        supabase.table("users").update({"onboarding_step": 3}).eq("id", user_id).execute()
        logger.info(f"[onboarding] user={user_id} goals saved: {[g['name'] for g in inserted_goals]}, step→3")

        coach_res = supabase.table("coach_settings").select("generated_system_prompt").eq("user_id", user_id).eq("is_active", True).limit(1).execute()
        coach_prompt = coach_res.data[0].get("generated_system_prompt", "") if coach_res.data else ""
        goal_names = [g["name"] for g in inserted_goals]
        goals_list = ", ".join(f"'{n}'" for n in goal_names)
        prompt_msg = await _coach_voice(
            coach_prompt,
            f"Goals are saved: {goals_list}. Ask the user which days and what time they plan to do each one. Write it as a single flowing sentence with no lists, no colons, no line breaks."
        )
        return prompt_msg

    # ── Step 3 — schedule loop (partial-reply safe, days + time per goal) ─
    if step == 3:
        ctx_res = (
            supabase.table("user_context")
            .select("description")
            .eq("user_id", user_id)
            .eq("type", "onboarding_goals")
            .limit(1)
            .execute()
        )
        if not ctx_res.data:
            supabase.table("users").update({"onboarding_step": 5}).eq("id", user_id).execute()
            return "You're all set. Your coach will be in touch."

        ctx = json.loads(ctx_res.data[0]["description"])
        goals = ctx["goals"]
        remaining_ids: list = ctx.get("remaining", [g["id"] for g in goals])

        coach_res = supabase.table("coach_settings").select("coach_name, generated_system_prompt").eq("user_id", user_id).eq("is_active", True).limit(1).execute()
        coach_row = coach_res.data[0] if coach_res.data else {}
        coach_prompt = coach_row.get("generated_system_prompt", "")

        # Build lookup by id and by name for remaining goals
        remaining_goals = [g for g in goals if g["id"] in remaining_ids]
        remaining_names = [g["name"] for g in remaining_goals]

        # Figure out which goals the user addressed in this message
        if len(remaining_goals) == 1:
            mentioned_names = [remaining_goals[0]["name"]]
        else:
            mentioned_names = await _goals_mentioned_gemini(message_body, remaining_names)
            if not mentioned_names:
                # Assume they're replying about the first remaining goal
                mentioned_names = [remaining_goals[0]["name"]]

        scheduled_any = False
        for goal in remaining_goals:
            if goal["name"] not in mentioned_names:
                continue
            days = await _parse_days_smart(message_body, goal["name"])
            if not days:
                continue
            time_str = await _extract_time_gemini(message_body, goal["name"])
            update_payload: dict = {"days": days}
            if time_str:
                update_payload["times_per_day"] = {"default": time_str}
            supabase.table("goals").update(update_payload).eq("id", goal["id"]).execute()
            logger.info(f"[onboarding] user={user_id} goal '{goal['name']}' days={days} time={time_str}")
            remaining_ids.remove(goal["id"])
            scheduled_any = True

        if not scheduled_any:
            # Couldn't parse anything — ask to clarify the first remaining goal
            clarify = await _coach_voice(
                coach_prompt,
                f"Ask the user which days and what time they want to do '{remaining_goals[0]['name']}'. Direct, one sentence."
            )
            return clarify

        if not remaining_ids:
            try:
                supabase.table("user_context").delete().eq("user_id", user_id).eq("type", "onboarding_goals").execute()
                supabase.table("users").update({"onboarding_step": 5}).eq("id", user_id).execute()
            except Exception:
                logger.exception(f"[onboarding] failed to finalize step 5 for user={user_id}")
            logger.info(f"[onboarding] user={user_id} all goals scheduled, step→5")
            wrap_up = await _coach_voice(
                coach_prompt,
                "All goals are scheduled. Tell the user you will be texting them on schedule and you are ready to get to work. One message, in character."
            )
            return wrap_up

        # Some goals still unscheduled — ask about all remaining at once
        ctx["remaining"] = remaining_ids
        supabase.table("user_context").update({
            "description": json.dumps(ctx),
            "expires_at": _expires_24h(),
        }).eq("user_id", user_id).eq("type", "onboarding_goals").execute()

        still_remaining = [g for g in goals if g["id"] in remaining_ids]
        still_names = ", ".join(f"'{g['name']}'" for g in still_remaining)
        next_prompt = await _coach_voice(
            coach_prompt,
            f"Ask the user which days and what time they plan to do each of these: {still_names}. Write it as a single flowing sentence with no lists, no colons, no line breaks."
        )
        return next_prompt

    # ── Step 5+ — fall through to normal pipeline ─────────────────────────
    return None
