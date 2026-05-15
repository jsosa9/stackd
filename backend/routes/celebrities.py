import re
import json
import logging
import os
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from supabase import create_client
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
router = APIRouter()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class CelebrityLookupRequest(BaseModel):
    name: str
    category: str
    social_url: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_search_key(name: str, category: str) -> str:
    raw = f"{name}_{category}".lower().replace(" ", "_")
    return re.sub(r"[^a-z0-9_]", "", raw)


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


async def _call_perplexity(name: str, category: str, social_url: str | None) -> dict:
    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="search_failed: PERPLEXITY_API_KEY not set")

    social_line = f"Social: {social_url}" if social_url else ""

    prompt = (
        f"Who is {name}? Category: {category}. {social_line}\n"
        "Return JSON only, no markdown:\n"
        "{\n"
        '  "confirmed_name": "string",\n'
        '  "known_for": "string",\n'
        '  "confidence": 0.9,\n'
        '  "communication_style": "string",\n'
        '  "energy_level": "string",\n'
        '  "top_3_quotes": ["string"],\n'
        '  "tone_keywords": ["string"]\n'
        "}\n"
        "If not recognizable set confidence below 0.7."
    )

    payload = {
        "model": "sonar",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 800,
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.perplexity.ai/chat/completions",
            headers=headers,
            json=payload,
        )

    if resp.status_code != 200:
        logger.error(f"Perplexity {resp.status_code}: {resp.text}")
        raise HTTPException(status_code=500, detail="search_failed")

    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(_strip_fence(content))


def _call_gemini(perplexity_data: dict) -> dict:
    prompt = (
        f"Build coach personality from:\n"
        f"Name: {perplexity_data.get('confirmed_name', '')}\n"
        f"Known for: {perplexity_data.get('known_for', '')}\n"
        f"Style: {perplexity_data.get('communication_style', '')}\n"
        f"Energy: {perplexity_data.get('energy_level', '')}\n"
        f"Quotes: {json.dumps(perplexity_data.get('top_3_quotes', []))}\n\n"
        "Return JSON only, no markdown:\n"
        "{\n"
        '  "intensity": 4,\n'
        '  "talk_style": ["Direct", "Motivational"],\n'
        '  "miss_behavior": "Call it out",\n'
        '  "message_length": "Short",\n'
        '  "tone_keywords": ["intense"],\n'
        '  "signature_energy": "string",\n'
        '  "example_messages": {\n'
        '    "confirm": "string",\n'
        '    "miss": "string",\n'
        '    "motivation": "string"\n'
        "  }\n"
        "}\n"
        "Allowed values — talk_style: [Motivational, Direct, Casual, Energetic, Warm, Playful, No-nonsense, Encouraging]. "
        "miss_behavior: [Push harder, Call it out, Check in kindly, Light roast, Give space]. "
        "message_length: [Short, Medium, Long]. intensity: 1-5."
    )

    try:
        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(prompt)
        return json.loads(_strip_fence(response.text))
    except Exception as e:
        logger.exception("Gemini personality build failed")
        raise HTTPException(status_code=500, detail="personality_failed")


# ---------------------------------------------------------------------------
# POST /celebrities/lookup
# ---------------------------------------------------------------------------

@router.post("/lookup")
async def celebrity_lookup(req: CelebrityLookupRequest):
    """
    Single endpoint that handles the full celebrity → coach personality pipeline:
    1. Normalize search key
    2. Check Supabase cache
    3. Call Perplexity (on miss)
    4. Call Gemini to build personality (on miss)
    5. Save to Supabase
    6. Return personality_json
    """
    search_key = _make_search_key(req.name, req.category)

    # ── Step 2: check cache ──────────────────────────────────────────────────
    try:
        cached = (
            supabase.table("celebrities")
            .select("personality_json, usage_count")
            .eq("search_key", search_key)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.warning(f"Supabase cache read failed: {e}")
        cached = type("obj", (object,), {"data": []})()

    if cached.data:
        row = cached.data[0]
        logger.info(f"Celebrity cache HIT: {search_key}")

        # Increment usage_count (best-effort)
        try:
            supabase.table("celebrities").update(
                {"usage_count": (row.get("usage_count") or 0) + 1}
            ).eq("search_key", search_key).execute()
        except Exception as e:
            logger.warning(f"Failed to increment usage_count for {search_key}: {e}")

        return {"hit": True, "warning": False, "personality_json": row["personality_json"]}

    # ── Step 3: Perplexity ───────────────────────────────────────────────────
    logger.info(f"Celebrity cache MISS: {search_key} — calling Perplexity")
    perplexity_data = await _call_perplexity(req.name, req.category, req.social_url)

    low_confidence = perplexity_data.get("confidence", 1) < 0.7

    # ── Step 4: Gemini ───────────────────────────────────────────────────────
    personality_json = _call_gemini(perplexity_data)

    # ── Step 5: save to Supabase ─────────────────────────────────────────────
    try:
        supabase.table("celebrities").upsert(
            {
                "search_key":       search_key,
                "confirmed_name":   perplexity_data.get("confirmed_name", req.name),
                "category":         req.category,
                "known_for":        perplexity_data.get("known_for", ""),
                "confidence":       perplexity_data.get("confidence", 0),
                "perplexity_data":  perplexity_data,
                "personality_json": personality_json,
                "social_url":       req.social_url,
                "usage_count":      1,
            },
            on_conflict="search_key",
        ).execute()
    except Exception as e:
        logger.error(f"Failed to cache celebrity {search_key}: {e}")
        # Never block the response on a DB write failure

    # ── Step 6: return ───────────────────────────────────────────────────────
    return {
        "hit":              False,
        "warning":          low_confidence,
        "personality_json": personality_json,
        **({"data": perplexity_data} if low_confidence else {}),
    }
