from typing import Literal, Optional
from pydantic import BaseModel, Field


class OtpRequest(BaseModel):
    phone: str = Field(..., description="Telefon raqami (masalan: +998901234567)")
    first_name: Optional[str] = Field(None, description="Foydalanuvchi ismi")
    last_name: Optional[str] = Field(None, description="Foydalanuvchi familiyasi")
    telegram_chat_id: Optional[int] = Field(None, description="Telegram chat ID si")


class OtpRequestResponse(BaseModel):
    ok: bool = True
    message: str
    phone: str
    channel: str = "telegram"
    debug_code: Optional[str] = None  # Available for development & fallback display


class OtpVerifyRequest(BaseModel):
    phone: str
    code: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    telegram_chat_id: Optional[int] = None


class SyncCodeVerifyRequest(BaseModel):
    code: str


class ProfileUpdateRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    gender: Optional[Literal["male", "female"]] = None
    age: Optional[int] = Field(None, ge=1, le=120)
    height_cm: Optional[int] = Field(None, ge=40, le=250)
    weight_kg: Optional[int] = Field(None, ge=2, le=300)
    avatar_data: Optional[str] = None
    daily_steps: Optional[int] = None
    daily_target_steps: Optional[int] = None
    daily_water_ml: Optional[int] = None
    daily_kcal: Optional[int] = None
    health_plan_json: Optional[str] = None
    health_plan_updated_at: Optional[str] = None
    sport_activities_json: Optional[str] = None
    alarm_time: Optional[str] = None
    alarm_enabled: Optional[bool] = None


class UserProfileResponse(BaseModel):
    id: int
    phone: str
    first_name: str
    last_name: Optional[str] = None
    gender: Optional[Literal["male", "female"]] = None
    age: Optional[int] = None
    height_cm: Optional[int] = None
    weight_kg: Optional[int] = None
    avatar_data: Optional[str] = None
    token: str
    is_profile_complete: bool

    # Subscription & Plans
    plan: str = "standard"
    plan_cycle: Optional[str] = None
    plan_expires_at: Optional[str] = None

    # Streak & Health stats
    streak_count: int = 0
    streak_last_date: Optional[str] = None
    daily_steps: int = 0
    daily_target_steps: int = 6000
    daily_water_ml: int = 2000
    daily_kcal: int = 0
    health_plan_json: Optional[str] = None
    health_plan_updated_at: Optional[str] = None
    sport_activities_json: Optional[str] = None

    # Alarm
    alarm_time: Optional[str] = None
    alarm_enabled: bool = False
