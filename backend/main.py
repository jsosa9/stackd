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

load_dotenv()

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_URL", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sms_router, prefix="/sms", tags=["SMS"])
app.include_router(scheduler_router, prefix="/scheduler", tags=["Scheduler"])
app.include_router(ai_router, prefix="/ai", tags=["AI"])
app.include_router(users_router, prefix="/users", tags=["Users"])


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "stackd-api"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
