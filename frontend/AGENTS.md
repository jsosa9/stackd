<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know
This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

<!-- BEGIN:stackd-context -->
# stackd Project Context

## Overview
SMS accountability app where users build a personalized AI coach that texts them daily.

## Tech Stack
- Frontend: Next.js 14 App Router, TypeScript, Tailwind CSS
- Backend: Python FastAPI
- Database: Supabase (PostgreSQL)
- SMS: Twilio
- AI: Gemini 2.5 Flash Lite — handles ALL AI tasks (persona generation, conversations, vision, scheduling)
- Hosting: Frontend on Vercel, Backend on Railway

## Key Files
- backend/routes/ai.py — AI functions, persona research, personality generation
- backend/routes/sms.py — Twilio webhook, incoming SMS handling
- backend/routes/scheduler.py — scheduled jobs, reminders, streaks
- frontend/app/quiz/ — all 6 quiz steps
- frontend/lib/supabase.ts — Supabase client

## Architecture Rules
- Gemini 2.5 Flash Lite handles ALL AI tasks — persona generation, conversations, vision, and scheduled texts
- Every Gemini call must include HUMAN_BEHAVIOR_RULES and CONVICTION_RULES constants
- Never let one user error affect other users in batch jobs
- All Python functions must be async
- Log everything to backend/logs/
- Backend uses Supabase service role key — RLS applies to frontend only

## Persona Accuracy (CRITICAL)
The coach persona is the core product. Whether the user picks a celebrity or builds a custom coach, the AI must authentically replicate that person's voice — not a generic approximation.

- For celebrities: research their known speech patterns, catchphrases, philosophies, interview style, and how they actually motivate people. David Goggins should sound like David Goggins — raw, brutal honesty, "stay hard", no sugarcoating. Not a generic tough coach.
- For custom coaches: honor every field the user filled out — favorite phrases, avoid phrases, tone, missed day response, celebration style. These are not suggestions, they are rules.
- generated_system_prompt in coach_settings is the source of truth for every Gemini call for that user. It must be rich, specific, and character-accurate.
- persona_research in coach_settings stores the raw research used to build the prompt. Keep it — it's used for prompt regeneration and versioning.
- Never let the persona drift toward generic motivational language. If it sounds like it could be any coach, it's wrong.

## Inbound SMS Intent Router
Every inbound SMS goes through a single intent classifier before any action is taken. Gemini classifies the message as one of:
- **check-in** — user reporting on a goal (log progress, update streak)
- **to-do** — user wants a reminder ("remind me to X at 5pm") → parse time, create unified reminder
- **journal** — user is reflecting, venting, or narrating their day → store entry, respond in coach voice
- **question** — user asking the coach something → respond in persona
- **general chat** — everything else → respond in persona

Journal entries can also double as goal check-ins if they mention goal activity. Gemini should detect and handle both in one pass.

## Features


### 📋 Planned features 
- Google OAuth sign in
- Quiz onboarding (6 steps — goals, schedule, coach persona)
- Celebrity coach persona generation (e.g. David Goggins)
- Custom coach builder
- Daily check-in SMS via Twilio
- Gemini 2.5 Flash Lite conversations
- **Unified reminder system** — to-dos created via SMS and Google Calendar events live in the same reminders table. source field tracks origin: 'sms' | 'calendar' | 'system'. Same scheduler fires all of them.
- **Google Calendar sync** — OAuth scope addition, poll/webhook for upcoming events, auto-create reminder rows from calendar events, text user before events
- **SMS journal** — detect journal intent via inbound router, store entries in journal_entries table, respond in coach voice, weekly summary texts
- **Ad-hoc to-dos via SMS** — user texts "do X at 5pm", Gemini parses intent + time, creates reminder row with source='sms', scheduler fires reminder at 4:30
- **Calorie tracking** — MMS photo → Gemini vision analyzes food → logs to nutrition_logs table, coach responds with estimate in persona voice

## Database Schema (Supabase/PostgreSQL)

### Tables & Purpose
- **users** — id, email, phone, name, age, occupation, paused
- **goals** — user_id, activity, category, days[], times_per_day (JSONB)
- **coach_settings** — user_id, coach_name, personality_preset, tone, emoji_usage, message_length, miss_behavior, intensity, coach_setup_type, sounds_like, custom_build (JSONB), generated_system_prompt, persona_research, personality_id, is_active
- **schedule** — user_id (unique), checkin_time, timezone, motivation_enabled, motivation_frequency, motivation_window_start/end, motivation_styles[], morning_kickstart, evening_reflection
- **messages** — user_id, direction ('inbound'|'outbound'), body, created_at
- **streaks** — user_id, goal_id, current_streak, longest_streak, last_checkin
- **reminders** — user_id, description, scheduled_for, reminder_message, sent, source ('sms'|'calendar'|'system')
- **deadlines** — user_id, description, deadline_date, daily_checkin, active
- **user_context** — user_id, type, description, expires_at
- **habit_patterns** — user_id, pattern_type, description, day_of_week, time_of_day, confidence, active
- **social_bets** — user_id, description, target, deadline, completed
- **sent_quotes** — user_id, quote_id
- **personas** — personality_id (unique), name (unique), system_instruction, few_shot_examples (JSONB), is_active
- **phone_link_tokens** — user_id, token, used, expires_at

### Planned Tables
- **journal_entries** — user_id, body, coach_response, created_at
- **nutrition_logs** — user_id, image_url, gemini_analysis (JSONB), estimated_calories, created_at
- **calendar_events** — user_id, google_event_id, title, start_time, reminder_sent, created_at


### Architecture Principles

Strict Separation of Concerns:

Persona Module (personas.py): Acts as the "Soul." It is a lookup service. It fetches the system_instruction and few_shot_examples from Supabase based on a 4-char + 4-digit ID. It DOES NOT contain coaching logic.

Coach Module (coach.py): Acts as the "Brain." It manages the coaching state (goals, schedules, reminders, nutrition logs). It is the service that constructs the Full Prompt by wrapping the Persona's DNA with functional Coaching Context.

The Prompt Assembly Contract:
When assembling the final prompt for Gemini, the structure MUST follow this hierarchy:

        System DNA: (The Persona's philosophy and voice).

        Coaching Protocols: (The specific, functional tasks the user needs the coach to perform, e.g., "Check status of goal X").

        Few-Shot Examples: (The Persona's verbal style).

        Current Session Data: (Real-time data from database, e.g., "Today is May 3rd, user has a gym event at 6pm").

        User Message: (The raw inbound text).

Behavioral Guidelines for Coding

    No Hardcoding: All persona content resides in Supabase. The Python code must be purely functional.

    Routing over Guessing: If a user input requires an action (like logging food or setting a reminder), use a routing function to determine intent BEFORE sending the message to Gemini.

    Proactive Coaching: When building prompts in coach.py, the AI must be instructed to never end a turn with a passive question. It must either confirm an action taken or prompt for the next accountability milestone.

    Error Handling: If an ID is invalid or a database fetch fails, the system must gracefully fall back to a "Default Coach" persona rather than crashing the session.

Persona Constraints

    The model must NEVER reveal that it is an AI.

    It must NEVER use "As an AI assistant..." or polite filler phrases.

    It must embody the celebrity's persona while executing the functionality of an elite, high-accountability coach.

Routing & Orchestration (SMS Lifecycle)

All inbound messages must follow a strict execution pipeline to ensure data integrity and personality consistency:

    Intent Classification: Every inbound SMS must first pass through classify_intent(). The AI must categorize the intent into one of five buckets: ['check-in', 'to-do', 'journal', 'question', 'general_chat'].

    State Execution (The "Brain"): The system performs the database mutation based on the intent (e.g., logging a streak, inserting a reminder, saving a journal entry). This occurs before any response generation.

    Persona Synthesis (The "Soul"): After the DB state is updated, the system calls PersonaManager to retrieve the active persona's configuration.

    Final Prompt Assembly: The system constructs the final Gemini prompt using the Prompt Assembly Contract (DNA + Protocols + Examples + Session Data + Result of State Execution).

    Response Generation: Gemini generates a response that performs the requested task while maintaining the persona's voice and accountability standards.

Development Guidelines for Routes

    Decoupled Handlers: Each intent type should ideally have a dedicated handler function in routes/sms.py (or a sub-module) to keep the routing logic clean.

    Atomic Operations: Ensure database operations are successful before attempting to generate the coach's reply.

    No Response-Only Logic: Never bypass the Intent Router. If an input is ambiguous, it should default to general_chat rather than attempting to guess a state change.


Code Organization & Reusability (DRY Principle)

    Single Source of Truth: Any logic that handles intent classification, database mutations, or Gemini prompt assembly must reside in backend/services/.

    Dual-Channel Integration: All input sources (e.g., routes/sms.py for Twilio and routes/dev.py for local testing) must act as thin wrappers that only handle channel-specific concerns (e.g., parsing the Twilio payload or API request body). They must import the shared business logic from services/.

    No Redundant Logic: If the agent proposes code that mirrors logic existing elsewhere, it must instead refactor the existing code into a shared service.

    Identical Behavior: /quiz/dev must produce the exact same outcome as the SMS pipeline. If a feature (like calorie tracking) is added to the shared service, it is automatically available to both channels.

Coaching Engine Architecture (The Triple-Layer Stack)

    Input Routing (Router Layer): Every message passes through classify_intent first. This prevents the persona from "guessing" the intent and ensures atomic database operations.

    Context Injection (Fact Layer): Before generating a response, the system must fetch the UserState (goals, reminders, streaks). This data is passed as a system-level injection, not as part of the user's chat history. This ensures the coach is always "aware" of the truth.

    Identity Overlay (Persona Layer): The final step is passing the UserState + Original Message + Persona Instructions into Gemini. The Persona instructions must explicitly command: "Use the provided UserState to drive accountability. If the user is failing, use Inquiry Mode to pick their brain about the root cause."

Constraint: The AI is strictly forbidden from logging data or performing calculations inside the Persona prompt. It must only interpret the data provided by the Context Injector.

The Data-to-Persona Contract

    No Raw DB Access: The Persona is strictly forbidden from querying the database directly. It must rely exclusively on the CoachingContext string provided by the coaching_service.

    State-Driven Triggers: The Inquiry Mode must be explicitly triggered by the has_anomaly flag from the coaching_service. The model should not be allowed to decide "on its own" when to be investigative—it must react to the data provided.

    Atomic Pipeline: Every request must follow: Router → Data Persistence → Context Assembly → Persona Synthesis. Any breach of this order (e.g., generating a response before a DB write) is a critical bug.

Scalable Context Architecture

    Modular Providers: All data retrieval must be abstracted into "Context Providers." To add a new coaching domain (e.g., Sleep), create a new Provider class rather than modifying existing service logic.

    Context Isolation: Each Provider is responsible for detecting its own anomalies. If a provider flags an anomaly, it must provide a specific reason that the Inquiry Mode can then use for questioning.

    Standardized Output: Every Provider must return a string that follows the structure: [Category]\n- Status\n- Anomaly/Risk. This ensures the AI prompt remains consistent regardless of how many domains are being tracked.

Decision Architecture

    Intent Router (The Brain): Always uses gemini-2.5-flash-lite. Its ONLY job is to analyze raw SMS strings and output strict JSON intents (["INTENT1", "INTENT2"]). It MUST NOT attempt to generate coaching advice.

    Response Generator (The Persona): Always uses gemini-2.5-flash-lite. It receives the processed results from the Providers (execution_results) and generates the persona-driven SMS response.

    Constraint: Never combine routing and generating into a single LLM call. This keeps the coach's personality clean and the routing logic deterministic.


### Key Relationships
- All tables cascade delete from users
- coach_settings: one active row per user (is_active = TRUE)
- streaks links to both users and goals
- reminders.source distinguishes to-dos from calendar events from system messages
- RLS enabled on all tables — backend uses service role key

## Conventions
- Frontend: yarn, TypeScript strict mode
- Backend: uv, async Python, try/except everything
- Quiz data persisted to sessionStorage (Zustand + persist middleware) so it survives the Google OAuth redirect; cleared by useQuiz.clear() after Step 7 writes to Supabase

FOR SQL REFER TO THE SUPABASE_SCHEMA.SQL THIS HAS ALL THE SQL IN SUPABASE IN ONE FILE 
<!-- END:stackd-context -->