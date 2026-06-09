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

import asyncio
import json
import logging
import math
import os
import re
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from logging.handlers import RotatingFileHandler

import pytz
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

_COACHING_PHILOSOPHY = (
    "You are a personal coaching assistant.\n\n"
    "You are NOT responding only to the last message. "
    "You are responding based on the user's current life state.\n\n"
    "You will be given structured context below.\n\n"
    "Your job is to:\n"
    "1. Understand what is happening in the user's life RIGHT NOW\n"
    "2. Identify what is emotionally or practically most important\n"
    "3. Respond naturally like a coach who remembers context without forcing it\n"
    "4. Decide whether to:\n"
    "   - respond directly\n"
    "   - ask a follow-up question\n"
    "   - bring up a relevant past topic\n"
    "   - check in on an unresolved issue\n\n"
    "Rules:\n"
    "- Do NOT list topics unless necessary.\n"
    "- Do NOT say 'I remember you said…'\n"
    "- Integrate memory naturally into conversation.\n"
    "- Be concise and SMS-like.\n"
    "- Be curious, not interrogative.\n"
    "- Only bring up past topics if they are relevant to the user's current message or emotional state."
)

_GOAL_OWNERSHIP_RULES = (
    "GOAL OWNERSHIP\n\n"
    "Only the user can create goals, commitments, habits, standards, or desired outcomes.\n\n"
    "Suggestions from the coach are NOT goals.\n"
    "Recommendations from the coach are NOT commitments.\n"
    "Never treat a coach suggestion as something the user has agreed to or should already be doing.\n\n"
    "If the user rejects a suggestion:\n"
    "- Accept the preference immediately.\n"
    "- Do not argue, justify, or attempt persuasion.\n"
    "- Do not bring it up again unless the user reopens it.\n"
    "- Adapt to what the user prefers instead.\n\n"
    "USER PREFERENCES AND CONSTRAINTS:\n"
    "The life state block may include USER PREFERENCES, USER CONSTRAINTS, BEHAVIORAL CONSTRAINTS, "
    "and REJECTED APPROACHES.\n"
    "These are facts about how this specific person operates. Honor them unconditionally.\n"
    "Never recommend something listed under REJECTED APPROACHES or BEHAVIORAL CONSTRAINTS.\n"
    "Always adapt suggestions to fit USER CONSTRAINTS before offering them.\n\n"
    "PROHIBITED BEHAVIORS — THESE ARE HARD BANS:\n"
    "- Never reframe a rejected suggestion in softer terms.\n"
    "- Never suggest a 'lighter version' or 'easier starting point' of something the user refused.\n"
    "- Never use motivational language to push through a user's rejection ('I know it's hard, but...').\n"
    "- Never treat silence or non-response to a suggestion as implicit acceptance.\n"
    "- Never escalate a coach suggestion into a user goal or commitment.\n"
    "- When the user says no: acknowledge it, then pivot to a completely different direction.\n"
    "- A rejection is final unless the user explicitly and voluntarily reopens it.\n\n"
    "When uncertain about what the user wants, ask. Do not assume."
)

_MEMORY_REASONING_RULES = (
    "MEMORY REASONING RULES\n\n"
    "PRIORITY ORDER — when memory sources conflict, always resolve in this order (1 = highest):\n"
    "  1. USER CONSTRAINTS  — immutable hard limits, never overridden\n"
    "  2. REJECTED APPROACHES — permanent, never reversed unless user explicitly reopens\n"
    "  3. BEHAVIORAL CONSTRAINTS — semantic rules derived from rejections\n"
    "  4. TOPIC STANCES — engagement signals from this user's recent behavior\n"
    "  5. ACTIVE TOPICS — current life state\n"
    "  6. RECENT CONTEXT — last few exchanges\n"
    "  7. COMPRESSED MEMORY — background only, lowest authority\n\n"
    "When a lower-priority source contradicts a higher-priority one:\n"
    "→ follow the higher-priority source unconditionally\n"
    "→ silently reinterpret or ignore the lower-priority signal\n"
    "→ never surface the conflict in your reply\n\n"
    "CONTRADICTION RESOLUTION — resolve silently, never mention to user:\n"
    "- Preference conflict (past like vs current dislike): most recent user-stated constraint wins.\n"
    "- Behavioral conflict (old habit vs current rejection): rejection wins.\n"
    "- Goal conflict (past goal vs current resistance): resistance wins.\n"
    "Never say 'I thought you liked...' or reveal that memories conflict.\n\n"
    "ANTI-REPETITION — before generating any suggestion:\n"
    "Check BEHAVIORAL CONSTRAINTS, REJECTED APPROACHES, and recent conversation context.\n"
    "If a proposed suggestion is semantically similar to a prior rejection:\n"
    "→ do NOT suggest it in any form — original, reframed, gentler, or 'lighter version'\n"
    "→ pivot to a genuinely different direction instead\n"
    "Semantic equivalence counts: 'workout at 5am' and 'early morning training' are the same rejection.\n\n"
    "STANCE-BASED COACHING ADAPTATION:\n"
    "Use the [stance] tag shown next to each ACTIVE TOPIC to calibrate coaching intensity:\n"
    "- [resistant]: acknowledge and offer a different direction only — no motivation, no push\n"
    "- [inconsistent]: focus on simplification; ask what's getting in the way — do not increase demands\n"
    "- [engaged]: deepen coaching — ask about goals, obstacles, progress, next steps\n"
    "- [neutral] or no tag: exploratory questions only — do not assume direction or readiness\n\n"
    "MEMORY AUTHORITY WEIGHTS (internal reasoning only — never mention to user):\n"
    "  constraints        = 10  (non-negotiable)\n"
    "  rejections         = 10  (permanent)\n"
    "  behavioral constraints = 9  (semantic coverage of rejections)\n"
    "  stances            = 8   (strong recent signal)\n"
    "  active topics      = 6   (current focus)\n"
    "  recent context     = 5   (immediate state)\n"
    "  compressed memory  = 2   (background only — never override current signals)\n"
    "Higher weight = higher influence on response strategy."
)

_COACHING_EXPERIENCE_RULES = (
    "COACHING EXPERIENCE RULES\n\n"

    "HOW TO USE THE USER STATE VECTOR:\n"
    "The USER STATE VECTOR tells you HOW to respond, not WHAT to say about it. "
    "Translate it into behavior. Never name these fields to the user. "
    "Never say 'I see you're in [mode]' or 'your resistance is high' or anything that "
    "reveals the internal model you're working from.\n\n"

    "dominant_mode → behavior:\n"
    "  building    → support momentum; give something concrete and specific; "
    "introduce one small challenge if they're clearly ready for it\n"
    "  struggling  → drop your agenda; acknowledge what they said first; "
    "one gentle question; no new asks or suggestions this turn\n"
    "  maintaining → raise the bar; they're stable, push them toward what's next; "
    "don't just check in, challenge them\n"
    "  exploring   → ask before suggesting; follow their lead; "
    "don't lock them into anything; hold opinions loosely\n"
    "  unstable    → presence only; short; one question about right now, not the future; "
    "no plans, no summaries of what you thought you knew\n\n"

    "emotional_state → behavior:\n"
    "  overwhelmed  → be a human first; no goals; respond to the emotion, not the situation\n"
    "  stressed     → acknowledge before redirecting; never lead with accountability\n"
    "  inconsistent → don't interpret; just listen; ask what's actually happening\n"
    "  motivated    → match energy; give them something real to move on right now\n"
    "  neutral      → normal register; read the specific message and match its tone\n\n"

    "Other USV rules:\n"
    "  resistance_level > 0.5 → work only within what the user already accepted; "
    "no new suggestions; if they push back say ok and pivot completely\n"
    "  confidence < 0.6       → treat this message as your primary signal; "
    "don't reference trends or patterns; you don't have enough reliable data yet\n"
    "  volatility = high      → short messages; no multi-part asks; right now only\n"
    "  state_drift > 0.5      → this message matters more than your prior read of them; "
    "don't reference what you thought you knew\n\n"

    "HARD BAN — NEVER SAY OR IMPLY:\n"
    "- 'Based on your memory / history / patterns...' "
    "(unless referencing something explicit from RECENT CONTEXT)\n"
    "- Any version of 'I see you're in [mode / state]'\n"
    "- Any numeric internal metric to the user (resistance, stability, confidence, drift)\n"
    "- 'You've mentioned this N times' → say 'you keep coming back to this' if needed\n"
    "- 'This seems to be an emerging concern' or any system-architecture framing\n"
    "- 'Based on my analysis' — this is a conversation, not a report\n"
    "- 'According to your profile' or 'I've noted that you...'\n"
    "Show your read of the user through what you DO, not what you say about them.\n\n"

    "WHEN TO IGNORE THE USV ENTIRELY:\n"
    "- The user asks a direct factual question → answer it; USV is secondary\n"
    "- The user sends a very short reply ('ok', 'lol', 'yep') → match their brevity; "
    "no state commentary\n"
    "- The user is mid-story or venting → don't interrupt with pattern observations\n"
    "- The user expresses genuine distress → drop everything and be present\n"
    "The message is always primary. Read it before applying anything else."
)

_PERSONALITY_SWAP_RE = re.compile(r'^[A-Z]{4}[0-9]{4}$')

# Regex fast-path for notification replies — handles obvious YES/NO/RESCHEDULE
# without burning a Gemini call. Mirrors the patterns in routes/sms.py.
_NOTIF_YES_RE = re.compile(
    r'\b(yes|yeah|yep|yup|sure|definitely|im in|i\'m in|let\'?s go|ok|okay|yea|doing it|on it|will do)\b',
    re.IGNORECASE,
)
_NOTIF_NO_RE = re.compile(
    r'\b(no|nope|nah|can\'?t|cannot|skip|not today|not gonna|won\'?t|wont|pass|bail|skipping)\b',
    re.IGNORECASE,
)
_NOTIF_RESCHEDULE_RE = re.compile(
    r'\b(reschedule|tomorrow|later|move|shift|delay|another time|push|soon|instead|different time|change)\b'
    r'|\b(\d{1,2}:\d{2})\b'
    r'|\b(\d{1,2}\s*(am|pm))\b'
    r'|can\s+we\s+do\s+\d',
    re.IGNORECASE,
)


def _quick_classify_notif_reply(text: str) -> str | None:
    """Regex fast-path: YES / NO / RESCHEDULE or None for ambiguous messages."""
    if _NOTIF_RESCHEDULE_RE.search(text):   # check first — "yes, push to 3pm" → RESCHEDULE
        return "RESCHEDULE"
    if _NOTIF_YES_RE.search(text):
        return "YES"
    if _NOTIF_NO_RE.search(text):
        return "NO"
    return None

VALID_CATEGORIES = (
    "GOAL", "CREATE_GOAL", "MODIFY_GOAL", "DELETE_GOAL",
    "STATS_QUERY", "MOTIVATION_REQUEST",
    "NUTRITION", "TASK", "JOURNAL", "BET", "GENERAL",
    "COACHING_OPPORTUNITY",
)

_CLASSIFIER_SYSTEM = (
    "You are classifying an SMS message for a coaching app.\n"
    "Return only one word. The category name, nothing else.\n\n"
    "CREATE_GOAL: user is explicitly asking to register, add, or track a NEW habit/goal in the app as an ongoing commitment. "
    "REQUIRED: the message must contain at least one of these exact trigger words or phrases: 'add', 'track', 'create', 'register', 'set up', 'start tracking', 'can we track', 'log this as', 'make this a goal', 'add as a goal'. "
    "Examples that ARE CREATE_GOAL: 'I want to add a goal', 'can we track my running', 'add journaling as a habit to track', 'track this for me'. "
    "Examples that are NOT CREATE_GOAL (classify as COACHING_OPPORTUNITY): 'I want to start meditating', 'I want to begin reading', 'I want to reflect on things', 'maybe I should meditate', 'I want to do yoga', 'I want to wake up at 5am'. "
    "The phrase 'I want to [verb] [activity]' without track/add/register is NEVER CREATE_GOAL — it is COACHING_OPPORTUNITY.\n"
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
    "A message that simply mentions a time or speculation ('maybe I should meditate at 5am', 'I wake up at 5am') is NOT TASK — classify those as COACHING_OPPORTUNITY.\n"
    "JOURNAL: any mention of feelings, tiredness, wanting to quit, "
    "emotions, mood, energy, stress, or mental state. "
    "JOURNAL requires a clear emotional signal — do not use for desire or ambition language.\n"
    "BET: any mention of a challenge, bet, or competing with someone\n"
    "COACHING_OPPORTUNITY: user expresses a desire, ambition, struggle, or area they want to improve "
    "WITHOUT using explicit tracking trigger words (add/track/create/register). "
    "REQUIRED: message contains aspiration, improvement, or struggle language. "
    "Examples that ARE COACHING_OPPORTUNITY: "
    "'I want to lose weight', 'I've been struggling with consistency', "
    "'I need to get back into reading', 'I'm trying to stop procrastinating', "
    "'I want to become a better software engineer', 'I should exercise more', "
    "'I keep failing at waking up early', 'I've been inconsistent with my diet'. "
    "NOT COACHING_OPPORTUNITY: messages with add/track/create/register (→ CREATE_GOAL); "
    "pure emotional state with no improvement framing (→ JOURNAL); requests for advice (→ GENERAL).\n"
    "GENERAL: only if it truly fits none of the above. "
    "Does NOT include desire, ambition, struggle, or improvement language — those are COACHING_OPPORTUNITY."
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
    """Remove markdown bold/italic markers and em/en dashes so they never reach SMS."""
    text = re.sub(r'\*{1,2}([^*\n]+)\*{1,2}', r'\1', text)
    text = re.sub(r'_{1,2}([^_\n]+)_{1,2}', r'\1', text)
    # Replace em dashes (—), en dashes (–), and double-hyphens with a space
    text = re.sub(r'\s*[—–]\s*', ' ', text)
    text = re.sub(r'\s*--\s*', ' ', text)
    text = re.sub(r'  +', ' ', text)   # collapse any double spaces left behind
    return text.strip()


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


_MULTI_CLASSIFIER_SYSTEM = (
    _CLASSIFIER_SYSTEM
    + "\n\n"
    "MULTI-INTENT MODE:\n"
    "Return a JSON array of 1 or 2 intent categories.\n"
    "Return 2 ONLY when the message clearly contains two separate intents — "
    "e.g., checking in on a goal AND sharing an emotional state or struggle.\n"
    "GENERAL is never returned as a second category — only as the primary when nothing else fits.\n"
    "Never duplicate categories.\n"
    "GOAL is never suppressed by COACHING_OPPORTUNITY — if a message contains a concrete action or "
    "completion AND a struggle/desire, return both: [\"GOAL\", \"COACHING_OPPORTUNITY\"].\n"
    "Examples:\n"
    "  'I ran 3 miles but I'm completely burnt out' → [\"GOAL\", \"JOURNAL\"]\n"
    "  'Had a burger for lunch and feeling tired' → [\"NUTRITION\", \"JOURNAL\"]\n"
    "  'Did my workout' → [\"GOAL\"]\n"
    "  'What are my goals?' → [\"STATS_QUERY\"]\n"
    "  'I want to lose weight' → [\"COACHING_OPPORTUNITY\"]\n"
    "  'I've been struggling with consistency' → [\"COACHING_OPPORTUNITY\"]\n"
    "  'I want to become a better software engineer' → [\"COACHING_OPPORTUNITY\"]\n"
    "  'I ran 3 miles but I keep losing motivation' → [\"GOAL\", \"COACHING_OPPORTUNITY\"]\n"
    "  'Finished my workout but I've been inconsistent lately' → [\"GOAL\", \"COACHING_OPPORTUNITY\"]\n"
    "Return ONLY a valid JSON array. No explanation."
)


async def classify_multi(message_body: str) -> list[str]:
    """
    Returns a list of 1–2 intent categories. Falls back to ['GENERAL'] on any error.
    Uses the same category definitions as classify() but allows compound detection.
    """
    try:
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash-lite",
            system_instruction=_MULTI_CLASSIFIER_SYSTEM,
        )
        response = model.generate_content(f"Message: {message_body}")
        raw      = _strip_json_fences(response.text.strip())
        parsed   = json.loads(raw)
        if not isinstance(parsed, list):
            return ["GENERAL"]
        valid = [c.upper() for c in parsed if c.upper() in VALID_CATEGORIES]
        # Deduplicate, cap at 2, ensure at least one result
        seen: list[str] = []
        for c in valid[:2]:
            if c not in seen:
                seen.append(c)
        return seen if seen else ["GENERAL"]
    except Exception:
        logger.exception("[classifier_multi] Gemini call failed — falling back to single classify")
        return [await classify(message_body)]


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

        streak_note = ""
        if completed and goal_id:
            try:
                from routes.ai import update_streak
                streak_data    = await update_streak(user_id, goal_id)
                current        = streak_data.get("current_streak", 0)
                milestone_hit  = streak_data.get("milestone_hit", False)
                milestone_num  = streak_data.get("milestone_number")
                if milestone_hit and milestone_num:
                    streak_note = f" — MILESTONE: just hit a {milestone_num}-day streak"
                elif current > 0:
                    streak_note = f" — streak is now {current} day{'s' if current != 1 else ''}"
                logger.info(f"[goal] streak updated for user={user_id} goal={goal_id}: {current} days milestone={milestone_hit}")
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
            # Mark any NOTIFIED activity notification for this goal today as CONFIRMED
            try:
                goal_activity = next((g["activity"] for g in goals if g["id"] == goal_id), None)
                if goal_activity:
                    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                    supabase.table("activity_notifications") \
                        .update({"state": "CONFIRMED"}) \
                        .eq("user_id", user_id) \
                        .eq("activity", goal_activity) \
                        .eq("state", "NOTIFIED") \
                        .gte("scheduled_time", today_start) \
                        .execute()
                    logger.info(f"[goal] activity_notification confirmed for '{goal_activity}' user={user_id}")
            except Exception:
                logger.exception(f"[goal] failed to confirm activity_notification for user={user_id}")

        ctx_type = "win" if completed else "struggle"
        supabase.table("user_context").insert({
            "user_id":     user_id,
            "type":        ctx_type,
            "description": metric[:500],
            "expires_at":  _hours_from_now(72),
        }).execute()

        logger.info(f"[goal] logged {ctx_type} for user={user_id}: {metric[:60]}{streak_note}")
        return f"Goal logged: {metric}{streak_note}"

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

        # Rebuild persona system prompt in background so it knows about the new goal
        import asyncio as _asyncio
        _asyncio.create_task(_rebuild_coach_prompt(user_id))

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
    """Two-step delete: first call stores pending_delete and asks to confirm; second call does the delete."""
    try:
        # Check for a pending delete awaiting confirmation
        pending_res = supabase.table("user_context").select("id, description").eq("user_id", user_id).eq("type", "pending_delete").limit(1).execute()
        if pending_res.data:
            pending = pending_res.data[0]
            ctx_id = pending["id"]
            stored = pending.get("description", "")  # "goal_id:activity"
            # Parse yes/no from message
            lowered = message_body.strip().lower()
            is_yes = any(w in lowered for w in ["yes", "yeah", "yep", "yup", "do it", "delete it", "confirm"])
            is_no  = any(w in lowered for w in ["no", "nope", "cancel", "keep", "nevermind", "never mind", "stop"])
            supabase.table("user_context").delete().eq("id", ctx_id).execute()
            if is_yes and ":" in stored:
                goal_id, activity = stored.split(":", 1)
                supabase.table("goals").delete().eq("id", goal_id).eq("user_id", user_id).execute()
                logger.info(f"[delete_goal] confirmed delete goal='{activity}' for user={user_id}")
                return f"Goal deleted: {activity}"
            else:
                return f"Got it, keeping {stored.split(':', 1)[-1] if ':' in stored else 'it'}"

        # First call — identify the goal and ask for confirmation
        goals_res = supabase.table("goals").select("id, activity").eq("user_id", user_id).execute()
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

        # Store pending delete and request confirmation
        supabase.table("user_context").delete().eq("user_id", user_id).eq("type", "pending_delete").execute()
        supabase.table("user_context").insert({
            "user_id": user_id,
            "type": "pending_delete",
            "description": f"{goal_id}:{activity}",
            "expires_at": _hours_from_now(2),
        }).execute()
        logger.info(f"[delete_goal] awaiting confirmation to delete '{activity}' for user={user_id}")
        return f"confirm_delete:{activity}"

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

        # Rebuild persona system prompt in background so schedule changes are reflected
        import asyncio as _asyncio
        _asyncio.create_task(_rebuild_coach_prompt(user_id))

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

        parts = []
        for g in goals:
            s = streak_map.get(g["id"], {})
            current = s.get("current_streak", 0)
            longest = s.get("longest_streak", 0)
            streak_note = f"{current}-day streak" if current > 0 else "0-day streak"
            if longest > 0 and longest != current:
                streak_note += f" (best: {longest})"
            parts.append(f"{g['activity']}: {streak_note}")

        summary = "User stats — " + ". ".join(parts) + "."
        logger.info(f"[stats_query] fetched {len(goals)} goals for user={user_id}")
        return summary

    except Exception:
        logger.exception(f"[stats_query] failed for user={user_id}")
        return ""


async def _rebuild_coach_prompt(user_id: str) -> None:
    """
    Fire-and-forget background task: rebuild generated_system_prompt after a goal
    change so the persona's core context reflects the user's latest goals.
    """
    try:
        from routes.ai import build_coach_personality
        await build_coach_personality(user_id)
        logger.info(f"[rebuild] coach prompt updated after goal change for user={user_id}")
    except Exception:
        logger.exception(f"[rebuild] prompt rebuild failed for user={user_id}")


async def handle_motivation_request(user_id: str, message_body: str, user_timezone: str = "") -> str:
    """Signal to the voice generator that the user wants on-demand motivation."""
    logger.info(f"[motivation_request] user={user_id} requested motivation")
    return "user is explicitly asking for motivation and a push right now"


# Advice-seeking language — user is asking for a real answer, not just motivation
_ADVICE_RE = re.compile(
    r"\b(what should i|any advice|what do you think|how do i|what would you|"
    r"what's your take|should i|is it worth|what do you recommend|help me with|"
    r"not sure what to|what's the best way|how can i|any tips|what would help)\b",
    re.IGNORECASE,
)


async def handle_general(user_id: str, message_body: str, user_timezone: str = "") -> str:
    """
    For GENERAL messages that don't fit any specific category.
    Detects advice-seeking language and returns a hint for the voice generator.
    Opportunity/desire/struggle language is now handled by COACHING_OPPORTUNITY.
    Returns empty string when no actionable signal is found.
    """
    if _ADVICE_RE.search(message_body):
        logger.info(f"[general] advice-seeking detected for user={user_id}")
        return (
            "user is asking for specific advice or guidance — give a concrete, "
            "thoughtful answer to their actual question. Do not replace a real answer "
            "with generic motivation or a pep talk."
        )
    return ""


async def handle_coaching_opportunity(user_id: str, message_body: str, user_timezone: str = "") -> str:
    """
    Store the user's expressed desire, ambition, or struggle as a coaching opportunity.
    Deduplicates by exact description to prevent double-storing the same message.
    Returns a curiosity-first hint for the voice generator.
    Never creates goals, schedules, or reminders.
    """
    description = message_body[:500]
    is_duplicate = False
    try:
        existing = (
            supabase.table("user_context")
            .select("id")
            .eq("user_id", user_id)
            .eq("type", "coaching_opportunity")
            .eq("description", description)
            .limit(1)
            .execute()
        )
        is_duplicate = bool(existing.data)
    except Exception:
        logger.exception(f"[coaching_opportunity] dedup check failed for user={user_id}")

    if not is_duplicate:
        try:
            supabase.table("user_context").insert({
                "user_id": user_id,
                "type": "coaching_opportunity",
                "description": description,
                "metadata": {"confidence": 0.9},
                "expires_at": _hours_from_now(168),
            }).execute()
            logger.info(f"[coaching_opportunity] stored for user={user_id}: {description[:60]}")
        except Exception:
            logger.exception(f"[coaching_opportunity] DB write failed for user={user_id}")
    else:
        logger.info(f"[coaching_opportunity] duplicate skipped for user={user_id}")

    if is_duplicate:
        return (
            "user has expressed this desire or struggle before — acknowledge the pattern naturally. "
            "Ask ONE question about what has changed or what has been getting in the way. "
            "Do NOT create goals, schedules, or reminders."
        )
    return (
        "user expressed a desire, ambition, or challenge — respond with curiosity. "
        "Ask ONE follow-up question to understand their situation better. "
        "Do NOT suggest creating a goal, schedule, or reminder yet."
    )


async def handle_notification_reply(user_id: str, message_body: str, user_timezone: str) -> str | None:
    """
    Check if the user has a NOTIFIED activity notification and classify their reply
    as YES / NO / RESCHEDULE. Returns an execution_result string if handled, None to
    fall through to the normal classifier pipeline.

    On RESCHEDULE:
    - If new time is > 5 min away: inserts a new SCHEDULED row; scheduler fires the
      30-min warning at the right time.
    - If new time is ≤ 5 min away: inserts as NOTIFIED so the scheduler fires at T=0
      if the user confirms; voice reply communicates it's almost time now.
    - If no time extracted: returns rescheduled:activity:no_time so voice generator
      asks for a specific time.
    """
    try:
        notified_res = (
            supabase.table("activity_notifications")
            .select("id, activity, scheduled_time, scheduled_date, notified_at")
            .eq("user_id", user_id)
            .eq("state", "NOTIFIED")
            .order("notified_at", desc=True)
            .limit(1)
            .execute()
        )
        if not notified_res.data:
            return None

        notif    = notified_res.data[0]
        activity = notif["activity"]
        now_utc  = datetime.now(timezone.utc)
        now_iso  = now_utc.isoformat()

        # Regex fast-path: handles obvious YES/NO/RESCHEDULE without a Gemini call.
        # Gemini only fires for ambiguous messages the regex can't classify.
        quick  = _quick_classify_notif_reply(message_body)
        intent = quick or ""
        new_time_from_regex: str | None = None

        if quick == "RESCHEDULE":
            from services.onboarding import _normalize_time
            new_time_from_regex = _normalize_time(message_body.strip())

        if not intent:
            model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
            resp  = model.generate_content(
                f"The user received a notification for '{activity}' and replied: \"{message_body}\"\n"
                f"Classify their reply as YES (they will do it), NO (declining/skipping today), "
                f"RESCHEDULE (wants a different time today), or UNRELATED (message is about something else entirely).\n"
                f"If RESCHEDULE, also extract the new time as HH:MM 24h. If they say 'later' with no time use null.\n"
                f"Return only JSON: {{\"intent\": \"YES\"|\"NO\"|\"RESCHEDULE\"|\"UNRELATED\", \"new_time\": \"HH:MM\"|null}}"
            )
            try:
                data  = json.loads(_strip_json_fences(resp.text))
                intent = (data.get("intent") or "").upper()
                if intent == "RESCHEDULE":
                    new_time_from_regex = data.get("new_time")
            except Exception:
                return None

        if intent == "UNRELATED":
            return None

        if intent == "YES":
            supabase.table("activity_notifications").update({
                "state":      "CONFIRMED",
                "replied_at": now_iso,
                "reply_text": message_body[:500],
                "updated_at": now_iso,
            }).eq("id", notif["id"]).execute()
            logger.info(f"[notif_reply] user={user_id} CONFIRMED {activity}")
            return f"confirmed:{activity}"

        if intent == "NO":
            supabase.table("activity_notifications").update({
                "state":      "DECLINED",
                "replied_at": now_iso,
                "reply_text": message_body[:500],
                "updated_at": now_iso,
            }).eq("id", notif["id"]).execute()
            logger.info(f"[notif_reply] user={user_id} DECLINED {activity}")
            return f"declined:{activity}"

        if intent == "RESCHEDULE":
            new_time = new_time_from_regex  # "HH:MM" or None — set by regex or Gemini path above
            supabase.table("activity_notifications").update({
                "state":          "RESCHEDULED",
                "replied_at":     now_iso,
                "reply_text":     message_body[:500],
                "rescheduled_to": new_time,
                "updated_at":     now_iso,
            }).eq("id", notif["id"]).execute()
            logger.info(f"[notif_reply] user={user_id} RESCHEDULED {activity} → {new_time}")

            if new_time:
                try:
                    tz        = pytz.timezone(user_timezone)
                    local_now = datetime.now(tz)
                    h, m      = map(int, new_time.split(":"))
                    new_dt    = local_now.replace(hour=h, minute=m, second=0, microsecond=0)
                    mins_away = (new_dt - local_now).total_seconds() / 60

                    if mins_away > 5:
                        today_str = local_now.strftime("%Y-%m-%d")
                        new_state = "SCHEDULED" if mins_away > 30 else "NOTIFIED"
                        supabase.table("activity_notifications").insert({
                            "user_id":        user_id,
                            "activity":       activity,
                            "scheduled_date": today_str,
                            "scheduled_time": new_time,
                            "state":          new_state,
                        }).execute()
                        logger.info(
                            f"[notif_reply] inserted {new_state} row for {activity} at {new_time} "
                            f"({int(mins_away)}m away)"
                        )
                        return f"rescheduled:{activity}:new_time={new_time}"
                    else:
                        return f"rescheduled:{activity}:too_soon"
                except Exception:
                    logger.exception(f"[notif_reply] failed to insert rescheduled row for user={user_id}")

            return f"rescheduled:{activity}:no_time"

    except Exception:
        logger.exception(f"[notif_reply] failed for user={user_id}")
    return None


_HANDLER_MAP = {
    "CREATE_GOAL":          handle_create_goal,
    "MODIFY_GOAL":          handle_modify_goal,
    "DELETE_GOAL":          handle_delete_goal,
    "STATS_QUERY":          handle_stats_query,
    "MOTIVATION_REQUEST":   handle_motivation_request,
    "GOAL":                 handle_goal,
    "NUTRITION":            handle_nutrition,
    "TASK":                 handle_task,
    "JOURNAL":              handle_journal,
    "BET":                  handle_bet,
    "GENERAL":              handle_general,
    "COACHING_OPPORTUNITY": handle_coaching_opportunity,
}


# ---------------------------------------------------------------------------
# Context helpers — 4-section context doc system
# ---------------------------------------------------------------------------

# Keyword extraction for historical retrieval (no AI needed)
_STOPWORDS = {
    "their", "there", "would", "could", "should", "really", "going", "being",
    "having", "doing", "about", "after", "before", "these", "those", "where",
    "which", "while", "still", "often", "every", "again", "never", "always",
    "maybe", "since", "until", "think", "feels", "start", "thing", "right",
    "today", "just", "like", "know", "want", "need", "make", "take", "feel",
    "good", "well", "also", "then", "than", "into", "over", "more", "very",
    "only", "most", "other", "same", "when", "been", "were", "they", "will",
    "have", "this", "with", "from", "what", "that", "some", "back",
}


def _extract_keywords(text: str, max_kw: int = 3) -> list[str]:
    """Extract significant words from message text for keyword retrieval. No AI needed."""
    words = re.findall(r'\b[a-z]{5,}\b', text.lower())
    seen: list[str] = []
    for w in words:
        if w not in _STOPWORDS and w not in seen:
            seen.append(w)
        if len(seen) >= max_kw:
            break
    return seen


async def _retrieve_relevant_messages(
    user_id: str,
    message_body: str,
    oldest_recent_created_at: str | None,
) -> str:
    """
    Keyword ILIKE search across the full message history. Returns a formatted
    RELEVANT FROM YOUR HISTORY block, or "" if nothing useful found.
    Only searches messages older than the current 30-message window (no duplication).
    """
    keywords = _extract_keywords(message_body)
    if not keywords:
        return ""
    try:
        or_filter = ",".join(f"body.ilike.%25{kw}%25" for kw in keywords)
        query = (
            supabase.table("messages")
            .select("body, created_at")
            .eq("user_id", user_id)
            .eq("direction", "inbound")
            .or_(or_filter)
            .order("created_at", desc=True)
            .limit(5)
        )
        if oldest_recent_created_at:
            query = query.lt("created_at", oldest_recent_created_at)
        res = query.execute()
        if not res.data:
            return ""

        now = datetime.now(timezone.utc)
        lines: list[str] = []
        for row in res.data:
            try:
                msg_dt = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                days   = (now - msg_dt).days
                if days == 0:
                    label = "today (earlier)"
                elif days == 1:
                    label = "yesterday"
                elif days < 7:
                    label = f"{days} days ago"
                elif days < 30:
                    weeks = days // 7
                    label = f"{weeks} week{'s' if weeks != 1 else ''} ago"
                else:
                    months = days // 30
                    label = f"{months} month{'s' if months != 1 else ''} ago"
                lines.append(f"{label}: \"{row['body'][:180]}\"")
            except Exception:
                pass
        if not lines:
            return ""
        return (
            "RELEVANT FROM YOUR HISTORY (things this person has said before about this topic):\n"
            + "\n".join(lines)
        )
    except Exception:
        logger.exception(f"[retrieval] keyword search failed for user={user_id}")
        return ""


def _build_context_block(
    memory_doc: dict,
    context_doc: str,
    compressed_memory: str = "",
) -> str:
    """
    Assemble the full relationship context block injected into every system prompt.

    Layers (ordered so Gemini prioritises recent over old):
      1. Open loops — things requiring proactive follow-up
      2. Active goals — user's stated objectives
      3. Behavior patterns — recurring patterns observed over time
      4. Cold memory — compressed older conversations (permanent, never deleted)
      5. Recent conversation — verbatim last ~20-30 exchanges (hot memory)
    """
    sections: list[str] = []

    open_loops = memory_doc.get("open_loops") or []
    if open_loops:
        loop_lines: list[str] = []
        for loop in open_loops:
            if isinstance(loop, dict):
                topic  = loop.get("topic", "")
                source = loop.get("source", "user")
                prefix = "[You committed to this] " if source == "coach" else ""
                loop_lines.append(f"- {prefix}{topic}")
            elif loop:
                loop_lines.append(f"- {loop}")
        if loop_lines:
            sections.append("[OPEN LOOPS — follow up on these naturally]\n" + "\n".join(loop_lines))

    active_goals = memory_doc.get("active_goals") or []
    if active_goals:
        sections.append("[ACTIVE GOALS]\n" + "\n".join(f"- {g}" for g in active_goals))

    patterns = (
        memory_doc.get("patterns")
        or memory_doc.get("recurring_obstacles")
        or []
    )
    if patterns:
        sections.append("[BEHAVIOR PATTERNS]\n" + "\n".join(f"- {p}" for p in patterns))

    if compressed_memory and compressed_memory.strip():
        sections.append("[COLD MEMORY — compressed older conversations, permanent]\n" + compressed_memory.strip())

    if context_doc and context_doc.strip():
        sections.append("[RECENT CONVERSATION — verbatim, most recent first]\n" + context_doc.strip())

    if not sections:
        return ""
    return (
        "[RELATIONSHIP CONTEXT — use this to feel like you know this person]\n\n"
        + "\n\n".join(sections)
    )


# Patterns for inline open loop detection (no Gemini, instant)
_LOOP_TOPIC_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\b(interview|job interview)\b', re.IGNORECASE), "interview"),
    (re.compile(r'\b(doctor|appointment|check.?up|hospital|dentist|therapist)\b', re.IGNORECASE), "appointment"),
    (re.compile(r'\b(race|5k|10k|marathon|half marathon|competition|tournament)\b', re.IGNORECASE), "race/event"),
    (re.compile(r'\b(exam|test|presentation|pitch|audition|tryout)\b', re.IGNORECASE), "event"),
    (re.compile(r'\b(weigh.?in|check.*weight|scale)\b', re.IGNORECASE), "weight check"),
]
_LOOP_TIME_RE  = re.compile(
    r'\b(tomorrow|tonight|this (friday|monday|tuesday|wednesday|thursday|saturday|sunday|week|evening)|next week|in \d+ (days?|weeks?))\b',
    re.IGNORECASE,
)
_LOOP_RESOLVE_RE = re.compile(
    r'\b(had the|went to|went for|did the|finished|completed|done with|just got back|just finished|got back from)\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Topic Memory — structured, per-topic memory extracted from every message
# ---------------------------------------------------------------------------

async def _extract_topic_memories(user_id: str, message_body: str) -> None:
    """
    Analyze an inbound message and create/update structured topic memory rows.
    Fire-and-forget — runs after every reply, never blocks the response.
    Skips messages under 15 chars to avoid burning API calls on "ok" / "thanks".
    """
    if len(message_body.strip()) < 15:
        return
    try:
        existing_res = (
            supabase.table("topic_memory")
            .select("id, topic, summary, importance, status")
            .eq("user_id", user_id)
            .in_("status", ["active", "dormant"])
            .order("importance", desc=True)
            .limit(20)
            .execute()
        )
        existing = existing_res.data or []
        existing_json = json.dumps(
            [{"id": e["id"], "topic": e["topic"], "summary": e["summary"], "status": e["status"]}
             for e in existing],
            ensure_ascii=False,
        )

        prompt = (
            "You extract and update structured memory items from a coaching conversation message.\n\n"
            f"Existing memory items:\n{existing_json}\n\n"
            f"New user message: \"{message_body[:600]}\"\n\n"
            "For each significant topic, project, event, goal, or concern you detect:\n"
            "- If it clearly matches an existing memory: update or resolve it\n"
            "- If it is genuinely new and worth remembering: create it\n"
            "- Skip filler, small talk, simple check-ins, and passing mentions\n\n"
            "Return a JSON array. Each item:\n"
            "  action: \"create\" | \"update\" | \"resolve\"\n"
            "  existing_id: id string from existing memories if update/resolve, else null\n"
            "  topic: short label, 3-7 words\n"
            "  summary: one factual sentence using the user's own words where possible\n"
            "  intent_summary: 1-2 sentences on WHY the user cares about this topic, "
            "using their own framing. Empty string if unclear from this message alone.\n"
            "  importance: float 0.1-1.0\n"
            "  confidence_of_persistence: float 0.0-1.0 reflecting how likely this topic "
            "will recur in future conversations. "
            "0.2-0.4 = one-off or venting; 0.5-0.7 = mild recurring concern; "
            "0.8-1.0 = stable ongoing goal or pattern. "
            "Use lower end if this is the first mention and the user shows no strong attachment.\n"
            "  status: \"active\" | \"resolved\" | \"dormant\"\n"
            "  stance: how the user relates to this topic — "
            "\"engaged\" (pursuing it actively), \"neutral\" (mentions it without feeling), "
            "\"resistant\" (avoiding or refusing it), or \"inconsistent\" (mixed signals). "
            "Omit stance field if unclear from this single message alone.\n\n"
            "Importance guide:\n"
            "  1.0 = life-changing event (job loss, health crisis, major relationship change)\n"
            "  0.8 = major goal or important upcoming event\n"
            "  0.6 = active project or recurring habit/commitment\n"
            "  0.4 = obstacle, frustration, or concern\n"
            "  0.2 = passing mention worth noting\n\n"
            "Return [] if nothing significant. Return only valid JSON. No explanation."
        )

        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        raw   = model.generate_content(prompt).text.strip()
        items = json.loads(_strip_json_fences(raw))
        if not isinstance(items, list) or not items:
            return

        now_iso          = datetime.now(timezone.utc).isoformat()
        valid_ids        = {e["id"] for e in existing}
        existing_imp     = {e["id"]: float(e.get("importance") or 0.5) for e in existing}
        new_stances:     dict[str, str] = {}
        new_ephemerals:  list[dict]     = []
        _valid_stances   = {"engaged", "neutral", "resistant", "inconsistent"}
        _CONFIDENCE_GATE = 0.35

        for item in items:
            action      = (item.get("action") or "").lower()
            existing_id = item.get("existing_id")
            topic       = (item.get("topic")          or "").strip()[:120]
            summary     = (item.get("summary")        or "").strip()[:500]
            intent_summ = (item.get("intent_summary") or "").strip()[:300]
            importance  = min(1.0, max(0.1, float(item.get("importance") or 0.5)))
            confidence  = min(1.0, max(0.0, float(item.get("confidence_of_persistence") or 0.5)))
            status_raw  = item.get("status", "active")
            status      = status_raw if status_raw in ("active", "resolved", "dormant") else "active"
            stance      = item.get("stance") if item.get("stance") in _valid_stances else None

            if action == "create" and topic and summary:
                if confidence < _CONFIDENCE_GATE:
                    new_ephemerals.append({
                        "topic":      topic,
                        "intent":     intent_summ or summary,
                        "confidence": round(confidence, 3),
                        "at":         now_iso,
                    })
                    logger.info(
                        f"[topic_memory] ephemeral '{topic}' conf={confidence:.2f} "
                        f"(below gate) for user={user_id}"
                    )
                    continue

                # Importance floor: confidence can only raise importance, never lower it
                importance = max(importance, confidence)
                insert_res = (
                    supabase.table("topic_memory")
                    .insert({
                        "user_id":           user_id,
                        "topic":             topic,
                        "summary":           summary,
                        "importance":        importance,
                        "status":            status,
                        "last_mentioned_at": now_iso,
                    })
                    .select("id")
                    .execute()
                )
                new_id = (insert_res.data[0].get("id")) if insert_res.data else None
                if stance and new_id:
                    new_stances[new_id] = stance
                logger.info(
                    f"[topic_memory] created '{topic}' importance={importance:.2f} "
                    f"conf={confidence:.2f} stance={stance} for user={user_id}"
                )

            elif action in ("update", "resolve") and existing_id in valid_ids:
                payload: dict = {"last_mentioned_at": now_iso, "updated_at": now_iso}
                if summary:
                    payload["summary"] = summary
                if action != "resolve" and confidence >= _CONFIDENCE_GATE:
                    # Only raise importance, never lower it
                    payload["importance"] = max(importance, confidence, existing_imp.get(existing_id, 0.0))
                payload["status"] = "resolved" if action == "resolve" else status
                supabase.table("topic_memory").update(payload).eq("id", existing_id).eq("user_id", user_id).execute()
                if stance:
                    new_stances[existing_id] = stance
                logger.info(
                    f"[topic_memory] {action} '{topic or existing_id[:8]}' "
                    f"conf={confidence:.2f} stance={stance} for user={user_id}"
                )

        # Persist stances + ephemeral mentions to memory_doc in a single write.
        if new_stances or new_ephemerals:
            try:
                stance_res = (
                    supabase.table("user_memory")
                    .select("memory_doc")
                    .eq("user_id", user_id)
                    .limit(1)
                    .execute()
                )
                stance_mem = (stance_res.data[0] if stance_res.data else {}).get("memory_doc") or {}

                if new_stances:
                    existing_stances: dict = stance_mem.get("topic_stances") or {}
                    existing_stances.update(new_stances)
                    stance_mem["topic_stances"] = existing_stances

                if new_ephemerals:
                    prior_eph: list = stance_mem.get("ephemeral_mentions") or []
                    prior_eph.extend(new_ephemerals)
                    stance_mem["ephemeral_mentions"] = prior_eph[-20:]  # cap at 20

                supabase.table("user_memory").upsert(
                    {"user_id": user_id, "memory_doc": stance_mem, "updated_at": now_iso},
                    on_conflict="user_id",
                ).execute()
                if new_stances:
                    logger.info(f"[topic_memory] stances written: {new_stances} for user={user_id}")
                if new_ephemerals:
                    logger.info(f"[topic_memory] {len(new_ephemerals)} ephemeral(s) written for user={user_id}")
            except Exception:
                logger.exception(f"[topic_memory] memory_doc write failed for user={user_id}")

    except Exception:
        logger.exception(f"[topic_memory] extraction failed for user={user_id}")


async def _extract_user_memory_async(user_id: str, user_msg: str, bot_reply: str) -> None:
    """
    Fire-and-forget: extract user preferences, constraints, rejections, behavioral
    patterns, and identity facts from each conversation turn.

    Called after _update_context_async in the chained post-reply task so the read
    sees the already-updated memory_doc (eliminates write-write race).

    Single Gemini call — no additional latency path added.
    """
    if len(user_msg.strip()) < 8:
        return
    try:
        res = (
            supabase.table("user_memory")
            .select("memory_doc")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        mem_data    = res.data[0] if res.data else {}
        memory_doc  = mem_data.get("memory_doc") or {}
        now_iso     = datetime.now(timezone.utc).isoformat()

        existing_prefs       = memory_doc.get("preferences")  or []
        existing_rejections  = memory_doc.get("rejections")   or []
        existing_patterns    = memory_doc.get("patterns")     or []
        existing_identity    = memory_doc.get("identity")     or {}
        existing_constraints = memory_doc.get("constraints")  or []
        existing_pref_texts  = [p.get("preference", "").lower() for p in existing_prefs]
        existing_rej_topics  = [r.get("topic", "").lower()      for r in existing_rejections]
        existing_pat_texts   = [p.lower() if isinstance(p, str) else "" for p in existing_patterns]
        existing_con_texts   = [c.lower() if isinstance(c, str) else "" for c in existing_constraints]

        prompt = (
            "Analyze this coaching conversation turn. Extract durable facts about the user.\n\n"
            f"Coach said: \"{bot_reply[:400]}\"\n"
            f"User said: \"{user_msg[:400]}\"\n\n"
            "EXTRACTION RULES — read carefully before filling each field:\n\n"
            "PREFERENCES / CONSTRAINTS:\n"
            "- Only from what the USER explicitly stated. Never from coach suggestions.\n"
            "- type 'preference': things they like, want, or prefer.\n"
            "- type 'constraint': things they dislike, avoid, or refuse.\n"
            "- confidence: 0.0–1.0. Single vague signal caps at 0.5. Strong direct statement = 0.9+.\n\n"
            "REJECTIONS:\n"
            "- Only when user clearly dismisses a specific coach suggestion.\n"
            "- Include constraint_signals: 2-4 short lowercase phrases that capture what kinds of things\n"
            "  this rejection rules out semantically (e.g., topic='5am wakeup' → "
            "['avoids early wakeup schedules', 'rejects extreme morning routines']).\n"
            "- constraint_signals must generalize the refusal, not just restate it.\n"
            "- Omit constraint_signals if nothing meaningful to generalize.\n\n"
            "PATTERNS:\n"
            "- Recurring behaviors or tendencies the user explicitly names, or that are confirmed\n"
            "  across multiple exchanges (not inferred from one message).\n"
            "- Examples: 'skips workouts when stressed', 'more productive at night', 'loses focus on weekends'.\n"
            "- Return [] if nothing clearly repeating or self-described.\n\n"
            "IDENTITY (optional — only if explicitly stated by the user):\n"
            "- motivators: what drives them (only if user said so directly).\n"
            "- accountability_style: how they respond to pressure (only if clearly self-described).\n"
            "- life_context: their situation — job, school, role (only if explicitly mentioned).\n"
            "- communication_style: how they prefer to interact (only if clearly stated).\n"
            "- Omit any identity field that is empty or unclear. Never infer.\n\n"
            f"Already stored preferences (skip duplicates): {json.dumps(existing_pref_texts[:15])}\n"
            f"Already stored rejections (skip duplicates): {json.dumps(existing_rej_topics[:15])}\n"
            f"Already stored patterns (skip duplicates): {json.dumps(existing_pat_texts[:10])}\n\n"
            "Return JSON only. No explanation.\n"
            "{\n"
            "  \"preferences\": [{\"preference\": \"...\", \"type\": \"preference|constraint\", \"confidence\": 0.0}],\n"
            "  \"rejections\":  [{\"topic\": \"...\", \"constraint_signals\": [\"...\", \"...\"]}],\n"
            "  \"patterns\":    [\"...\"],\n"
            "  \"identity\":    {\"motivators\": \"...\", \"accountability_style\": \"...\", \"life_context\": \"...\", \"communication_style\": \"...\"}\n"
            "}\n"
            "Omit any field that has nothing to extract. Return {} for identity if nothing applies."
        )

        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        raw   = model.generate_content(prompt).text.strip()
        data  = json.loads(_strip_json_fences(raw))

        new_prefs    = data.get("preferences") or []
        new_rejs     = data.get("rejections")  or []
        new_patterns = data.get("patterns")    or []
        new_identity = data.get("identity")    or {}

        if not new_prefs and not new_rejs and not new_patterns and not new_identity:
            return

        changed = False

        # Preferences and constraints — unchanged merge logic
        for item in new_prefs:
            pref = (item.get("preference") or "").strip()[:200]
            if not pref:
                continue
            ptype      = item.get("type", "preference")
            ptype      = ptype if ptype in ("preference", "constraint") else "preference"
            confidence = min(1.0, max(0.0, float(item.get("confidence") or 0.5)))
            match = next((p for p in existing_prefs if p.get("preference", "").lower() == pref.lower()), None)
            if match:
                match["last_confirmed_at"] = now_iso
                match["confidence"]        = max(match.get("confidence", 0), confidence)
            else:
                existing_prefs.append({
                    "preference":        pref,
                    "type":              ptype,
                    "source":            "user",
                    "confidence":        confidence,
                    "last_confirmed_at": now_iso,
                })
            changed = True

        # Rejections — merge by topic; collect semantic constraint_signals from new rejections
        for item in new_rejs:
            topic = (item.get("topic") or "").strip()[:200]
            if not topic or topic.lower() in existing_rej_topics:
                continue
            existing_rejections.append({"topic": topic, "rejected_at": now_iso})
            existing_rej_topics.append(topic.lower())
            changed = True
            logger.info(f"[user_memory] rejection stored: '{topic}' for user={user_id}")

            # Flatten semantic expansions into the constraints list
            for signal in (item.get("constraint_signals") or []):
                signal = (signal or "").strip().lower()[:200]
                if signal and signal not in existing_con_texts and len(existing_constraints) < 20:
                    existing_constraints.append(signal)
                    existing_con_texts.append(signal)

        # Patterns — append new, deduplicate by exact lowercase match, cap at 5
        for pat in new_patterns:
            pat = (pat or "").strip()[:200] if isinstance(pat, str) else ""
            if not pat or pat.lower() in existing_pat_texts:
                continue
            if len(existing_patterns) >= 5:
                break
            existing_patterns.append(pat)
            existing_pat_texts.append(pat.lower())
            changed = True
            logger.info(f"[user_memory] pattern stored: '{pat}' for user={user_id}")

        # Identity — merge field-by-field, never overwrite with empty
        if isinstance(new_identity, dict):
            for field in ("motivators", "accountability_style", "life_context", "communication_style"):
                val = (new_identity.get(field) or "").strip()[:300]
                if val and val != existing_identity.get(field, ""):
                    existing_identity[field] = val
                    changed = True

        if not changed:
            return

        memory_doc["preferences"]  = existing_prefs
        memory_doc["rejections"]   = existing_rejections
        memory_doc["patterns"]     = existing_patterns
        memory_doc["constraints"]  = existing_constraints
        if existing_identity:
            memory_doc["identity"] = existing_identity

        supabase.table("user_memory").upsert(
            {"user_id": user_id, "memory_doc": memory_doc, "updated_at": now_iso},
            on_conflict="user_id",
        ).execute()
        logger.info(
            f"[user_memory] prefs={len(new_prefs)} rejs={len(new_rejs)} "
            f"patterns={len(new_patterns)} constraints={len(existing_constraints)} "
            f"identity_fields={len(new_identity)} for user={user_id}"
        )

    except Exception:
        logger.exception(f"[user_memory] extraction failed for user={user_id}")


async def _retrieve_topic_context(user_id: str) -> dict:
    """
    Fetch structured topic context for injection into the voice generator.
    Returns: active topics (by importance), recently resolved (last 7d), open coaching opportunities.
    """
    try:
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        now_iso        = datetime.now(timezone.utc).isoformat()

        active_res = (
            supabase.table("topic_memory")
            .select("id, topic, summary, importance, last_mentioned_at")
            .eq("user_id", user_id)
            .eq("status", "active")
            .order("importance", desc=True)
            .limit(8)
            .execute()
        )

        resolved_res = (
            supabase.table("topic_memory")
            .select("topic, summary, last_mentioned_at")
            .eq("user_id", user_id)
            .eq("status", "resolved")
            .gte("last_mentioned_at", seven_days_ago)
            .order("last_mentioned_at", desc=True)
            .limit(3)
            .execute()
        )

        opp_res = (
            supabase.table("user_context")
            .select("description, created_at")
            .eq("user_id", user_id)
            .eq("type", "coaching_opportunity")
            .gt("expires_at", now_iso)
            .order("created_at", desc=True)
            .limit(4)
            .execute()
        )

        return {
            "active":                 active_res.data or [],
            "recently_resolved":      resolved_res.data or [],
            "coaching_opportunities": opp_res.data or [],
        }
    except Exception:
        logger.exception(f"[topic_memory] retrieval failed for user={user_id}")
        return {"active": [], "recently_resolved": [], "coaching_opportunities": []}


def _build_topic_memory_block(topic_context: dict) -> str:
    """Deprecated — use _build_memory_context. Kept to avoid import breaks."""
    return ""


async def _build_memory_context(user_id: str) -> tuple[str, dict]:
    """
    Ranked memory assembly — single source of truth for all prompt memory injection.

    Each memory item is scored before rendering. Items below the drop threshold
    are excluded. Rejection conflicts are detected and suppressed. Section order
    is strict and never reordered dynamically.

    Score model:
        identity           → 1.0  (always include)
        rejections         → 1.0  (always include, highest authority)
        constraints        → 0.95
        active topics      → importance × exp(-days / 14)  (drop if < 0.4)
        patterns           → 0.6  (flat; always pass threshold)
        recent context     → 0.7  (blob; capped, not per-item filtered)
        compressed memory  → 0.3  (blob; exempt from drop, always capped to last chunk)
        coaching signals   → 0.5  (flat)

    Returns:
        memory_block: structured prompt string, sections in strict priority order
        metadata: {active_topic_ids, high_importance_topics, rejection_count,
                   constraint_count, dropped_items_count, conflict_drops}
    """
    _SCORE_DROP_THRESHOLD = 0.4

    _STOP_WORDS: frozenset[str] = frozenset({
        "the", "a", "an", "is", "are", "was", "were", "to", "do", "you",
        "at", "it", "in", "on", "up", "be", "or", "and", "for", "of",
        "with", "not", "no", "can", "if", "so", "that", "this", "my",
        "by", "but", "as", "how", "too", "all", "any", "get", "got",
        "had", "has", "have", "been", "more", "than", "just", "also",
    })

    now = datetime.now(timezone.utc)

    def _age_days(iso_str: str) -> int:
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return max(0, (now - dt).days)
        except Exception:
            return 0

    def _age_label(days: int) -> str:
        if days == 0:  return "today"
        if days == 1:  return "yesterday"
        if days < 7:   return f"{days}d ago"
        if days < 30:  return f"{days // 7}w ago"
        return f"{days // 30}mo ago"

    def _exp_decay(days: int, half_life: int) -> float:
        return math.exp(-days / half_life)

    # Type-aware half-lives (days). Identity and rejections have no decay
    # and are handled as score=1.0 without calling this function.
    _HL_CONSTRAINT   = 90   # hard limits fade very slowly
    _HL_CORE_TOPIC   = 60   # recurring behavioral themes survive months
    _HL_NORMAL_TOPIC = 14   # one-off discussions fade quickly
    _HL_PATTERN      = 45   # behavioral observations fade moderately

    def _keywords(text: str) -> set[str]:
        return {
            w.lower().strip(".,!?;:'\"") for w in text.split()
            if len(w.strip(".,!?;:'\"")) >= 3
            and w.lower().strip(".,!?;:'\"") not in _STOP_WORDS
        }

    def _core_confidence(imp: float, distinct_days: int, days_since_last: int) -> float:
        """
        Multi-signal CORE confidence score.
        Combines normalized importance, temporal reinforcement depth, and recency.
        """
        return (
            0.5 * imp
            + 0.3 * min(distinct_days / 5, 1.0)
            + 0.2 * _exp_decay(days_since_last, 14)
        )

    def _stats_for_kws(kws: set[str], msgs: list[dict]) -> dict:
        """
        Compute mention_count and distinct_day_count for a keyword set against
        the pre-fetched message batch.  No extra DB call — O(n·m) in-memory.
        Used for both raw topics and canonical groups (union of member keywords).
        """
        if not kws:
            return {"mention_count": 0, "distinct_days": 0}
        mention_count = 0
        seen_days: set[str] = set()
        for msg in msgs:
            if kws & _keywords(msg.get("body", "")):
                mention_count += 1
                day = (msg.get("created_at") or "")[:10]
                if day:
                    seen_days.add(day)
        return {"mention_count": mention_count, "distinct_days": len(seen_days)}

    # ── Canonical topic map (static, no schema) ──────────────────────────────
    # Maps canonical concept names to synonym lists used for grouping.
    # Single-word entries match whole words; multi-word entries match as phrases.
    _CANONICAL_MAP: dict[str, list[str]] = {
        "fitness":       ["gym", "workout", "working out", "fitness", "exercise",
                          "training", "lift", "lifting", "run", "running", "weights"],
        "career":        ["job", "internship", "career", "work", "interview",
                          "employment", "office", "boss", "salary", "promotion", "hiring"],
        "productivity":  ["focus", "discipline", "routine", "productivity", "habit",
                          "schedule", "organize", "procrastinat", "goals"],
        "mental_health": ["stress", "anxiety", "burnout", "overwhelmed", "mental",
                          "depression", "therapy", "anxious", "panic"],
        "sleep":         ["sleep", "tired", "fatigue", "rest", "insomnia", "nap",
                          "exhausted", "wake"],
    }

    def _preclassify_topic_intent(label: str, summary: str) -> str:
        """
        Return a canonical group name (or 'other') based on topic label + summary.
        Used as tie-breaking hint in _assign_canonical — NOT as a replacement.
        Phrase-based matching (multi-word phrases score 2, single words score 1).
        """
        text  = (label + " " + summary).lower()
        words = set(text.split())
        _INTENT_PATTERNS: list[tuple[str, list[str]]] = [
            ("fitness",       ["gym", "workout", "exercise", "training", "lift", "lifting",
                               "run", "physical", "nutrition", "diet", "weight loss", "working out"]),
            ("mental_health", ["stress", "anxiety", "burnout", "overwhelmed", "depression",
                               "therapy", "anxious", "mental health", "panic"]),
            ("career",        ["job", "internship", "career", "interview", "salary", "promotion",
                               "employment", "office", "job offer", "job loss"]),
            ("productivity",  ["focus", "discipline", "routine", "habit", "procrastinat",
                               "organize", "schedule", "time management"]),
            ("sleep",         ["sleep", "insomnia", "fatigue", "rest", "exhausted", "tired",
                               "can't sleep", "nap"]),
        ]
        best_intent = "other"
        best_score  = 0
        for intent, phrases in _INTENT_PATTERNS:
            score = 0
            for p in phrases:
                if " " in p:
                    score += 2 if p in text else 0
                else:
                    score += 1 if p in words else 0
            if score > best_score:
                best_score = score
                best_intent = intent
        return best_intent

    def _assign_canonical(label: str, summary: str, intent_hint: str | None = None) -> str | None:
        """
        Return the canonical group key for a topic, or None if no match.
        Scores by number of term hits so the best-fit group wins on ties.
        Multi-word phrases score double (more specific match).
        When two groups tie, prefer the one matching intent_hint (if provided).
        """
        text  = (label + " " + summary).lower()
        words = set(text.split())
        best: str | None = None
        best_score       = 0
        for group, terms in _CANONICAL_MAP.items():
            score = 0
            for term in terms:
                if " " in term:          # phrase match
                    if term in text:
                        score += 2
                elif term in words:      # whole-word match
                    score += 1
            if score > best_score:
                best_score = score
                best       = group
            elif score > 0 and score == best_score and intent_hint and group == intent_hint:
                # Tie-break: prefer group matching pre-classified intent
                best = group
        return best  # None → no canonical match → use raw topic label

    def _aggregate_ephemeral_signals(ephemerals: list[dict]) -> list[dict]:
        """
        Group ephemeral mentions by canonical group and promote those that cross
        the frequency/day gate to candidate_topic status for prompt injection.

        Gate: mentions >= 3 OR distinct_days >= 2.
        Pure in-memory — no DB calls, no writes.
        Cap at 5 candidates, conflict-filtered by caller.
        """
        if not ephemerals:
            return []

        groups: dict[str, list[dict]] = {}
        for eph in ephemerals:
            topic  = eph.get("topic", "")
            intent = eph.get("intent", "")
            canon  = _assign_canonical(topic, intent) or topic.lower()
            groups.setdefault(canon, []).append(eph)

        candidates: list[dict] = []
        for canon, members in groups.items():
            days_seen     = {m["at"][:10] for m in members if m.get("at")}
            freq          = len(members)
            distinct_days = len(days_seen)

            if freq < 3 and distinct_days < 2:
                continue

            confs      = [float(m.get("confidence", 0.0)) for m in members]
            avg_conf   = sum(confs) / len(confs) if confs else 0.0
            low, high  = min(confs), max(confs)
            conf_range = (
                f"{low:.2f}–{high:.2f}" if round(low, 2) != round(high, 2) else f"{low:.2f}"
            )
            candidates.append({
                "canonical":     canon,
                "mentions":      freq,
                "distinct_days": distinct_days,
                "avg_conf":      round(avg_conf, 3),
                "conf_range":    conf_range,
            })

        candidates.sort(key=lambda c: (c["distinct_days"], c["mentions"]), reverse=True)
        return candidates[:5]

    sections:            list[str] = []
    dropped_items_count: int       = 0
    conflict_drops:      list[str] = []
    topic_reinforcement: dict      = {}
    metadata: dict = {
        "active_topic_ids":       [],
        "high_importance_topics": [],
        "rejection_count":        0,
        "constraint_count":       0,
        "dropped_items_count":    0,
        "conflict_drops":         [],
        "topic_reinforcement":    {},
    }

    # ── Fetch 1: user profile ────────────────────────────────────────────────
    try:
        user_res = (
            supabase.table("users")
            .select("name, age, occupation")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        user_row = user_res.data[0] if user_res.data else {}
    except Exception:
        logger.exception(f"[memory_ctx] user profile fetch failed for user={user_id}")
        user_row = {}

    # ── Fetch 2: user_memory (all JSONB fields + rolling context) ───────────
    try:
        mem_res = (
            supabase.table("user_memory")
            .select("memory_doc, context_doc, compressed_memory")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        mem_data = mem_res.data[0] if mem_res.data else {}
    except Exception:
        logger.exception(f"[memory_ctx] user_memory fetch failed for user={user_id}")
        mem_data = {}

    memory_doc      = mem_data.get("memory_doc") or {}
    raw_ctx         = mem_data.get("context_doc") or ""
    cold_memory     = (mem_data.get("compressed_memory") or "").strip()
    identity        = memory_doc.get("identity")      or {}
    preferences     = memory_doc.get("preferences")   or []
    rejections      = memory_doc.get("rejections")    or []
    patterns        = memory_doc.get("patterns") or memory_doc.get("recurring_obstacles") or []
    beh_constraints = memory_doc.get("constraints")   or []
    open_loops         = memory_doc.get("open_loops")         or []
    topic_stances      = memory_doc.get("topic_stances")      or {}
    ephemeral_mentions = memory_doc.get("ephemeral_mentions") or []

    # ── Fetch 3: topic_memory + coaching_opportunities ───────────────────────
    topic_ctx     = await _retrieve_topic_context(user_id)
    active_topics = (topic_ctx.get("active")                 or [])[:10]
    coaching_opps = (topic_ctx.get("coaching_opportunities") or [])[:3]

    # ── Fetch 4: inbound message batch for reinforcement tracking ────────────
    # Single query for all topics; per-topic stats computed in-memory (no per-topic queries).
    try:
        msgs_res = (
            supabase.table("messages")
            .select("body, created_at")
            .eq("user_id", user_id)
            .eq("direction", "inbound")
            .order("created_at", desc=True)
            .limit(200)
            .execute()
        )
        recent_msgs: list[dict] = msgs_res.data or []
    except Exception:
        logger.exception(f"[memory_ctx] message batch fetch failed for user={user_id}")
        recent_msgs = []

    # ── Build keyword indexes ─────────────────────────────────────────────────
    # rejection_kws: used for conflict detection against topics (broadest set)
    # constraint_signal_kws: used for core-topic detection (constraint_signals only)
    rejection_kws:         set[str] = set()
    constraint_signal_kws: set[str] = set()
    for r in rejections:
        rejection_kws |= _keywords(r.get("topic", ""))
    for c in beh_constraints:
        c_kws = _keywords(c)
        rejection_kws         |= c_kws
        constraint_signal_kws |= c_kws

    # ── Step 1: Assign each topic to a canonical group ───────────────────────
    canon_buckets: dict[str, list[dict]] = {}
    for t in active_topics:
        intent_hint = _preclassify_topic_intent(t.get("topic", ""), t.get("summary", ""))
        key = _assign_canonical(t.get("topic", ""), t.get("summary", ""), intent_hint) \
              or t.get("topic", "unknown").lower()
        canon_buckets.setdefault(key, []).append(t)

    # ── Step 2: Merge each canonical group into a single virtual topic ────────
    # Merging: importance→max, last_mentioned_at→most recent, keywords→union.
    # Message scan runs ONCE per group on the combined keyword set — no
    # double-counting of days when multiple raw topics share the same concept.
    merged_topics: list[dict] = []
    for canon_key, members in canon_buckets.items():
        primary   = max(members, key=lambda m: m.get("importance") or 0)
        max_imp   = primary.get("importance") or 0.5
        last_ts   = max(m.get("last_mentioned_at") or "" for m in members)
        raw_labels = [m["topic"] for m in members if m.get("topic")]

        # Union of all member keywords for reinforcement scan + conflict check
        group_kws: set[str] = set()
        for m in members:
            group_kws |= _keywords(m.get("topic", "") + " " + m.get("summary", ""))

        # Stance: use stance of the member mentioned most recently
        most_recent_member = max(members, key=lambda m: m.get("last_mentioned_at") or "")
        group_stance = topic_stances.get(most_recent_member.get("id") or "")

        merged_topics.append({
            "canonical":         canon_key,
            "raw_labels":        raw_labels,
            "importance":        max_imp,
            "last_mentioned_at": last_ts,
            "id":                primary.get("id") or "",
            "member_ids":        [m.get("id") for m in members if m.get("id")],
            "group_kws":         group_kws,
            "stance":            group_stance,
        })

    # ── Step 3: Score + filter canonical groups (CORE detection at group level) ─
    #
    # CORE promotion gates (any sufficient):
    #   A) max importance >= 0.8
    #   B) constraint_signal overlap (concept formalized into a constraint rule)
    #   C) distinct_day_count >= 3  (primary reinforcement signal, now at group level)
    #   D) core_confidence >= 0.65  (composite formula)
    #
    # CORE  → 60-day half-life  |  NORMAL → 14-day half-life
    # Conflict check operates on group_kws (union of all member keywords).
    scored_topics: list[tuple[float, dict, str]] = []  # (score, merged_topic, type_label)
    for t in merged_topics:
        imp       = t["importance"]
        days      = _age_days(t["last_mentioned_at"])
        group_kws = t["group_kws"]

        # Message scan using combined group keyword set (no per-topic DB calls)
        stats         = _stats_for_kws(group_kws, recent_msgs)
        distinct_days = stats["distinct_days"]
        mention_count = stats["mention_count"]
        conf          = _core_confidence(imp, distinct_days, days)

        is_core = (
            imp >= 0.8
            or distinct_days >= 3
            or bool(constraint_signal_kws and group_kws & constraint_signal_kws)
            or conf >= 0.65
        )
        type_label = "core" if is_core else "normal"
        half_life  = _HL_CORE_TOPIC if is_core else _HL_NORMAL_TOPIC
        score      = imp * _exp_decay(days, half_life)

        # Record reinforcement stats keyed by primary topic ID
        tid = t["id"]
        topic_reinforcement[tid] = {
            "canonical":       t["canonical"],
            "members":         t["raw_labels"],
            "distinct_days":   distinct_days,
            "mention_count":   mention_count,
            "core_confidence": round(conf, 3),
            "is_core":         is_core,
        }

        if score < _SCORE_DROP_THRESHOLD:
            dropped_items_count += 1
            logger.debug(
                f"[memory_ctx] canonical group '{t['canonical']}' dropped "
                f"(type={type_label} conf={conf:.2f} score={score:.2f}) for user={user_id}"
            )
            continue

        # Conflict check at group level — requires ≥ 2 overlapping keywords to suppress.
        # A single shared word (e.g. "run" in both fitness group and one rejection) is
        # insufficient; the user may have rejected one specific approach, not the topic.
        if rejection_kws and len(group_kws & rejection_kws) >= 2:
            overlap   = group_kws & rejection_kws
            drop_note = (
                f"canonical:'{t['canonical']}' ({', '.join(t['raw_labels'][:2])}) "
                f"suppressed — conflicts with rejection keywords {sorted(overlap)[:3]}"
            )
            conflict_drops.append(drop_note)
            dropped_items_count += 1
            logger.info(f"[memory_ctx] {drop_note} for user={user_id}")
            continue

        scored_topics.append((score, t, type_label))

    scored_topics.sort(key=lambda x: x[0], reverse=True)
    scored_topics = scored_topics[:10]

    # ── Score constraints (type: CONSTRAINT, half-life 90 days) ─────────────
    # Preferences with last_confirmed_at use time-aware scoring.
    # Behavioral constraints (no timestamp) use base 0.95 (no decay info).
    # Both exempt from drop if score >= threshold; cap at 8 total.
    scored_constraints: list[str] = []
    for p in preferences:
        if p.get("type") != "constraint" or (p.get("confidence") or 0) < 0.5:
            continue
        days  = _age_days(p.get("last_confirmed_at", ""))
        score = 0.95 * _exp_decay(days, _HL_CONSTRAINT)
        if score >= _SCORE_DROP_THRESHOLD:
            scored_constraints.append(p["preference"])
        else:
            dropped_items_count += 1
    for c in beh_constraints:
        # No timestamp → treat as freshly confirmed (score=0.95, always passes)
        text = (c or "").strip()
        if text:
            scored_constraints.append(text)
    scored_constraints = scored_constraints[:8]

    # ── Rejections (type: REJECTION) — score 1.0, no decay, most recent 5 ───
    sorted_rejs = sorted(
        rejections, key=lambda r: r.get("rejected_at", ""), reverse=True
    )[:5]

    # ── Patterns (type: PATTERN, half-life 45 days) ──────────────────────────
    # Per-pattern timestamps are not stored, so decay cannot be computed
    # per item. All stored patterns passed dedup at extraction time and are
    # treated as confirmed. Score = 0.6 flat (above threshold; no per-item drop).
    scored_patterns = [p for p in patterns[:5] if p]

    # ── Ephemeral signal aggregation ─────────────────────────────────────────
    # Runtime-only — groups low-confidence ephemerals by canonical bucket and
    # promotes those that cross the gate (>= 3 mentions OR >= 2 distinct days).
    # Conflict-filtered against rejection_kws before prompt injection.
    raw_candidates   = _aggregate_ephemeral_signals(ephemeral_mentions)
    candidate_topics = [
        c for c in raw_candidates
        if not (rejection_kws and _keywords(c["canonical"]) & rejection_kws)
    ]

    # ── USER STATE VECTOR — runtime only, never stored ────────────────────────
    # Derived entirely from already-computed scoring data. No DB calls, no writes.

    # core_count needed here (also used in logger below)
    core_count   = sum(1 for v in topic_reinforcement.values() if v.get("is_core"))
    normal_count = len(topic_reinforcement) - core_count

    # primary_focus: canonical label of the highest-scoring verified topic
    _usv_primary_focus = scored_topics[0][1]["canonical"] if scored_topics else "none"

    # resistance_level (0–1): rejection density + constraint load + resistant stances
    _usv_resistant_stances = sum(
        1 for _, t, _ in scored_topics if t.get("stance") == "resistant"
    )
    _usv_resistance = round(
        min(len(rejections) / 10, 1.0)              * 0.5
        + min(len(beh_constraints) / 8, 1.0)        * 0.3
        + min(_usv_resistant_stances
              / max(len(scored_topics), 1), 1.0)     * 0.2,
        2,
    )

    # stability (0–1): core topic ratio + average reinforcement depth
    _usv_avg_days = (
        sum(v.get("distinct_days", 0) for v in topic_reinforcement.values())
        / len(topic_reinforcement)
        if topic_reinforcement else 0
    )
    _usv_stability = round(
        min(core_count / max(len(scored_topics), 1), 1.0) * 0.6
        + min(_usv_avg_days / 7, 1.0)                     * 0.4,
        2,
    )

    # emotional_state: stances + recent-context sentiment keywords
    # Strip bot-turn lines so coach language doesn't contaminate keyword signals.
    _ctx_user_only = "\n".join(
        ln for ln in (raw_ctx or "").splitlines() if "| Bot: " not in ln
    )
    _ctx_lower = _ctx_user_only.lower()
    _ctx_words = set(_ctx_lower.split())
    _STRESS_KW    = {"stress", "stressed", "overwhelm", "overwhelmed", "anxious",
                     "anxiety", "panic", "burnout", "burned", "struggling",
                     "difficult", "failing", "lost", "hopeless"}
    _MOTIVATED_KW = {"excited", "motivated", "progress", "crushing",
                     "great", "amazing", "nailed", "achieved", "love", "ready",
                     "pumped", "winning", "confident", "proud"}
    _ctx_has_stress    = bool(_ctx_words & _STRESS_KW)
    _ctx_has_motivated = bool(_ctx_words & _MOTIVATED_KW)
    _engaged_count     = sum(1 for _, t, _ in scored_topics if t.get("stance") == "engaged")
    _inconsistent_ct   = sum(1 for _, t, _ in scored_topics if t.get("stance") == "inconsistent")
    if _ctx_has_stress and len(rejections) >= 3:
        _usv_emotional = "overwhelmed"
    elif _ctx_has_stress or (_usv_resistant_stances >= 1 and len(rejections) >= 2):
        _usv_emotional = "stressed"
    elif _inconsistent_ct > _engaged_count:
        _usv_emotional = "inconsistent"
    elif _ctx_has_motivated or (_engaged_count >= 1 and _usv_resistant_stances == 0):
        _usv_emotional = "motivated"
    else:
        _usv_emotional = "neutral"

    # ── confidence (0–1) ─────────────────────────────────────────────────────
    _usv_topic_canonicals     = {t["canonical"] for _, t, _ in scored_topics}
    _usv_candidate_canonicals = {c["canonical"] for c in candidate_topics}
    _usv_unvalidated          = len(_usv_candidate_canonicals - _usv_topic_canonicals)
    _usv_data_pts             = len(scored_topics) + len(scored_patterns) + min(len(recent_msgs) / 20, 5)
    _usv_data_score           = min(_usv_data_pts / 10, 1.0)
    _usv_core_score           = (core_count / max(len(scored_topics), 1)) if scored_topics else 0.0
    _usv_consistency_penalty  = min(_usv_unvalidated / 5, 0.4)
    _usv_confidence = round(
        max(0.0, min(1.0,
            _usv_data_score  * 0.4
            + _usv_core_score * 0.4
            + (1.0 - _usv_consistency_penalty) * 0.2,
        )),
        2,
    )

    # ── volatility ("low" | "medium" | "high") ───────────────────────────────
    _vol_topic_sets: list[set[str]] = []
    for _vm in recent_msgs[:20]:
        _vbody  = (_vm.get("body") or "").lower()
        _vwords = set(_vbody.split())
        _vgroup: set[str] = set()
        for _vg, _vterms in _CANONICAL_MAP.items():
            for _vt in _vterms:
                if (" " in _vt and _vt in _vbody) or (" " not in _vt and _vt in _vwords):
                    _vgroup.add(_vg)
                    break
        if _vgroup:
            _vol_topic_sets.append(_vgroup)

    _vol_switches = sum(
        1 for _i in range(1, len(_vol_topic_sets))
        if _vol_topic_sets[_i] != _vol_topic_sets[_i - 1]
    )
    _ctx_stress_hits = sum(1 for w in _STRESS_KW    if w in _ctx_lower)
    _ctx_motiv_hits  = sum(1 for w in _MOTIVATED_KW if w in _ctx_lower)
    _emotional_mix   = min(_ctx_stress_hits, _ctx_motiv_hits)

    _vol_score = (
        min(_vol_switches / 10, 1.0) * 0.6
        + min(_emotional_mix / 3, 1.0)  * 0.2
        + (0.2 if _inconsistent_ct >= 2 else 0.0)
    )
    if _vol_score >= 0.5:
        _usv_volatility = "high"
    elif _vol_score >= 0.2:
        _usv_volatility = "medium"
    else:
        _usv_volatility = "low"

    # ── state_drift (0–1) ────────────────────────────────────────────────────
    # Compares behavioral signals in newer messages (0–9) against older ones
    # (10–19) to detect how much the user's state has shifted recently.
    # Three components: topic Jaccard distance, stress-level delta, volatility load.

    def _batch_canonicals(msgs: list[dict]) -> set[str]:
        result: set[str] = set()
        for _bm in msgs:
            _bb = (_bm.get("body") or "").lower()
            _bw = set(_bb.split())
            for _bg, _bt in _CANONICAL_MAP.items():
                for _bterm in _bt:
                    if (" " in _bterm and _bterm in _bb) or (" " not in _bterm and _bterm in _bw):
                        result.add(_bg)
                        break
        return result

    def _batch_stress_ratio(msgs: list[dict]) -> float:
        if not msgs:
            return 0.0
        _hits = sum(1 for _sm in msgs for _sw in _STRESS_KW
                    if _sw in (_sm.get("body") or "").lower())
        return min(_hits / (len(msgs) * 2), 1.0)

    _newer_slice       = recent_msgs[:10]
    _older_slice       = recent_msgs[10:20]
    _newer_canon       = _batch_canonicals(_newer_slice)
    _older_canon       = _batch_canonicals(_older_slice)
    _canon_union       = _newer_canon | _older_canon
    _canon_intersect   = _newer_canon & _older_canon
    _topic_drift_score = 1.0 - (len(_canon_intersect) / max(len(_canon_union), 1))
    _stress_delta      = abs(_batch_stress_ratio(_newer_slice) - _batch_stress_ratio(_older_slice))

    # Gate: skip drift computation when older baseline is too thin to be meaningful.
    if len(_older_slice) < 3:
        _state_drift = 0.0
    else:
        _state_drift = round(min(1.0, max(0.0,
            _topic_drift_score * 0.4
            + _stress_delta    * 0.3
            + min(_vol_score, 1.0) * 0.2
            + (0.1 if _inconsistent_ct >= 1 else 0.0),
        )), 2)

    # ── dampening (applied when state_drift is high and confidence is low) ───
    # Reduces emotional intensity by one level and resistance by ~20%.
    # Prevents amplification of self-reinforced negative states.
    _EMOTIONAL_DOWNGRADE = {
        "overwhelmed":  "stressed",
        "stressed":     "neutral",
        "inconsistent": "neutral",
    }
    if _state_drift > 0.5 and _usv_confidence < 0.7:
        _usv_emotional  = _EMOTIONAL_DOWNGRADE.get(_usv_emotional, _usv_emotional)
        _usv_resistance = round(_usv_resistance * 0.80, 2)
        logger.debug(
            f"[usv] dampening applied (drift={_state_drift} conf={_usv_confidence}) "
            f"for user={user_id}"
        )

    # ── state inertia cap ────────────────────────────────────────────────────
    # Skip cap for users whose primary topic is CORE and currently engaged —
    # they are demonstrably committed, not stuck, so dampening would be wrong.
    _primary_engaged_core = (
        bool(scored_topics)
        and scored_topics[0][1].get("stance") == "engaged"
        and topic_reinforcement.get(scored_topics[0][1].get("id", ""), {}).get("is_core")
    )
    _inertia_turns = int(_usv_avg_days * 1.5)
    if _inertia_turns > 5 and not _primary_engaged_core:
        _excess          = _inertia_turns - 5
        _inertia_damping = min(_excess * 0.10, 0.40)     # 10% per excess turn, max 40%
        _usv_resistance  = round(_usv_resistance * (1.0 - _inertia_damping), 2)
        _usv_stability   = round(min(_usv_stability, 1.0 - _inertia_damping * 0.5), 2)

    # Floor: prevent compounded dampening (state_drift reduction + inertia) from
    # driving resistance to near-zero for users with real constraints on file.
    _usv_resistance = max(_usv_resistance, 0.05)

    # ── dominant_mode ────────────────────────────────────────────────────────
    # High volatility OR high drift blocks stable modes ("maintaining", "building")
    # unless the user is demonstrably grounded (stability > 0.8 AND confidence > 0.7).
    _usv_unstable_signal = (
        _usv_volatility == "high" or _state_drift > 0.5
    ) and not (_usv_stability > 0.8 and _usv_confidence > 0.7)

    if _usv_unstable_signal:
        _usv_mode = "unstable" if _usv_resistance >= 0.3 else "exploring"
    elif _usv_resistance >= 0.5:
        _usv_mode = "struggling"
    elif _usv_stability >= 0.6 and _usv_primary_focus != "none":
        _usv_mode = "maintaining"
    elif len(candidate_topics) >= 2:
        _usv_mode = "exploring"
    else:
        _usv_mode = "building"

    _USV_BLOCK = (
        "[USER STATE VECTOR]\n"
        "The USER STATE VECTOR is advisory, not absolute. "
        "Always prefer recent context when confidence < 0.6. "
        "It is the single source for tone and response strategy when confidence ≥ 0.6. "
        "It is reactive and must not reinforce its own past outputs. "
        "Always apply dampening when instability is detected.\n"
        f"primary_focus:    {_usv_primary_focus}\n"
        f"emotional_state:  {_usv_emotional}\n"
        f"resistance_level: {_usv_resistance}\n"
        f"stability:        {_usv_stability}\n"
        f"dominant_mode:    {_usv_mode}\n"
        f"confidence:       {_usv_confidence}\n"
        f"volatility:       {_usv_volatility}\n"
        f"state_drift:      {_state_drift}"
    )

    _MEMORY_AUTHORITY_HEADER = (
        "[MEMORY AUTHORITY RULES]\n"
        "- USER CONSTRAINTS + REJECTIONS → HIGHEST PRIORITY (absolute, never override)\n"
        "- TOPICS → HIGH PRIORITY (verified persistent user intent)\n"
        "- EMERGING SIGNALS → MEDIUM PRIORITY (hypothesis only, not confirmed fact)\n"
        "- PATTERNS → MEDIUM PRIORITY (behavioral trends, not commitments)\n"
        "- RECENT CONTEXT → LOW PRIORITY (recency signal only)\n"
        "- COMPRESSED MEMORY → LOWEST PRIORITY (historical background only)\n"
        "If any conflict exists between layers: always follow the higher-priority layer "
        "without blending, averaging, or reconciling."
    )

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION ASSEMBLY — strict order, no dynamic reordering
    # ─────────────────────────────────────────────────────────────────────────

    # [IDENTITY] — score 1.0, always include all
    id_lines: list[str] = []
    if user_row.get("name"):        id_lines.append(f"Name: {user_row['name']}")
    if user_row.get("age"):         id_lines.append(f"Age: {user_row['age']}")
    if user_row.get("occupation"):  id_lines.append(f"Occupation: {user_row['occupation']}")
    for field, label in (
        ("life_context",         "Life context"),
        ("motivators",           "Motivated by"),
        ("accountability_style", "Accountability style"),
        ("communication_style",  "Communication style"),
    ):
        val = (identity.get(field) or "").strip()
        if val:
            id_lines.append(f"{label}: {val}")
    pref_labels = [
        p["preference"] for p in preferences
        if p.get("type") == "preference" and (p.get("confidence") or 0) >= 0.5
    ][:4]
    if pref_labels:
        id_lines.append("Preferences: " + "; ".join(pref_labels))
    if id_lines:
        sections.append("[IDENTITY]\n" + "\n".join(id_lines))

    # [REJECTIONS] — score 1.0, highest authority
    rej_lines = [f"- {r['topic']}" for r in sorted_rejs if r.get("topic")]
    metadata["rejection_count"] = len(rej_lines)
    if rej_lines:
        sections.append("[REJECTIONS]\n" + "\n".join(rej_lines))

    # [CONSTRAINTS] — score 0.95, cap 8
    cons_lines = [f"- {c}" for c in scored_constraints]
    metadata["constraint_count"] = len(cons_lines)
    if cons_lines:
        sections.append("[CONSTRAINTS]\n" + "\n".join(cons_lines))

    # [TOPICS] — canonicalized, type-aware scored, conflict-filtered, cap 10
    if scored_topics:
        topic_lines: list[str] = []
        for score, t, type_label in scored_topics:
            imp        = t["importance"]
            days       = _age_days(t["last_mentioned_at"])
            age_str    = f", {_age_label(days)}" if days >= 0 else ""
            stance_str = (
                f" [{t['stance']}]"
                if t.get("stance") and t["stance"] != "neutral" else ""
            )
            # Display: CANONICAL (raw1, raw2) or CANONICAL when only one raw label
            canon_upper = t["canonical"].upper()
            raw         = t["raw_labels"]
            if len(raw) > 1 or (raw and raw[0].lower() != t["canonical"].lower()):
                display = f"{canon_upper} ({', '.join(raw[:3])})"
            else:
                display = canon_upper
            topic_lines.append(
                f"- {display} (importance: {imp:.1f}{age_str}){stance_str}"
            )
            for mid in t.get("member_ids") or []:
                if mid:
                    metadata["active_topic_ids"].append(mid)
            if imp >= 0.8:
                metadata["high_importance_topics"].append(t["canonical"])
        sections.append("[TOPICS — override emerging signals when conflicting]\n" + "\n".join(topic_lines))

    # [EMERGING SIGNALS] — candidate topics promoted from ephemeral_mentions
    # These are NOT committed to topic_memory. They surface weak recurring signals
    # so the model can factor them in without treating them as established facts.
    # A future message with confidence_of_persistence > 0.5 on the same topic
    # will write a full topic_memory entry through the normal extraction path.
    if candidate_topics:
        emerging_lines: list[str] = []
        for c in candidate_topics:
            label      = c["canonical"].upper()
            day_note   = f", {c['distinct_days']}d" if c["distinct_days"] > 1 else ""
            emerging_lines.append(
                f"- {label} ({c['mentions']} mentions{day_note}, confidence: {c['conf_range']})"
            )
        sections.append(
            "[EMERGING SIGNALS — HYPOTHESIS ONLY]\n"
            "Do NOT treat as confirmed topic unless explicitly reinforced by user.\n"
            + "\n".join(emerging_lines)
        )

    # [PATTERNS] — score 0.6, cap 5
    pat_lines = [f"- {p}" for p in scored_patterns]
    if pat_lines:
        sections.append("[PATTERNS]\n" + "\n".join(pat_lines))

    # [RECENT CONTEXT] — score 0.7; capped to ~5 exchanges (~700 chars)
    context_tail = raw_ctx.strip()[-700:] if raw_ctx.strip() else ""
    if context_tail:
        sections.append("[RECENT CONTEXT]\n" + context_tail)

    # [LONG TERM MEMORY] — score 0.3; exempt from drop filter; capped to last chunk
    if cold_memory:
        sections.append(
            "[BACKGROUND ONLY — DO NOT OVERRIDE CURRENT UNDERSTANDING]\n"
            + cold_memory[-600:]
        )

    # [COACHING SIGNALS] — score 0.5; open loops + coaching opportunities; cap 5
    signal_lines: list[str] = []
    for loop in open_loops[:2]:
        if isinstance(loop, dict):
            t      = loop.get("topic", "")
            prefix = "[Committed] " if loop.get("source") == "coach" else ""
            if t:
                signal_lines.append(f"- {prefix}{t}")
        elif loop:
            signal_lines.append(f"- {loop}")
    for opp in coaching_opps:
        desc = (opp.get("description") or "")[:120].strip()
        if desc:
            signal_lines.append(f"- {desc}")
    if signal_lines:
        sections.append("[COACHING SIGNALS]\n" + "\n".join(signal_lines[:5]))

    metadata["dropped_items_count"] = dropped_items_count
    metadata["conflict_drops"]      = conflict_drops
    metadata["topic_reinforcement"] = topic_reinforcement
    # USV summary — used by _generate_voice_reply for tone guidance without reparsing the prompt
    metadata["usv"] = {
        "dominant_mode":   _usv_mode,
        "emotional_state": _usv_emotional,
        "resistance":      _usv_resistance,
        "confidence":      _usv_confidence,
        "volatility":      _usv_volatility,
        "state_drift":     _state_drift,
        "primary_focus":   _usv_primary_focus,
    }

    body         = "\n\n".join(sections)
    memory_block = (
        _MEMORY_AUTHORITY_HEADER + "\n\n"
        + _USV_BLOCK + "\n\n"
        + body
    ) if body else (_MEMORY_AUTHORITY_HEADER + "\n\n" + _USV_BLOCK)
    logger.info(
        f"[memory_ctx] assembled for user={user_id}: "
        f"topics={len(scored_topics)} (core={core_count} normal={normal_count}) "
        f"emerging={len(candidate_topics)} (raw={len(raw_candidates)}) "
        f"msg_batch={len(recent_msgs)} constraints={metadata['constraint_count']} "
        f"rejections={metadata['rejection_count']} patterns={len(pat_lines)} "
        f"dropped={dropped_items_count} conflicts={len(conflict_drops)}"
    )
    return memory_block, metadata


async def _build_life_state_block(user_id: str) -> tuple[str, str]:
    """Thin wrapper — delegates to _build_memory_context."""
    memory_block, _ = await _build_memory_context(user_id)
    return memory_block, ""


async def _compress_exchanges(exchanges_text: str) -> str:
    """
    Compress a block of old conversation exchanges into structured cold memory.
    Called only when context_doc overflows — one Gemini call per compression event.
    Returns a dated block appended permanently to compressed_memory.

    Strict grounding: only facts explicitly present in exchanges_text are allowed.
    No inference, no generalization, no context from outside this block.
    """
    prompt = (
        "You are given ONLY the following exchanges. You have no other context about this user.\n\n"
        "You are compressing these coaching conversation exchanges into structured memory.\n\n"
        "STRICT RULES — YOU MUST FOLLOW THESE:\n"
        "- ONLY include facts explicitly stated in the exchanges below\n"
        "- DO NOT infer, guess, or generalize anything\n"
        "- DO NOT interpret behavior or add context beyond what is directly stated\n"
        "- DO NOT create patterns unless the same thing is explicitly repeated multiple times IN THESE EXCHANGES\n"
        "- If a section has nothing explicitly verifiable, leave it blank — do NOT fill it\n\n"
        "OUTPUT FORMAT:\n"
        "KEY FACTS: [verbatim or near-verbatim facts the user stated about themselves]\n"
        "EVENTS: [only explicitly mentioned events, dates, or commitments]\n"
        "PATTERNS: [only if the SAME behavior is explicitly repeated in these exchanges — not inferred]\n"
        "NOTABLE: [only direct quotes or statements of clear long-term significance]\n\n"
        "The exchanges to compress:\n\n"
        f"{exchanges_text}\n\n"
        "Plain text only. No markdown. No preamble. No invented content."
    )
    try:
        model    = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(prompt)
        compressed = response.text.strip()
        ts_label   = datetime.now(timezone.utc).strftime("%b %Y")
        return f"--- {ts_label} ---\n{compressed}\n"
    except Exception:
        logger.exception("[compress] Gemini compression failed")
        return ""


async def _update_context_async(user_id: str, user_msg: str, bot_reply: str) -> None:
    """
    Fire-and-forget task called after every successful reply:
    1. Append exchange to context_doc (hot memory, verbatim).
    2. When context_doc exceeds threshold: compress oldest exchange pairs into
       compressed_memory (cold memory, permanent), then remove them from context_doc.
       Exchange boundaries are always preserved — never cuts a User/Bot pair.
    3. Detect new/resolved open loops via regex and update memory_doc.
    """
    try:
        ts           = datetime.now(timezone.utc).strftime("%b %-d %-I:%M%p").lower()
        new_exchange = f"{ts} | User: {user_msg[:200]}\n{ts} | Bot: {bot_reply[:200]}\n"

        res = (
            supabase.table("user_memory")
            .select("memory_doc, context_doc, compressed_memory")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        row_exists        = bool(res.data)
        memory_doc        = (res.data[0].get("memory_doc")        or {}) if res.data else {}
        context_doc       = (res.data[0].get("context_doc")       or "") if res.data else ""
        compressed_memory = (res.data[0].get("compressed_memory") or "") if res.data else ""

        context_doc += new_exchange

        # Size-driven compression — triggered by character count, never by time.
        # Content-based pairing: a valid exchange is a "| User: " line immediately
        # followed by a "| Bot: " line. Any other combination is silently discarded —
        # this is safe against orphan lines anywhere in the doc, not just at the tail.
        if len(context_doc) > 3500:
            lines = [l for l in context_doc.strip().split("\n") if l]
            pairs: list[tuple[str, str]] = []
            i = 0
            while i < len(lines):
                if "| User: " in lines[i]:
                    if i + 1 < len(lines) and "| Bot: " in lines[i + 1]:
                        pairs.append((lines[i], lines[i + 1]))
                        i += 2
                    else:
                        i += 1  # User line with no Bot line — discard
                else:
                    i += 1  # Bot line without User, or unknown format — discard

            if len(pairs) > 8:
                to_compress = pairs[:8]
                keep_pairs  = pairs[8:]

                compress_text    = "\n".join(f"{p[0]}\n{p[1]}" for p in to_compress)
                compressed_chunk = await _compress_exchanges(compress_text)
                if compressed_chunk:
                    compressed_memory = (compressed_memory or "") + compressed_chunk
                    logger.info(f"[compress] appended cold memory for user={user_id} ({len(compressed_memory)} chars total)")

                # Rebuild from complete pairs only — no orphan, ever
                context_doc = "\n".join(f"{p[0]}\n{p[1]}" for p in keep_pairs)

        # Open loop detection
        loops = list(memory_doc.get("open_loops") or [])

        if _LOOP_RESOLVE_RE.search(user_msg):
            resolved = []
            for loop in loops:
                topic_words = [
                    w for w in (loop.get("topic", "") if isinstance(loop, dict) else str(loop)).lower().split()
                    if len(w) > 3
                ]
                if any(w in user_msg.lower() for w in topic_words):
                    resolved.append(loop)
            for r in resolved:
                loops.remove(r)
                logger.info(f"[loops] resolved '{r}' for user={user_id}")

        if _LOOP_TIME_RE.search(user_msg):
            for pattern, category in _LOOP_TOPIC_PATTERNS:
                if pattern.search(user_msg):
                    already = any(
                        (l.get("topic", "") if isinstance(l, dict) else str(l)).startswith(category)
                        for l in loops
                    )
                    if not already:
                        loops.append({
                            "source": "user",
                            "topic":  f"{category} mentioned: {user_msg[:120]}",
                            "added":  date.today().isoformat(),
                        })
                        logger.info(f"[loops] new loop '{category}' for user={user_id}")

        memory_doc["open_loops"] = loops
        now_iso = datetime.now(timezone.utc).isoformat()

        update_payload = {
            "context_doc":       context_doc,
            "compressed_memory": compressed_memory,
            "memory_doc":        memory_doc,
            "updated_at":        now_iso,
        }

        if row_exists:
            supabase.table("user_memory").update(update_payload).eq("user_id", user_id).execute()
        else:
            supabase.table("user_memory").upsert(
                {"user_id": user_id, **update_payload},
                on_conflict="user_id",
            ).execute()

        logger.info(f"[context] updated user={user_id} hot={len(context_doc)}c cold={len(compressed_memory)}c loops={len(loops)}")
    except Exception:
        logger.exception(f"[context] async update failed for user={user_id}")


# ---------------------------------------------------------------------------
# Step 4 — Voice generator
# ---------------------------------------------------------------------------

async def _generate_voice_reply(
    user_id:          str,
    message_body:     str,
    coach:            dict,
    execution_result: str,
    user_timezone:    str = "America/New_York",
) -> str:
    """
    Build the coach reply. Context layers injected into user_prompt:
      1. Persona (generated_system_prompt + few-shot examples) → system_instruction
      2. _COACHING_PHILOSOPHY + HUMAN_BEHAVIOR_RULES + CONVICTION_RULES → system_instruction
      3. Life state block (active topics, open loops, patterns, recent + cold memory) → user_prompt
      4. Live coaching data (goals today, streaks, reminders, nutrition) → user_prompt

    After generating: async fire-and-forget updates context_doc and topic_memory.
    """
    system_prompt = coach.get("generated_system_prompt") or ""

    # Persona examples
    try:
        from routes.ai import get_persona_examples_block
        examples_block = await get_persona_examples_block(coach)
        if examples_block and examples_block not in system_prompt:
            system_prompt += "\n\nVOICE CALIBRATION — match this speaking style EXACTLY:\n\n" + examples_block
    except Exception:
        logger.exception("[voice] persona augmentation failed")

    # Message history for Gemini chat continuity (last 30, ends on "model" turn)
    try:
        msgs_res = (
            supabase.table("messages")
            .select("direction, body, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(30)
            .execute()
        )
        history = list(reversed(msgs_res.data or []))
    except Exception:
        logger.exception(f"[voice] message history fetch failed for user={user_id}")
        history = []

    oldest_created_at = history[0]["created_at"] if history else None

    gemini_history = [
        {"role": "user" if m["direction"] == "inbound" else "model", "parts": [m["body"]]}
        for m in history
    ]
    # Strip trailing "user" turns — inbound is saved before this runs, creating
    # a double user-turn that makes Gemini ignore all history.
    while gemini_history and gemini_history[-1]["role"] == "user":
        gemini_history.pop()

    # ── Memory context — single assembly point ──────────────────────────────
    # _build_memory_context is the ONLY source of memory injection into the prompt.
    # It reads user profile, user_memory, topic_memory, and coaching_opportunities.
    memory_block, mem_meta = await _build_memory_context(user_id)

    # ── Live coaching data (operational — NOT memory) ────────────────────────
    try:
        from services.coaching_service import get_coaching_context
        ctx            = await get_coaching_context(user_id, user_timezone)
        coaching_block = ctx.to_prompt_block()
        fitness_meta   = ctx.provider_data.get("fitness", {})
    except Exception:
        logger.exception(f"[voice] coaching context failed for user={user_id}")
        ctx = None; coaching_block = ""; fitness_meta = {}

    streak_hints: list[str] = []
    for s in fitness_meta.get("streak_summary", []):
        activity = s.get("activity", "")
        current  = s.get("current", 0)
        if current >= 7:
            streak_hints.append(f"{activity}: {current}-day streak — make them feel what it costs to break it")
        elif current >= 3:
            streak_hints.append(f"{activity}: {current}-day streak — acknowledge the build, make it real")
        elif current == 0:
            streak_hints.append(f"{activity}: no current streak — ask one question about what got in the way")
    streak_section = ("\nStreak context:\n" + "\n".join(streak_hints) + "\n") if streak_hints else ""

    anomaly_hint = (
        "\nDATA ANOMALY: something doesn't add up — investigate before praising or pushing.\n"
        if (ctx and ctx.has_anomaly) else ""
    )

    coaching_section = ""
    if coaching_block or streak_section or anomaly_hint:
        coaching_section = (
            "COACHING DATA (today):\n"
            f"{coaching_block}\n"
            f"{streak_section}"
            f"{anomaly_hint}"
        )

    # ── Constraint reminder — natural language, not bureaucratic ─────────────
    total_limits = mem_meta.get("rejection_count", 0) + mem_meta.get("constraint_count", 0)
    constraint_reminder = (
        "\nThis user has specific things they've said no to. "
        "Check [CONSTRAINTS] and [REJECTIONS] before any suggestion.\n"
    ) if total_limits > 0 else ""

    # ── Onboarding note — differentiated for new vs returning users ───────────
    history_len = len(history)
    if history_len == 0:
        onboarding_note = (
            "FIRST MESSAGE FROM THIS USER:\n"
            "You have no history with this person yet. Don't fake continuity.\n"
            "Pick ONE thing from their message and go deep on it. Not a survey of their goals.\n"
            "Don't explain how coaching works. Don't say you'll remember things. Just respond.\n"
            "One question only if you ask at all.\n\n"
        )
    elif history_len < 6:
        onboarding_note = (
            "EARLY RELATIONSHIP:\n"
            "You're still building context with this person. Don't fill gaps with assumptions.\n"
            "Reference at most one specific thing from before. Mirror their communication style.\n"
            "Don't draw pattern conclusions yet. Don't summarize their situation back to them.\n\n"
        )
    else:
        onboarding_note = ""

    # ── Tone guidance from USV ────────────────────────────────────────────────
    # Translate key USV values into one concrete behavioral instruction so the
    # model has a single clear directive for this turn rather than inferring from
    # numeric fields alone.
    usv = mem_meta.get("usv", {})
    _mode        = usv.get("dominant_mode", "building")
    _emotion     = usv.get("emotional_state", "neutral")
    _resistance  = usv.get("resistance", 0.0)
    _confidence  = usv.get("confidence", 0.5)
    _volatility  = usv.get("volatility", "low")
    _drift       = usv.get("state_drift", 0.0)

    _tone_lines: list[str] = []
    if _emotion in ("overwhelmed", "stressed"):
        _tone_lines.append("acknowledge what they said before anything else")
    if _mode == "struggling":
        _tone_lines.append("no new asks or suggestions this turn — just be present")
    elif _mode == "unstable":
        _tone_lines.append("short message, one question about right now only")
    elif _mode == "maintaining":
        _tone_lines.append("they're stable — challenge them toward what's next")
    if _resistance > 0.5:
        _tone_lines.append("work only within what they've already accepted — no new pushes")
    if _confidence < 0.6:
        _tone_lines.append("treat this message as your primary signal — don't reference trends")
    if _volatility == "high" or _drift > 0.5:
        _tone_lines.append("keep it short, stay present, don't plan ahead")

    tone_guidance = (
        "THIS TURN: " + "; ".join(_tone_lines) + ".\n"
    ) if _tone_lines else ""

    user_prompt = (
        f"MEMORY CONTEXT:\n{memory_block or 'No prior context available.'}\n\n"
        "---\n\n"
        + onboarding_note
        + (coaching_section + "\n" if coaching_section else "")
        + "WHAT YOU KNOW VS WHAT YOU DON'T:\n"
        "- Coaching context shows what was RECEIVED — no check-in means no data came in, not that they skipped.\n"
        "- If the user's message confirms something happened — treat it as fact.\n"
        "- Anything not confirmed is UNKNOWN. Don't fill gaps with assumptions.\n\n"
        f"USER MESSAGE:\n{message_body}\n\n"
        f"Actions taken: {execution_result or 'none'}"
        f"{constraint_reminder}\n"
        + tone_guidance
        + "HOW TO RESPOND:\n"
        "Read the message first. The message beats everything else.\n"
        "One question only, if you ask at all — make it the sharpest one.\n"
        "Never assume something happened that the user didn't confirm.\n"
        "If their message is short or vague, pick the ONE most relevant open thread.\n"
        "SMS. Real. Never more than 4 sentences."
    )

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-lite",
        system_instruction=(
            f"{system_prompt}\n\n"
            f"{_COACHING_PHILOSOPHY}\n\n"
            f"{_GOAL_OWNERSHIP_RULES}\n\n"
            f"{_MEMORY_REASONING_RULES}\n\n"
            f"{_COACHING_EXPERIENCE_RULES}\n\n"
            f"{HUMAN_BEHAVIOR_RULES}\n\n"
            f"{CONVICTION_RULES}"
        ),
    )
    chat     = model.start_chat(history=gemini_history)
    response = chat.send_message(user_prompt)
    reply    = _strip_markdown(_strip_emojis(response.text.strip()))
    logger.info(f"[voice] reply for user={user_id} ({len(reply)} chars)")

    # Post-reply tasks run sequentially in a single fire-and-forget task so each
    # write sees the previous one's changes — eliminates memory_doc write races.
    async def _post_reply_updates() -> None:
        await _update_context_async(user_id, message_body, reply)
        await _extract_topic_memories(user_id, message_body)
        await _extract_user_memory_async(user_id, message_body, reply)

    asyncio.create_task(_post_reply_updates())

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

      0. Personality swap   — instant if message matches AAAA9999 and ID is found
      1. Gatekeeper         — return minimal ack if no coach persona exists
      2a. Notification reply — if user has a NOTIFIED activity notification, handle
                               YES/NO/RESCHEDULE before the classifier sees the message
      2b. Classify           — one Gemini call → GOAL/NUTRITION/TASK/JOURNAL/BET/GENERAL
      3. Handle              — category-specific DB writes → execution_result string
      4. Voice               — Gemini chat reply in the coach's persona voice
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

    # 2a. Notification reply — check before classifier so YES/NO/RESCHEDULE responses
    #     to activity pre-alerts are handled with full context instead of as generic chat.
    notif_result = await handle_notification_reply(user_id, message_body, user_timezone)
    if notif_result is not None:
        logger.info(f"[pipeline] user={user_id} notification reply: {notif_result}")
        try:
            reply = await _generate_voice_reply(user_id, message_body, coach, notif_result, user_timezone)
        except Exception:
            logger.exception(f"[pipeline] voice generator failed for notification reply user={user_id}")
            reply = "Got it."
        return reply

    # 2b. Classify — short-circuit if a pending_delete is awaiting confirmation,
    #     otherwise use the multi-intent classifier (returns 1–2 categories).
    pending_del = supabase.table("user_context").select("id").eq("user_id", user_id).eq("type", "pending_delete").limit(1).execute()
    if pending_del.data:
        categories: list[str] = ["DELETE_GOAL"]
    else:
        categories = await classify_multi(message_body)
    logger.info(f"[pipeline] user={user_id} categories={categories}")

    # 3. Route to all matched handlers — run sequentially, combine results
    execution_parts: list[str] = []
    for category in categories:
        handler = _HANDLER_MAP.get(category)
        if handler is not None:
            try:
                part = await handler(user_id, message_body, user_timezone)
                if part:
                    execution_parts.append(part)
                logger.info(f"[pipeline] user={user_id} [{category}]: {(part or 'no result')[:80]}")
            except Exception:
                logger.exception(f"[pipeline] handler [{category}] failed for user={user_id}")
    execution_result = " | ".join(execution_parts)

    # 4. Voice generator
    try:
        reply = await _generate_voice_reply(user_id, message_body, coach, execution_result, user_timezone)
    except Exception:
        logger.exception(f"[pipeline] voice generator failed for user={user_id}")
        reply = "Got you. Let's keep going."
    return reply
