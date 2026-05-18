"""
tests/test_goggins.py — Live conversation test for the Goggins persona.

Finds the first user whose coach_settings row has personality_id=JMOY2753,
then runs six messages through the full process_inbound_sms() pipeline,
printing each exchange in YOU / GOGGINS / CATEGORY format.

Run from the backend directory:
    python tests/test_goggins.py
"""

import asyncio
import os
import sys

# Make sure the backend package root is on sys.path regardless of cwd
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from dotenv import load_dotenv
load_dotenv(os.path.join(_BACKEND, ".env"))

from supabase import create_client
import services.message_router as router_module

# ---------------------------------------------------------------------------
# DB client (reads real creds from .env)
# ---------------------------------------------------------------------------

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

# ---------------------------------------------------------------------------
# Find the user linked to JMOY2753
# ---------------------------------------------------------------------------

def _find_user(personality_id: str) -> tuple[str, str]:
    """
    Return (user_id, user_timezone) for the first user with this personality_id.
    Falls back to any row whose coach_name contains 'Goggins' if the exact ID
    isn't found (IDs are regenerated each time the coach is saved in the app).
    """
    res = (
        supabase.table("coach_settings")
        .select("user_id, personality_id, coach_name")
        .eq("personality_id", personality_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        # Fetch all rows and filter locally (avoids ilike compatibility issues)
        all_rows = (
            supabase.table("coach_settings")
            .select("user_id, personality_id, coach_name")
            .execute()
        )
        goggins_rows = [
            r for r in (all_rows.data or [])
            if "goggins" in (r.get("coach_name") or "").lower()
        ]
        if not goggins_rows:
            print(f"ERROR: no coach_settings row found with personality_id={personality_id} "
                  "or coach_name matching 'Goggins'.")
            print("Available personalities:")
            for r in (all_rows.data or []):
                print(f"  {r.get('personality_id')} — {r.get('coach_name')}")
            sys.exit(1)
        row = goggins_rows[0]
        print(f"NOTE: personality_id={personality_id} not found — "
              f"using '{row['coach_name']}' ({row['personality_id']}) instead\n")
        res = type("R", (), {"data": [row]})()  # wrap to match expected shape

    user_id = res.data[0]["user_id"]

    # Fetch timezone from schedule
    sched = (
        supabase.table("schedule")
        .select("timezone")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    user_timezone = (sched.data[0]["timezone"] if sched.data else None) or "America/New_York"
    return user_id, user_timezone


# ---------------------------------------------------------------------------
# Monkey-patch classify() to surface the category without modifying the module
# ---------------------------------------------------------------------------

_last_category: dict[str, str] = {"value": "GENERAL"}
_original_classify = router_module.classify


async def _classify_and_capture(message_body: str) -> str:
    category = await _original_classify(message_body)
    _last_category["value"] = category
    return category


router_module.classify = _classify_and_capture  # swap in before any call

# ---------------------------------------------------------------------------
# Test messages
# ---------------------------------------------------------------------------

MESSAGES = [
    "I skipped my workout today",
    "just ate a burger and fries",
    "remind me to train at 6pm tomorrow",
    "did my run, 5 miles",
    "I'm too tired and want to quit",
    "who are you",
]

PERSONALITY_ID = "JMOY2753"

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    user_id, user_timezone = _find_user(PERSONALITY_ID)

    print()
    print(f"Persona  : {PERSONALITY_ID}")
    print(f"User ID  : {user_id}")
    print(f"Timezone : {user_timezone}")
    print("=" * 60)
    print()

    for message in MESSAGES:
        response = await router_module.process_inbound_sms(
            user_id=user_id,
            message_body=message,
            user_timezone=user_timezone,
        )
        category = _last_category["value"]

        print(f"YOU: {message}")
        print(f"GOGGINS: {response}")
        print(f"CATEGORY: {category}")
        print("---")

        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
