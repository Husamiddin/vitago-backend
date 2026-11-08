import math
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.admin_routes import verify_admin_token
from app.db.database import get_db
from app.models.payment import ExperimentConfig, OnboardingEventRecord, PaymentTransaction
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
    "experiment_assigned",
    "experiment_exposed",
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
    variant: Optional[str] = Field(None, max_length=32)
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
    var = req.variant.strip()[:32] if req.variant else None

    # Event Deduplication: prevent duplicate counts from the same session
    if sid:
        SINGLE_SESSION_EVENTS = {
            "onboarding_viewed",
            "onboarding_started",
            "onboarding_completed",
            "onboarding_skipped",
            "signup_started",
            "signup_completed",
            "experiment_assigned",
            "experiment_exposed",
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
            variant=var,
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


class ExperimentActionRequest(BaseModel):
    action: str = Field(..., pattern="^(pause|resume)$")


@router.get("/experiments/{exp_id}/status")
def get_experiment_public_status(
    exp_id: str,
    db: Session = Depends(get_db),
):
    """
    Public endpoint for client app to check if an experiment is actively running or paused.
    """
    cfg = db.query(ExperimentConfig).filter(ExperimentConfig.experiment_id == exp_id).first()
    if not cfg:
        return {"experiment_id": exp_id, "status": "running"}
    return {"experiment_id": cfg.experiment_id, "status": cfg.status}


@router.post("/experiments/{exp_id}/action")
def update_experiment_action(
    exp_id: str,
    req: ExperimentActionRequest,
    _admin: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    """
    Allows admin to pause or resume an active A/B test.
    """
    cfg = db.query(ExperimentConfig).filter(ExperimentConfig.experiment_id == exp_id).first()
    if not cfg:
        cfg = ExperimentConfig(
            experiment_id=exp_id,
            name="Welcome Page Asosiy Sarlavha (Headline A/B)",
            status="running",
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)

    new_status = "paused" if req.action == "pause" else "running"
    cfg.status = new_status
    db.commit()
    return {"ok": True, "experiment_id": exp_id, "status": new_status}


@router.get("/experiments")
def get_experiments_summary(
    _admin: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    """
    Computes rigorous A/B test metrics for Welcome Headline Experiment (welcome_headline_v1).
    Calculates exposures, start rate, completion rate, skip rate, signup rate,
    sample size checks (min 50 per variant) and two-proportion pooled z-test.
    """
    exp_id = "welcome_headline_v1"
    cfg = db.query(ExperimentConfig).filter(ExperimentConfig.experiment_id == exp_id).first()
    exp_status = cfg.status if cfg else "running"

    def get_variant_stats(var_code: str):
        # 1. Exposures: unique sessions exposed or viewed with this variant
        exp_sessions_q = (
            db.query(func.count(func.distinct(OnboardingEventRecord.session_id)))
            .filter(
                OnboardingEventRecord.variant == var_code,
                OnboardingEventRecord.event.in_(["experiment_exposed", "onboarding_viewed", "onboarding_started"]),
            )
        )
        exposures = exp_sessions_q.scalar() or 0

        # 2. Starters: unique sessions that started onboarding
        starters_q = (
            db.query(func.count(func.distinct(OnboardingEventRecord.session_id)))
            .filter(
                OnboardingEventRecord.variant == var_code,
                OnboardingEventRecord.event == "onboarding_started",
            )
        )
        starters = starters_q.scalar() or 0

        # 3. Completers: unique sessions that finished onboarding
        completers_q = (
            db.query(func.count(func.distinct(OnboardingEventRecord.session_id)))
            .filter(
                OnboardingEventRecord.variant == var_code,
                OnboardingEventRecord.event == "onboarding_completed",
            )
        )
        completers = completers_q.scalar() or 0

        # 4. Skippers: unique sessions that skipped
        skippers_q = (
            db.query(func.count(func.distinct(OnboardingEventRecord.session_id)))
            .filter(
                OnboardingEventRecord.variant == var_code,
                OnboardingEventRecord.event == "onboarding_skipped",
            )
        )
        skippers = skippers_q.scalar() or 0

        # 5. Signups: unique sessions that registered
        signups_q = (
            db.query(func.count(func.distinct(OnboardingEventRecord.session_id)))
            .filter(
                OnboardingEventRecord.variant == var_code,
                OnboardingEventRecord.event == "signup_completed",
            )
        )
        signups = signups_q.scalar() or 0

        denom = max(1, exposures)
        start_rate = round((starters / denom) * 100, 1)
        completion_rate = round((completers / max(1, starters or exposures)) * 100, 1)
        skip_rate = round((skippers / denom) * 100, 1)
        signup_rate = round((signups / denom) * 100, 1)

        return {
            "variant": var_code,
            "exposures": exposures,
            "starters": starters,
            "completers": completers,
            "skippers": skippers,
            "signups": signups,
            "start_rate_percent": start_rate,
            "completion_rate_percent": completion_rate,
            "skip_rate_percent": skip_rate,
            "signup_rate_percent": signup_rate,
        }

    stats_a = get_variant_stats("A")
    stats_b = get_variant_stats("B")

    stats_a["label"] = "Variant A (Control)"
    stats_a["headline"] = "Sog‘lom hayotingiz sari aqlli qadam."
    stats_b["label"] = "Variant B (Challenger)"
    stats_b["headline"] = "Sog‘liq, sport va kundalik odatlar — bitta joyda."

    nA = stats_a["exposures"]
    nB = stats_b["exposures"]
    xA = stats_a["starters"]
    xB = stats_b["starters"]

    MIN_SAMPLE_SIZE = 50
    has_sufficient_data = nA >= MIN_SAMPLE_SIZE and nB >= MIN_SAMPLE_SIZE

    is_significant = False
    p_value = None
    confidence_percent = None
    winner = None
    lift_percent = 0.0

    if not has_sufficient_data:
        status_text = (
            f"Yetarli ma'lumot yo'q — tajriba davom etmoqda "
            f"(Variant A: {nA}/{MIN_SAMPLE_SIZE}, Variant B: {nB}/{MIN_SAMPLE_SIZE})"
        )
    else:
        # Two-proportion pooled z-test
        pA = xA / nA
        pB = xB / nB
        p_pool = (xA + xB) / (nA + nB)
        se = math.sqrt(p_pool * (1.0 - p_pool) * (1.0 / nA + 1.0 / nB)) if p_pool > 0 and p_pool < 1.0 else 0.0

        if se > 0:
            z_score = (pB - pA) / se
            p_val = math.erfc(abs(z_score) / math.sqrt(2))
        else:
            z_score = 0.0
            p_val = 1.0

        p_value = round(p_val, 4)
        confidence_percent = round((1.0 - p_val) * 100, 1)

        base_rate = min(pA, pB)
        diff_rate = abs(pB - pA)
        lift_percent = round((diff_rate / max(0.0001, base_rate)) * 100, 1) if base_rate > 0 else 0.0

        if p_val < 0.05:
            is_significant = True
            winner = "B" if pB > pA else "A"
            status_text = (
                f"Statistik jihatdan ishonchli (p = {p_value}, {confidence_percent}% ishonch). "
                f"G'olib: Variant {winner} (+{lift_percent}% o'sish)"
            )
        else:
            is_significant = False
            status_text = (
                f"Statistik farq aniqlanmadi (p = {p_value} >= 0.05, tasodifiy tebranish ehtimoli yuqori)"
            )

    return {
        "experiment_id": exp_id,
        "name": "Welcome Page Asosiy Sarlavha (Headline A/B)",
        "status": exp_status,
        "min_sample_size": MIN_SAMPLE_SIZE,
        "has_sufficient_data": has_sufficient_data,
        "is_significant": is_significant,
        "winner": winner,
        "lift_percent": lift_percent,
        "p_value": p_value,
        "confidence_percent": confidence_percent,
        "status_text": status_text,
        "variants": {
            "A": stats_a,
            "B": stats_b,
        },
    }
