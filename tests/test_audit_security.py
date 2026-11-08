import os
import time
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


def test_onboarding_analytics_session_deduplication():
    """Verify session_id attribution and server-side deduplication within cooldown."""
    unique_session = f"sess_test_{int(os.getpid())}_{int(time.time() * 1000)}"
    with TestClient(app) as client:
        # First event with session_id
        res1 = client.post(
            "/api/v1/analytics/onboarding-event",
            json={
                "session_id": unique_session,
                "event": "feature_demo_opened",
                "feature_id": "smart_alarm",
                "category": "health",
                "platform": "web",
            },
        )
        assert res1.status_code == 200
        assert res1.json()["ok"] is True
        assert res1.json().get("deduplicated") is False or "deduplicated" not in res1.json()

        # Immediate identical event from the same session -> deduplicated
        res2 = client.post(
            "/api/v1/analytics/onboarding-event",
            json={
                "session_id": unique_session,
                "event": "feature_demo_opened",
                "feature_id": "smart_alarm",
                "category": "health",
                "platform": "web",
            },
        )
        assert res2.status_code == 200
        assert res2.json()["ok"] is True
        assert res2.json().get("deduplicated") is True

        # Check DB count for this session: should only be 1 row
        db = next(get_db())
        count = (
            db.query(OnboardingEventRecord)
            .filter(
                OnboardingEventRecord.session_id == unique_session,
                OnboardingEventRecord.event == "feature_demo_opened",
                OnboardingEventRecord.feature_id == "smart_alarm",
            )
            .count()
        )
        assert count == 1


def test_client_cannot_submit_subscription_succeeded():
    """Verify that frontend cannot forge subscription_succeeded event (returns 403)."""
    with TestClient(app) as client:
        res = client.post(
            "/api/v1/analytics/onboarding-event",
            json={
                "event": "subscription_succeeded",
                "category": "pricing",
                "platform": "web",
            },
        )
        assert res.status_code == 403
        assert "server tomonidan tasdiqlangan to'lov" in res.json()["detail"].lower()


def test_admin_role_separation_401_403_200():
    """Verify role-based access control:
    - No header / invalid token -> 401 Unauthorized
    - Authenticated regular user -> 403 Forbidden
    - Authenticated admin -> 200 OK
    """
    import time
    from app.models.user import AuthOtp

    with TestClient(app) as client:
        # 1. No token -> 401
        res_no_token = client.get("/api/v1/analytics/onboarding-stats")
        assert res_no_token.status_code == 401

        # 2. Register a standard non-admin user and get user token
        phone = f"+99890{int(time.time() * 1000) % 10000000:07d}"
        client.post("/api/v1/auth/request-otp", json={"phone": phone, "first_name": "User"})
        db = next(get_db())
        otp = db.query(AuthOtp).filter(AuthOtp.phone == phone).order_by(AuthOtp.id.desc()).first()
        assert otp is not None

        res_auth = client.post(
            "/api/v1/auth/verify-otp",
            json={"phone": phone, "code": otp.code, "first_name": "Regular", "last_name": "User"},
        )
        assert res_auth.status_code == 200
        user_token = res_auth.json()["token"]

        # Standard user tries to access admin endpoint -> 403 Forbidden
        res_user_forbidden = client.get(
            "/api/v1/analytics/onboarding-stats",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert res_user_forbidden.status_code == 403
        assert "admin" in res_user_forbidden.json()["detail"].lower()

        # 3. Valid admin HMAC token -> 200 OK
        admin_token = _generate_admin_token()
        res_admin_ok = client.get(
            "/api/v1/analytics/onboarding-stats",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_admin_ok.status_code == 200


def test_growth_analytics_periods_and_funnel():
    """Verify admin onboarding growth analytics periods (today, 7d, 30d, all) and funnel payload."""
    admin_token = _generate_admin_token()
    headers = {"Authorization": f"Bearer {admin_token}"}

    with TestClient(app) as client:
        for period in ["today", "7d", "30d", "all"]:
            res = client.get("/api/v1/analytics/onboarding-stats", params={"period": period}, headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert data["period"] == period
            assert "acquisition" in data
            assert "unique_visitors" in data["acquisition"]
            assert "start_rate_percent" in data["acquisition"]
            assert "funnel" in data
            assert "welcome_visitors" in data["funnel"]
            assert "confirmed_paying_users" in data["funnel"]
            assert "categories_viewed" in data
            assert "retention" in data
            assert "status_text" in data["retention"]
            assert "has_sufficient_data" in data["retention"]

