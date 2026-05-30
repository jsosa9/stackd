import json
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv
from fastapi import APIRouter
from pydantic import BaseModel
from supabase import create_client

from routes.ai import generate_personality_id

load_dotenv()

log_dir = Path(__file__).parent.parent.parent / "logs"
log_dir.mkdir(exist_ok=True)

logger = logging.getLogger("personas")
logger.setLevel(logging.DEBUG)
_handler = RotatingFileHandler(log_dir / "personas.log", maxBytes=10 * 1024 * 1024, backupCount=5)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logger.addHandler(_handler)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------

class Persona(BaseModel):
    personality_id: str  # 4 uppercase letters + 4 digits, e.g. "XKRB8472"
    name: str
    system_instruction: str
    few_shot_examples: list[dict]  # each dict has 'user' and 'assistant' keys
    is_active: bool = True


# ---------------------------------------------------------------------------
# Persona profile generation
# ---------------------------------------------------------------------------

async def generate_persona_profile(name: str) -> dict:
    prompt = f"""You are building a persona profile for {name} to be used as an SMS coach.

Return a JSON object with exactly two keys:

system_instruction: A paragraph describing {name}'s core identity, beliefs, communication style, pace, energy, and what makes their voice completely distinct. Write it as a direct instruction to an AI that must become this person. Be extremely specific — reference their actual philosophy, real speech patterns, and unique characteristics. No generic motivation language. This coach never lets a check-in end without either asking one specific follow-up question about what happened (how far, how long, how hard, what is next) or setting a specific expectation for tomorrow. End most replies with a question or a direct order, not a statement.

few_shot_examples: A list of 20 objects each with 'user' and 'assistant' keys. These are real example SMS exchanges showing exactly how {name} would respond. Cover these scenarios:
- user missed a workout
- user is making excuses
- user just hit a big goal
- user says they are tired
- user is doubting themselves
- user wants to quit
- user asks for advice
- user had a bad day
- user is feeling good
- user missed multiple days in a row
- user is just checking in
- user pushes back on the coach
- user shares a small win
- user says they are too busy
- user asks why they should keep going
- user gives a short one word answer
- user reports a win but undersells it
- user is being vague about what they did
- coach follows up on something from earlier in the conversation
- user hasn't been pushed hard enough yet

The assistant responses must sound EXACTLY like {name} — their actual vocabulary, their actual energy, their actual length. Not a generic version of them. If {name} curses, curse. If they are brief, be brief. If they use specific phrases, use them.

Return valid JSON only. No markdown, no explanation."""

    try:
        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(prompt)
        text = response.text.strip()

        # Strip markdown fences if present
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else text
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        data = json.loads(text)
        return {
            "system_instruction": data["system_instruction"],
            "few_shot_examples": data["few_shot_examples"],
        }
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse persona profile JSON for '{name}': {e}")
        raise
    except Exception as e:
        logger.error(f"Persona profile generation failed for '{name}': {e}", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# PersonaManager
# ---------------------------------------------------------------------------

class PersonaManager:
    def __init__(self, supabase_client):
        self.db = supabase_client

    def is_valid_id(self, input_string: str) -> bool:
        return bool(re.fullmatch(r'^[A-Z]{4}[0-9]{4}$', input_string))

    async def fetch_persona(self, personality_id: str) -> Persona | None:
        try:
            res = (
                self.db.table("personas")
                .select("*")
                .eq("personality_id", personality_id)
                .eq("is_active", True)
                .limit(1)
                .execute()
            )
            if not res.data:
                return None
            row = res.data[0]
            return Persona(
                personality_id=row["personality_id"],
                name=row["name"],
                system_instruction=row["system_instruction"] or "",
                few_shot_examples=row.get("few_shot_examples") or [],
                is_active=row.get("is_active", True),
            )
        except Exception as e:
            logger.error(f"fetch_persona failed for {personality_id}: {e}", exc_info=True)
            return None

    async def fetch_persona_by_name(self, name: str) -> Persona | None:
        def _to_persona(row: dict) -> Persona:
            return Persona(
                personality_id=row["personality_id"],
                name=row["name"],
                system_instruction=row["system_instruction"] or "",
                few_shot_examples=row.get("few_shot_examples") or [],
                is_active=row.get("is_active", True),
            )

        try:
            # Step 1: exact case-insensitive match
            res = (
                self.db.table("personas")
                .select("*")
                .ilike("name", name)
                .eq("is_active", True)
                .limit(1)
                .execute()
            )
            if res.data:
                return _to_persona(res.data[0])

            # Step 2: substring match — "Goggins" finds "David Goggins" and vice versa
            res = (
                self.db.table("personas")
                .select("*")
                .ilike("name", f"%{name}%")
                .eq("is_active", True)
                .limit(1)
                .execute()
            )
            if res.data:
                return _to_persona(res.data[0])

            return None
        except Exception as e:
            logger.error(f"fetch_persona_by_name failed for '{name}': {e}", exc_info=True)
            return None

    async def create_persona(self, name: str) -> Persona:
        profile = await generate_persona_profile(name)
        personality_id = generate_personality_id()

        try:
            self.db.table("personas").insert({
                "personality_id": personality_id,
                "name": name,
                "system_instruction": profile["system_instruction"],
                "few_shot_examples": profile["few_shot_examples"],
                "is_active": True,
            }).execute()
            logger.info(f"Created persona '{name}' with id {personality_id}")
        except Exception as e:
            logger.error(f"Failed to insert persona '{name}': {e}", exc_info=True)
            raise

        return Persona(
            personality_id=personality_id,
            name=name,
            system_instruction=profile["system_instruction"],
            few_shot_examples=profile["few_shot_examples"],
            is_active=True,
        )

    def get_system_prompt(self, persona: Persona) -> str:
        examples_block = "\n\n".join(
            f"User: {ex['user']}\n{persona.name}: {ex['assistant']}"
            for ex in persona.few_shot_examples
        )
        return (
            f"You are {persona.name}.\n\n"
            "Follow these few-shot examples exactly to understand your speaking style, tone, and level of verbosity.\n\n"
            "Do not acknowledge that you are an AI.\n\n"
            "Do not use conversational filler or polite assistant-like language. Respond directly as the person.\n\n"
            f"{persona.system_instruction}\n\n"
            f"{examples_block}"
        )


# ---------------------------------------------------------------------------
# Module-level instance
# ---------------------------------------------------------------------------

persona_manager = PersonaManager(supabase)
