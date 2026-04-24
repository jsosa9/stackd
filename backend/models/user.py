from pydantic import BaseModel, EmailStr
from typing import Optional, List, Any
from datetime import datetime


class User(BaseModel):
    id: Optional[str] = None
    email: EmailStr
    phone: str
    name: str
    age: Optional[int] = None
    occupation: Optional[str] = None
    created_at: Optional[datetime] = None


class Goal(BaseModel):
    id: Optional[str] = None
    user_id: str
    activity: str
    category: str
    days: List[str]
    times_per_day: dict
    created_at: Optional[datetime] = None


class CoachSettings(BaseModel):
    id: Optional[str] = None
    user_id: str
    coach_name: str
    coach_emoji: str
    personality_preset: str
    tone: str
    emoji_usage: str
    message_length: str
    miss_behavior: str
    checkin_start: str
    intensity: str
    custom_build: Optional[dict] = None
    created_at: Optional[datetime] = None


class Schedule(BaseModel):
    id: Optional[str] = None
    user_id: str
    checkin_time: str
    timezone: str
    motivation_enabled: bool = True
    motivation_frequency: str
    motivation_window_start: str
    motivation_window_end: str
    motivation_styles: List[str]
    created_at: Optional[datetime] = None


class Message(BaseModel):
    id: Optional[str] = None
    user_id: str
    direction: str  # "inbound" | "outbound"
    body: str
    created_at: Optional[datetime] = None
