import os
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.api.admin_routes import _generate_admin_token
from app.db.database import get_db
from app.models.payment import OnboardingEventRecord
from main import app


def test_onboarding_analytics_event_persistence():
    """Verify that onboarding events are validated against whitelists and persisted to SQL DB."""
    with TestClient(app) as client:
        # 1. Valid event submission
        res = client.post(
            "/api/v1/analytics/onboarding-event",
            json={
                "event": "onboarding_viewed",
                "category": "health",
                "platform": "web",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True

        # Check DB persistence
        db = next(get_db())
        saved_record = (
            db.query(OnboardingEventRecord)
            .filter(OnboardingEventRecord.event == "onboarding_viewed")
            .order_by(OnboardingEventRecord.id.desc())
            .first()
        )
        assert saved_record is not None
        assert saved_record.event == "onboarding_viewed"
        assert saved_record.category == "health"
        assert saved_record.platform == "web"

        # 2. Invalid event name should return 400
        res_bad_event = client.post(
            "/api/v1/analytics/onboarding-event",
            json={
                "event": "fake_hacked_event",
                "category": "health",
            },
        )
        assert res_bad_event.status_code == 400
        assert "Noto'g'ri event nomi" in res_bad_event.json()["detail"]

        # 3. Invalid category should return 400
        res_bad_cat = client.post(
            "/api/v1/analytics/onboarding-event",
            json={
                "event": "category_opened",
                "category": "unauthorized_category",
            },
        )
        assert res_bad_cat.status_code == 400
        assert "Noto'g'ri kategoriya" in res_bad_cat.json()["detail"]


def test_onboarding_stats_admin_auth():
    """Verify that GET /api/v1/analytics/onboarding-stats is strictly protected by admin token."""
    with TestClient(app) as client:
        # 1. Without Authorization header -> 401
        res_unauth = client.get("/api/v1/analytics/onboarding-stats")
        assert res_unauth.status_code == 401

        # 2. With invalid admin token -> 401
        res_bad = client.get(
            "/api/v1/analytics/onboarding-stats",
            headers={"Authorization": "Bearer invalid_secret_token"},
        )
        assert res_bad.status_code == 401

        # 3. With valid admin token -> 200 with valid analytics payload
        admin_token = _generate_admin_token()
        res_ok = client.get(
            "/api/v1/analytics/onboarding-stats",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_ok.status_code == 200
        stats = res_ok.json()
        assert "total_viewed" in stats
        assert "total_completed" in stats
        assert "completion_rate_percent" in stats
        assert "categories_viewed" in stats
        assert "features_tested" in stats
        assert stats["total_viewed"] >= 1


def test_rate_limiter_hardening():
    """Verify that testclient bypass is only granted in test environments."""
    from app.core.security import RateLimiter
    from starlette.requests import Request

    limiter = RateLimiter()

    # Create dummy request with client host 'testclient'
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/auth/request-otp",
        "headers": [],
        "client": ("testclient", 50000),
    }
    req = Request(scope)

    # 1. When in test environment (default during pytest), it should bypass
    is_limited, _, _ = limiter.is_rate_limited(req)
    assert is_limited is False

    # 2. When simulated outside test environment, testclient is NOT bypassed
    with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "", "ENV": "production", "TESTING": ""}, clear=False):
        # Fire 12 OTP requests (limit is 8 per minute)
        limited_flag = False
        for _ in range(12):
            is_lim, cur, max_lim = limiter.is_rate_limited(req)
            if is_lim:
                limited_flag = True
                break
        assert limited_flag is True, "Rate limiter must enforce limit when not in test environment"
