from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import logging
import os

from routes.sms import router as sms_router
from routes.scheduler import router as scheduler_router, start_scheduler, stop_scheduler
from routes.ai import router as ai_router
from routes.users import router as users_router
from routes.quiz import router as quiz_router
from routes.coach import router as coach_router
from routes.schedule import router as schedule_router
from routes.mock import router as mock_router
from routes.celebrity import router as celebrity_router
from routes.celebrities import router as celebrities_router
from routes.unsubscribe import router as unsubscribe_router

load_dotenv()

# ---------------------------------------------------------------------------
# Startup env validation — fail fast with a clear message
# ---------------------------------------------------------------------------

_REQUIRED_ENV = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "GEMINI_API_KEY",
    "BLOOIO_API_KEY",
    "BLOOIO_PHONE_NUMBER",
    "BLOOIO_WEBHOOK_SECRET",
]

_missing = [var for var in _REQUIRED_ENV if not os.getenv(var)]
if _missing:
    raise EnvironmentError(
        f"Missing required environment variables: {', '.join(_missing)}\n"
        "Check your .env file or Railway/Vercel environment settings."
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the background scheduler when the server boots
    start_scheduler()
    yield
    # Gracefully stop it on shutdown
    stop_scheduler()


app = FastAPI(title="stackd API", version="1.0.0", lifespan=lifespan)

_is_dev = os.getenv("ENV", "development") == "development"
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _is_dev else [os.getenv("FRONTEND_URL", "http://localhost:3000")],
    allow_credentials=not _is_dev,  # credentials + wildcard origin is not allowed by browsers
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sms_router, prefix="/sms", tags=["SMS"])
app.include_router(scheduler_router, prefix="/scheduler", tags=["Scheduler"])
app.include_router(ai_router, prefix="/ai", tags=["AI"])
app.include_router(users_router, prefix="/users", tags=["Users"])
app.include_router(quiz_router, prefix="/api")
app.include_router(coach_router, prefix="/api", tags=["Coach"])
app.include_router(schedule_router, prefix="/api", tags=["Schedule"])
app.include_router(mock_router, prefix="/mock", tags=["Mock"])
app.include_router(celebrity_router, prefix="/celebrity", tags=["Celebrity"])
app.include_router(celebrities_router, prefix="/api/celebrities", tags=["Celebrities"])
app.include_router(unsubscribe_router, prefix="/api", tags=["Unsubscribe"])


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "stackd-api"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
