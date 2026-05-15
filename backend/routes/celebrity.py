import logging
import os
import json
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


class CelebritySearchRequest(BaseModel):
    name: str
    category: str
    social_link: str = ""


class CelebrityBuildRequest(BaseModel):
    name: str
    category: str
    perplexity_data: dict


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


async def _call_perplexity(name: str, category: str, social_link: str = "") -> dict:
    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="Perplexity API key not configured")

    social_ctx = f"Social/website: {social_link}." if social_link.strip() else ""

    prompt = f"""Who is {name}? Category: {category}. {social_ctx}

Return JSON only with this exact structure:
{{
  "confirmed_name": "their full/common name",
  "known_for": "1-2 sentence description",
  "confidence": 0.9,
  "communication_style": "how they speak and present themselves",
  "energy_level": "high",
  "top_3_quotes": ["quote1", "quote2", "quote3"],
  "tone_keywords": ["word1", "word2", "word3", "word4", "word5"]
}}

Set confidence below 0.7 if you cannot confidently identify this person.
Return JSON only, no other text."""

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
        logger.error(f"Perplexity error {resp.status_code}: {resp.text}")
        raise HTTPException(status_code=502, detail="Celebrity lookup failed")

    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(_strip_json_fence(content))


# ---------------------------------------------------------------------------
# POST /celebrity/search
# ---------------------------------------------------------------------------

@router.post("/search")
async def celebrity_search(req: CelebritySearchRequest):
    """
    1. Check celebrities table (name ilike + category match).
    2. HIT → return cached perplexity_data + personality_json.
    3. MISS → call Perplexity, return raw result (personality not built yet).
    """
    cached = (
        supabase.table("celebrities")
        .select("perplexity_data, personality_json")
        .ilike("name", req.name.strip())
        .eq("category", req.category)
        .limit(1)
        .execute()
    )

    if cached.data:
        logger.info(f"Celebrity cache HIT: {req.name} ({req.category})")
        return {
            "source": "cache",
            "perplexity_data": cached.data[0]["perplexity_data"],
            "personality_json": cached.data[0].get("personality_json"),
        }

    logger.info(f"Celebrity cache MISS: {req.name} — calling Perplexity")

    try:
        perplexity_data = await _call_perplexity(req.name.strip(), req.category, req.social_link)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Perplexity failed for {req.name}")
        raise HTTPException(status_code=502, detail=f"Celebrity search failed: {e}")

    return {
        "source": "perplexity",
        "perplexity_data": perplexity_data,
        "personality_json": None,
    }


# ---------------------------------------------------------------------------
# POST /celebrity/build
# ---------------------------------------------------------------------------

@router.post("/build")
async def celebrity_build(req: CelebrityBuildRequest):
    """
    Call Gemini to convert perplexity_data into a structured coach personality JSON.
    Upsert result to celebrities table for future cache hits.
    """
    pd = req.perplexity_data
    confirmed_name = pd.get("confirmed_name", req.name).strip()

    prompt = f"""Build a coach personality based on {confirmed_name}.
Known for: {pd.get("known_for", "")}
Communication style: {pd.get("communication_style", "")}
Energy level: {pd.get("energy_level", "medium")}
Quotes: {json.dumps(pd.get("top_3_quotes", []))}
Tone keywords: {", ".join(pd.get("tone_keywords", []))}

Return JSON only:
{{
  "intensity": 4,
  "talk_style": ["Direct", "Motivational"],
  "miss_behavior": "Call it out",
  "message_length": "Short",
  "opener_style": "Direct check-in",
  "emoji_usage": "Minimal",
  "tone_keywords": ["intense", "no-excuses"],
  "signature_energy": "one sentence capturing their coaching energy",
  "example_messages": {{
    "confirm": "what they'd say when you confirm a session (1-2 sentences, SMS style)",
    "miss": "what they'd say when you miss a day (1-2 sentences, SMS style)",
    "motivation": "a random motivation message in their voice (1-2 sentences)"
  }}
}}

Allowed values:
- talk_style: any subset of [Motivational, Direct, Casual, Energetic, Warm, Playful, No-nonsense, Encouraging]
- miss_behavior: one of [Push harder, Call it out, Check in kindly, Light roast, Give space]
- message_length: one of [Short, Medium, Long]
- opener_style: one of [Hype intro, Direct check-in, Warm greeting, Funny opener, Custom question]
- emoji_usage: one of [None, Minimal, Some, Lots]
- intensity: integer 1–5

Return JSON only, no other text."""

    try:
        model = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        response = model.generate_content(prompt)
        personality_json = json.loads(_strip_json_fence(response.text))
    except Exception as e:
        logger.exception(f"Gemini build failed for {confirmed_name}")
        raise HTTPException(status_code=502, detail=f"Personality build failed: {e}")

    # Cache in DB
    try:
        supabase.table("celebrities").upsert(
            {
                "name": confirmed_name,
                "category": req.category,
                "perplexity_data": pd,
                "personality_json": personality_json,
                "confidence": pd.get("confidence", 0),
            },
            on_conflict="name,category",
        ).execute()
    except Exception as e:
        logger.warning(f"Failed to cache celebrity {confirmed_name}: {e}")

    return {"personality_json": personality_json, "confirmed_name": confirmed_name}
