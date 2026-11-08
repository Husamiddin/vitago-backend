from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, Integer, String, Text

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(32), unique=True, index=True, nullable=False)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=True)

    # Physical / Medical metrics for drug dosage & contraindication checks
    gender = Column(String(20), nullable=True)  # 'male' or 'female'
    age = Column(Integer, nullable=True)
    height_cm = Column(Integer, nullable=True)
    weight_kg = Column(Integer, nullable=True)

    avatar_data = Column(Text, nullable=True)
    telegram_id = Column(BigInteger, nullable=True, index=True)
    session_token = Column(Text, unique=True, index=True, nullable=True)

    # Subscription Plan: 'standard' | 'premium' | 'ultra'
    plan = Column(String(32), default="standard", nullable=False)
    plan_cycle = Column(String(32), nullable=True)  # 'monthly' | 'annually'
    plan_expires_at = Column(DateTime, nullable=True)

    # Streak & Health stats
    streak_count = Column(Integer, default=0, nullable=False)
    streak_last_date = Column(String(16), nullable=True)  # YYYY-MM-DD
    daily_steps = Column(Integer, default=0, nullable=False)
    daily_target_steps = Column(Integer, default=6000, nullable=False)
    daily_water_ml = Column(Integer, default=2000, nullable=False)
    daily_kcal = Column(Integer, default=0, nullable=False)
    health_plan_json = Column(Text, nullable=True)
    health_plan_updated_at = Column(String(64), nullable=True)
    sport_activities_json = Column(Text, nullable=True)

    # Ultra features: Alarm / Mission
    alarm_time = Column(String(16), nullable=True)  # HH:MM
    alarm_enabled = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class AuthOtp(Base):
    __tablename__ = "auth_otps"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(32), index=True, nullable=False)
    code = Column(String(8), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    telegram_chat_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_auth_otps_phone_code", "phone", "code"),
    )


class SportClub(Base):
    __tablename__ = "sport_clubs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(160), nullable=False)
    description = Column(Text, nullable=True)
    sport_type = Column(String(64), default="run", nullable=False)
    location = Column(String(160), default="O'zbekiston", nullable=False)
    invite_url = Column(String(500), nullable=True)
    members_json = Column(Text, default="[]", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class UserNotification(Base):
    __tablename__ = "user_notifications"

    id = Column(Integer, primary_key=True, index=True)
    target_user_id = Column(Integer, nullable=True, index=True)  # None/0 = all users, >0 = specific user
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    sender_name = Column(String(100), default="VitaGo Admin", nullable=False)
    read_by_json = Column(Text, default="[]", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

