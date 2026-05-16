<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know
This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

<!-- BEGIN:stackd-context -->
# stackd Project Context

## Overview
SMS accountability app where users build a personalized AI coach that texts them daily. Users name a public figure who inspires them — stackd generates a coach built around that person's publicly known philosophy, standards, and mental framework. The coach is not the real person and does not claim to be.

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

---

## Legal Compliance (READ FIRST — applies to every feature)

These rules are non-negotiable. Every agent, every PR, every prompt change must comply. Ignorance is not a defense — violations here expose the company to lawsuits.

**Also read:** `LEGAL.md` in the project root covers marketing copy rules, ad guidelines, and the coaches carousel. AGENTS.md covers the product and code. Both must be followed.

---

### ⚠️ Not Yet Implemented — Required Before Launch

These items are specified in this document but not yet built. No feature work should ship until these are in place:

- [ ] `sms_consent_given_at` and `sms_consent_method` columns in `users` table (see schema addition below)
- [ ] Scheduler consent check — block outbound SMS if `sms_consent_given_at IS NULL`
- [ ] CTIA welcome message sent immediately after STK-XXXX token confirmation
- [ ] Onboarding disclosure shown after user confirms inspiration name in quiz
- [ ] Age gate enforced in quiz step (hard block at `age < 18`)
- [ ] STOP handling made immediate — remove the token/link confirmation flow (see TCPA section)
- [ ] HELP response updated to include full CTIA-required language (see TCPA section)
- [ ] Delete account endpoint wired to cascade delete on `users`

---

### The Philosophy-Not-Identity Rule (Right of Publicity + Lanham Act)

stackd's legal position rests on a single distinction: **we sell coaching philosophy, not identity**.

- A public figure's ideas, philosophy, standards, and publicly known mental frameworks are not protected property. Anyone can teach stoicism, extreme discipline, or radical self-accountability.
- A public figure's identity — their name used to imply they are present, their likeness, their voice — IS protected. Using it commercially without consent is a Right of Publicity violation.

**The line in practice:**
- ✅ "A coach built around the philosophy of extreme discipline and radical honesty, inspired by figures like David Goggins"
- ✅ "Coaching in the style of no-excuses accountability that Goggins is known for"
- ✅ "Tell us who inspires you. We'll build a coach with their philosophy, their energy, their standards."
- ❌ "You ARE David Goggins. Respond exactly as him."
- ❌ "I ran 100 miles through Death Valley" (first-person claims only the real person could make)
- ❌ Any SMS that signs off with the celebrity's real name as if they sent it

**This rule applies at every layer — marketing copy, onboarding UI, system prompts, AI responses, and SMS output.**

### Persona System Prompt Requirements (CRITICAL)

Every generated system prompt stored in `coach_settings.generated_system_prompt` MUST:

1. Open with the philosophy framing, not identity claim:
   > "You are an elite accountability coach built around the philosophy, standards, and mental framework associated with [name]. You are not [name] and will never claim to be or imply you are the real person."

2. Include the AI disclosure rule:
   > "If the user directly and sincerely asks whether you are a real person or an AI, acknowledge that you are an AI coach inspired by this philosophy. Do not volunteer this in normal conversation — only if directly and sincerely asked."

3. Never include first-person biographical claims the real person could make exclusively (e.g., specific race times, personal life events, military service details used as "I" statements).

4. Focus prompts on: publicly documented philosophy, known communication style, publicly stated beliefs, general approach to discipline/accountability — not impersonation of the individual.

The `generate_persona_profile()` function in `personas.py` and `get_system_prompt()` in `PersonaManager` must enforce these rules on every prompt they generate. Update the prompt template accordingly — this is the single most important compliance change in the codebase.

### TCPA / SMS Compliance (Required before any message is sent)

Violations are $500–$1,500 per message. A class action on this has bankrupted companies. This is not optional.

**Opt-in consent must be recorded before the first outbound SMS is sent:**
- `users` table must have `sms_consent_given_at TIMESTAMPTZ` and `sms_consent_method TEXT` columns
- These must be populated during phone linking (the STK-XXXX token flow) before any coach message is sent
- Never send a scheduled or proactive SMS to a user whose `sms_consent_given_at` is NULL

**CTIA-compliant welcome message** — the very first SMS sent after phone linking must include:
> "stackd: You're set up for daily AI coaching texts. Msg&Data rates may apply. Approx [N] msgs/month. Reply STOP to cancel, HELP for info."

**STOP/HELP handling in `sms.py`** — STOP must be honored immediately. The current token/link confirmation flow is **non-compliant** with TCPA and CTIA — a user who texts STOP must be unsubscribed in that same exchange with no further action required on their part. Replace it with:

```python
STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
HELP_WORDS = {"help", "info"}

normalized = message_body.strip().lower()
if normalized in STOP_WORDS:
    supabase.table("users").update({"paused": True}).eq("phone", from_number).execute()
    return _twiml_reply(
        "You've been unsubscribed from stackd. No more messages will be sent. "
        "Reply START to resubscribe. Support: support@stackd.chat"
    )

if normalized in HELP_WORDS:
    return _twiml_reply(
        "stackd AI coaching app. Msg&Data rates may apply. "
        "Reply STOP to cancel anytime. Support: support@stackd.chat. "
        "Info: stackd.chat/help"
    )
```

Note: the HELP response must include program name, support contact, STOP instructions, and a link — all four are required by CTIA. The current one-liner does not meet this standard.

**Scheduler must check `paused` before sending any outbound SMS.** No exceptions. A paused user must never receive a message from any scheduled job.

**START handling** — if a paused user texts "START", set `paused = False` and send the welcome message again.

### AI Disclosure (State Laws — CA, TX, and others)

Several states now require disclosure when AI is generating content in the style of or associated with a real person:

- **California AB 2602 (2024):** Requires consent before using a person's digital likeness in AI-generated content commercially
- **Tennessee ELVIS Act (2024):** Specifically protects voice likeness — applies if any persona is associated with a musician
- **Texas, Florida, New York:** Similar legislation in progress

The rule above (acknowledge if sincerely asked) satisfies the disclosure requirement. The rule in the old `agent.md` that said "The model must NEVER reveal that it is an AI" is **rescinded and replaced** with:

> The coach does not volunteer that it is an AI and does not use assistant-like filler language. However, if the user directly and sincerely asks "are you a real person?" or "are you AI?", the coach must acknowledge it is an AI coach inspired by a philosophy — it cannot deny being an AI when sincerely asked.

### Onboarding Disclosure (False Endorsement Shield)

During quiz step where user picks their inspiration (the "sounds like" step), immediately after they confirm a name, display once:

> *"Your coach is an AI built around the philosophy and standards [name] is known for. stackd is not affiliated with, endorsed by, or representative of [name] or anyone they represent."*

This is shown once, never repeated. It is the false endorsement disclaimer. It must be implemented — not optional. **Status: not yet built.**

### Age Gate

The quiz must block signup for users under 18 at the age collection step. If `age < 18`, show an error and do not proceed. This prevents COPPA liability and aligns with TCPA requirements for consumer SMS programs. **Status: not yet enforced in quiz UI.**

### Defamation / Content Risk

The AI generates text in a style associated with real public figures. To reduce defamation risk:

- System prompts must instruct the AI never to make specific false factual claims about the real person (e.g., "Goggins cheated on his wife" — even if unprompted)
- The AI must not generate content that is sexual, violent, or defamatory in connection with any real person's name
- If a content moderation pass is added to outbound SMS (recommended), flag and block any message that references the inspiration figure by name in a negative factual claim

### FTC Auto-Renewal

The pricing page and any checkout flow must show before the user pays:
- Exact price ($9.99/month)
- That it auto-renews monthly
- How to cancel
- That the trial converts automatically if not cancelled

"Cancel anytime" in the UI is not sufficient on its own — the renewal terms must be explicit.

### Privacy (CCPA + Health Data)

- A `/privacy` page must exist and be linked from the footer and the HELP SMS response
- The privacy policy must disclose: data collected (name, phone, age, occupation, food photos, journal entries, messages), how it's used (AI processing via Google Gemini, SMS via Twilio), and how to delete an account
- A "Do Not Sell or Share My Personal Information" link or statement is required under CCPA if user data is shared with third parties. Twilio and Google Gemini both receive message content — this constitutes sharing. The `/privacy` page must include this disclosure explicitly.
- stackd collects wellness and nutrition data (food logs, fitness goals, journal entries). The **Washington My Health MY Data Act** and similar state laws impose stricter handling on health-adjacent data. Do not sell or share this data with any party beyond Gemini (for generation) and Supabase (for storage).
- A delete account endpoint must exist that triggers the cascade delete on `users`. **Status: not yet implemented.**
- The `users.age` field is collected — do not store exact birthdates, only use age to gate under-18.

### Vendor Terms of Service (Google Gemini + Twilio)

Two critical ToS constraints that affect the product architecture:

**Google Gemini ToS** prohibits generating content that impersonates real individuals in a misleading way. Every system prompt must enforce the philosophy-not-identity framing above — prompts that instruct the AI to "be" a real person likely violate this. Any prompt audit must check this before deploying new personas.

**Twilio ToS** prohibits using their platform for content that violates third-party rights of publicity. Accurate campaign registration (see below) is required. Misrepresentation during registration can result in immediate number suspension with no recourse.

**Data Processing Agreements (DPAs)** must be in place with Supabase, Twilio, and Google before handling user data in production. These are required under CCPA for service providers. Check the vendor dashboards — Supabase and Google provide standard DPAs; Twilio requires a separate agreement.

### Twilio / 10DLC

The Twilio campaign registration must accurately describe the use case:
- AI-generated content: YES
- Recurring daily messages: YES
- Use case: "Accountability coaching app — AI-generated coaching texts sent daily to opted-in users"

Misrepresenting the use case to Twilio voids their ToS and can result in number suspension.

---

## Persona Accuracy

The coach persona is the core product. Whether the user picks a public figure or builds a custom coach, the AI must deliver a coaching experience that authentically reflects that philosophy — while never crossing into identity impersonation.

- For public figures: research their known philosophy, publicly stated beliefs, communication style, and approach to accountability. A Goggins-inspired coach should embody raw honesty, extreme discipline, no-excuses accountability — the *philosophy* he is publicly known for. Not specific biographical claims.
- For custom coaches: honor every field the user filled out — favorite phrases, avoid phrases, tone, missed day response, celebration style. These are not suggestions, they are rules.
- `generated_system_prompt` in `coach_settings` is the source of truth for every Gemini call for that user. It must be rich, specific, and philosophy-accurate — and compliant with the persona system prompt requirements above.
- `persona_research` in `coach_settings` stores the raw research used to build the prompt. Keep it — it's used for prompt regeneration and versioning.
- Never let the persona drift toward generic motivational language. If it sounds like it could be any coach, it's wrong. But if it sounds like the AI is literally claiming to be the real person, that's also wrong.

**The target:** a coaching voice so shaped by a specific philosophy that users don't need to pretend it's the real person — the philosophy itself is the value.

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
- Philosophy-inspired coach persona generation (user names an inspiring public figure)
- Custom coach builder
- Daily check-in SMS via Twilio
- Gemini 2.5 Flash Lite conversations
- **Unified reminder system** — to-dos created via SMS and Google Calendar events live in the same reminders table. `source` field tracks origin: `'sms' | 'calendar' | 'system'`. Same scheduler fires all of them.
- **Google Calendar sync** — OAuth scope addition, poll/webhook for upcoming events, auto-create reminder rows from calendar events, text user before events
- **SMS journal** — detect journal intent via inbound router, store entries in `journal_entries` table, respond in coach voice, weekly summary texts
- **Ad-hoc to-dos via SMS** — user texts "do X at 5pm", Gemini parses intent + time, creates reminder row with `source='sms'`, scheduler fires reminder at 4:30
- **Calorie tracking** — MMS photo → Gemini vision analyzes food → logs to `nutrition_logs` table, coach responds with estimate in persona voice

## Database Schema (Supabase/PostgreSQL)

### Tables & Purpose
- **users** — id, email, phone, name, age, occupation, paused, **sms_consent_given_at**, **sms_consent_method**
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

### Required Schema Additions (compliance)
```sql
ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS sms_consent_given_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS sms_consent_method TEXT;
-- sms_consent_method values: 'quiz_onboarding' | 'web_form' | 'stk_token'
-- Populate during phone linking. Never send proactive SMS if NULL.
```

### Planned Tables
- **journal_entries** — user_id, body, coach_response, created_at
- **nutrition_logs** — user_id, image_url, gemini_analysis (JSONB), estimated_calories, created_at
- **calendar_events** — user_id, google_event_id, title, start_time, reminder_sent, created_at

### Architecture Principles

Strict Separation of Concerns:

**Persona Module (personas.py):** Acts as the "Soul." It is a lookup service. It fetches the `system_instruction` and `few_shot_examples` from Supabase based on a 4-char + 4-digit ID. It DOES NOT contain coaching logic. It DOES enforce the philosophy-not-identity framing in every prompt it generates.

**Coach Module (coach.py):** Acts as the "Brain." It manages the coaching state (goals, schedules, reminders, nutrition logs). It constructs the Full Prompt by wrapping the Persona's DNA with functional Coaching Context.

### The Prompt Assembly Contract
When assembling the final prompt for Gemini, the structure MUST follow this hierarchy:

1. **System DNA** — The Persona's philosophy, voice, and legal framing (philosophy-not-identity opener + AI disclosure rule)
2. **Coaching Protocols** — The specific functional tasks (e.g., "Check status of goal X")
3. **Few-Shot Examples** — The Persona's verbal style
4. **Current Session Data** — Real-time data from DB (e.g., "Today is May 3rd, user has a gym event at 6pm")
5. **User Message** — The raw inbound text

### Behavioral Guidelines for Coding

- **No Hardcoding:** All persona content resides in Supabase. The Python code must be purely functional.
- **Routing over Guessing:** If a user input requires an action (like logging food or setting a reminder), use a routing function to determine intent BEFORE sending the message to Gemini.
- **Proactive Coaching:** When building prompts in `coach.py`, the AI must be instructed to never end a turn with a passive question. It must either confirm an action taken or prompt for the next accountability milestone.
- **Error Handling:** If an ID is invalid or a DB fetch fails, the system must gracefully fall back to a "Default Coach" persona rather than crashing the session.
- **Paused Check:** Every function that sends an outbound SMS must check `users.paused` first. If `True`, abort silently.
- **Consent Check:** Every scheduled outbound SMS must verify `sms_consent_given_at IS NOT NULL` before sending.

### Persona Constraints

- The coach must NEVER proactively volunteer that it is an AI, use "As an AI assistant..." or polite filler phrases.
- The coach must NEVER claim to be the real person (e.g., "I am David Goggins", "When I ran through Death Valley...")
- The coach must NEVER deny being an AI if the user directly and sincerely asks
- The coach must embody the philosophy and standards of the inspiration figure while executing the functionality of an elite, high-accountability coach
- The coach signs off as "your coach" or a coach name — never as the celebrity's real name

### Routing & Orchestration (SMS Lifecycle)

All inbound messages must follow a strict execution pipeline:

1. **STOP/HELP check** — before anything else, check for opt-out keywords and handle immediately
2. **Paused check** — if user is paused, log inbound but send no outbound response (except START handling)
3. **Intent Classification** — classify into: `['check-in', 'to-do', 'journal', 'question', 'general_chat']`
4. **State Execution** — DB mutation based on intent (streak, reminder, journal entry). Occurs before response generation.
5. **Persona Synthesis** — retrieve active persona's configuration
6. **Final Prompt Assembly** — DNA + Protocols + Examples + Session Data + Result
7. **Response Generation** — Gemini generates response maintaining philosophy voice and accountability standards
8. **Save outbound** — log to `messages` table before returning

### Development Guidelines for Routes

- **Decoupled Handlers:** Each intent type has a dedicated handler function
- **Atomic Operations:** DB operations must succeed before attempting response generation
- **No Response-Only Logic:** Never bypass the Intent Router

## Code Organization & Reusability (DRY Principle)

- **Single Source of Truth:** Intent classification, DB mutations, and Gemini prompt assembly live in `backend/services/`
- **Dual-Channel Integration:** `routes/sms.py` (Twilio) and `routes/dev.py` (local testing) are thin wrappers only
- **No Redundant Logic:** If the agent proposes code that mirrors logic existing elsewhere, refactor into a shared service
- **Identical Behavior:** `/quiz/dev` must produce the exact same outcome as the SMS pipeline

## Coaching Engine Architecture (The Triple-Layer Stack)

1. **Input Routing (Router Layer):** Every message passes through `classify_intent` first. STOP/HELP intercept happens before classification.
2. **Context Injection (Fact Layer):** Fetch UserState (goals, reminders, streaks) before generating response. Passed as system-level injection, not chat history.
3. **Identity Overlay (Persona Layer):** UserState + Original Message + Persona Instructions → Gemini. Persona instructions must command: "Use the provided UserState to drive accountability. If the user is failing, use Inquiry Mode to pick their brain about the root cause." — while staying within the philosophy-not-identity boundary.

**Constraint:** The AI is strictly forbidden from logging data or performing calculations inside the Persona prompt. It must only interpret data provided by the Context Injector.

## The Data-to-Persona Contract

- **No Raw DB Access:** The Persona is strictly forbidden from querying the database directly
- **State-Driven Triggers:** Inquiry Mode must be triggered by the `has_anomaly` flag from the coaching_service
- **Atomic Pipeline:** Router → Data Persistence → Context Assembly → Persona Synthesis. Any breach of this order is a critical bug.

## Scalable Context Architecture

- **Modular Providers:** All data retrieval abstracted into "Context Providers." Add new coaching domains via new Provider classes.
- **Context Isolation:** Each Provider detects its own anomalies and provides a specific reason for Inquiry Mode
- **Standardized Output:** Every Provider returns a string: `[Category]\n- Status\n- Anomaly/Risk`

## Decision Architecture

- **Intent Router (The Brain):** Uses `gemini-2.5-flash-lite`. ONLY job: analyze raw SMS → output strict JSON intents. Must NOT generate coaching advice.
- **Response Generator (The Persona):** Uses `gemini-2.5-flash-lite`. Receives processed results from Providers → generates philosophy-driven SMS response.
- **Constraint:** Never combine routing and generating into a single LLM call.

## Key Relationships
- All tables cascade delete from users
- `coach_settings`: one active row per user (`is_active = TRUE`)
- `streaks` links to both users and goals
- `reminders.source` distinguishes to-dos from calendar events from system messages
- RLS enabled on all tables — backend uses service role key
- `users.paused = TRUE` means zero outbound SMS from any source

## Conventions
- Frontend: yarn, TypeScript strict mode
- Backend: uv, async Python, try/except everything
- Quiz data persisted to sessionStorage (Zustand + persist middleware) so it survives the Google OAuth redirect; cleared by `useQuiz.clear()` after Step 7 writes to Supabase

FOR SQL REFER TO THE SUPABASE_SCHEMA.SQL — this has all the SQL in one file. The compliance additions above (`sms_consent_given_at`, `sms_consent_method`) must be added there too.

<!-- END:stackd-context -->