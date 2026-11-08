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


def test_db_connection_pool_optimization_and_metrics():
    """Verify database connection pool optimization, metrics reporting, and admin endpoints."""
    from app.db.database import get_db_pool_status

    # 1. Inspect pool status metrics
    metrics = get_db_pool_status()
    assert "engine_dialect" in metrics
    assert "health" in metrics
    assert metrics["health"] == "healthy"
    assert "configured_pool_size" in metrics
    assert "configured_max_overflow" in metrics
    assert metrics["configured_pool_size"] >= 1
    assert "ping_latency_ms" in metrics
    assert isinstance(metrics["ping_latency_ms"], (int, float))

    with TestClient(app) as client:
        # 2. Health endpoint exposes safe database status without secrets
        res_health = client.get("/health")
        assert res_health.status_code == 200
        health_data = res_health.json()
        assert "database" in health_data
        assert health_data["database"]["health"] == "healthy"
        assert "ping_latency_ms" in health_data["database"]
        # Must not contain passwords or raw URLs
        assert "password" not in str(health_data).lower()
        assert "railway.net" not in str(health_data).lower()

        # 3. Admin db-pool-status requires admin auth
        res_unauth = client.get("/api/v1/admin/db-pool-status")
        assert res_unauth.status_code == 401

        admin_token = _generate_admin_token()
        res_auth = client.get(
            "/api/v1/admin/db-pool-status",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_auth.status_code == 200
        pool_admin_data = res_auth.json()
        assert pool_admin_data["health"] == "healthy"


def test_db_session_rollback_and_concurrency():
    """Verify controlled concurrency and session cleanup/rollback on failure."""
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import text
    from app.db.database import get_db

    # 1. Session rollback on simulated exception
    def failing_session_use():
        db_gen = get_db()
        db = next(db_gen)
        try:
            db.execute(text("SELECT 1"))
            raise RuntimeError("Simulated failure inside transaction")
        except RuntimeError:
            try:
                next(db_gen)
            except (StopIteration, RuntimeError):
                pass

    failing_session_use()

    # 2. Controlled concurrency test (10 parallel workers acquiring sessions and executing fast query)
    def worker_task(idx):
        db_gen = get_db()
        db = next(db_gen)
        try:
            val = db.execute(text("SELECT 1")).scalar()
            return val == 1
        finally:
            try:
                next(db_gen)
            except StopIteration:
                pass

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(worker_task, range(16)))
    assert all(results) is True, "All concurrent database sessions must succeed and clean up"


def test_telegram_account_linking_and_smart_notifications():
    """Verify secure one-time Telegram account linking, notification preferences, and reminder dispatching."""
    from datetime import datetime, timedelta, timezone
    from app.models.user import AuthOtp, User

    with TestClient(app) as client:
        # 1. Register test user
        phone = f"+99893{int(time.time() * 1000) % 10000000:07d}"
        client.post("/api/v1/auth/request-otp", json={"phone": phone, "first_name": "TestTg"})
        db = next(get_db())
        otp = db.query(AuthOtp).filter(AuthOtp.phone == phone).order_by(AuthOtp.id.desc()).first()
        assert otp is not None

        res_login = client.post(
            "/api/v1/auth/verify-otp",
            json={"phone": phone, "code": otp.code, "first_name": "TgUser"},
        )
        assert res_login.status_code == 200
        token = res_login.json()["token"]
        auth_headers = {"Authorization": f"Bearer {token}"}

        # 2. Generate secure one-time linking token
        res_link = client.post("/api/v1/auth/telegram-link-token", headers=auth_headers)
        assert res_link.status_code == 200
        link_data = res_link.json()
        assert link_data["ok"] is True
        link_token = link_data["token"]
        assert link_token.startswith("tgl_")
        assert f"link_{link_token}" in link_data["deep_link"]

        # 3. Simulate user sending /start link_TOKEN to Telegram webhook
        fake_chat_id = 99881122
        sent_calls = []

        async def fake_tg_api_call(method: str, payload: dict):
            sent_calls.append((method, payload))
            return {"ok": True}

        with patch("app.services.telegram_bot.tg_api_call", side_effect=fake_tg_api_call):
            res_webhook = client.post(
                "/api/v1/telegram/webhook",
                json={
                    "update_id": 99991,
                    "message": {
                        "message_id": 10,
                        "from": {"id": fake_chat_id, "first_name": "TgUser"},
                        "chat": {"id": fake_chat_id, "type": "private"},
                        "text": f"/start link_{link_token}",
                    },
                },
            )
            assert res_webhook.status_code == 200
            assert len(sent_calls) >= 1
            reply_text = sent_calls[0][1]["text"]
            assert "muvaffaqiyatli ulandi" in reply_text.lower()

        # Check DB to confirm telegram_id was linked
        u = db.query(User).filter(User.phone == phone).first()
        assert u.telegram_id == fake_chat_id

        # 4. Attempting to reuse the same linking token must fail with expired/used warning
        sent_calls.clear()
        with patch("app.services.telegram_bot.tg_api_call", side_effect=fake_tg_api_call):
            client.post(
                "/api/v1/telegram/webhook",
                json={
                    "update_id": 99992,
                    "message": {
                        "message_id": 11,
                        "from": {"id": fake_chat_id, "first_name": "TgUser"},
                        "chat": {"id": fake_chat_id, "type": "private"},
                        "text": f"/start link_{link_token}",
                    },
                },
            )
            assert len(sent_calls) >= 1
            assert "eskirgan yoki avval ishlatilgan" in sent_calls[0][1]["text"].lower()

        # 5. Check and update notification preferences
        res_prefs = client.get("/api/v1/auth/notification-preferences", headers=auth_headers)
        assert res_prefs.status_code == 200
        prefs_data = res_prefs.json()
        assert prefs_data["is_telegram_connected"] is True
        assert prefs_data["telegram_id"] == fake_chat_id
        assert prefs_data["notify_detailed"] is False  # Neutral by default

        res_update_prefs = client.put(
            "/api/v1/auth/notification-preferences",
            headers=auth_headers,
            json={
                "notify_health": True,
                "notify_sport": True,
                "notify_in_app": True,
                "notify_quiet_hours_start": "23:00",
                "notify_quiet_hours_end": "07:00",
            },
        )
        assert res_update_prefs.status_code == 200

        # 6. Schedule a reminder
        sched_time = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        res_sched = client.post(
            "/api/v1/notifications/schedule-reminder",
            headers=auth_headers,
            json={
                "category": "health",
                "title": "Kechki dori",
                "scheduled_for": sched_time,
                "delivery_channel": "both",
            },
        )
        assert res_sched.status_code == 200
        reminder_id = res_sched.json()["reminder_id"]

        # 7. List reminders
        res_list = client.get("/api/v1/notifications/my-reminders", headers=auth_headers)
        assert res_list.status_code == 200
        assert any(r["id"] == reminder_id for r in res_list.json())

        # 8. Dispatch due notifications
        with patch("app.services.telegram_bot.tg_api_call", side_effect=fake_tg_api_call):
            res_dispatch = client.post("/api/v1/notifications/dispatch-due")
            assert res_dispatch.status_code == 200
            disp_data = res_dispatch.json()
            assert disp_data["ok"] is True
            assert disp_data["processed"] >= 1

        # 9. Admin notification stats
        admin_token = _generate_admin_token()
        res_admin_notifs = client.get(
            "/api/v1/notifications/admin-stats",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_admin_notifs.status_code == 200
        notif_stats = res_admin_notifs.json()
        assert "connected_telegram_accounts" in notif_stats
        assert notif_stats["connected_telegram_accounts"] >= 1
        assert "total_scheduled" in notif_stats
        assert "categories" in notif_stats



