from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.admin_routes import verify_admin_token
from app.db.database import get_db
from app.models.payment import OnboardingEventRecord

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics & Onboarding Telemetry"])

ALLOWED_EVENTS = {
    "onboarding_viewed",
    "onboarding_started",
    "onboarding_skipped",
    "category_opened",
    "feature_demo_opened",
    "feature_demo_completed",
    "pricing_viewed",
    "signup_started",
    "signup_completed",
    "onboarding_completed",
    "subscription_started",
    "subscription_succeeded",
}

ALLOWED_CATEGORIES = {"health", "sport", "life", "pricing", "privacy"}
ALLOWED_PLATFORMS = {"web", "telegram", "mobile"}


class OnboardingEventRequest(BaseModel):
    event: str = Field(..., max_length=64)
    category: Optional[str] = Field(None, max_length=32)
    feature_id: Optional[str] = Field(None, max_length=64)
    step_index: Optional[int] = Field(None, ge=0, le=10)
    platform: Optional[str] = Field("web", max_length=32)
    timestamp: Optional[str] = None


@router.post("/onboarding-event")
def record_onboarding_event(
    req: OnboardingEventRequest,
    db: Session = Depends(get_db),
):
    """
    Records privacy-safe onboarding telemetry into the SQL database.
    Strictly NO patient medical data or prescription records accepted.
    """
    ev = req.event.strip()
    if ev not in ALLOWED_EVENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Noto'g'ri event nomi: '{ev}'. Ruxsat etilgan voqealar ro'yxatidan foydalaning.",
        )

    cat = req.category.strip().lower() if req.category else None
    if cat and cat not in ALLOWED_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Noto'g'ri kategoriya: '{cat}'. Ruxsat etilgan kategoriyalar: {', '.join(sorted(ALLOWED_CATEGORIES))}",
        )

    plat = (req.platform or "web").strip().lower()
    if plat not in ALLOWED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Noto'g'ri platforma: '{plat}'. Ruxsat etilgan platformalar: {', '.join(sorted(ALLOWED_PLATFORMS))}",
        )

    feat = req.feature_id.strip()[:64] if req.feature_id else None

    # Persist directly into database
    try:
        record = OnboardingEventRecord(
            event=ev,
            category=cat,
            feature_id=feat,
            step_index=req.step_index,
            platform=plat,
        )
        db.add(record)
        db.commit()
    except Exception as exc:
        db.rollback()
        # Non-fatal to keep user flow seamless
        print("Warning saving analytics event to DB:", exc)

    return {"ok": True, "event": ev}


@router.get("/onboarding-stats")
def get_onboarding_stats(
    _admin: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    """
    Returns aggregated onboarding metrics for admin dashboard.
    PROTECTED: Requires verified admin authentication.
    """
    try:
        # Event totals
        rows = (
            db.query(OnboardingEventRecord.event, func.count(OnboardingEventRecord.id))
            .group_by(OnboardingEventRecord.event)
            .all()
        )
        event_counts = dict(rows)

        total_viewed = event_counts.get("onboarding_viewed", 0)
        total_started = event_counts.get("onboarding_started", 0)
        total_completed = event_counts.get("onboarding_completed", 0)
        total_skipped = event_counts.get("onboarding_skipped", 0)

        # Categories
        cat_rows = (
            db.query(OnboardingEventRecord.category, func.count(OnboardingEventRecord.id))
            .filter(OnboardingEventRecord.category.isnot(None))
            .group_by(OnboardingEventRecord.category)
            .all()
        )
        categories_viewed = {c: 0 for c in ALLOWED_CATEGORIES}
        for c, count in cat_rows:
            if c in categories_viewed:
                categories_viewed[c] = count

        # Features
        feat_rows = (
            db.query(OnboardingEventRecord.feature_id, func.count(OnboardingEventRecord.id))
            .filter(OnboardingEventRecord.feature_id.isnot(None))
            .group_by(OnboardingEventRecord.feature_id)
            .order_by(func.count(OnboardingEventRecord.id).desc())
            .limit(10)
            .all()
        )
        features_tested = {f: cnt for f, cnt in feat_rows}

        # Platforms
        plat_rows = (
            db.query(OnboardingEventRecord.platform, func.count(OnboardingEventRecord.id))
            .group_by(OnboardingEventRecord.platform)
            .all()
        )
        platforms = {p: cnt for p, cnt in plat_rows}

        denom = max(1, total_started or total_viewed)
        completion_rate = round((total_completed / denom) * 100, 1)

        return {
            "total_viewed": total_viewed,
            "total_started": total_started,
            "total_completed": total_completed,
            "total_skipped": total_skipped,
            "completion_rate_percent": completion_rate,
            "categories_viewed": categories_viewed,
            "features_tested": features_tested,
            "signups_started": event_counts.get("signup_started", 0),
            "signups_completed": event_counts.get("signup_completed", 0),
            "subscriptions_started": event_counts.get("subscription_started", 0),
            "platforms": platforms,
        }
    except Exception as exc:
        print("Error compiling onboarding stats from DB:", exc)
        return {
            "total_viewed": 0,
            "total_started": 0,
            "total_completed": 0,
            "total_skipped": 0,
            "completion_rate_percent": 0.0,
            "categories_viewed": {c: 0 for c in ALLOWED_CATEGORIES},
            "features_tested": {},
            "signups_started": 0,
            "signups_completed": 0,
            "subscriptions_started": 0,
            "platforms": {"web": 0, "telegram": 0, "mobile": 0},
        }
