"""
Mock messaging — testing without Sendblue.

All functions write to the messages table and print to console.
Nothing here calls Sendblue. Safe to run locally for full pipeline testing.

Endpoints (mount at /mock):
    GET  /mock/chat-ui                  — browser chat UI for simulating SMS
    POST /mock/simulate-sms             — full pipeline, captures replies instead of sending
    DELETE /mock/reset-user             — wipe a test user from Supabase for fresh run
    POST /mock/test-message/{user_id}   — sends a fixed test string
    POST /mock/welcome/{user_id}        — welcome message using coach name + goals
    POST /mock/daily-sim/{user_id}      — simulates today's check-ins via AI
"""

import logging
import os
from datetime import datetime
from contextlib import contextmanager

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
router = APIRouter()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ---------------------------------------------------------------------------
# Core mock send — reuse everywhere instead of calling Twilio
# ---------------------------------------------------------------------------

def send_message(user_id: str, message: str) -> None:
    """
    Persist an outbound message to the DB and print it to the console.
    Drop-in replacement for scheduler.send_sms() that skips Twilio entirely.
    When Twilio is live, callers can swap this for the real send_sms + log_message pair.
    """
    try:
        supabase.table("messages").insert({
            "user_id": user_id,
            "direction": "outbound",
            "body": message,
        }).execute()
    except Exception:
        logger.exception(f"[MOCK] Failed to save message for user {user_id}")

    print(f"[SMS MOCK] -> {user_id}: {message}")
    logger.info(f"[SMS MOCK] -> {user_id}: {message[:120]}")


# ---------------------------------------------------------------------------
# Welcome message
# ---------------------------------------------------------------------------

async def send_welcome_message(user_id: str) -> str:
    """
    Generate a personalized welcome SMS using the coach's AI personality,
    then mock-send it. Falls back to a static template if AI fails.
    """
    from routes.ai import generate_welcome_text

    try:
        msg = await generate_welcome_text(user_id)
    except Exception as e:
        logger.error(f"AI welcome failed for {user_id}, using fallback: {e}")
        coach_res = (
            supabase.table("coach_settings")
            .select("coach_name")
            .eq("user_id", user_id)
            .execute()
        )
        coach_name = coach_res.data[0]["coach_name"] if coach_res.data else "Coach"
        goals_res = (
            supabase.table("goals")
            .select("activity")
            .eq("user_id", user_id)
            .execute()
        )
        activities = [g["activity"] for g in (goals_res.data or [])]
        if activities:
            listed = ", ".join(activities[:3])
            if len(activities) > 3:
                listed += f" and {len(activities) - 3} more"
            msg = (
                f"Hey! 👋 {coach_name} here — your accountability coach. "
                f"I see you're working on: {listed}. "
                f"I'll be checking in with you every day. Let's get it! 💪"
            )
        else:
            msg = (
                f"Hey! 👋 {coach_name} here — your accountability coach. "
                f"I'll be checking in with you every day. Reply any time. Let's go! 💪"
            )

    send_message(user_id, msg)
    return msg


# ---------------------------------------------------------------------------
# Daily simulation
# ---------------------------------------------------------------------------

def run_daily_simulation(user_id: str) -> list[str]:
    """
    Match today's goals and mock-send an AI-generated check-in for each one.
    Reuses generate_checkin_text from ai.py — same logic the real scheduler uses,
    just without Twilio at the end.
    """
    from routes.ai import generate_checkin_text
    from routes.scheduler import run_async

    today = DAY_NAMES[datetime.now().weekday()]

    goals_res = (
        supabase.table("goals")
        .select("activity, days")
        .eq("user_id", user_id)
        .execute()
    )

    todays_goals = [
        g["activity"]
        for g in (goals_res.data or [])
        if today in (g.get("days") or [])
    ]

    if not todays_goals:
        logger.info(f"[MOCK] No goals for user {user_id} on {today}")
        return []

    sent: list[str] = []
    for goal in todays_goals:
        try:
            text = run_async(generate_checkin_text(user_id, goal))
            send_message(user_id, text)
            sent.append(text)
        except Exception as e:
            logger.error(f"[MOCK] Failed check-in for goal '{goal}': {e}")

    return sent


# ---------------------------------------------------------------------------
# SMS Simulation — full pipeline, no Sendblue
# ---------------------------------------------------------------------------

class SimulateSmsRequest(BaseModel):
    from_number: str = "+19176316464"
    message: str


@contextmanager
def _capture_replies(captured: list[str]):
    """Patch send_reply in all modules that imported it to capture calls."""
    import services.messaging as _msg
    import services.onboarding as _onb
    import routes.sms as _sms

    original_msg = _msg.send_reply
    original_onb = _onb.send_reply
    original_sms = _sms.send_reply

    def _capture(to_number: str, message: str) -> None:
        captured.append(message)

    _msg.send_reply = _capture
    _onb.send_reply = _capture
    _sms.send_reply = _capture
    try:
        yield
    finally:
        _msg.send_reply = original_msg
        _onb.send_reply = original_onb
        _sms.send_reply = original_sms


@router.post("/simulate-sms")
async def simulate_sms(req: SimulateSmsRequest):
    """
    Run the full inbound SMS pipeline (including onboarding) and return
    what would have been sent to the user — without calling Sendblue.
    """
    import asyncio
    from routes.sms import _process_inbound

    captured: list[str] = []
    bt = BackgroundTasks()

    with _capture_replies(captured):
        await _process_inbound(req.from_number, req.message, bt)
        # Drain background tasks inline while patch is still active.
        # Clear the list first so FastAPI doesn't re-run them after the response.
        tasks_to_run = list(bt.tasks)
        bt.tasks.clear()
        for task in tasks_to_run:
            try:
                if asyncio.iscoroutinefunction(task.func):
                    await task.func(*task.args, **task.kwargs)
                else:
                    task.func(*task.args, **task.kwargs)
            except Exception as e:
                logger.exception(f"[MOCK] Background task failed: {e}")

    return {
        "from_number": req.from_number,
        "message": req.message,
        "replies": captured,
    }


@router.delete("/reset-user")
async def reset_user(phone: str = Query(..., description="E.164 phone number e.g. +19176316464")):
    """Delete a test user from Supabase (auth + public) so onboarding can restart fresh."""
    placeholder_email = f"sms_{phone.lstrip('+').replace(' ', '')}@stackd.app"
    deleted = []
    errors = []

    try:
        supabase.table("users").delete().eq("phone", phone).execute()
        deleted.append("public.users")
    except Exception as e:
        errors.append(f"public.users: {e}")

    try:
        # Find auth user by email and delete
        users_res = supabase.auth.admin.list_users()
        for u in (users_res or []):
            if u.email == placeholder_email:
                supabase.auth.admin.delete_user(u.id)
                deleted.append(f"auth.users ({u.id})")
                break
    except Exception as e:
        errors.append(f"auth.users: {e}")

    return {"deleted": deleted, "errors": errors, "phone": phone}


@router.get("/chat-ui", response_class=HTMLResponse)
async def chat_ui():
    """Self-contained SMS simulation UI — no Sendblue, full pipeline accuracy."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>stackd SMS Simulator</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1c1c1e; color: #fff; height: 100dvh; display: flex; flex-direction: column; }
  header { background: #2c2c2e; padding: 14px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #3a3a3c; flex-shrink: 0; }
  header h1 { font-size: 16px; font-weight: 600; }
  #phone-input { background: #3a3a3c; border: none; color: #fff; padding: 6px 10px; border-radius: 8px; font-size: 13px; width: 180px; }
  #reset-btn { background: #ff453a; color: #fff; border: none; padding: 6px 12px; border-radius: 8px; font-size: 13px; cursor: pointer; }
  #reset-btn:hover { background: #ff6961; }
  #messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  .msg-group { display: flex; flex-direction: column; gap: 4px; }
  .bubble { max-width: 72%; padding: 10px 14px; border-radius: 18px; font-size: 15px; line-height: 1.45; word-break: break-word; }
  .user-wrap { align-self: flex-end; display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
  .bot-wrap { align-self: flex-start; display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
  .user .bubble { background: #0a84ff; color: #fff; border-bottom-right-radius: 4px; }
  .bot .bubble { background: #2c2c2e; color: #fff; border-bottom-left-radius: 4px; }
  .json-toggle { font-size: 11px; color: #8e8e93; cursor: pointer; padding: 2px 6px; border-radius: 4px; background: #2c2c2e; border: 1px solid #3a3a3c; }
  .json-toggle:hover { background: #3a3a3c; }
  .json-block { display: none; background: #0d0d0f; border: 1px solid #3a3a3c; border-radius: 10px; padding: 10px; font-family: monospace; font-size: 11px; color: #a8ff78; max-width: 90%; overflow-x: auto; white-space: pre; }
  .json-block.visible { display: block; }
  .typing { align-self: flex-start; background: #2c2c2e; border-radius: 18px; border-bottom-left-radius: 4px; padding: 10px 16px; }
  .typing span { display: inline-block; width: 6px; height: 6px; background: #8e8e93; border-radius: 50%; animation: bounce 1.2s infinite; margin: 0 2px; }
  .typing span:nth-child(2) { animation-delay: 0.2s; }
  .typing span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes bounce { 0%,60%,100% { transform: translateY(0); } 30% { transform: translateY(-6px); } }
  footer { padding: 12px 16px; background: #2c2c2e; border-top: 1px solid #3a3a3c; display: flex; gap: 10px; flex-shrink: 0; }
  #msg-input { flex: 1; background: #3a3a3c; border: none; color: #fff; padding: 10px 14px; border-radius: 20px; font-size: 15px; outline: none; }
  #msg-input::placeholder { color: #636366; }
  #send-btn { background: #0a84ff; border: none; color: #fff; width: 36px; height: 36px; border-radius: 50%; cursor: pointer; font-size: 18px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; align-self: center; }
  #send-btn:disabled { background: #3a3a3c; cursor: not-allowed; }
</style>
</head>
<body>
<header>
  <h1>stackd SMS Simulator</h1>
  <div style="display:flex;gap:8px;align-items:center;">
    <input id="phone-input" type="text" value="+19176316464" placeholder="+1..." />
    <button id="reset-btn" onclick="resetUser()">Reset User</button>
  </div>
</header>
<div id="messages"></div>
<footer>
  <input id="msg-input" type="text" placeholder="iMessage" autocomplete="off" />
  <button id="send-btn" onclick="sendMessage()">&#8593;</button>
</footer>
<script>
  const messagesEl = document.getElementById('messages');
  const inputEl = document.getElementById('msg-input');
  const sendBtn = document.getElementById('send-btn');

  inputEl.addEventListener('keydown', e => { if (e.key === 'Enter') sendMessage(); });

  function getPhone() { return document.getElementById('phone-input').value.trim(); }

  function addUserBubble(text) {
    const wrap = document.createElement('div');
    wrap.className = 'msg-group user-wrap user';
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text;
    wrap.appendChild(bubble);
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function addBotBubbles(replies, rawJson) {
    replies.forEach((text, i) => {
      const wrap = document.createElement('div');
      wrap.className = 'msg-group bot-wrap bot';
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.textContent = text;
      wrap.appendChild(bubble);
      if (i === replies.length - 1) {
        const toggle = document.createElement('button');
        toggle.className = 'json-toggle';
        toggle.textContent = 'Show JSON';
        const jsonBlock = document.createElement('pre');
        jsonBlock.className = 'json-block';
        jsonBlock.textContent = JSON.stringify(rawJson, null, 2);
        toggle.onclick = () => {
          jsonBlock.classList.toggle('visible');
          toggle.textContent = jsonBlock.classList.contains('visible') ? 'Hide JSON' : 'Show JSON';
        };
        wrap.appendChild(toggle);
        wrap.appendChild(jsonBlock);
      }
      messagesEl.appendChild(wrap);
    });
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function addTyping() {
    const el = document.createElement('div');
    el.className = 'typing';
    el.id = 'typing-indicator';
    el.innerHTML = '<span></span><span></span><span></span>';
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return el;
  }

  async function sendMessage() {
    const text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = '';
    sendBtn.disabled = true;
    addUserBubble(text);
    const typing = addTyping();
    try {
      const res = await fetch('/mock/simulate-sms', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from_number: getPhone(), message: text }),
      });
      const data = await res.json();
      typing.remove();
      if (data.replies && data.replies.length > 0) {
        addBotBubbles(data.replies, data);
      } else {
        addBotBubbles(['(no reply captured)'], data);
      }
    } catch (err) {
      typing.remove();
      addBotBubbles([`Error: ${err.message}`], { error: err.message });
    }
    sendBtn.disabled = false;
    inputEl.focus();
  }

  async function resetUser() {
    const phone = getPhone();
    if (!confirm(`Delete user ${phone} from Supabase?`)) return;
    try {
      const res = await fetch(`/mock/reset-user?phone=${encodeURIComponent(phone)}`, { method: 'DELETE' });
      const data = await res.json();
      messagesEl.innerHTML = '';
      const note = document.createElement('div');
      note.style.cssText = 'text-align:center;color:#636366;font-size:13px;padding:20px;';
      note.textContent = `User reset. Deleted: ${data.deleted.join(', ') || 'nothing found'}`;
      messagesEl.appendChild(note);
    } catch (err) {
      alert(`Reset failed: ${err.message}`);
    }
  }

  inputEl.focus();
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# HTTP endpoints — testable via /docs or curl
# ---------------------------------------------------------------------------

@router.post("/test-message/{user_id}")
async def test_message(user_id: str):
    """
    Quickest smoke test: sends a fixed string to confirm the DB write and
    console log pipeline works end-to-end.
    """
    msg = "Test message from your coach 👋"
    send_message(user_id, msg)
    return {"sent": msg}


@router.post("/welcome/{user_id}")
async def welcome(user_id: str):
    """Send a welcome message using the user's real coach name and goal list."""
    msg = await send_welcome_message(user_id)
    return {"sent": msg}


@router.post("/daily-sim/{user_id}")
async def daily_sim(user_id: str):
    """
    Run today's check-in simulation for a specific user without Twilio.
    Matches goals against today's day of week and generates an AI message per goal.
    """
    sent = run_daily_simulation(user_id)
    if not sent:
        today = DAY_NAMES[datetime.now().weekday()]
        return {"sent": [], "note": f"No goals matched {today}"}
    return {"sent": sent, "count": len(sent)}


class SeedRequest(BaseModel):
    name: str = "Jordan"
    age: str = "27"
    occupation: str = "working"
    coach_name: str = "Alex"
    personality_preset: str = "hype"
    coach_talk_style: list = ["Motivational", "Energetic"]
    coach_emoji_usage: str = "Lots"
    coach_message_length: str = "Balanced"
    coach_miss_behavior: str = "Tough love"
    coach_intensity: int = 5
    obstacles: list = ["Inconsistency", "Busy schedule", "No accountability"]
    success_vision: str = "Running a 5K without stopping and finally building a consistent gym habit"
    checkin_time: str = "7:00 AM"
    timezone: str = "America/Los_Angeles"


@router.post("/seed/{user_id}")
async def seed_db(user_id: str, req: SeedRequest = SeedRequest()):
    """
    Write test data to Supabase for a user and generate their coach personality.
    Called from the dev page when a user ID is set — lets you test the AI chat
    without completing the full quiz.
    """
    from routes.ai import build_coach_personality

    try:
        # Fetch email from auth so we satisfy the NOT NULL constraint
        try:
            auth_user = supabase.auth.admin.get_user_by_id(user_id)
            email = auth_user.user.email if auth_user.user else f"{user_id}@dev.local"
        except Exception:
            email = f"{user_id}@dev.local"

        # Upsert user profile (only columns that exist in the users table)
        supabase.table("users").upsert({
            "id": user_id,
            "email": email,
            "name": req.name,
            "age": int(req.age) if req.age.isdigit() else None,
            "occupation": req.occupation,
        }, on_conflict="id").execute()

        # Store obstacles + success_vision in user_context
        context_entries = []
        for obstacle in req.obstacles:
            context_entries.append({
                "user_id": user_id,
                "type": "obstacle",
                "description": obstacle,
                "expires_at": None,
            })
        if req.success_vision:
            context_entries.append({
                "user_id": user_id,
                "type": "success_vision",
                "description": req.success_vision,
                "expires_at": None,
            })
        if context_entries:
            supabase.table("user_context").delete().eq("user_id", user_id).in_("type", ["obstacle", "success_vision"]).execute()
            supabase.table("user_context").insert(context_entries).execute()

        # Upsert coach settings
        supabase.table("coach_settings").upsert({
            "user_id": user_id,
            "coach_name": req.coach_name,
            "personality_preset": req.personality_preset,
            "coach_personality": req.personality_preset,
            "coach_talk_style": req.coach_talk_style,
            "coach_emoji_usage": req.coach_emoji_usage,
            "coach_message_length": req.coach_message_length,
            "coach_miss_behavior": req.coach_miss_behavior,
            "coach_intensity": req.coach_intensity,
        }, on_conflict="user_id").execute()

        # Upsert schedule
        supabase.table("schedule").upsert({
            "user_id": user_id,
            "checkin_time": req.checkin_time,
            "timezone": req.timezone,
            "motivation_enabled": True,
            "motivation_frequency": "Once a day",
            "motivation_styles": ["Hype & pump-up 🎉", "Tough love 💪"],
        }, on_conflict="user_id").execute()

        # Delete existing goals then insert fresh ones
        supabase.table("goals").delete().eq("user_id", user_id).execute()
        goals = [
            {"user_id": user_id, "activity": "Running",             "category": "Fitness & Sports", "days": ["monday", "wednesday", "friday"]},
            {"user_id": user_id, "activity": "Gym / Weightlifting", "category": "Fitness & Sports", "days": ["tuesday", "thursday", "saturday"]},
            {"user_id": user_id, "activity": "Yoga",                "category": "Fitness & Sports", "days": ["monday", "wednesday", "sunday"]},
            {"user_id": user_id, "activity": "Reading",             "category": "Education",        "days": ["monday", "tuesday", "wednesday", "thursday", "friday"]},
            {"user_id": user_id, "activity": "Meditation",          "category": "Mind",             "days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]},
        ]
        supabase.table("goals").insert(goals).execute()

        # Generate coach personality
        await build_coach_personality(user_id)

        return {"status": "ok", "message": f"Seeded DB and generated personality for {user_id}"}

    except Exception as e:
        logger.exception(f"[MOCK SEED] Failed for {user_id}")
        raise HTTPException(status_code=500, detail=str(e))


class ChatRequest(BaseModel):
    message: str


@router.post("/chat/{user_id}")
async def mock_chat(user_id: str, req: ChatRequest):
    """
    Simulate a full SMS conversation turn without Twilio.
    Delegates to the shared process_inbound_sms pipeline so local testing
    is 100% representative of the live SMS webhook experience.
    """
    from services.message_router import process_inbound_sms

    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    try:
        # Fetch user row so process_inbound_sms has the same user_data shape as the SMS webhook
        user_res = (
            supabase.table("users")
            .select("*, schedule(*), coach_settings(*)")
            .eq("id", user_id)
            .execute()
        )
        if not user_res.data:
            raise HTTPException(status_code=404, detail="User not found")

        user_data = user_res.data[0]
        user_timezone = (user_data.get("schedule") or {}).get("timezone", "America/New_York")

        # Save inbound message
        try:
            supabase.table("messages").insert({
                "user_id": user_id,
                "direction": "inbound",
                "body": req.message,
            }).execute()
        except Exception as e:
            logger.warning(f"[MOCK CHAT] Failed to save inbound message: {e}")

        # Run through the same pipeline as the live SMS webhook
        reply = await process_inbound_sms(user_id, req.message, user_data, user_timezone)

        # Save outbound message
        send_message(user_id, reply)

        return {"reply": reply}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[MOCK CHAT] Unhandled error for {user_id}")
        return {"reply": f"Server error: {str(e)}", "error": str(e)}
