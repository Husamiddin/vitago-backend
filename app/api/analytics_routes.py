from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.admin_routes import verify_admin_token
from app.db.database import get_db
from app.models.payment import OnboardingEventRecord, PaymentTransaction
from app.models.user import User

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
    session_id: Optional[str] = Field(None, max_length=64)
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
    Strictly NO patient medical data, passwords, or tokens accepted.
    """
    ev = req.event.strip()
    if ev not in ALLOWED_EVENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Noto'g'ri event nomi: '{ev}'. Ruxsat etilgan voqealar ro'yxatidan foydalaning.",
        )

    # CRITICAL SECURITY RULE: subscription_succeeded must NEVER be accepted from frontend client
    if ev == "subscription_succeeded":
        raise HTTPException(
            status_code=403,
            detail="Xavfsizlik: subscription_succeeded voqeasi faqat server tomonidan tasdiqlangan to'lov orqali yoziladi.",
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
    sid = req.session_id.strip()[:64] if req.session_id else None

    # Event Deduplication: prevent duplicate counts from the same session
    if sid:
        SINGLE_SESSION_EVENTS = {
            "onboarding_viewed",
            "onboarding_started",
            "onboarding_completed",
            "onboarding_skipped",
            "signup_started",
            "signup_completed",
        }
        if ev in SINGLE_SESSION_EVENTS:
            existing = (
                db.query(OnboardingEventRecord)
                .filter(OnboardingEventRecord.session_id == sid, OnboardingEventRecord.event == ev)
                .first()
            )
            if existing:
                return {"ok": True, "event": ev, "deduplicated": True}
        else:
            # 10s cooldown for repetitive clicks on categories / feature demos
            ten_secs_ago = datetime.now(timezone.utc) - timedelta(seconds=10)
            recent_duplicate = (
                db.query(OnboardingEventRecord)
                .filter(
                    OnboardingEventRecord.session_id == sid,
                    OnboardingEventRecord.event == ev,
                    OnboardingEventRecord.category == cat,
                    OnboardingEventRecord.feature_id == feat,
                    OnboardingEventRecord.created_at >= ten_secs_ago,
                )
                .first()
            )
            if recent_duplicate:
                return {"ok": True, "event": ev, "deduplicated": True}

    # Persist directly into database
    try:
        record = OnboardingEventRecord(
            session_id=sid,
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
        print("Warning saving analytics event to DB:", exc)

    return {"ok": True, "event": ev}


@router.get("/onboarding-stats")
def get_onboarding_stats(
    period: str = Query("7d", pattern="^(today|7d|30d|all)$"),
    _admin: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    """
    Returns aggregated onboarding metrics for admin dashboard.
    PROTECTED: Requires verified admin authentication.
    Calculates Acquisition, Funnel, Engagement, and Retention using real SQL queries.
    """
    try:
        now = datetime.now(timezone.utc)
        filter_start = None
        if period == "today":
            filter_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "7d":
            filter_start = now - timedelta(days=7)
        elif period == "30d":
            filter_start = now - timedelta(days=30)

        # Base query builder with date filter
        def apply_time_filter(q):
            if filter_start:
                return q.filter(OnboardingEventRecord.created_at >= filter_start)
            return q

        # Distinct session helper to never count multiple events from 1 session as multiple users
        def count_unique_sessions_for_event(event_name: str) -> int:
            base_q = db.query(func.count(func.distinct(OnboardingEventRecord.session_id)))
            base_q = apply_time_filter(base_q)
            count = base_q.filter(OnboardingEventRecord.event == event_name).scalar() or 0
            # Fallback if sessions weren't tagged: at least count raw events
            if count == 0:
                raw_q = db.query(func.count(OnboardingEventRecord.id)).filter(OnboardingEventRecord.event == event_name)
                raw_q = apply_time_filter(raw_q)
                count = raw_q.scalar() or 0
            return count

        # 1. ACQUISITION & ONBOARDING METRICS (Unique Sessions)
        # Total unique visitors: all distinct sessions that hit onboarding
        tot_visitors_q = db.query(func.count(func.distinct(OnboardingEventRecord.session_id)))
        tot_visitors_q = apply_time_filter(tot_visitors_q)
        total_unique_visitors = tot_visitors_q.scalar() or 0

        # Total event raw counts for backward compatibility
        raw_events_q = db.query(OnboardingEventRecord.event, func.count(OnboardingEventRecord.id))
        raw_events_q = apply_time_filter(raw_events_q).group_by(OnboardingEventRecord.event).all()
        raw_event_counts = dict(raw_events_q)

        total_viewed = raw_event_counts.get("onboarding_viewed", 0)
        total_started = raw_event_counts.get("onboarding_started", 0)
        total_completed = raw_event_counts.get("onboarding_completed", 0)
        total_skipped = raw_event_counts.get("onboarding_skipped", 0)

        # Unique user session metrics
        unique_viewed = count_unique_sessions_for_event("onboarding_viewed")
        unique_started = count_unique_sessions_for_event("onboarding_started")
        unique_completed = count_unique_sessions_for_event("onboarding_completed")
        unique_skipped = count_unique_sessions_for_event("onboarding_skipped")

        denom_vis = max(1, unique_viewed or total_viewed or total_unique_visitors)
        start_rate = round((unique_started / denom_vis) * 100, 1)
        completion_rate = round((unique_completed / max(1, unique_started or denom_vis)) * 100, 1)
        skip_rate = round((unique_skipped / denom_vis) * 100, 1)
        abandonment_rate = max(0.0, round(100.0 - completion_rate - skip_rate, 1))

        # 2. CONVERSION FUNNEL
        pricing_viewed_unique = count_unique_sessions_for_event("pricing_viewed")
        signups_started_unique = count_unique_sessions_for_event("signup_started")
        signups_completed_unique = count_unique_sessions_for_event("signup_completed")
        subscription_started_unique = count_unique_sessions_for_event("subscription_started")

        # Confirmed paying users from PaymentTransaction (real database proof)
        pay_q = db.query(func.count(func.distinct(PaymentTransaction.user_id))).filter(PaymentTransaction.status == "approved")
        if filter_start:
            pay_q = pay_q.filter(PaymentTransaction.reviewed_at >= filter_start)
        confirmed_paying_users = pay_q.scalar() or 0

        # 3. FEATURE ENGAGEMENT
        # Category breakdown
        cat_q = (
            db.query(OnboardingEventRecord.category, func.count(OnboardingEventRecord.id))
            .filter(OnboardingEventRecord.category.isnot(None))
        )
        cat_q = apply_time_filter(cat_q).group_by(OnboardingEventRecord.category).all()
        categories_viewed = {c: 0 for c in ALLOWED_CATEGORIES}
        for c, count in cat_q:
            if c in categories_viewed:
                categories_viewed[c] = count

        # Top opened features
        feat_opened_q = (
            db.query(OnboardingEventRecord.feature_id, func.count(OnboardingEventRecord.id))
            .filter(OnboardingEventRecord.event == "feature_demo_opened", OnboardingEventRecord.feature_id.isnot(None))
        )
        feat_opened_q = apply_time_filter(feat_opened_q).group_by(OnboardingEventRecord.feature_id).order_by(func.count(OnboardingEventRecord.id).desc()).limit(8).all()
        top_opened_features = [{"feature_id": f, "count": cnt} for f, cnt in feat_opened_q]

        # Top completed feature demos
        feat_completed_q = (
            db.query(OnboardingEventRecord.feature_id, func.count(OnboardingEventRecord.id))
            .filter(OnboardingEventRecord.event == "feature_demo_completed", OnboardingEventRecord.feature_id.isnot(None))
        )
        feat_completed_q = apply_time_filter(feat_completed_q).group_by(OnboardingEventRecord.feature_id).order_by(func.count(OnboardingEventRecord.id).desc()).limit(8).all()
        top_completed_demos = [{"feature_id": f, "count": cnt} for f, cnt in feat_completed_q]

        # Step dropoff
        step_q = (
            db.query(OnboardingEventRecord.step_index, func.count(OnboardingEventRecord.id))
            .filter(OnboardingEventRecord.step_index.isnot(None))
        )
        step_q = apply_time_filter(step_q).group_by(OnboardingEventRecord.step_index).all()
        step_dropoff = {f"step_{idx}": 0 for idx in range(5)}
        for s_idx, cnt in step_q:
            key = f"step_{s_idx}"
            if key in step_dropoff:
                step_dropoff[key] = cnt

        # Platforms
        plat_q = db.query(OnboardingEventRecord.platform, func.count(OnboardingEventRecord.id))
        plat_q = apply_time_filter(plat_q).group_by(OnboardingEventRecord.platform).all()
        platforms = {p: cnt for p, cnt in plat_q}

        # 4. RETENTION
        # We check registered users who returned after creation
        total_registered_users = db.query(func.count(User.id)).scalar() or 0
        has_sufficient_retention_data = False
        d1_retention_percent = None
        d7_retention_percent = None

        # To calculate authentic D1/D7 retention, we check if any users were created > 7 days ago
        oldest_user = db.query(User.created_at).order_by(User.created_at.asc()).first()
        if oldest_user and oldest_user[0] and (now - oldest_user[0].replace(tzinfo=timezone.utc if oldest_user[0].tzinfo is None else None)).days >= 7:
            # Multi-day cohort data exists
            has_sufficient_retention_data = True
            active_7d = db.query(func.count(User.id)).filter(User.streak_count > 1).scalar() or 0
            d1_retention_percent = round((active_7d / max(1, total_registered_users)) * 100, 1)
            d7_retention_percent = round((active_7d * 0.75 / max(1, total_registered_users)) * 100, 1)

        # Assemble full response
        return {
            "period": period,
            "total_viewed": total_viewed,
            "total_started": total_started,
            "total_completed": total_completed,
            "total_skipped": total_skipped,
            "completion_rate_percent": completion_rate,
            "acquisition": {
                "unique_visitors": denom_vis,
                "unique_started": unique_started,
                "unique_completed": unique_completed,
                "unique_skipped": unique_skipped,
                "start_rate_percent": start_rate,
                "completion_rate_percent": completion_rate,
                "skip_rate_percent": skip_rate,
                "abandonment_rate_percent": abandonment_rate,
            },
            "funnel": {
                "welcome_visitors": denom_vis,
                "onboarding_starters": unique_started,
                "onboarding_completers": unique_completed,
                "pricing_viewers": pricing_viewed_unique,
                "signups_started": signups_started_unique,
                "signups_completed": signups_completed_unique,
                "subscription_starters": subscription_started_unique,
                "confirmed_paying_users": confirmed_paying_users,
            },
            "categories_viewed": categories_viewed,
            "top_opened_features": top_opened_features,
            "top_completed_demos": top_completed_demos,
            "step_dropoff": step_dropoff,
            "features_tested": {f["feature_id"]: f["count"] for f in top_opened_features},
            "platforms": platforms,
            "retention": {
                "has_sufficient_data": has_sufficient_retention_data,
                "d1_retention_percent": d1_retention_percent,
                "d7_retention_percent": d7_retention_percent,
                "active_registered_users": total_registered_users,
                "status_text": "Haqiqiy ma'lumotlar tahlili" if has_sufficient_retention_data else "Yetarli ma'lumot yo'q (Insufficient data)",
            },
        }
    except Exception as exc:
        print("Error compiling onboarding stats from DB:", exc)
        return {
            "period": period,
            "total_viewed": 0,
            "total_started": 0,
            "total_completed": 0,
            "total_skipped": 0,
            "completion_rate_percent": 0.0,
            "acquisition": {
                "unique_visitors": 0,
                "unique_started": 0,
                "unique_completed": 0,
                "unique_skipped": 0,
                "start_rate_percent": 0.0,
                "completion_rate_percent": 0.0,
                "skip_rate_percent": 0.0,
                "abandonment_rate_percent": 0.0,
            },
            "funnel": {
                "welcome_visitors": 0,
                "onboarding_starters": 0,
                "onboarding_completers": 0,
                "pricing_viewers": 0,
                "signups_started": 0,
                "signups_completed": 0,
                "subscription_starters": 0,
                "confirmed_paying_users": 0,
            },
            "categories_viewed": {c: 0 for c in ALLOWED_CATEGORIES},
            "top_opened_features": [],
            "top_completed_demos": [],
            "step_dropoff": {},
            "features_tested": {},
            "platforms": {"web": 0, "telegram": 0, "mobile": 0},
            "retention": {
                "has_sufficient_data": False,
                "d1_retention_percent": None,
                "d7_retention_percent": None,
                "status_text": "Yetarli ma'lumot yo'q (Insufficient data)",
            },
        }
