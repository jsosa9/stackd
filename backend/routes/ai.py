import logging
import os
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from supabase import create_client
from anthropic import Anthropic
import google.generativeai as genai
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

anthropic = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class BuildCoachRequest(BaseModel):
    user_id: str

class CheckinRequest(BaseModel):
    user_id: str
    goal: str  # activity name, e.g. "Running"

class MotivationRequest(BaseModel):
    user_id: str


# ---------------------------------------------------------------------------
# Core AI functions
# ---------------------------------------------------------------------------

async def build_coach_personality(user_id: str) -> str:
    """
    Fetches all user data from Supabase, sends it to Claude Haiku, and gets back
    a fully personalized SMS coach system prompt. Saves the result to the
    coach_settings table under generated_system_prompt. Returns the prompt.

    This runs once after onboarding and whenever the user updates coach settings.
    """
    logger.info(f"Building coach personality for user {user_id}")

    # Fetch user profile
    user_res = supabase.table("users").select("*").eq("id", user_id).execute()
    if not user_res.data:
        raise HTTPException(status_code=404, detail="User not found")
    user = user_res.data[0]

    # Fetch coach settings
    coach_res = supabase.table("coach_settings").select("*").eq("user_id", user_id).execute()
    coach = coach_res.data[0] if coach_res.data else {}

    # Fetch all goals
    goals_res = supabase.table("goals").select("*").eq("user_id", user_id).execute()
    goals = goals_res.data or []

    # Fetch schedule preferences
    sched_res = supabase.table("schedule").select("*").eq("user_id", user_id).execute()
    sched = sched_res.data[0] if sched_res.data else {}

    # Build a comprehensive data summary to hand to Claude Haiku
    goals_text = "\n".join(
        f"  - {g.get('activity', 'Unknown')} "
        f"(days: {', '.join(g.get('days', [])) or 'flexible'}, "
        f"category: {g.get('category', '?')})"
        for g in goals
    ) or "  - No specific goals set yet"

    avoid_topics = ", ".join(sched.get("avoid_topics") or []) or "None specified"
    motivation_styles = ", ".join(sched.get("motivation_styles") or []) or "General"

    haiku_prompt = f"""You are designing a personalized AI SMS accountability coach.
Based on the user data below, write a detailed system prompt that will make the coach feel
completely tailored to this specific person. The system prompt should cover:

1. The coach's name, avatar/emoji, and core personality
2. Tone, style, emoji usage, and message length
3. How the coach handles missed days, celebrates wins, and opens messages
4. The user's goals and what the coach tracks
5. Topics to never mention and any personal boundaries
6. References to the user's background and what success looks like to them
7. Any special rules or signature phrases
8. A short "backstory" paragraph that gives the coach a sense of identity

--- USER DATA ---
Name: {user.get('name', 'User')}
Age: {user.get('age', 'unknown')}
Occupation: {user.get('occupation', 'unknown')}
Phone: {user.get('phone', 'unknown')}

Coach name: {coach.get('coach_name', 'Alex')}
Coach avatar: {coach.get('coach_avatar', '🦁')}
Personality preset: {coach.get('coach_personality', 'hype')}
Talk style: {', '.join(coach.get('coach_talk_style') or [])}
Emoji usage: {coach.get('coach_emoji_usage', 'Some')}
Message length: {coach.get('coach_message_length', 'Medium')}
Miss behavior: {coach.get('coach_miss_behavior', 'Check in kindly')}
Opener style: {coach.get('coach_opener_style', 'Direct check-in')}
Intensity: {coach.get('coach_intensity', 3)}/5

Custom build details:
  Sounds like: {coach.get('custom_coach_sounds_like', 'N/A')}
  Personality description: {coach.get('custom_coach_personality_desc', 'N/A')}
  Celebration style: {coach.get('custom_coach_celebration_style', 'N/A')}
  Missed day response: {coach.get('custom_coach_missed_day_response', 'N/A')}
  Favorite phrase: {coach.get('custom_coach_favorite_phrase', 'N/A')}
  Phrases to avoid: {coach.get('custom_coach_avoid_phrases', 'N/A')}
  Tone: {coach.get('custom_coach_tone', 'N/A')}
  Special rules: {coach.get('custom_coach_special_rules', 'N/A')}

Obstacles the user faces: {', '.join(user.get('obstacles') or [])}
Experience level: {user.get('experience', 'unknown')}
Success vision: {user.get('success_vision', 'Not specified')}

Goals being tracked:
{goals_text}

Topics to never bring up: {avoid_topics}
Motivation styles preferred: {motivation_styles}
Check-in time: {sched.get('checkin_hour', 8)}:{str(sched.get('checkin_minute', 0)).zfill(2)} {sched.get('checkin_ampm', 'AM')}
Timezone: {sched.get('timezone', 'America/New_York')}
--- END USER DATA ---

Write the system prompt now. Start directly — no preamble.
The prompt should be written in second person to the coach (e.g. "You are Alex...").
Keep it under 800 words. Make it specific, vivid, and actionable."""

    response = anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1200,
        messages=[{"role": "user", "content": haiku_prompt}],
    )

    generated_prompt = response.content[0].text
    logger.info(f"Generated system prompt for user {user_id} ({len(generated_prompt)} chars)")

    # Save the generated system prompt to coach_settings
    supabase.table("coach_settings").upsert({
        "user_id": user_id,
        "generated_system_prompt": generated_prompt,
    }).execute()

    return generated_prompt


async def generate_motivation_text(user_id: str) -> str:
    """
    Fetches the user's generated_system_prompt and motivation style preferences,
    then calls Gemini Flash 1.5 to generate a single short motivational text
    in the coach's voice matching their chosen styles. Returns the text.
    """
    logger.info(f"Generating motivation text for user {user_id}")

    # Fetch the saved system prompt
    coach_res = supabase.table("coach_settings").select("generated_system_prompt, coach_name").eq("user_id", user_id).execute()
    if not coach_res.data or not coach_res.data[0].get("generated_system_prompt"):
        # Fallback: build it first
        system_prompt = await build_coach_personality(user_id)
    else:
        system_prompt = coach_res.data[0]["generated_system_prompt"]

    # Fetch motivation style preferences
    sched_res = supabase.table("schedule").select("motivation_styles, motivation_frequency").eq("user_id", user_id).execute()
    sched = sched_res.data[0] if sched_res.data else {}
    styles = ", ".join(sched.get("motivation_styles") or []) or "general motivation"

    # Call Gemini Flash 1.5
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=system_prompt,
    )

    prompt = (
        f"Send a single motivational text right now. "
        f"Style it using these approaches: {styles}. "
        f"Keep it short — 1-2 sentences max. SMS-friendly. No hashtags."
    )

    response = model.generate_content(prompt)
    text = response.text.strip()
    logger.info(f"Generated motivation text for user {user_id}: {text[:60]}...")
    return text


async def generate_checkin_text(user_id: str, goal: str) -> str:
    """
    Fetches the user's generated_system_prompt and the last 10 messages from Supabase,
    then calls Gemini Flash 1.5 with full context to generate a check-in message
    for the specific goal. Returns the text.
    """
    logger.info(f"Generating check-in text for user {user_id}, goal: {goal}")

    # Fetch the saved system prompt
    coach_res = supabase.table("coach_settings").select("generated_system_prompt").eq("user_id", user_id).execute()
    if not coach_res.data or not coach_res.data[0].get("generated_system_prompt"):
        system_prompt = await build_coach_personality(user_id)
    else:
        system_prompt = coach_res.data[0]["generated_system_prompt"]

    # Fetch last 10 messages for conversation context
    messages_res = (
        supabase.table("messages")
        .select("direction, body, created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    history = list(reversed(messages_res.data or []))

    # Build Gemini chat history
    gemini_history = []
    for msg in history:
        role = "user" if msg["direction"] == "inbound" else "model"
        gemini_history.append({"role": role, "parts": [msg["body"]]})

    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=system_prompt,
    )

    chat = model.start_chat(history=gemini_history)
    prompt = (
        f"It's check-in time. Send a check-in message asking about today's goal: '{goal}'. "
        f"Be specific to this goal. Keep it SMS-length. Stay in character."
    )
    response = chat.send_message(prompt)
    text = response.text.strip()
    logger.info(f"Generated check-in for user {user_id}, goal '{goal}': {text[:60]}...")
    return text


async def deliver_motivation_text(user_id: str) -> str:
    """
    Fetches a random inspirational quote from the Quotable API, then passes it
    to Gemini Flash 1.5 along with the user's generated system prompt.
    Gemini re-delivers the quote's idea in the coach's own voice — same energy,
    different words. Returns the final SMS text.

    Architecture note: Anthropic (Claude Haiku) is ONLY used in
    build_coach_personality. All text generation uses Gemini Flash.
    """
    logger.info(f"Delivering motivation text (with quote) for user {user_id}")

    # Fetch the saved system prompt
    coach_res = supabase.table("coach_settings").select("generated_system_prompt").eq("user_id", user_id).execute()
    if not coach_res.data or not coach_res.data[0].get("generated_system_prompt"):
        system_prompt = await build_coach_personality(user_id)
    else:
        system_prompt = coach_res.data[0]["generated_system_prompt"]

    # Pull a random quote from Quotable API
    quote_text = ""
    quote_author = ""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://api.quotable.io/quotes/random?limit=1")
            if resp.status_code == 200:
                data = resp.json()
                # API returns a list with one item
                item = data[0] if isinstance(data, list) else data
                quote_text = item.get("content", "")
                quote_author = item.get("author", "")
    except Exception:
        logger.warning("Quotable API unavailable — sending motivation without quote")

    # Ask Gemini to deliver the quote's message in the coach's voice
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=system_prompt,
    )

    if quote_text:
        prompt = (
            f"Deliver this idea to the user as a short SMS motivational message in your own voice: "
            f'"{quote_text}" — {quote_author}. '
            f"Don't quote it verbatim. Translate its energy into your style. "
            f"1-2 sentences max. No hashtags."
        )
    else:
        prompt = (
            "Send a short motivational SMS right now. "
            "1-2 sentences. Stay in character. No hashtags."
        )

    response = model.generate_content(prompt)
    text = response.text.strip()
    logger.info(f"Delivered motivation for user {user_id}: {text[:60]}...")
    return text


# ---------------------------------------------------------------------------
# API routes to trigger these functions manually / via webhook
# ---------------------------------------------------------------------------

@router.post("/build-coach")
async def api_build_coach(req: BuildCoachRequest):
    """Trigger coach personality generation for a user. Called after onboarding."""
    try:
        prompt = await build_coach_personality(req.user_id)
        return {"status": "ok", "preview": prompt[:200] + "..."}
    except Exception as e:
        logger.exception(f"Failed to build coach for {req.user_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/checkin-message")
async def api_checkin_message(req: CheckinRequest):
    """Generate a check-in message for a specific goal."""
    try:
        text = await generate_checkin_text(req.user_id, req.goal)
        return {"message": text}
    except Exception as e:
        logger.exception(f"Failed to generate check-in for {req.user_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/motivation-message")
async def api_motivation_message(req: MotivationRequest):
    """Generate a motivation message for a user."""
    try:
        text = await generate_motivation_text(req.user_id)
        return {"message": text}
    except Exception as e:
        logger.exception(f"Failed to generate motivation for {req.user_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/deliver-motivation")
async def api_deliver_motivation(req: MotivationRequest):
    """Fetch a Quotable quote and deliver it in the coach's voice via Gemini."""
    try:
        text = await deliver_motivation_text(req.user_id)
        return {"message": text}
    except Exception as e:
        logger.exception(f"Failed to deliver motivation for {req.user_id}")
        raise HTTPException(status_code=500, detail=str(e))
