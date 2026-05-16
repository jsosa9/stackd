"""
test_personas.py — End-to-end persona quality test suite.

Runs 15 realistic messages against 5 personas using the real pipeline
(process_inbound_sms), then scores each response with a Gemini judge call.

Usage:
    cd backend
    python tests/test_personas.py
    # or
    python -m pytest tests/test_personas.py -s -v
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allow running from backend/ or backend/tests/
# ---------------------------------------------------------------------------

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from dotenv import load_dotenv
load_dotenv(_backend_dir / ".env")

import google.generativeai as genai
from supabase import create_client

from services.message_router import process_inbound_sms, classify
from routes.personas import generate_persona_profile

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

PERSONAS = [
    "David Goggins",
    "Jocko Willink",
    "Kobe Bryant",
    "Tony Robbins",
    "Elon Musk",
]

TEST_MESSAGES = [
    "I skipped the gym today",
    "Just finished my run. 5 miles.",
    "I'm so tired I can't do anything",
    "I want to quit. This is too hard.",
    "I had a burger and fries for lunch",
    "Remind me to work out tomorrow at 6am",
    "I feel really good today, hit all my goals",
    "I've missed 3 days in a row",
    "I'm doubting whether this is working",
    "I just ran my first 10k",
    "I'm too busy today, no time",
    "I need advice on staying consistent",
    "Had a bad day at work, feeling low",
    "I bet my friend I'd work out 5 days this week",
    "Why should I keep going",
]

TEST_USER_EMAIL = "test@stackd.app"
TEST_TIMEZONE   = "America/New_York"

_JUDGE_PROMPT = """You are evaluating an AI accountability coach response for quality and persona accuracy.

Persona: {persona_name}
User said: {user_message}
Coach responded: {bot_response}

Score this response 1-5:
5 = Unmistakably sounds like this person's philosophy. Sharp, specific, no filler.
4 = Strong. Clearly in the right energy. Minor generic drift.
3 = Acceptable but could be any tough coach. Missing the specific voice.
2 = Generic motivational language. No persona character.
1 = Wrong tone entirely, or assistant-like filler language.

Return JSON only: {{"score": int, "reason": "one sentence"}}"""

# ---------------------------------------------------------------------------
# Test user setup
# ---------------------------------------------------------------------------

async def get_or_create_test_user() -> str:
    """Return the test user's id, creating the row if needed."""
    res = supabase.table("users").select("id").eq("email", TEST_USER_EMAIL).execute()
    if res.data:
        return res.data[0]["id"]

    insert_res = supabase.table("users").insert({
        "email": TEST_USER_EMAIL,
        "name":  "Test User",
        "phone": "+10000000000",
    }).execute()
    return insert_res.data[0]["id"]


async def activate_persona(user_id: str, persona_name: str) -> str:
    """
    Generate a persona profile, upsert into coach_settings with is_active=True.
    Deactivates all other coach_settings rows for this user first.
    Returns the generated system prompt.
    """
    print(f"  Generating persona profile for {persona_name}...", flush=True)
    profile = await generate_persona_profile(persona_name)
    system_prompt = profile["system_instruction"]

    # Deactivate existing rows
    supabase.table("coach_settings").update({"is_active": False}).eq("user_id", user_id).execute()

    # Upsert active row
    existing = (
        supabase.table("coach_settings")
        .select("id")
        .eq("user_id", user_id)
        .eq("sounds_like", persona_name)
        .execute()
    )

    payload = {
        "user_id":                  user_id,
        "sounds_like":              persona_name,
        "coach_name":               persona_name,
        "generated_system_prompt":  system_prompt,
        "is_active":                True,
        "coach_setup_type":         "sounds_like",
    }

    if existing.data:
        supabase.table("coach_settings").update(payload).eq("id", existing.data[0]["id"]).execute()
    else:
        supabase.table("coach_settings").insert(payload).execute()

    return system_prompt


async def deactivate_persona(user_id: str) -> None:
    supabase.table("coach_settings").update({"is_active": False}).eq("user_id", user_id).execute()


# ---------------------------------------------------------------------------
# Instrumented pipeline call — captures category alongside the reply
# ---------------------------------------------------------------------------

async def run_pipeline(user_id: str, message: str) -> tuple[str, str, str]:
    """
    Returns (category, bot_response, execution_result).
    Patches classify() to capture the category without altering the pipeline.
    """
    import services.message_router as _router

    _captured_category: list[str] = []
    _original_classify = _router.classify

    async def _patched_classify(msg: str) -> str:
        cat = await _original_classify(msg)
        _captured_category.append(cat)
        return cat

    _router.classify = _patched_classify
    try:
        bot_response = await process_inbound_sms(user_id, message, user_timezone=TEST_TIMEZONE)
    finally:
        _router.classify = _original_classify

    category         = _captured_category[0] if _captured_category else "UNKNOWN"
    execution_result = ""  # captured via side effects; not directly returned by pipeline

    return category, bot_response, execution_result


# ---------------------------------------------------------------------------
# Gemini judge
# ---------------------------------------------------------------------------

async def judge_response(persona_name: str, user_message: str, bot_response: str) -> tuple[int, bool, str]:
    """Returns (score, passed, reason)."""
    prompt = _JUDGE_PROMPT.format(
        persona_name=persona_name,
        user_message=user_message,
        bot_response=bot_response,
    )
    try:
        model    = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(prompt)
        text     = response.text.strip()

        if text.startswith("```"):
            parts = text.split("```")
            text  = parts[1].lstrip("json").strip() if len(parts) > 1 else text

        data   = json.loads(text)
        score  = int(data.get("score", 1))
        reason = str(data.get("reason", ""))
        return score, score >= 4, reason
    except Exception as e:
        return 1, False, f"Judge error: {e}"


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

async def run_tests() -> None:
    print("Setting up test user...", flush=True)
    user_id = await get_or_create_test_user()
    print(f"Test user id: {user_id}", flush=True)

    results: list[dict] = []

    for persona_name in PERSONAS:
        print(f"\n{'='*60}", flush=True)
        print(f"PERSONA: {persona_name}", flush=True)
        print(f"{'='*60}", flush=True)

        try:
            await activate_persona(user_id, persona_name)
        except Exception as e:
            print(f"  ERROR generating persona for {persona_name}: {e}", flush=True)
            for msg in TEST_MESSAGES:
                results.append({
                    "persona":          persona_name,
                    "user_message":     msg,
                    "category":         "ERROR",
                    "bot_response":     "",
                    "execution_result": "",
                    "score":            0,
                    "pass":             False,
                    "judge_reason":     f"Persona generation failed: {e}",
                })
            continue

        for i, message in enumerate(TEST_MESSAGES, 1):
            print(f"  [{i:02d}/15] {message[:50]}", end="", flush=True)
            entry = {
                "persona":          persona_name,
                "user_message":     message,
                "category":         "UNKNOWN",
                "bot_response":     "",
                "execution_result": "",
                "score":            0,
                "pass":             False,
                "judge_reason":     "",
            }

            try:
                category, bot_response, execution_result = await run_pipeline(user_id, message)
                entry["category"]         = category
                entry["bot_response"]     = bot_response
                entry["execution_result"] = execution_result

                score, passed, reason = await judge_response(persona_name, message, bot_response)
                entry["score"]        = score
                entry["pass"]         = passed
                entry["judge_reason"] = reason

                status = "PASS" if passed else "FAIL"
                print(f"  → [{category}] score={score} {status}", flush=True)

            except Exception as e:
                entry["judge_reason"] = f"Pipeline error: {e}"
                print(f"  → ERROR: {e}", flush=True)

            results.append(entry)

        await deactivate_persona(user_id)

    # Final cleanup
    await deactivate_persona(user_id)

    # ---------------------------------------------------------------------------
    # Build summary
    # ---------------------------------------------------------------------------

    by_persona: dict[str, dict] = {}
    for name in PERSONAS:
        rows      = [r for r in results if r["persona"] == name]
        scores    = [r["score"] for r in rows if r["score"] > 0]
        passed    = sum(1 for r in rows if r["pass"])
        avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
        by_persona[name] = {
            "avg_score": avg_score,
            "passed":    passed,
            "total":     len(rows),
        }

    all_scores = [r["score"] for r in results if r["score"] > 0]
    total_pass = sum(1 for r in results if r["pass"])
    avg_total  = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0.0

    output = {
        "run_at":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {
            "total":      len(results),
            "passed":     total_pass,
            "avg_score":  avg_total,
            "by_persona": by_persona,
        },
        "results": results,
    }

    out_path = Path(__file__).parent / "persona_test_results.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))

    # ---------------------------------------------------------------------------
    # Print summary table
    # ---------------------------------------------------------------------------

    print(f"\n{'='*50}")
    print("PERSONA TEST RESULTS")
    print(f"{'='*50}")
    for name in PERSONAS:
        p         = by_persona[name]
        flag      = "  <- needs work" if p["avg_score"] < 3.5 else ""
        print(f"{name:<18} {p['passed']:>2}/{p['total']} passed  avg score: {p['avg_score']}{flag}")
    print(f"{'='*50}")
    print(f"TOTAL: {total_pass}/{len(results)} passed  avg: {avg_total}")
    print(f"Results saved to {out_path}")


# ---------------------------------------------------------------------------
# pytest entry point
# ---------------------------------------------------------------------------

def test_persona_suite():
    """pytest-compatible wrapper — runs the full async suite."""
    asyncio.run(run_tests())


if __name__ == "__main__":
    asyncio.run(run_tests())
