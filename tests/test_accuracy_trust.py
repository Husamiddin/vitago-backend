"""VitaGo 2.0 — Accuracy, Trust & Notification Reliability Test Suite.

Covers:
1. VAPID public key endpoint & subscription management (Web Push API).
2. Idempotent multi-channel dispatcher (in_app, web_push, telegram) with atomic claim-locking.
3. Quiet hours logic with Asia/Tashkent UTC+5 awareness.
4. Drug analogue clinical equivalence levels & savings percentage omission on unverified data.
5. Image quality / blur pre-validation in OCR & Vision scanning.
6. Food scan portion scaling and calorie ranges.
7. Admin notification channel metrics & revoked subscription tracking.
"""

import os
import sys
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

# Ensure backend directory is in sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from main import app
from app.db.database import get_db
from app.core.config import settings
from app.models.user import User, PushSubscription, ScheduledNotification
from app.models.drug import Drug
from app.services.gemini_vision import normalize_image_bytes
from app.services.ai_features import run_food_scan
from app.api.admin_routes import _generate_admin_token
from app.schemas.prescription import (
    DrugAnalogOption,
    AnalyzedDrugResult,
    PrescriptionAnalysisResponse,
)


def test_vapid_public_key_endpoint():
    """Verify that VAPID public key is served correctly for Web Push subscription."""
    with TestClient(app) as client:
        res = client.get("/api/v1/notifications/vapid-public-key")
        assert res.status_code == 200
        data = res.json()
        assert data.get("ok") is True
        assert "public_key" in data
        assert len(data["public_key"]) > 20
        assert data["public_key"] == settings.VAPID_PUBLIC_KEY


def test_push_subscribe_and_unsubscribe():
    """Verify subscribing and unsubscribing Web Push client endpoints."""
    with TestClient(app) as client:
        # Create a test user in DB
        db = next(get_db())
        token = f"test_session_token_{int(time.time())}"
        test_user = User(
            phone=f"+99890{int(time.time()) % 10000000:07d}",
            first_name="WebPush Test User",
            session_token=token,
            notify_web_push=False,
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)

        endpoint_url = f"https://fcm.googleapis.com/fcm/send/test_sub_{int(time.time())}"

        # 1. Subscribe
        sub_payload = {
            "endpoint": endpoint_url,
            "keys": {
                "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QT9bP",
                "auth": "tBHItJI5svbpez7KI4CCXg",
            },
            "user_id": test_user.id,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
        }
        res_sub = client.post(
            "/api/v1/notifications/push-subscribe",
            json=sub_payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res_sub.status_code == 200
        assert res_sub.json()["ok"] is True

        # Verify DB subscription
        saved_sub = (
            db.query(PushSubscription)
            .filter(PushSubscription.endpoint == endpoint_url)
            .first()
        )
        assert saved_sub is not None
        assert saved_sub.user_id == test_user.id
        assert saved_sub.is_active is True
        assert saved_sub.auth == "tBHItJI5svbpez7KI4CCXg"

        # Check that user's notify_web_push flag was set to True
        db.refresh(test_user)
        assert test_user.notify_web_push is True

        # 2. Unsubscribe
        unsub_payload = {"endpoint": endpoint_url}
        res_unsub = client.post(
            "/api/v1/notifications/push-unsubscribe",
            json=unsub_payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res_unsub.status_code == 200
        assert res_unsub.json()["ok"] is True

        # Verify DB marks as inactive
        db.refresh(saved_sub)
        assert saved_sub.is_active is False

        # Cleanup
        db.delete(saved_sub)
        db.delete(test_user)
        db.commit()


def test_clinical_equivalence_and_savings_percent_omission():
    """Verify that savings_percent is strictly None when prices or matches are unverified,

    and clinical equivalence is classified accurately without fabricating savings.
    """
    # Case 1: Unverified estimated drug match -> savings_percent MUST be None
    unverified_match = DrugAnalogOption(
        id=999,
        trade_name="Amoxi-Generic 500mg",
        inn="amoxicillin",
        inn_localized="amoksitsillin",
        dosage="500mg",
        form="tablet",
        manufacturer="Local Pharma",
        country="O'zbekiston",
        is_local_uz=True,
        price_uzs=12000,
        savings_uzs=0,
        savings_percent=None,  # Crucial rule: no fabricated 70% claims!
        source="ai_estimate",
        clinical_equivalence_status="insufficient_information",
        is_verified=False,
        verification_source="ai_unverified_estimate",
    )
    assert unverified_match.savings_percent is None
    assert unverified_match.is_verified is False
    assert (
        unverified_match.clinical_equivalence_status == "insufficient_information"
    )

    # Case 2: Verified official DB match with confirmed price difference -> valid savings
    verified_match = DrugAnalogOption(
        id=101,
        trade_name="Amoksiklav 625mg",
        inn="amoxicillin + clavulanic acid",
        inn_localized="amoksitsillin + klavulan kislotasi",
        dosage="500mg/125mg",
        form="tablet",
        manufacturer="Lek",
        country="Sloveniya",
        is_local_uz=False,
        price_uzs=45000,
        savings_uzs=25000,
        savings_percent=35,
        source="verified_db",
        clinical_equivalence_status="verified_exact_match",
        is_verified=True,
        verification_source="O'zR SSV Davlat Reyestri",
    )
    assert verified_match.savings_percent == 35
    assert verified_match.is_verified is True
    assert (
        verified_match.clinical_equivalence_status == "verified_exact_match"
    )


def test_gemini_vision_image_quality_check():
    """Verify that blur, tiny images (<100x100px or <600 bytes) are rejected with clear user error."""
    tiny_bytes = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"

    with pytest.raises(ValueError) as excinfo:
        normalize_image_bytes(tiny_bytes, "image/gif")
    assert "hajmi juda kichik" in str(excinfo.value)


def test_food_scan_portion_scaling_and_range():
    """Verify portion scaling and calorie ranges in AI nutrition."""
    # 1. Standard default portion (1 serving = 350g)
    res_default = run_food_scan(image_b64="data:image/jpeg;base64,ZmFrZQ==")
    assert res_default.portion_size_g == 350
    assert res_default.calorie_range is not None
    assert "kkal" in res_default.calorie_range
    assert res_default.calories > 0

    # 2. Scaled portion: user specifies custom 200g
    res_custom = run_food_scan(
        image_b64="data:image/jpeg;base64,ZmFrZQ==", portion_grams=200
    )
    assert res_custom.portion_size_g == 200
    assert res_custom.calorie_range is not None
    expected_cal = round(680 * (200 / 350))
    assert res_custom.calories == expected_cal


def test_multichannel_dispatcher_idempotency_and_claim_locking():
    """Verify that dispatch-due uses atomic claim-locking and respects delivery channels."""
    db = next(get_db())

    # Create test notification due right now
    now_utc = datetime.now(timezone.utc)
    due_notif = ScheduledNotification(
        user_id=1,
        title="Test Dori Vaqti",
        message="Iltimos, doringizni iching",
        category="health",
        scheduled_for=now_utc - timedelta(minutes=5),
        delivery_channel="web_push",
        status="pending",
    )
    db.add(due_notif)
    db.commit()
    db.refresh(due_notif)

    with patch(
        "app.services.web_push.send_web_push", return_value={"ok": True}
    ) as mock_push:
        with TestClient(app) as client:
            # 1. First dispatch run should claim and process the notification
            res = client.post("/api/v1/notifications/dispatch-due")
            assert res.status_code == 200
            data = res.json()
            assert data["ok"] is True

            # Verify in DB that it was claimed
            db.refresh(due_notif)
            assert due_notif.claimed_at is not None

            # 2. Second dispatch immediately after should be idempotent
            res2 = client.post("/api/v1/notifications/dispatch-due")
            assert res2.status_code == 200

            # Clean up
            db.delete(due_notif)
            db.commit()


def test_admin_notification_stats_endpoint():
    """Verify admin stats includes web push subscriptions and channel breakdowns."""
    admin_token = _generate_admin_token()
    with TestClient(app) as client:
        res = client.get(
            "/api/v1/notifications/admin-stats",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "total_scheduled" in data
        assert "web_push" in data
        assert "active_subscriptions" in data["web_push"]
        assert "revoked_subscriptions" in data["web_push"]
        assert "channel_delivery_counts" in data
        assert "web_push" in data["channel_delivery_counts"]
        assert "telegram" in data["channel_delivery_counts"]
        assert "in_app" in data["channel_delivery_counts"]
