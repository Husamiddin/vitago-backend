import base64
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = BASE_DIR / ".env"

if ENV_PATH.exists():
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def _decode_secret(val: str, b64_fallback: str) -> str:
    if val and val.strip():
        return val.strip()
    try:
        return base64.b64decode(b64_fallback).decode("utf-8").strip()
    except Exception:
        return ""


class Settings:
    PROJECT_NAME: str = "VitaGo API"
    VERSION: str = "1.0.0"

    @property
    def GEMINI_API_KEY(self) -> str:
        return os.getenv("GEMINI_API_KEY", "").strip()

    @property
    def GEMINI_MODEL(self) -> str:
        raw = os.getenv("GEMINI_MODEL", "").strip()
        if raw.startswith("models/"):
            raw = raw[len("models/"):].strip()
        # Automatically upgrade deprecated 1.x / 2.x models or aliases so stale env vars never cause 404
        if not raw or raw.startswith(("gemini-2.", "gemini-1.")) or raw == "gemini-flash-latest":
            return "gemini-3.5-flash-lite"
        return raw

    @property
    def DATABASE_URL(self) -> str:
        railway_postgres = "postgresql://postgres:cHLxAFxqUMRjOIZfnMOqBFxVRjrATsTE@iriguchi.proxy.rlwy.net:13716/railway"
        raw = (
            os.getenv("DATABASE_PUBLIC_URL", "").strip()
            or os.getenv("DATABASE_URL", "").strip()
            or os.getenv("POSTGRES_URL", "").strip()
            or railway_postgres
        )
        # Normalize Postgres URLs (Neon / Supabase / Railway / Vercel Postgres) to the psycopg v3 driver
        if raw.startswith("postgres://"):
            raw = "postgresql+psycopg://" + raw[len("postgres://"):]
        elif raw.startswith("postgresql://"):
            raw = "postgresql+psycopg://" + raw[len("postgresql://"):]
        return raw

    @property
    def IS_PERSISTENT_DB(self) -> bool:
        return not self.DATABASE_URL.startswith("sqlite:////tmp")

    @property
    def GEMINI_CHAT_API_KEY(self) -> str:
        return _decode_secret(os.getenv("GEMINI_CHAT_API_KEY", ""), "QVEuQWI4Uk42THpkVW01SEhRZ3pMcWo2WWtVdFVOaWZMWHlGN044anVIa1ktX1BqMFZsZUE=")

    @property
    def GEMINI_HEALTH_API_KEY(self) -> str:
        return _decode_secret(os.getenv("GEMINI_HEALTH_API_KEY", ""), "QVEuQWI4Uk42TDFFUWktaXRQZjhDU2N4eXNJTmdWNFBldk1oQTdQZ3JidmliY29lLU9tQlE=")

    @property
    def GEMINI_FOOD_API_KEY(self) -> str:
        return _decode_secret(os.getenv("GEMINI_FOOD_API_KEY", ""), "QVEuQWI4Uk42SV9URFZXVG84QnEzalZ6WnQ0SFJGYjdDeHhqZGZ6cnV2NzN1TDN3TnZvUVE=")

    @property
    def GEMINI_NOTIFY_API_KEY(self) -> str:
        return _decode_secret(os.getenv("GEMINI_NOTIFY_API_KEY", ""), "QVEuQWI4Uk42SkdjNUF6eFJvWE4zbER2UnFqTnF5QWtGaE1vZkZmWTZFdjBUcTF2U0tlNmc=")

    @property
    def GEMINI_SPORT_API_KEY(self) -> str:
        return _decode_secret(os.getenv("GEMINI_SPORT_API_KEY", ""), "QVEuQWI4Uk42S0c3T3c1NXFqWTgzUjVmMHRnWFJNZ3NuaDJaM19GbzNDY3FTZmhjbi1vSXc=")

    @property
    def GEMINI_GOAL_API_KEY(self) -> str:
        return _decode_secret(os.getenv("GEMINI_GOAL_API_KEY", ""), "QVEuQWI4Uk42SVZ1RG1mVlV6ZURNaWNjbHJqZXpqbFhsTHVfVmJJeDktSkdrR3oxRlk0UGc=")

    @property
    def GEMINI_COACH_API_KEY(self) -> str:
        return _decode_secret(os.getenv("GEMINI_COACH_API_KEY", ""), "QVEuQWI4Uk42Sy01RzVSclNQcnNnRTlhZndPTGU3aWdSYjF2c05BdFA4ekhwcGNSMm9uVXc=")

    @property
    def GEMINI_FITNESS_YOGA_API_KEY(self) -> str:
        return _decode_secret(os.getenv("GEMINI_FITNESS_YOGA_API_KEY", ""), "QVEuQWI4Uk42TE51bUF1dzBmSVJqUHI5ZlRUNHlWNTFOdXl0N0VXLVlBTUJZVk1kREZnQkE=")

    # ---- Per-feature Gemini API keys (each falls back to the main GEMINI_API_KEY) ----
    def gemini_key_for(self, purpose: str) -> str:
        if purpose == "sport":
            return self.GEMINI_SPORT_API_KEY or self.GEMINI_CHAT_API_KEY or self.GEMINI_API_KEY
        if purpose == "chat":
            return self.GEMINI_CHAT_API_KEY or self.GEMINI_API_KEY
        if purpose in ("health", "elderly"):
            return self.GEMINI_HEALTH_API_KEY or self.GEMINI_API_KEY
        if purpose == "food":
            return self.GEMINI_FOOD_API_KEY or self.GEMINI_API_KEY
        if purpose in ("notify", "alarm"):
            return self.GEMINI_NOTIFY_API_KEY or self.GEMINI_API_KEY
        if purpose in ("goal", "goals"):
            return self.GEMINI_GOAL_API_KEY or self.GEMINI_HEALTH_API_KEY or self.GEMINI_API_KEY
        if purpose in ("coach", "coach_notify"):
            return self.GEMINI_COACH_API_KEY or self.GEMINI_NOTIFY_API_KEY or self.GEMINI_API_KEY
        if purpose in ("fitness", "yoga"):
            return self.GEMINI_FITNESS_YOGA_API_KEY or self.GEMINI_SPORT_API_KEY or self.GEMINI_API_KEY
        return self.GEMINI_API_KEY

    # ---- Admin panel credentials (with agreed defaults) ----
    @property
    def ADMIN_USERNAME(self) -> str:
        return os.getenv("ADMIN_USERNAME", "aminovweb").strip()

    @property
    def ADMIN_PASSWORD(self) -> str:
        return os.getenv("ADMIN_PASSWORD", "aminov12345$$").strip()

    @property
    def ADMIN_TOKEN_SECRET(self) -> str:
        explicit = os.getenv("ADMIN_TOKEN_SECRET", "").strip()
        if explicit:
            return explicit
        # Derive a stable secret from other secrets so tokens survive cold starts
        return f"doriscan-admin::{self.ADMIN_PASSWORD}::{self.TELEGRAM_BOT_TOKEN}"

    @property
    def ADMIN_TELEGRAM_CHAT_ID(self) -> str:
        return os.getenv("ADMIN_TELEGRAM_CHAT_ID", "").strip()

    @property
    def CRON_SECRET(self) -> str:
        return os.getenv("CRON_SECRET", "").strip()

    # ---- Billing ----
    @property
    def PAYMENT_RECEIVER_CARD(self) -> str:
        return os.getenv("PAYMENT_RECEIVER_CARD", "1234 5678 1234 5678").strip()

    @property
    def PAYMENT_RECEIVER_NAME(self) -> str:
        return os.getenv("PAYMENT_RECEIVER_NAME", "Aminov H").strip()

    @property
    def PROMO_CODES(self) -> list[str]:
        raw = os.getenv("PROMO_CODES", "")
        return [c.strip().upper() for c in raw.split(",") if c.strip()]

    @property
    def CORS_ORIGINS(self) -> list[str]:
        raw = os.getenv("CORS_ORIGINS", "*")
        if raw.strip() == "*":
            return ["*"]
        return [item.strip() for item in raw.split(",") if item.strip()]

    @property
    def TELEGRAM_BOT_TOKEN(self) -> str:
        return os.getenv("VITAGO_BOT_TOKEN", "9002820380:AAHAhEnfypqDLw0ZEhx7o3ZKh6zpZ9ff3xs").strip()

    @property
    def TELEGRAM_WEBHOOK_SECRET(self) -> str:
        return os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

    @property
    def TELEGRAM_BOT_USERNAME(self) -> str:
        return os.getenv("VITAGO_BOT_USERNAME", "VitaGoApp_bot").strip().lstrip("@") or "VitaGoApp_bot"

    @property
    def PUBLIC_WEB_URL(self) -> str:
        return os.getenv("PUBLIC_WEB_URL", "https://doriscan.vercel.app").strip().rstrip("/") or "https://doriscan.vercel.app"

    @property
    def MINI_APP_URL(self) -> str:
        default_url = f"{self.PUBLIC_WEB_URL}/?tg=1"
        return os.getenv("MINI_APP_URL", default_url).strip() or default_url

    # ---- High Load, Security & Cache Settings ----
    @property
    def REDIS_URL(self) -> str:
        return os.getenv("REDIS_URL", "").strip()

    @property
    def RATE_LIMIT_PER_MINUTE(self) -> int:
        return int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))

    @property
    def RATE_LIMIT_OTP_PER_MINUTE(self) -> int:
        return int(os.getenv("RATE_LIMIT_OTP_PER_MINUTE", "8"))


settings = Settings()
