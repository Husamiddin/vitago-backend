import json
from pathlib import Path
import threading
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

is_sqlite = settings.DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}
engine_kwargs = {"connect_args": connect_args, "pool_pre_ping": settings.DB_POOL_PRE_PING}
if not is_sqlite:
    engine_kwargs["pool_recycle"] = settings.DB_POOL_RECYCLE
    engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
    engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
    engine_kwargs["pool_timeout"] = settings.DB_POOL_TIMEOUT
    engine_kwargs["pool_use_lifo"] = True

engine = create_engine(settings.DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

_init_lock = threading.Lock()
_initialized = False


def ensure_db_initialized() -> None:
    """Idempotent initialization for both ASGI lifespan and serverless cold starts."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        from app.models.drug import Drug, TelegramUserPref  # noqa: F401
        from app.models.user import AuthOtp, SportClub, User, UserNotification  # noqa: F401
        from app.models.payment import (  # noqa: F401
            PaymentTransaction,
            ScanRecord,
            FoodNutritionRecord,
            OnboardingEventRecord,
        )

        # Ensure onboarding_events table exists
        Base.metadata.create_all(bind=engine, tables=[OnboardingEventRecord.__table__])

        # Auto-migrate session_id column if onboarding_events was created earlier
        try:
            from sqlalchemy import inspect, text
            insp = inspect(engine)
            if "onboarding_events" in insp.get_table_names():
                existing_cols = {c["name"] for c in insp.get_columns("onboarding_events")}
                if "session_id" not in existing_cols:
                    with engine.begin() as conn:
                        conn.execute(text("ALTER TABLE onboarding_events ADD COLUMN session_id VARCHAR(64)"))
        except Exception as e:
            pass

        if not is_sqlite:
            # Persistent Railway PostgreSQL is already migrated and seeded in the cloud;
            # skip heavy DDL/inspection roundtrips on serverless cold starts.
            _initialized = True
            return

        from app.data.seed_drugs import seed_database_if_empty
        Base.metadata.create_all(bind=engine)

        # Ensure any missing columns or widened types on existing tables are migrated cleanly
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(engine)
            if "users" in inspector.get_table_names():
                cols_info = inspector.get_columns("users")
                existing_cols = {c["name"] for c in cols_info}
                col_types = {c["name"]: str(c["type"]).upper() for c in cols_info}
                bool_default = "0" if is_sqlite else "FALSE"
                dt_type = "DATETIME" if is_sqlite else "TIMESTAMP"
                col_defs = [
                    ("plan", "VARCHAR(32) DEFAULT 'standard'"),
                    ("plan_cycle", "VARCHAR(32)"),
                    ("plan_expires_at", dt_type),
                    ("streak_count", "INTEGER DEFAULT 0"),
                    ("streak_last_date", "VARCHAR(16)"),
                    ("daily_steps", "INTEGER DEFAULT 0"),
                    ("daily_target_steps", "INTEGER DEFAULT 6000"),
                    ("daily_water_ml", "INTEGER DEFAULT 2000"),
                    ("daily_kcal", "INTEGER DEFAULT 0"),
                    ("health_plan_json", "TEXT"),
                    ("health_plan_updated_at", "VARCHAR(64)"),
                    ("alarm_time", "VARCHAR(16)"),
                    ("alarm_enabled", f"BOOLEAN DEFAULT {bool_default}"),
                    ("session_token", "TEXT"),
                    ("avatar_data", "TEXT"),
                    ("gender", "VARCHAR(20)"),
                    ("age", "INTEGER"),
                    ("height_cm", "INTEGER"),
                    ("weight_kg", "INTEGER"),
                    ("telegram_id", "BIGINT"),
                ]
                for col_name, col_type in col_defs:
                    if col_name not in existing_cols:
                        try:
                            with engine.begin() as conn:
                                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"))
                        except Exception:
                            pass

                if not is_sqlite and "VARCHAR" in col_types.get("session_token", ""):
                    try:
                        with engine.begin() as conn:
                            conn.execute(text("ALTER TABLE users ALTER COLUMN session_token TYPE TEXT"))
                    except Exception:
                        pass
        except Exception as e:
            print("Auto-migration notice:", e)

        db = SessionLocal()
        try:
            seed_database_if_empty(db)

            # Purge any legacy fake/dummy test user numbers
            fake_test_phones = ["+998901234567", "+998907502587", "+998909433309"]
            db.query(User).filter(User.phone.in_(fake_test_phones)).delete(synchronize_session=False)

            # Ensure the registered real user has ultra plan and no oversized avatar
            real_u = db.query(User).filter(User.phone == "+998872207844").first()
            if real_u:
                real_u.plan = "ultra"
                if real_u.avatar_data and len(real_u.avatar_data) > 100000:
                    real_u.avatar_data = None
            db.commit()

            # Seed real users into database if table is empty
            if db.query(User).count() == 0:
                init_users_file = Path(__file__).resolve().parent.parent / "data" / "initial_users.json"
                if init_users_file.is_file():
                    try:
                        with open(init_users_file, "r", encoding="utf-8") as f:
                            user_list = json.load(f)
                        for u in user_list:
                            new_u = User(
                                phone=u.get("phone"),
                                first_name=u.get("first_name", "Foydalanuvchi"),
                                last_name=u.get("last_name"),
                                gender=u.get("gender"),
                                age=u.get("age"),
                                height_cm=u.get("height_cm"),
                                weight_kg=u.get("weight_kg"),
                                avatar_data=u.get("avatar_data"),
                                telegram_id=u.get("telegram_id"),
                                session_token=u.get("session_token"),
                                plan=u.get("plan", "ultra"),
                                streak_count=u.get("streak_count", 1),
                                daily_steps=u.get("daily_steps", 0),
                            )
                            db.add(new_u)
                        db.commit()
                    except Exception as err:
                        print("Warning seeding initial users:", err)
        finally:
            db.close()
        _initialized = True


def get_db():
    ensure_db_initialized()
    db = SessionLocal()
    try:
        yield db
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        db.close()


def get_db_pool_status() -> dict:
    """
    Returns non-sensitive operational metrics of the SQLAlchemy connection pool.
    Calculates active, idle, overflow, and query ping latency.
    Never leaks passwords, tokens, or raw credentials.
    """
    import time
    from sqlalchemy import text

    pool = getattr(engine, "pool", None)
    pool_info = {
        "engine_dialect": engine.dialect.name,
        "is_sqlite": is_sqlite,
        "pool_type": pool.__class__.__name__ if pool else "unknown",
        "configured_pool_size": settings.DB_POOL_SIZE if not is_sqlite else 1,
        "configured_max_overflow": settings.DB_MAX_OVERFLOW if not is_sqlite else 0,
        "pool_size": getattr(pool, "size", lambda: 0)() if pool and hasattr(pool, "size") else None,
        "checkedin_connections": getattr(pool, "checkedin", lambda: 0)() if pool and hasattr(pool, "checkedin") else None,
        "checkedout_connections": getattr(pool, "checkedout", lambda: 0)() if pool and hasattr(pool, "checkedout") else None,
        "overflow": getattr(pool, "overflow", lambda: 0)() if pool and hasattr(pool, "overflow") else None,
        "pool_timeout_s": getattr(pool, "_timeout", None),
        "pool_recycle_s": getattr(pool, "_recycle", None),
        "pool_use_lifo": getattr(pool, "_use_lifo", None),
        "pool_pre_ping": getattr(pool, "_pre_ping", None),
    }

    t0 = time.monotonic()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = round((time.monotonic() - t0) * 1000, 2)
        pool_info["health"] = "healthy"
        pool_info["ping_latency_ms"] = latency_ms
    except Exception as exc:
        pool_info["health"] = "unhealthy"
        pool_info["ping_error"] = str(exc)[:120]

    return pool_info

