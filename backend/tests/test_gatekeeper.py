"""
tests/test_gatekeeper.py — Persona Gatekeeper unit tests.

Verifies that process_inbound_sms() enforces the active-persona gate
BEFORE touching Gemini or the CoachingEngine, and that malformed
personality_id values are silently skipped rather than passed downstream.

Run:
    cd backend
    python -m pytest tests/test_gatekeeper.py -v
"""

import asyncio
import sys
import os
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# sys.path — must come first so real project packages are found
# ---------------------------------------------------------------------------

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# ---------------------------------------------------------------------------
# Stub third-party packages that have no real implementation in the venv
# (google.generativeai, supabase, dotenv, pytz)
# These must be in sys.modules BEFORE any project module is imported.
# ---------------------------------------------------------------------------

def _build_genai_stub():
    mod = types.ModuleType("google.generativeai")
    mod.configure = MagicMock()

    chat_instance = MagicMock()
    chat_instance.send_message.return_value = MagicMock(text="Coach reply here")

    model_instance = MagicMock()
    model_instance.generate_content.return_value = MagicMock(text="general_chat")
    model_instance.start_chat.return_value = chat_instance

    mod.GenerativeModel = MagicMock(return_value=model_instance)
    return mod, model_instance


_google_mod = types.ModuleType("google")
_genai_mod, _genai_model = _build_genai_stub()
_google_mod.generativeai = _genai_mod
sys.modules.setdefault("google", _google_mod)
sys.modules["google.generativeai"] = _genai_mod

_supabase_pkg = types.ModuleType("supabase")
_supabase_pkg.create_client = MagicMock()
sys.modules.setdefault("supabase", _supabase_pkg)

_dotenv = types.ModuleType("dotenv")
_dotenv.load_dotenv = MagicMock()
sys.modules.setdefault("dotenv", _dotenv)

_pytz = types.ModuleType("pytz")
_pytz.timezone = MagicMock(return_value=MagicMock())
sys.modules.setdefault("pytz", _pytz)

# ---------------------------------------------------------------------------
# Stub lazy-imported project modules (routes.ai, routes.personas,
# services.coaching_service).  These are imported inside function bodies in
# message_router.py, so Python reads sys.modules at call time — our stubs
# will be found before the real files.
# ---------------------------------------------------------------------------

_EMPTY_CTX = MagicMock(
    has_anomaly=False,
    anomaly_score=0,
    anomaly_reasons=[],
    provider_data={"reminders": {"overdue": []}, "fitness": {"missed_today": []}},
    to_prompt_block=MagicMock(return_value=""),
)

_mock_persona_manager = MagicMock()
_mock_persona_manager.fetch_persona     = AsyncMock(return_value=None)
_mock_persona_manager.get_system_prompt = MagicMock(return_value="")

# routes package
if "routes" not in sys.modules:
    sys.modules["routes"] = types.ModuleType("routes")

_routes_ai = types.ModuleType("routes.ai")
_routes_ai.HUMAN_BEHAVIOR_RULES    = ""
_routes_ai.CONVICTION_RULES        = ""
_routes_ai.build_coach_personality = AsyncMock()
sys.modules["routes.ai"] = _routes_ai

_routes_personas = types.ModuleType("routes.personas")
_routes_personas.persona_manager = _mock_persona_manager
sys.modules["routes.personas"] = _routes_personas

# services package — the real package exists on disk; only override the
# coaching_service submodule so we don't hit the real DB.
_coaching_svc_stub = types.ModuleType("services.coaching_service")
_coaching_svc_stub.get_coaching_context = AsyncMock(return_value=_EMPTY_CTX)
_coaching_svc_stub.save_coach_insight   = AsyncMock()
sys.modules["services.coaching_service"] = _coaching_svc_stub

# ---------------------------------------------------------------------------
# NOW import the module under test (all its deps are stubbed)
# ---------------------------------------------------------------------------

import services.message_router as router_module  # noqa: E402

# ---------------------------------------------------------------------------
# Supabase stub factory
# ---------------------------------------------------------------------------

def _make_supabase_stub():
    """MagicMock satisfying the Supabase fluent query API; all queries → empty."""
    stub = MagicMock()
    empty = MagicMock(data=[])
    stub.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value    = empty
    stub.table.return_value.select.return_value.eq.return_value.execute.return_value                    = empty
    stub.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = empty
    stub.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
    return stub


def _make_active_coach_stub(personality_id: str, system_prompt: str = "You are a coach."):
    """Supabase stub whose gatekeeper query returns one active coach row."""
    stub = _make_supabase_stub()
    active_row = MagicMock(data=[{
        "personality_id":          personality_id,
        "generated_system_prompt": system_prompt,
    }])
    # Gatekeeper chain: .table().select().eq(user_id).eq(is_active).execute()
    stub.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = active_row
    return stub


# Replace the module-level supabase instance created at import time
router_module.supabase = _make_supabase_stub()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_USER_DATA = {"id": "user-123"}
_DUMMY_TIMEZONE  = "America/New_York"


def run(coro):
    """Fresh event loop per call — prevents cross-test task contamination."""
    return asyncio.run(coro)


def _reset_stubs():
    _genai_mod.GenerativeModel.reset_mock()
    _mock_persona_manager.fetch_persona     = AsyncMock(return_value=None)
    _mock_persona_manager.get_system_prompt = MagicMock(return_value="")
    _coaching_svc_stub.get_coaching_context = AsyncMock(return_value=_EMPTY_CTX)


# ---------------------------------------------------------------------------
# Test 1 — No active persona → "Message received." + zero engine calls
# ---------------------------------------------------------------------------

class TestGatekeeperNoPersona(unittest.TestCase):
    """User has no row with is_active=True → minimal ack, engine never called."""

    def setUp(self):
        _reset_stubs()
        router_module.supabase = _make_supabase_stub()

    def test_returns_message_received(self):
        reply = run(router_module.process_inbound_sms(
            user_id="user-no-persona",
            message_body="Hey what's up",
            user_data=_DUMMY_USER_DATA,
            user_timezone=_DUMMY_TIMEZONE,
        ))
        self.assertEqual(reply, "Message received.")

    def test_gemini_never_called(self):
        run(router_module.process_inbound_sms(
            user_id="user-no-persona",
            message_body="Hey",
            user_data=_DUMMY_USER_DATA,
            user_timezone=_DUMMY_TIMEZONE,
        ))
        _genai_mod.GenerativeModel.assert_not_called()

    def test_coaching_context_never_fetched(self):
        run(router_module.process_inbound_sms(
            user_id="user-no-persona",
            message_body="Hey",
            user_data=_DUMMY_USER_DATA,
            user_timezone=_DUMMY_TIMEZONE,
        ))
        _coaching_svc_stub.get_coaching_context.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2 — Malformed personality_id (BADID123) → persona fetch skipped
# ---------------------------------------------------------------------------

class TestGatekeeperMalformedPersonaId(unittest.TestCase):
    """
    Active coach row exists but personality_id='BADID123' fails [A-Z]{4}[0-9]{4}.
    Gatekeeper passes (coach row found), but fetch_persona must NOT be called.
    """

    def setUp(self):
        _reset_stubs()
        router_module.supabase = _make_active_coach_stub("BADID123")

    def test_fetch_persona_not_called_with_bad_id(self):
        run(router_module.process_inbound_sms(
            user_id="user-bad-id",
            message_body="Let's go",
            user_data=_DUMMY_USER_DATA,
            user_timezone=_DUMMY_TIMEZONE,
        ))
        _mock_persona_manager.fetch_persona.assert_not_called()

    def test_does_not_return_message_received(self):
        """Gatekeeper passes — reply must come from the engine, not the fallback."""
        reply = run(router_module.process_inbound_sms(
            user_id="user-bad-id",
            message_body="Let's go",
            user_data=_DUMMY_USER_DATA,
            user_timezone=_DUMMY_TIMEZONE,
        ))
        self.assertNotEqual(reply, "Message received.")


# ---------------------------------------------------------------------------
# Test 3 — Valid persona ID (JMOY2753) → CoachingEngine runs, Gemini called
# ---------------------------------------------------------------------------

class TestGatekeeperValidPersona(unittest.TestCase):
    """
    Active coach row with personality_id='JMOY2753'.
    Pipeline must: pass gatekeeper, classify, and generate a reply via Gemini.
    The new pipeline reads coach data directly from DB — no persona_manager call.
    """

    def setUp(self):
        _reset_stubs()
        router_module.supabase = _make_active_coach_stub(
            "JMOY2753", system_prompt="You are a tough coach."
        )

    def test_gemini_called_for_valid_persona(self):
        reply = run(router_module.process_inbound_sms(
            user_id="user-valid",
            message_body="I crushed my workout",
            user_timezone=_DUMMY_TIMEZONE,
        ))
        _genai_mod.GenerativeModel.assert_called()
        self.assertIsInstance(reply, str)
        self.assertGreater(len(reply), 0)

    def test_coach_settings_queried_for_valid_persona(self):
        """_get_active_coach must query coach_settings (not persona_manager)."""
        stub = _make_active_coach_stub("JMOY2753", system_prompt="Tough coach.")
        router_module.supabase = stub
        run(router_module.process_inbound_sms(
            user_id="user-valid",
            message_body="I crushed my workout",
            user_timezone=_DUMMY_TIMEZONE,
        ))
        queried_tables = {c.args[0] for c in stub.table.call_args_list}
        self.assertIn("coach_settings", queried_tables)

    def test_does_not_return_message_received_for_valid_persona(self):
        reply = run(router_module.process_inbound_sms(
            user_id="user-valid",
            message_body="I crushed my workout",
            user_timezone=_DUMMY_TIMEZONE,
        ))
        self.assertNotEqual(reply, "Message received.")


# ---------------------------------------------------------------------------
# Test 4 — Gatekeeper blocks: Supabase engine tables never touched
# ---------------------------------------------------------------------------

class TestGatekeeperIsolation(unittest.TestCase):
    """
    When the gatekeeper returns early, Supabase tables owned by the
    CoachingEngine (user_context, goals, reminders, nutrition_logs) must
    never be queried.
    """

    ENGINE_TABLES = {"user_context", "goals", "reminders", "nutrition_logs"}

    def setUp(self):
        _reset_stubs()

    def test_engine_tables_not_queried_when_gatekeeper_blocks(self):
        stub = _make_supabase_stub()
        router_module.supabase = stub

        run(router_module.process_inbound_sms(
            user_id="user-blocked",
            message_body="Morning",
            user_data=_DUMMY_USER_DATA,
            user_timezone=_DUMMY_TIMEZONE,
        ))

        queried_tables = {c.args[0] for c in stub.table.call_args_list}
        overlap = queried_tables & self.ENGINE_TABLES
        self.assertEqual(
            overlap, set(),
            f"Engine tables queried despite gatekeeper blocking: {overlap}",
        )


if __name__ == "__main__":
    unittest.main()
