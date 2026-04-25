import logging
import os
import json
import httpx
import re
from pathlib import Path
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
import pytz
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from supabase import create_client
from anthropic import Anthropic
import google.generativeai as genai
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
import requests
from html.parser import HTMLParser

load_dotenv()

# Setup logging with rotating file handlers for different modules
log_dir = Path(__file__).parent.parent.parent / "logs"
log_dir.mkdir(exist_ok=True)

# Persona logger
persona_logger = logging.getLogger("persona")
persona_logger.setLevel(logging.DEBUG)
persona_handler = RotatingFileHandler(
    log_dir / "persona.log",
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
)
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
persona_handler.setFormatter(formatter)
persona_logger.addHandler(persona_handler)

# Intents logger
intents_logger = logging.getLogger("intents")
intents_logger.setLevel(logging.DEBUG)
intents_handler = RotatingFileHandler(
    log_dir / "intents.log",
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
)
intents_handler.setFormatter(formatter)
intents_logger.addHandler(intents_handler)

# Patterns logger
patterns_logger = logging.getLogger("patterns")
patterns_logger.setLevel(logging.DEBUG)
patterns_handler = RotatingFileHandler(
    log_dir / "patterns.log",
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
)
patterns_handler.setFormatter(formatter)
patterns_logger.addHandler(patterns_handler)

# Streaks logger
streaks_logger = logging.getLogger("streaks")
streaks_logger.setLevel(logging.DEBUG)
streaks_handler = RotatingFileHandler(
    log_dir / "streaks.log",
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
)
streaks_handler.setFormatter(formatter)
streaks_logger.addHandler(streaks_handler)

logger = logging.getLogger(__name__)

router = APIRouter()

# Human behavior rules — foundation for all AI responses
HUMAN_BEHAVIOR_RULES = """CRITICAL RULES — YOU MUST FOLLOW THESE AT ALL TIMES:

NEVER USE THESE WORDS OR PHRASES:
- Absolutely, Certainly, Of course, Great question, Sounds good
- As your coach, Feel free to, Don't hesitate, I'm here to help
- That's totally valid, It's important to note, Remember that
- I understand that, I want you to know, Let's dive in
- You've got this (unless used very rarely and naturally)
- Keep it up, Stay focused, Stay consistent, Stay on track
- I'm proud of you, I believe in you (unless personality calls for it)
- Reach out, Touch base, Circle back
- Any corporate or customer service language whatsoever

NEVER DO THESE THINGS:
- Use em dashes — like this — ever
- Use ellipses... for dramatic effect
- Send bullet points or numbered lists
- Use more than one emoji per message
- Use multiple emojis in a row like 💪🔥⚡
- Put an emoji at the end of every sentence
- Start every message with the user's name
- Send the same length message every time
- Always end with a question
- Always be positive and encouraging — push back when needed
- Use perfect grammar and punctuation every single time
- Repeat the same opening twice in a row
- Sound like a therapist, customer service bot, or life coach
- Refer to yourself as an AI, bot, assistant, or coach
- Use exclamation marks on everything
- Send more than 4 sentences in one message
- Use formal or corporate language unless personality is set to professional

YOU SHOULD NATURALLY DO THESE THINGS:
- Vary message length — sometimes one word, sometimes four sentences
- Use lowercase sometimes depending on personality and mood
- Skip periods on short punchy messages
- React first before responding when something is surprising — like "wait" or "ok but"
- Use natural filler words that fit the personality — "ngl", "tbh", "fr", "lowkey", "deadass", "ok but", "actually"
- Send one word reactions sometimes — "respect", "facts", "let's go", "cap"
- Reference things from earlier in the conversation naturally
- Match the emotional tone of what the user sent — if they seem down, shift accordingly
- Push back and be real — not every response should be validation
- Occasionally trail off without asking a question
- Sound like a specific real person based on the personality settings
- Use zero emojis on most messages — only use one when it genuinely fits
- For tough personalities use almost no emojis ever
- Vary punctuation naturally — not every sentence needs to end perfectly
- Interrupt yourself occasionally — "ok so I was going to say — actually just go do it"
- Use silence effectively — short messages hit harder than long ones sometimes

CONTEXT RULES:
- This is SMS. You are texting a real person. Write like a human texts.
- Never write in a way that would feel out of place as a real text message
- If the user is clearly upset or struggling, drop the agenda and just be real with them
- Never mention you are an AI. If directly asked, deflect in character naturally
- Read between the lines — if someone says "I'm fine" after missing 3 days they probably are not fine
- The conversation history is your memory — use it, reference it, build on it
- Never reset the relationship tone — if you have been texting for weeks it should feel like weeks"""

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
# Persona Research Pipeline
# ---------------------------------------------------------------------------

async def search_youtube_transcripts(person_name: str) -> str:
    """
    Search for YouTube videos of a person and extract transcripts.
    
    Tries multiple search query variations to find relevant videos:
    - '{person_name} full interview'
    - '{person_name} motivation speech'
    - '{person_name} podcast long form'
    
    For each query, extracts video IDs and attempts to get transcripts.
    Collects up to 3 transcripts total and concatenates them, trimmed to 5000 words.
    
    Args:
        person_name: Name of the person to research
        
    Returns:
        Concatenated transcript text (up to 5000 words), or empty string if no transcripts found
        
    Error handling:
        - If a video has no transcript, skips to next one
        - If YouTube search/parsing fails, continues gracefully
        - Logs all errors to persona.log without crashing
    """
    person_name = person_name.strip()
    transcripts = []
    queries = [
        f"{person_name} full interview",
        f"{person_name} motivation speech",
        f"{person_name} podcast long form",
    ]
    
    for query in queries:
        if len(transcripts) >= 3:
            break
            
        try:
            # Search YouTube using requests
            search_url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            resp = requests.get(search_url, headers=headers, timeout=5)
            
            # Extract video IDs from HTML using regex
            video_ids = re.findall(r'"videoId":"([^"]+)"', resp.text)
            
            for video_id in video_ids[:3]:  # Try up to 3 videos per query
                if len(transcripts) >= 3:
                    break
                    
                try:
                    transcript = YouTubeTranscriptApi.get_transcript(video_id)
                    transcript_text = " ".join([item["text"] for item in transcript])
                    transcripts.append(transcript_text)
                    persona_logger.info(f"Successfully extracted transcript from {video_id} ({len(transcript_text)} chars)")
                except Exception as e:
                    persona_logger.debug(f"Failed to get transcript from {video_id}: {str(e)}")
                    continue
                    
        except Exception as e:
            persona_logger.warning(f"Error searching YouTube for '{query}': {str(e)}")
            continue
    
    # Concatenate and trim to 5000 words
    all_text = " ".join(transcripts)
    words = all_text.split()
    if len(words) > 5000:
        all_text = " ".join(words[:5000])
        
    if all_text:
        persona_logger.info(f"YouTube research for '{person_name}': {len(all_text)} chars across {len(transcripts)} transcripts")
    
    return all_text


async def research_persona_perplexity(person_name: str) -> str:
    """
    Use Perplexity API to research how a person communicates and speaks.
    
    Makes a POST request to Perplexity's sonar model asking for:
    - Exact phrases and words they repeat
    - Core philosophy in their own words
    - How they respond to excuses
    - How they celebrate wins
    - Writing/speaking style details
    - Recurring stories and references
    - Words/phrases they'd never use
    - Coaching/mentoring tone
    - What makes them distinct
    
    Args:
        person_name: Name of the person to research
        
    Returns:
        Perplexity response text describing their communication patterns,
        or empty string if the API call fails
        
    Error handling:
        - If API call fails, logs error and returns empty string
        - Never crashes the function
    """
    person_name = person_name.strip()
    api_key = os.getenv("PERPLEXITY_API_KEY")
    
    if not api_key:
        persona_logger.warning("PERPLEXITY_API_KEY not set — skipping Perplexity research")
        return ""
    
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": "sonar",
            "messages": [
                {
                    "role": "user",
                    "content": f"""Research how {person_name} communicates and speaks. I need ONLY primary source information — their actual words from podcasts, interviews, books, and social media. Return:

Exact phrases and words they repeat constantly with direct quotes where possible
Their core philosophy in their own words
How they specifically respond when someone makes excuses — exact language and tone
How they celebrate wins — what they actually say
Their natural writing and speaking style — short or long, formal or casual, punctuation habits, energy level
Specific personal stories and references they bring up repeatedly
Words phrases and topics they would NEVER use or discuss
Their energy and tone when mentoring or coaching someone one on one
What makes their voice completely distinct from generic motivation content

Focus entirely on direct quotes and primary sources. Do not summarize or interpret — give me their actual words and patterns. If the person is not well known or has limited public presence say so clearly."""
                }
            ]
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post("https://api.perplexity.ai/chat/completions", headers=headers, json=payload)
            
        if resp.status_code != 200:
            persona_logger.error(f"Perplexity API error ({resp.status_code}): {resp.text}")
            return ""
            
        data = resp.json()
        research_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        if research_text:
            persona_logger.info(f"Perplexity research for '{person_name}': {len(research_text)} chars")
        
        return research_text
        
    except Exception as e:
        persona_logger.error(f"Perplexity research failed for '{person_name}': {str(e)}", exc_info=True)
        return ""


async def synthesize_persona(person_name: str, transcript_data: str, perplexity_data: str) -> str:
    """
    Use Claude Haiku to synthesize YouTube transcripts and Perplexity research
    into a detailed persona profile.
    
    Extracts from the research data:
    - SIGNATURE PHRASES — exact words and phrases they use repeatedly
    - VOCABULARY — words they love/never use, vocabulary level
    - SENTENCE STRUCTURE — length, fragments, punctuation habits
    - CORE PHILOSOPHY — their beliefs in their own words
    - RESPONSE TO EXCUSES — exactly how they react and what they say
    - RESPONSE TO WINS — exactly how they celebrate
    - RECURRING STORIES OR REFERENCES — specific examples they keep mentioning
    - WHAT MAKES THEM UNIQUE — what makes them recognizable vs generic
    - TEXTING STYLE — how they'd write as an SMS text
    - ENERGY LEVEL — 1-10 intensity and how it shows in language
    
    Args:
        person_name: Name of the person being researched
        transcript_data: YouTube transcript text
        perplexity_data: Perplexity API research text
        
    Returns:
        Detailed persona profile text with direct quotes,
        or empty string if synthesis fails
        
    Error handling:
        - If Claude call fails, logs error and returns empty string
        - Never crashes the function
    """
    person_name = person_name.strip()
    
    try:
        prompt = f"""You are analyzing research about {person_name} to extract their communication DNA for an SMS coach personality.

HERE ARE ACTUAL TRANSCRIPTS OF THEM SPEAKING:
{transcript_data}

HERE IS RESEARCH ABOUT HOW THEY COMMUNICATE:
{perplexity_data}

From all of the above extract and return a detailed persona profile covering:

SIGNATURE PHRASES — exact words and phrases they use repeatedly. Quote them directly.
VOCABULARY — words they love, words they never use, their vocabulary level and style
SENTENCE STRUCTURE — how long are their sentences, do they use fragments, how do they punctuate
CORE PHILOSOPHY — their beliefs in their own words not a summary
RESPONSE TO EXCUSES — exactly how they react, what they say, their tone
RESPONSE TO WINS — exactly how they celebrate, what they say
RECURRING STORIES OR REFERENCES — specific examples they keep coming back to
WHAT MAKES THEM UNIQUE — what would make someone immediately recognize this is them and not generic motivation
TEXTING STYLE — if they were texting a friend how would they write, short or long, casual or intense
ENERGY LEVEL — on a scale of 1 to 10 how intense are they and how does that intensity show up in language

Be extremely specific. Use direct quotes wherever possible. This profile will be used to make an AI sound exactly like this person over SMS so accuracy is everything.

Return only the persona profile, no preamble."""
        
        response = anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        
        profile = response.content[0].text
        persona_logger.info(f"Synthesized persona for '{person_name}': {len(profile)} chars")
        
        return profile
        
    except Exception as e:
        persona_logger.error(f"Persona synthesis failed for '{person_name}': {str(e)}", exc_info=True)
        return ""


async def research_persona(person_name: str) -> str:
    """
    Main entry point for persona research — orchestrates the full pipeline.
    
    Flow:
    1. Normalize the person name (lowercase, stripped whitespace)
    2. Check Supabase personas table for cached entry
    3. If found: increment request_count and return cached synthesized_profile
    4. If not found: run full pipeline:
       - Get YouTube transcripts
       - Get Perplexity research
       - Synthesize into persona profile
       - Insert into Supabase
       - Return synthesized profile
    
    Args:
        person_name: Name of the person to research (e.g., "Arnold Schwarzenegger")
        
    Returns:
        Synthesized persona profile string
        
    Error handling:
        - If Supabase insert fails, still returns the synthesized profile
        - YouTube/Perplexity failures don't crash the function
        - Always returns a profile even if some research sources failed
    """
    person_name = person_name.strip().lower()
    
    try:
        # Check cache
        existing = supabase.table("personas").select("*").eq("name", person_name).execute()
        if existing.data:
            persona = existing.data[0]
            # Increment request count
            try:
                supabase.table("personas").update({
                    "request_count": persona.get("request_count", 1) + 1,
                    "updated_at": "now()"
                }).eq("name", person_name).execute()
            except Exception as e:
                persona_logger.warning(f"Failed to update request_count for '{person_name}': {str(e)}")
            
            persona_logger.info(f"Cache hit for persona '{person_name}' (total requests: {persona.get('request_count', 1) + 1})")
            return persona.get("synthesized_profile", "")
            
    except Exception as e:
        persona_logger.warning(f"Error checking persona cache: {str(e)}")
    
    # Run full pipeline
    persona_logger.info(f"Starting persona research pipeline for '{person_name}'")
    
    youtube_data = await search_youtube_transcripts(person_name)
    perplexity_data = await research_persona_perplexity(person_name)
    synthesized = await synthesize_persona(person_name, youtube_data, perplexity_data)
    
    # Insert into Supabase (don't crash if this fails)
    try:
        supabase.table("personas").insert({
            "name": person_name,
            "youtube_transcripts": youtube_data,
            "perplexity_research": perplexity_data,
            "synthesized_profile": synthesized,
            "request_count": 1,
        }).execute()
        persona_logger.info(f"Persona for '{person_name}' cached in Supabase")
    except Exception as e:
        persona_logger.error(f"Failed to cache persona in Supabase: {str(e)}", exc_info=True)
        # But still return the synthesized profile — user experience shouldn't break
    
    return synthesized

async def build_coach_personality(user_id: str) -> str:
    """
    Build a personalized SMS coach system prompt using multi-source persona research.
    
    Flow:
    1. Fetch full user data (profile, goals, coach settings, schedule)
    2. If "sounds_like" is set, run persona research pipeline to extract their communication patterns
    3. Build Claude Haiku prompt with user data + persona profile
    4. Generate system prompt with embedded HUMAN_BEHAVIOR_RULES
    5. Save to coach_settings.generated_system_prompt
    6. Return the system prompt
    
    Args:
        user_id: UUID of the user
        
    Returns:
        Generated system prompt text
        
    Error handling:
        - If persona research fails, still generates prompt from quiz data
        - If Supabase fetch fails, raises HTTPException
        - Never crashes even if some parts of pipeline fail
    """
    logger.info(f"Building coach personality for user {user_id}")

    # Fetch all user data
    try:
        user_res = supabase.table("users").select("*").eq("id", user_id).execute()
        if not user_res.data:
            raise HTTPException(status_code=404, detail="User not found")
        user = user_res.data[0]

        coach_res = supabase.table("coach_settings").select("*").eq("user_id", user_id).execute()
        coach = coach_res.data[0] if coach_res.data else {}

        goals_res = supabase.table("goals").select("*").eq("user_id", user_id).execute()
        goals = goals_res.data or []

        sched_res = supabase.table("schedule").select("*").eq("user_id", user_id).execute()
        sched = sched_res.data[0] if sched_res.data else {}
    except Exception as e:
        logger.exception(f"Failed to fetch user data for {user_id}")
        raise HTTPException(status_code=500, detail="Failed to fetch user data")

    # Build user data summary
    user_data = {
        "name": user.get('name', 'User'),
        "age": user.get('age'),
        "occupation": user.get('occupation'),
        "phone": user.get('phone'),
        "obstacles": user.get('obstacles', []),
        "experience_level": user.get('experience'),
        "success_vision": user.get('success_vision'),
        "coach": {
            "coach_name": coach.get('coach_name', 'Alex'),
            "coach_avatar": coach.get('coach_avatar', '💪'),
            "personality_preset": coach.get('coach_personality', 'balanced'),
            "talk_style": coach.get('coach_talk_style', []),
            "emoji_usage": coach.get('coach_emoji_usage', 'moderate'),
            "message_length": coach.get('coach_message_length', 'medium'),
            "miss_behavior": coach.get('coach_miss_behavior', 'compassionate'),
            "intensity": coach.get('coach_intensity', 5),
            "custom_sounds_like": coach.get('custom_coach_sounds_like'),
            "custom_personality": coach.get('custom_coach_personality_desc'),
            "custom_celebration": coach.get('custom_coach_celebration_style'),
            "custom_missed_day": coach.get('custom_coach_missed_day_response'),
            "custom_favorite_phrase": coach.get('custom_coach_favorite_phrase'),
            "custom_avoid_phrases": coach.get('custom_coach_avoid_phrases'),
            "custom_tone": coach.get('custom_coach_tone'),
            "custom_special_rules": coach.get('custom_coach_special_rules'),
        },
        "goals": [
            {
                "activity": g.get('activity', 'Unknown'),
                "category": g.get('category', '?'),
                "days": g.get('days', []),
                "times_per_day": g.get('times_per_day', {}),
            }
            for g in goals
        ],
        "schedule": {
            "checkin_time": sched.get('checkin_time', '08:00'),
            "timezone": sched.get('timezone', 'America/New_York'),
            "motivation_enabled": sched.get('motivation_enabled', True),
            "motivation_frequency": sched.get('motivation_frequency', 'Once a day'),
            "motivation_window_start": sched.get('motivation_window_start', '09:00'),
            "motivation_window_end": sched.get('motivation_window_end', '20:00'),
            "motivation_styles": sched.get('motivation_styles', []),
            "avoid_topics": sched.get('avoid_topics', []),
        }
    }

    # Run persona research if "sounds_like" is specified
    persona_context = ""
    persona_profile = ""
    sounds_like = coach.get('custom_coach_sounds_like') or coach.get('coach_name')
    
    if sounds_like and sounds_like.lower() not in ['alex', 'coach', 'none', 'unknown', '']:
        try:
            persona_logger.info(f"Running persona research for '{sounds_like}'")
            persona_profile = await research_persona(sounds_like)
            if persona_profile:
                persona_context = f"""PERSONA PROFILE FOR {sounds_like} — use this to make the coach sound EXACTLY like this person. Their actual phrases, their actual philosophy, their actual communication patterns:
{persona_profile}"""
                logger.info(f"Persona research completed for '{sounds_like}' ({len(persona_profile)} chars)")
        except Exception as e:
            logger.warning(f"Persona research failed for '{sounds_like}': {str(e)}")
            # Continue anyway — don't crash the function

    # Build the Claude Haiku prompt
    haiku_prompt = f"""You are generating a personalized SMS accountability coach system prompt.

{persona_context}

USER QUIZ DATA:
{json.dumps(user_data, indent=2)}

HUMAN BEHAVIOR RULES — THESE ARE NON NEGOTIABLE:
{HUMAN_BEHAVIOR_RULES}

Generate a detailed system prompt for an AI that will text this user daily via SMS as their accountability coach.

Cover all of these in the system prompt:

VOICE AND PERSONALITY
If a persona profile was provided above the coach must sound UNMISTAKABLY like that person. Use their actual phrases. Reference their actual stories. Think like them. Someone reading the texts should immediately recognize who it sounds like. If no persona was provided build the personality purely from the quiz data.

COMMUNICATION STYLE
Emoji usage based on user preference. Message length. Punctuation habits. Slang level. Formality. Energy level.

THE USERS GOALS
Their specific activities. Their schedule. Why these goals matter to them personally based on what they shared.

HANDLING MISSED GOALS
Exactly what to say and how to respond when the user slips based on their miss behavior preference and the persona style.

HARD LIMITS
Topics to never bring up. Things to never say. The user's off limits list must be respected absolutely.

USER CONTEXT
Age, occupation, biggest obstacle, success vision, habit history, anything personal they shared.

REFERENCES TO WEAVE IN
Sports, music, shows, people, anything they mentioned that the coach should reference naturally.

THE CORE REMINDER
The one thing to bring up when the user is really struggling. Based on their success vision.

RELATIONSHIP DYNAMIC
The coach knows this person. They have been texting for a while. It feels like a real ongoing relationship not a first meeting.

CELEBRATION STYLE
How to respond to wins big and small based on personality and user preference.

CRITICAL INSTRUCTION: Write this system prompt in second person directed at the AI coach. Start with who they are, how they speak, and what they care about. Be extremely specific — vague instructions produce generic responses. The more specific this prompt is the more human and accurate the coach will feel.

Return only the system prompt text. No preamble, no explanation, no labels. Just the prompt itself. Keep it under 900 tokens."""

    # Call Claude Haiku to generate the system prompt
    try:
        response = anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": haiku_prompt}],
        )

        generated_prompt = response.content[0].text
        logger.info(f"Generated system prompt for user {user_id} ({len(generated_prompt)} chars)")

        # Save to coach_settings
        try:
            supabase.table("coach_settings").upsert({
                "user_id": user_id,
                "generated_system_prompt": generated_prompt,
                "persona_research": persona_profile if persona_profile else None,
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to save system prompt to coach_settings: {str(e)}")
            # But still return it

        return generated_prompt

    except Exception as e:
        logger.exception(f"Failed to generate system prompt for user {user_id}")
        raise HTTPException(status_code=500, detail="Failed to generate coach personality")


async def generate_motivation_text(user_id: str) -> str:
    """
    Fetches the user's generated_system_prompt and motivation style preferences,
    then calls Gemini Flash 1.5 to generate a single short motivational text
    in the coach's voice matching their chosen styles. Returns the text.
    
    Now includes active user context for situation awareness.
    """
    logger.info(f"Generating motivation text for user {user_id}")

    # Fetch the saved system prompt
    coach_res = supabase.table("coach_settings").select("generated_system_prompt, coach_name").eq("user_id", user_id).execute()
    if not coach_res.data or not coach_res.data[0].get("generated_system_prompt"):
        system_prompt = await build_coach_personality(user_id)
    else:
        system_prompt = coach_res.data[0]["generated_system_prompt"]

    # Get active context
    active_context = await get_active_context(user_id)
    if active_context:
        system_prompt = f"{system_prompt}\n\n{active_context}"

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
    
    Now includes active user context and upcoming reminders for situation awareness.
    """
    logger.info(f"Generating check-in text for user {user_id}, goal: {goal}")

    # Fetch the saved system prompt
    coach_res = supabase.table("coach_settings").select("generated_system_prompt").eq("user_id", user_id).execute()
    if not coach_res.data or not coach_res.data[0].get("generated_system_prompt"):
        system_prompt = await build_coach_personality(user_id)
    else:
        system_prompt = coach_res.data[0]["generated_system_prompt"]

    # Get active context and upcoming reminders
    active_context = await get_active_context(user_id)
    upcoming_reminders = await get_upcoming_reminders_preview(user_id)
    
    context_additions = []
    if active_context:
        context_additions.append(active_context)
    if upcoming_reminders:
        context_additions.append(upcoming_reminders)
    
    if context_additions:
        system_prompt = f"{system_prompt}\n\n{chr(10).join(context_additions)}"

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
# Intent Detection and Context Awareness
# ---------------------------------------------------------------------------

async def extract_intents(user_id: str, message: str, user_timezone: str) -> dict:
    """
    Extract all actionable information from an incoming SMS message using Claude Haiku.
    
    Parses commitments, deadlines, context updates, rescheduling requests, social bets,
    mood, energy, and progress updates from natural language.
    
    Args:
        user_id: UUID of the user
        message: SMS message body
        user_timezone: User's timezone (e.g., 'America/New_York')
        
    Returns:
        Dict with keys: has_actionable_content, commitments, deadlines, context_updates,
        rescheduling, social_bets, mood, energy, progress_update
        
    Error handling:
        - If JSON parsing fails, logs error and returns dict with has_actionable_content=False
        - Never crashes the incoming SMS handler
    """
    intents_logger.debug(f"Extracting intents from message by user {user_id}")
    
    try:
        # Get current time in user's timezone
        user_tz = pytz.timezone(user_timezone)
        current_time_user = datetime.now(user_tz)
        current_time_str = current_time_user.strftime("%Y-%m-%d %H:%M %Z")
        
        prompt = f"""Analyze this SMS message and extract ALL actionable information. Be thorough but only extract what is clearly stated, never assume.

Message: {message}
User timezone: {user_timezone}
Current time: {current_time_str}

Return a JSON object with exactly these fields:
{{
    "has_actionable_content": true/false,
    "commitments": [
        {{
            "description": "what they committed to doing",
            "scheduled_for_iso": "ISO 8601 datetime in UTC based on their timezone",
            "scheduled_for_human": "human readable like tomorrow morning at 8am",
            "reminder_message": "natural casual reminder to send at that time, written in coach voice, max 2 sentences"
        }}
    ],
    "deadlines": [
        {{
            "description": "what the deadline is for",
            "deadline_date_iso": "ISO 8601 date",
            "daily_checkin": true/false,
            "urgency": "low/medium/high"
        }}
    ],
    "context_updates": [
        {{
            "type": "struggle/win/personal/travel/health/mood/energy/social",
            "description": "specific thing to remember, written as a fact about the user",
            "expires_in_hours": 24
        }}
    ],
    "rescheduling": [
        {{
            "original_goal": "what they are rescheduling",
            "new_time": "when they want to do it instead",
            "skip_todays_checkin": true/false
        }}
    ],
    "social_bets": [
        {{
            "description": "what they bet or committed to with someone",
            "target": "what they need to achieve",
            "deadline_iso": "ISO 8601 date if mentioned"
        }}
    ],
    "mood": {{
        "detected": true/false,
        "level": "great/good/neutral/low/struggling",
        "reason": "brief reason if stated"
    }},
    "energy": {{
        "detected": true/false,
        "level": "high/normal/low",
        "reason": "brief reason if stated e.g. bad sleep, sick, energized"
    }},
    "progress_update": {{
        "detected": true/false,
        "goal": "what goal this relates to",
        "achievement": "what they accomplished",
        "metric": "number or specific result if mentioned"
    }}
}}

Return valid JSON only. No markdown, no explanation, just the JSON object."""
        
        response = anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        
        response_text = response.content[0].text
        
        # Try to parse as JSON
        try:
            intents = json.loads(response_text)
            intents_logger.info(f"Extracted intents for user {user_id}: {json.dumps(intents, default=str)}")
            return intents
        except json.JSONDecodeError as e:
            intents_logger.error(f"Failed to parse intents JSON for user {user_id}: {str(e)}\nResponse: {response_text}")
            return {
                "has_actionable_content": False,
                "commitments": [],
                "deadlines": [],
                "context_updates": [],
                "rescheduling": [],
                "social_bets": [],
                "mood": {"detected": False},
                "energy": {"detected": False},
                "progress_update": {"detected": False}
            }
            
    except Exception as e:
        intents_logger.error(f"Intent extraction failed for user {user_id}: {str(e)}", exc_info=True)
        return {
            "has_actionable_content": False,
            "commitments": [],
            "deadlines": [],
            "context_updates": [],
            "rescheduling": [],
            "social_bets": [],
            "mood": {"detected": False},
            "energy": {"detected": False},
            "progress_update": {"detected": False}
        }


async def process_intents(user_id: str, intents: dict, user_timezone: str) -> None:
    """
    Process extracted intents and insert them into appropriate Supabase tables.
    
    Handles: commitments → reminders, deadlines, context_updates, rescheduling,
    social_bets, mood, energy, and progress updates with streak management.
    
    Args:
        user_id: UUID of the user
        intents: Dict returned from extract_intents()
        user_timezone: User's timezone for timestamp conversion
        
    Error handling:
        - Logs all database operations and errors
        - Never crashes even if individual inserts fail
        - Continues processing other intents if one fails
    """
    intents_logger.info(f"Processing intents for user {user_id}")
    user_tz = pytz.timezone(user_timezone)
    now_utc = datetime.now(pytz.UTC)
    
    try:
        # Process commitments → reminders
        for commitment in intents.get("commitments", []):
            try:
                scheduled_iso = commitment.get("scheduled_for_iso")
                if scheduled_iso:
                    scheduled_dt = datetime.fromisoformat(scheduled_iso.replace('Z', '+00:00'))
                    
                    reminder_data = {
                        "user_id": user_id,
                        "description": commitment.get("description"),
                        "scheduled_for": scheduled_dt.isoformat(),
                        "reminder_message": commitment.get("reminder_message"),
                        "sent": False,
                    }
                    supabase.table("reminders").insert(reminder_data).execute()
                    intents_logger.info(f"Inserted reminder for user {user_id}: {commitment.get('description')}")
            except Exception as e:
                intents_logger.error(f"Failed to insert reminder for user {user_id}: {str(e)}")
                continue
        
        # Process deadlines
        for deadline in intents.get("deadlines", []):
            try:
                deadline_data = {
                    "user_id": user_id,
                    "description": deadline.get("description"),
                    "deadline_date": deadline.get("deadline_date_iso"),
                    "daily_checkin": deadline.get("daily_checkin", True),
                    "active": True,
                }
                supabase.table("deadlines").insert(deadline_data).execute()
                intents_logger.info(f"Inserted deadline for user {user_id}: {deadline.get('description')}")
            except Exception as e:
                intents_logger.error(f"Failed to insert deadline for user {user_id}: {str(e)}")
                continue
        
        # Process context updates
        for context in intents.get("context_updates", []):
            try:
                expires_hours = context.get("expires_in_hours", 24)
                expires_at = now_utc + timedelta(hours=expires_hours)
                
                context_data = {
                    "user_id": user_id,
                    "type": context.get("type"),
                    "description": context.get("description"),
                    "expires_at": expires_at.isoformat(),
                }
                supabase.table("user_context").insert(context_data).execute()
                intents_logger.info(f"Inserted context update for user {user_id}: {context.get('type')}")
            except Exception as e:
                intents_logger.error(f"Failed to insert context update for user {user_id}: {str(e)}")
                continue
        
        # Process rescheduling
        for resched in intents.get("rescheduling", []):
            try:
                context_data = {
                    "user_id": user_id,
                    "type": "personal",
                    "description": f"Rescheduled '{resched.get('original_goal')}' to {resched.get('new_time')}",
                    "expires_at": (now_utc + timedelta(hours=48)).isoformat(),
                }
                supabase.table("user_context").insert(context_data).execute()
                intents_logger.info(f"Inserted rescheduling context for user {user_id}")
            except Exception as e:
                intents_logger.error(f"Failed to insert rescheduling context for user {user_id}: {str(e)}")
                continue
        
        # Process social bets
        for bet in intents.get("social_bets", []):
            try:
                bet_data = {
                    "user_id": user_id,
                    "description": bet.get("description"),
                    "target": bet.get("target"),
                    "deadline": bet.get("deadline_iso"),
                    "completed": False,
                }
                supabase.table("social_bets").insert(bet_data).execute()
                intents_logger.info(f"Inserted social bet for user {user_id}: {bet.get('description')}")
            except Exception as e:
                intents_logger.error(f"Failed to insert social bet for user {user_id}: {str(e)}")
                continue
        
        # Process mood
        if intents.get("mood", {}).get("detected"):
            try:
                mood_info = intents.get("mood", {})
                context_data = {
                    "user_id": user_id,
                    "type": "mood",
                    "description": f"Mood: {mood_info.get('level')}. {mood_info.get('reason', '')}",
                    "expires_at": (now_utc + timedelta(hours=24)).isoformat(),
                }
                supabase.table("user_context").insert(context_data).execute()
                intents_logger.info(f"Inserted mood context for user {user_id}: {mood_info.get('level')}")
            except Exception as e:
                intents_logger.error(f"Failed to insert mood context for user {user_id}: {str(e)}")
        
        # Process energy
        if intents.get("energy", {}).get("detected"):
            try:
                energy_info = intents.get("energy", {})
                context_data = {
                    "user_id": user_id,
                    "type": "energy",
                    "description": f"Energy: {energy_info.get('level')}. {energy_info.get('reason', '')}",
                    "expires_at": (now_utc + timedelta(hours=12)).isoformat(),
                }
                supabase.table("user_context").insert(context_data).execute()
                intents_logger.info(f"Inserted energy context for user {user_id}: {energy_info.get('level')}")
            except Exception as e:
                intents_logger.error(f"Failed to insert energy context for user {user_id}: {str(e)}")
        
        # Process progress updates
        if intents.get("progress_update", {}).get("detected"):
            try:
                progress = intents.get("progress_update", {})
                context_data = {
                    "user_id": user_id,
                    "type": "win",
                    "description": f"Achieved: {progress.get('achievement')}. {progress.get('metric', '')}",
                    "expires_at": (now_utc + timedelta(hours=72)).isoformat(),
                }
                supabase.table("user_context").insert(context_data).execute()
                intents_logger.info(f"Inserted progress update for user {user_id}: {progress.get('achievement')}")
                
                # Update streak if goal_id can be determined
                goal_name = progress.get("goal", "").lower()
                goals_res = supabase.table("goals").select("id, activity").eq("user_id", user_id).execute()
                for goal in goals_res.data or []:
                    if goal_name in goal.get("activity", "").lower():
                        streak = await update_streak(user_id, goal["id"])
                        intents_logger.info(f"Updated streak for goal {goal['id']}: {streak}")
                        break
            except Exception as e:
                intents_logger.error(f"Failed to process progress update for user {user_id}: {str(e)}")
        
        intents_logger.info(f"Finished processing intents for user {user_id}")
        
    except Exception as e:
        intents_logger.error(f"Critical error processing intents for user {user_id}: {str(e)}", exc_info=True)


async def get_active_context(user_id: str) -> str:
    """
    Fetch all active context for a user and format for injection into system prompt.
    
    Returns context entries where expires_at > now.
    
    Args:
        user_id: UUID of the user
        
    Returns:
        Formatted string like:
        'ACTIVE USER CONTEXT — factor this naturally into your response:
        mood: feeling great after finishing the project
        energy: low because bad sleep'
        
        Or empty string if no active context.
    """
    try:
        now_utc = datetime.now(pytz.UTC)
        context_res = (
            supabase.table("user_context")
            .select("*")
            .eq("user_id", user_id)
            .gt("expires_at", now_utc.isoformat())
            .execute()
        )
        
        if not context_res.data:
            return ""
        
        context_lines = ["ACTIVE USER CONTEXT — factor this naturally into your response without explicitly referencing that you remember it:"]
        for ctx in context_res.data:
            line = f"{ctx['type']}: {ctx['description']}"
            context_lines.append(line)
        
        return "\n".join(context_lines)
        
    except Exception as e:
        logger.warning(f"Failed to get active context for user {user_id}: {str(e)}")
        return ""


async def get_upcoming_reminders_preview(user_id: str) -> str:
    """
    Get upcoming unsent reminders scheduled within next 6 hours.
    
    Args:
        user_id: UUID of the user
        
    Returns:
        Formatted string like:
        'UPCOMING REMINDERS: Call mom (in 2 hours), Submit project (in 4 hours)'
        
        Or empty string if no upcoming reminders.
    """
    try:
        now_utc = datetime.now(pytz.UTC)
        six_hours_later = now_utc + timedelta(hours=6)
        
        reminders_res = (
            supabase.table("reminders")
            .select("*")
            .eq("user_id", user_id)
            .eq("sent", False)
            .gte("scheduled_for", now_utc.isoformat())
            .lte("scheduled_for", six_hours_later.isoformat())
            .execute()
        )
        
        if not reminders_res.data:
            return ""
        
        reminder_items = []
        for reminder in reminders_res.data:
            scheduled = datetime.fromisoformat(reminder["scheduled_for"].replace('Z', '+00:00'))
            hours_remaining = int((scheduled - now_utc).total_seconds() / 3600)
            item = f"{reminder['description']} (in {hours_remaining} hours)"
            reminder_items.append(item)
        
        return f"UPCOMING REMINDERS: {', '.join(reminder_items)}"
        
    except Exception as e:
        logger.warning(f"Failed to get upcoming reminders for user {user_id}: {str(e)}")
        return ""


async def update_streak(user_id: str, goal_id: str) -> dict:
    """
    Update or create a streak entry for a user's goal.
    
    Logic:
    - If no streak exists: create with current_streak=1, longest_streak=1
    - If last_checkin was yesterday: increment current_streak
    - If last_checkin was before yesterday: reset current_streak to 1
    - Update longest_streak if current exceeds it
    - Check for milestone hits (3, 7, 14, 30, 60, 100)
    
    Args:
        user_id: UUID of the user
        goal_id: UUID of the goal
        
    Returns:
        Dict with: current_streak, longest_streak, milestone_hit (bool), milestone_number (int or None)
        
    Error handling:
        - Logs all operations
        - Returns empty dict if operation fails
    """
    try:
        today = datetime.now().date()
        
        # Fetch existing streak
        streak_res = (
            supabase.table("streaks")
            .select("*")
            .eq("user_id", user_id)
            .eq("goal_id", goal_id)
            .execute()
        )
        
        if not streak_res.data:
            # Create new streak
            streak_data = {
                "user_id": user_id,
                "goal_id": goal_id,
                "current_streak": 1,
                "longest_streak": 1,
                "last_checkin": today.isoformat(),
            }
            supabase.table("streaks").insert(streak_data).execute()
            streaks_logger.info(f"Created new streak for user {user_id}, goal {goal_id}")
            return {
                "current_streak": 1,
                "longest_streak": 1,
                "milestone_hit": False,
                "milestone_number": None,
            }
        
        streak = streak_res.data[0]
        last_checkin = datetime.fromisoformat(streak["last_checkin"]).date() if streak.get("last_checkin") else None
        
        current_streak = streak.get("current_streak", 0)
        longest_streak = streak.get("longest_streak", 0)
        
        # Determine new streak count
        if last_checkin == today:
            # Already checked in today, don't increment
            pass
        elif last_checkin == today - timedelta(days=1):
            # Last checkin was yesterday, continue streak
            current_streak += 1
        else:
            # Broke the streak
            current_streak = 1
        
        # Update longest streak if needed
        if current_streak > longest_streak:
            longest_streak = current_streak
        
        # Check for milestones
        milestones = [3, 7, 14, 30, 60, 100]
        milestone_hit = current_streak in milestones
        
        # Update database
        update_data = {
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "last_checkin": today.isoformat(),
            "updated_at": datetime.now(pytz.UTC).isoformat(),
        }
        supabase.table("streaks").update(update_data).eq("id", streak["id"]).execute()
        
        streaks_logger.info(
            f"Updated streak for user {user_id}, goal {goal_id}: "
            f"current={current_streak}, longest={longest_streak}, milestone={milestone_hit}"
        )
        
        return {
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "milestone_hit": milestone_hit,
            "milestone_number": current_streak if milestone_hit else None,
        }
        
    except Exception as e:
        streaks_logger.error(f"Failed to update streak for user {user_id}, goal {goal_id}: {str(e)}", exc_info=True)
        return {}


async def get_message_history(user_id: str, limit: int = 20) -> list:
    """
    Fetch recent message history for a user.
    
    Args:
        user_id: UUID of the user
        limit: How many messages to fetch (default 20)
        
    Returns:
        List of messages in conversation order (oldest first)
    """
    try:
        messages_res = (
            supabase.table("messages")
            .select("direction, body, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        
        # Reverse to get chronological order (oldest first)
        return list(reversed(messages_res.data or []))
        
    except Exception as e:
        logger.warning(f"Failed to get message history for user {user_id}: {str(e)}")
        return []


async def generate_gemini_response(
    system_prompt: str,
    message_history: list,
    new_message: str,
) -> str:
    """
    Generate a response using Gemini 1.5 Flash with full conversation context.
    
    Args:
        system_prompt: System instruction for the coach personality
        message_history: List of recent messages [{"direction": "inbound/outbound", "body": "...", "created_at": "..."}]
        new_message: The latest incoming message
        
    Returns:
        Generated response text
        
    Error handling:
        - Logs errors and returns fallback message
    """
    try:
        # Build Gemini chat history
        gemini_history = []
        for msg in message_history:
            role = "user" if msg["direction"] == "inbound" else "model"
            gemini_history.append({"role": role, "parts": [msg["body"]]})
        
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=system_prompt,
        )
        
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(new_message)
        text = response.text.strip()
        
        logger.info(f"Generated Gemini response: {text[:60]}...")
        return text
        
    except Exception as e:
        logger.error(f"Gemini response generation failed: {str(e)}", exc_info=True)
        return "got it 👊"





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
