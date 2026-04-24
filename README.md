# stackd — SMS Accountability Coach

Build habits that stick with your personal AI-powered SMS accountability coach.

## Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14 (App Router), Tailwind CSS, TypeScript |
| Backend | Python, FastAPI |
| Database & Auth | Supabase (PostgreSQL + Realtime + Google OAuth) |
| SMS | Twilio |
| AI | Anthropic Claude API |
| Frontend Hosting | Vercel |
| Backend Hosting | Railway |

---

## Project Structure

```
stackd/
├── frontend/          # Next.js app
│   ├── app/
│   │   ├── page.tsx           # Landing page
│   │   ├── quiz/step1–6/      # Onboarding quiz
│   │   ├── dashboard/         # User dashboard
│   │   └── layout.tsx
│   ├── components/
│   │   └── QuizLayout.tsx
│   └── lib/
│       ├── supabase.ts
│       └── quiz-store.ts
├── backend/           # FastAPI app
│   ├── main.py
│   ├── routes/
│   │   ├── users.py       # Onboarding endpoint
│   │   ├── sms.py         # Twilio webhook
│   │   ├── scheduler.py   # Cron jobs
│   │   └── ai.py          # Anthropic API
│   ├── models/user.py
│   ├── requirements.txt
│   └── railway.toml
└── supabase_schema.sql
```

---

## Setup

### 1. Supabase

1. Create a project at [supabase.com](https://supabase.com)
2. Run `supabase_schema.sql` in the SQL Editor
3. Enable Google OAuth under **Authentication → Providers**
4. Copy your project URL and keys

### 2. Twilio

1. Create an account at [twilio.com](https://twilio.com)
2. Get a phone number
3. Set the webhook URL to `https://your-railway-url/sms/webhook`

### 3. Anthropic

1. Get an API key at [console.anthropic.com](https://console.anthropic.com)

### 4. Backend (local)

```bash
cd backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # Fill in your keys
uvicorn main:app --reload
```

### 5. Frontend (local)

```bash
cd frontend
npm install
cp .env.local.example .env.local   # Fill in your keys
npm run dev
```

---

## Deployment

### Frontend → Vercel

```bash
cd frontend
npx vercel --prod
```

Add environment variables in Vercel dashboard:
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `NEXT_PUBLIC_API_URL` (your Railway URL)

### Backend → Railway

1. Push the `backend/` folder to GitHub
2. Create a new Railway project from the repo
3. Add all environment variables from `.env.example`
4. Railway will auto-deploy using `railway.toml`

---

## Environment Variables

### Backend (`backend/.env`)

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key (never expose to frontend) |
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | Your Twilio phone number |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `FRONTEND_URL` | Frontend URL for CORS |

### Frontend (`frontend/.env.local`)

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon key |
| `NEXT_PUBLIC_API_URL` | Backend API URL |
