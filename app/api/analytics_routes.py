from datetime import datetime, timezone
import json
import os
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics & Onboarding Telemetry"])

ANALYTICS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "onboarding_analytics.json")

# In-memory aggregate storage with disk persistence
ONBOARDING_STATS: dict = {
    "total_viewed": 0,
    "total_started": 0,
    "total_completed": 0,
    "total_skipped": 0,
    "completion_rate_percent": 0.0,
    "categories_viewed": {
        "health": 0,
        "sport": 0,
        "life": 0,
        "pricing": 0,
        "privacy": 0,
    },
    "features_tested": {},
    "signups_started": 0,
    "signups_completed": 0,
    "subscriptions_started": 0,
    "platforms": {
        "web": 0,
        "telegram": 0,
        "mobile": 0,
    },
    "recent_events": [],
}


def _load_stats_from_disk():
    global ONBOARDING_STATS
    try:
        if os.path.exists(ANALYTICS_FILE):
            with open(ANALYTICS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    ONBOARDING_STATS.update(data)
    except Exception:
        pass


def _save_stats_to_disk():
    try:
        os.makedirs(os.path.dirname(ANALYTICS_FILE), exist_ok=True)
        with open(ANALYTICS_FILE, "w", encoding="utf-8") as f:
            json.dump(ONBOARDING_STATS, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


_load_stats_from_disk()


class OnboardingEventRequest(BaseModel):
    event: str
    category: Optional[str] = None
    feature_id: Optional[str] = None
    step_index: Optional[int] = None
    platform: Optional[str] = "web"
    timestamp: Optional[str] = None


@router.post("/onboarding-event")
def record_onboarding_event(req: OnboardingEventRequest):
    """
    Records privacy-safe onboarding telemetry.
    Strictly NO patient medical data or prescription records accepted.
    """
    ev = req.event
    plat = (req.platform or "web").lower()

    if plat in ONBOARDING_STATS["platforms"]:
        ONBOARDING_STATS["platforms"][plat] += 1
    else:
        ONBOARDING_STATS["platforms"]["web"] += 1

    if ev == "onboarding_viewed":
        ONBOARDING_STATS["total_viewed"] += 1
    elif ev == "onboarding_started":
        ONBOARDING_STATS["total_started"] += 1
    elif ev == "onboarding_completed":
        ONBOARDING_STATS["total_completed"] += 1
    elif ev == "onboarding_skipped":
        ONBOARDING_STATS["total_skipped"] += 1
    elif ev == "signup_started":
        ONBOARDING_STATS["signups_started"] += 1
    elif ev == "signup_completed":
        ONBOARDING_STATS["signups_completed"] += 1
    elif ev == "subscription_started":
        ONBOARDING_STATS["subscriptions_started"] += 1

    if req.category and req.category in ONBOARDING_STATS["categories_viewed"]:
        ONBOARDING_STATS["categories_viewed"][req.category] += 1

    if req.feature_id:
        f_id = req.feature_id[:50]
        cur = ONBOARDING_STATS["features_tested"].get(f_id, 0)
        ONBOARDING_STATS["features_tested"][f_id] = cur + 1

    # Update completion percentage
    denom = max(1, ONBOARDING_STATS["total_started"] or ONBOARDING_STATS["total_viewed"])
    rate = (ONBOARDING_STATS["total_completed"] / denom) * 100
    ONBOARDING_STATS["completion_rate_percent"] = round(rate, 1)

    # Append to recent events (capped at 50)
    now_iso = req.timestamp or datetime.now(timezone.utc).isoformat()
    ONBOARDING_STATS["recent_events"].insert(
        0,
        {
            "event": ev,
            "category": req.category,
            "feature_id": req.feature_id,
            "platform": plat,
            "timestamp": now_iso,
        },
    )
    if len(ONBOARDING_STATS["recent_events"]) > 50:
        ONBOARDING_STATS["recent_events"] = ONBOARDING_STATS["recent_events"][:50]

    _save_stats_to_disk()
    return {"ok": True, "event": ev}


@router.get("/onboarding-stats")
def get_onboarding_stats():
    """
    Returns aggregated onboarding metrics for admin dashboard and monitoring.
    """
    return ONBOARDING_STATS
