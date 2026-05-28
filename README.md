# stackd

SMS accountability coaching powered by AI. No app to download. You just text a number.

You pick a public figure whose mindset drives you and stackd builds an AI coach around their philosophy. The coach texts you every day, holds you to your goals, tracks your streaks, and pushes back when you make excuses. It lives entirely in your messages app.


## What It Does

Users sign up by texting a number. They name a public figure who inspires them and the app generates a coach built around that person's publicly known philosophy, communication style, and standards. The coach checks in daily, responds to whatever the user sends, tracks progress over time, and delivers motivation on a schedule the user sets. After a 5 day free trial the user is prompted to subscribe to keep their coach.


## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14 (App Router), Tailwind CSS, TypeScript |
| Backend | Python, FastAPI |
| Database | Supabase (PostgreSQL) |
| SMS | Sendblue |
| AI | Gemini 2.5 Flash Lite |
| Payments | Stripe |
| Frontend Hosting | Vercel |
| Backend Hosting | Railway |


## Project Structure

```
stackd/
    frontend/
        app/
            page.tsx              Landing page
            quiz/                 Onboarding quiz steps
            dashboard/            User dashboard
        components/
        lib/
            supabase.ts
            quiz-store.ts
    backend/
        main.py
        routes/
            sms.py                Sendblue webhook and inbound SMS pipeline
            scheduler.py          All background jobs (check-ins, motivation, trial warnings)
            ai.py                 Gemini calls, HUMAN_BEHAVIOR_RULES, coach voice functions
            personas.py           Persona generation and management
            stripe_webhook.py     Stripe subscription webhook
        services/
            billing.py            Trial logic, Stripe checkout, is_billable gate
            messaging.py          Outbound SMS via Sendblue
            onboarding.py         SMS onboarding state machine
            message_router.py     Inbound SMS intent classification and routing
    supabase_schema.sql
```


## How the Coach Works

Every inbound SMS goes through a pipeline:

1. STOP and HELP keywords are intercepted immediately before anything else runs
2. The message is classified into one of: check-in, task, journal, nutrition, bet, or general
3. The appropriate handler writes structured data to the database
4. The voice generator assembles the coach persona, conversation history, and HUMAN_BEHAVIOR_RULES and sends it to Gemini
5. The reply is sent back to the user via Sendblue

The coach voice is governed by HUMAN_BEHAVIOR_RULES defined in routes/ai.py and injected into every user-facing Gemini call across the entire codebase. This ensures consistent tone, no markdown formatting, no corporate language, and no AI-sounding filler phrases regardless of which function generates the message.


## Trial and Billing

New users get a 5 day free trial. Days 1 through 3 the coach just coaches with no mention of payment. On day 4 the coach delivers an in-character upsell message with a Stripe checkout link. On day 5 there is one final in-character push if the user has not converted. On day 6 access ends and the user receives a plain message with the checkout link. Paid users have subscription_status set to active and are never blocked.


## Local Setup

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload
```

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

### Testing Without Real SMS

The backend exposes a `/mock/simulate-sms` endpoint that runs the full inbound pipeline and returns what would have been sent to the user without calling Sendblue. Use `/mock/reset-user` to wipe a test phone number and start onboarding fresh.


## Deployment

### Frontend

Deploy to Vercel. Set the following environment variables in the Vercel dashboard:

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon key |
| `NEXT_PUBLIC_API_URL` | Railway backend URL |

### Backend

Deploy to Railway from the repo. Railway auto-deploys using the Dockerfile in the backend folder. Set all environment variables listed below before the first deploy.


## Environment Variables

### Backend

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key, never expose to frontend |
| `SENDBLUE_API_KEY` | Sendblue API key |
| `SENDBLUE_API_SECRET` | Sendblue API secret |
| `SENDBLUE_PHONE_NUMBER` | Your Sendblue phone number |
| `GEMINI_API_KEY` | Google Gemini API key |
| `STRIPE_SECRET_KEY` | Stripe secret key |
| `STRIPE_PRICE_ID` | Stripe price ID for the monthly subscription |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret |
| `FRONTEND_URL` | Frontend URL used in Stripe checkout redirects |
| `ENV` | Set to production to disable mock routes |

### Frontend

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon key |
| `NEXT_PUBLIC_API_URL` | Backend API URL |
| `NEXT_PUBLIC_SENDBLUE_NUMBER` | Phone number shown on landing page CTA |
