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

load_dotenv()

logger = logging.getLogger("onboarding")

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DAY_MAP = {
    "mon": "Monday", "monday": "Monday",
    "tue": "Tuesday", "tues": "Tuesday", "tuesday": "Tuesday",
    "wed": "Wednesday", "wednesday": "Wednesday",
    "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday", "thursday": "Thursday",
    "fri": "Friday", "friday": "Friday",
    "sat": "Saturday", "saturday": "Saturday",
    "sun": "Sunday", "sunday": "Sunday",
}

_ALL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_ALL_DAY_PHRASES = {"every day", "everyday", "all", "all week", "all 7 days", "7 days", "daily", "all days"}


def _parse_days(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.lower().strip())
    if normalized in _ALL_DAY_PHRASES:
        return _ALL_DAYS[:]
    tokens = re.split(r"[\s,/]+", normalized)
    days = []
    for token in tokens:
        token = token.strip(".")
        if token in _DAY_MAP:
            day = _DAY_MAP[token]
            if day not in days:
                days.append(day)
    return days


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
        _send_sms(to_number, "Now tell me — what are your goals? Be specific.")

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
        f"Extract fitness or accountability goals from this message. "
        f"Return a JSON array of goal name strings only. "
        f"Each goal should be a short activity label (e.g. 'Run 3 miles', 'Do 100 pushups'). "
        f"Message: {message_body}\n"
        f"Return valid JSON array only. No markdown."
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
        logger.exception("[onboarding] goal extraction failed — using raw message as single goal")
        return [message_body[:80]]


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
        goal_names = await _extract_goals(message_body)
        if not goal_names:
            return "I didn't catch any goals. Tell me what you want to work on — be specific."

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

        # Store loop state in user_context (delete old entry first, then insert)
        context_payload = json.dumps({"goals": inserted_goals, "current_index": 0})
        supabase.table("user_context").delete().eq("user_id", user_id).eq("type", "onboarding_goals").execute()
        supabase.table("user_context").insert({
            "user_id": user_id,
            "type": "onboarding_goals",
            "description": context_payload,
            "expires_at": _expires_24h(),
        }).execute()

        supabase.table("users").update({"onboarding_step": 3}).eq("id", user_id).execute()
        logger.info(f"[onboarding] user={user_id} goals saved: {[g['name'] for g in inserted_goals]}, step→3")

        first = inserted_goals[0]["name"]
        return f"Got it. For '{first}' — which days? (e.g. Mon, Wed, Fri)"

    # ── Step 3 — schedule loop ────────────────────────────────────────────
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
            # Context lost — skip ahead
            supabase.table("users").update({"onboarding_step": 4}).eq("id", user_id).execute()
            return "You're all set. Your coach will be in touch."

        ctx = json.loads(ctx_res.data[0]["description"])
        goals = ctx["goals"]
        idx = ctx["current_index"]
        current_goal = goals[idx]

        days = _parse_days(message_body)
        if days:
            supabase.table("goals").update({"days": days}).eq("id", current_goal["id"]).execute()
            logger.info(f"[onboarding] user={user_id} goal '{current_goal['name']}' days={days}")
        else:
            return f"I didn't catch the days. For '{current_goal['name']}' — which days? (e.g. Mon, Wed, Fri)"

        next_idx = idx + 1
        if next_idx < len(goals):
            # More goals to schedule
            ctx["current_index"] = next_idx
            supabase.table("user_context").update({
                "description": json.dumps(ctx),
                "expires_at": _expires_24h(),
            }).eq("user_id", user_id).eq("type", "onboarding_goals").execute()

            next_goal = goals[next_idx]["name"]
            return f"Got it. For '{next_goal}' — which days?"
        else:
            # All goals scheduled
            supabase.table("user_context").delete().eq("user_id", user_id).eq("type", "onboarding_goals").execute()
            supabase.table("users").update({"onboarding_step": 5}).eq("id", user_id).execute()
            logger.info(f"[onboarding] user={user_id} all goals scheduled, step→5")

            # Fetch coach name for confirmation
            coach_res = supabase.table("coach_settings").select("coach_name").eq("user_id", user_id).eq("is_active", True).limit(1).execute()
            coach_name = coach_res.data[0]["coach_name"] if coach_res.data else "Your coach"
            return f"Locked in. {coach_name} will text you on schedule. Let's get to work."

    # ── Step 5+ — fall through to normal pipeline ─────────────────────────
    return None
