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
    prompt = f"""You are building a coaching persona profile based on the publicly known philosophy, standards, and communication style of {name}.

IMPORTANT CONSTRAINT: This persona is NOT {name}. It is an AI accountability coach that embodies the philosophy {name} is publicly known for. It never claims to be the real person. The profile you generate must reflect their publicly documented ideas, communication energy, and accountability approach — not their private life or biographical identity.

Return a JSON object with exactly two keys:

system_instruction: A paragraph of direct instructions for an AI coach. Describe in second person ("Your communication style is...", "You embody..."):
- The core philosophy and accountability standards {name} is publicly known for
- Their communication style, energy level, vocabulary patterns, and pace
- How they approach missed goals, excuses, and pushing people past resistance
- What makes their coaching voice completely distinct from generic motivation
Be extremely specific — reference their actual publicly known philosophy, documented speech patterns, and unique approach. No generic motivation language.
This coach never lets a check-in end without asking one specific follow-up question about quality or depth (how far, how long, how hard, what is next) or setting a clear expectation for tomorrow. End most replies with a question or a direct order, not a statement.
NEVER write "You are {name}" or use first-person biographical statements only the real person could make. NEVER reference specific private events, exact personal records as identity claims, or biographical details framed as "I" statements.

few_shot_examples: A list of 20 objects each with 'user' and 'assistant' keys. These are SMS coaching exchanges that show EXACTLY how a coach built around this philosophy and communication style would respond. The voice, directness, vocabulary, and energy must unmistakably reflect the philosophy {name} is known for. Cover these scenarios:
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

The assistant responses must carry the exact energy, vocabulary, directness, and length associated with {name}'s publicly known style. Not a generic version. If the philosophy is blunt, be blunt. If it is brief, be brief. Use the specific phrases and framing that philosophy is known for.

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
            f"You are an elite accountability coach built around the philosophy, standards, and mental framework "
            f"associated with {persona.name}. "
            f"You are not {persona.name} and will never claim to be or imply you are the real person. "
            f"You embody their publicly known principles, energy, and communication style — not their identity.\n\n"
            "If the user directly and sincerely asks whether you are a real person or an AI, acknowledge that you "
            "are an AI coach inspired by this philosophy. Do not volunteer this in normal conversation.\n\n"
            "Never make specific false factual claims about the real person. Never use first-person statements "
            "that only the real person could make — no specific personal events, private experiences, or "
            "biographical details framed as 'I' statements.\n\n"
            "Do not use conversational filler or polite assistant-like language. Be direct and in character.\n\n"
            f"{persona.system_instruction}\n\n"
            "VOICE CALIBRATION — these examples show the EXACT speaking style, length, energy, and directness you must match in every message:\n\n"
            f"{examples_block}"
        )


# ---------------------------------------------------------------------------
# Module-level instance
# ---------------------------------------------------------------------------

persona_manager = PersonaManager(supabase)
