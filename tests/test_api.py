import io
import time
from unittest.mock import patch
from fastapi.testclient import TestClient
from PIL import Image

from app.db.database import get_db
from app.models.user import AuthOtp
from app.schemas.prescription import AIExtractedDrug, AIPrescriptionExtract, AISuggestedAnalog
from main import app


def test_health_and_stats():
    with TestClient(app) as client:
        r_health = client.get("/health")
        assert r_health.status_code == 200
        assert r_health.json()["status"] == "ok"
        assert r_health.json()["model"].startswith("gemini-3.")

        r_stats = client.get("/api/v1/stats")
        assert r_stats.status_code == 200
        stats = r_stats.json()
        assert stats["total_drugs"] >= 100
        assert stats["total_inn_groups"] >= 20
        assert stats["local_uz_drugs"] >= 30


def test_multilingual_fuzzy_drug_search():
    with TestClient(app) as client:
        # 1. Latin search
        res_uz = client.get("/api/v1/drugs/search", params={"q": "Sumamed", "lang": "uz"})
        assert res_uz.status_code == 200
        items_uz = res_uz.json()
        assert len(items_uz) >= 1
        assert items_uz[0]["inn"] == "Azithromycin"
        assert items_uz[0]["analogs_count"] >= 4
        assert items_uz[0]["cheapest_analog_uzs"] == 21000

        # 2. Russian Cyrillic search
        res_ru = client.get("/api/v1/drugs/search", params={"q": "Сумамед", "lang": "ru"})
        assert res_ru.status_code == 200
        items_ru = res_ru.json()
        assert len(items_ru) >= 1
        assert items_ru[0]["trade_name"] == "Сумамед"
        assert items_ru[0]["inn_localized"] == "Азитромицин"

        # 3. Phonetic cross-script search (Sitramon / Цитрамон / Tsitramon)
        for spelling in ["Цитрамон", "Sitramon", "Tsitramon"]:
            res_cit = client.get("/api/v1/drugs/search", params={"q": spelling, "lang": "uz_cyrl"})
            assert res_cit.status_code == 200
            assert len(res_cit.json()) >= 1


def test_drug_analogs_and_savings_calculation_three_languages():
    with TestClient(app) as client:
        search = client.get("/api/v1/drugs/search", params={"q": "Sumamed", "lang": "uz"}).json()
        sumamed_id = search[0]["id"]

        for lang in ("uz", "uz_cyrl", "ru"):
            res = client.get(f"/api/v1/drugs/{sumamed_id}/analogs", params={"lang": lang})
            assert res.status_code == 200
            payload = res.json()
            assert payload["language"] == lang
            assert payload["total_prescribed_uzs"] == 138000
            assert payload["total_with_analogs_uzs"] == 21000
            assert payload["total_savings_uzs"] == 117000
            assert payload["total_savings_percent"] >= 80
            assert len(payload["items"][0]["analogs"]) >= 4
            assert payload["items"][0]["recommended_analog"]["is_local_uz"] is True


def test_demo_prescriptions():
    with TestClient(app) as client:
        samples = client.get("/api/v1/prescription/samples", params={"lang": "uz_cyrl"}).json()
        assert len(samples) == 3

        demo = client.post("/api/v1/prescription/demo/shamollash-antibiotik", params={"lang": "uz_cyrl"})
        assert demo.status_code == 200
        data = demo.json()
        assert data["is_valid_prescription"] is True
        assert len(data["items"]) == 4
        assert data["total_savings_uzs"] > 250000
        assert "интеллектуал тизим" in data["disclaimer"]


def test_scan_endpoint_with_mocked_gemini_vision_hybrid_db_and_fallback():
    mock_extract = AIPrescriptionExtract(
        is_valid_prescription=True,
        doctor_notes_summary="Shifokor antibiotik va noyob vitamin majmuasini yozgan.",
        items=[
            # 1) Drug present in verified local DB -> should match DB prices & analogs
            AIExtractedDrug(
                raw_written_text="Rp: Tab. Augmentin 875/125 N14",
                recognized_trade_name="Augmentin",
                inn="Amoxicillin + Clavulanic acid",
                inn_localized="Amoksitsillin + Klavulan kislotasi",
                dosage="875/125 mg",
                regimen="Kuniga 2 mahal, 7 kun",
                confidence=0.95,
                simple_purpose="Bakterial infeksiyaga qarshi",
                how_to_take="Ovqat boshida",
                warnings="Penitsillinga allergiyada mumkin emas",
                estimated_brand_price_uzs=120000,
            ),
            # 2) Rare drug NOT in local DB -> should seamlessly use Gemini AI fallback analogs
            AIExtractedDrug(
                raw_written_text="Rp: Caps. Ursosan 250mg N50",
                recognized_trade_name="Ursosan",
                inn="Ursodeoxycholic acid",
                inn_localized="Ursodezoksixol kislotasi",
                dosage="250 mg №50",
                regimen="Kechqurun 2 kapsula",
                confidence=0.91,
                simple_purpose="O't pufagi va jigarni himoya qiluvchi vosita.",
                how_to_take="Uxlashdan oldin suv bilan ichiladi.",
                warnings="O'tkir xoletsistitda shifokor bilan maslahatlashing.",
                estimated_brand_price_uzs=240000,
                ai_fallback_analogs=[
                    AISuggestedAnalog(
                        trade_name="Ursodez-Nika",
                        manufacturer="Nika Pharm",
                        is_local_uz=True,
                        estimated_price_uzs=95000,
                        dosage_and_form="250 mg kapsula №50",
                    )
                ],
            ),
        ],
    )

    img = Image.new("RGB", (320, 200), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)

    with patch("app.api.routes.analyze_prescription_with_gemini", return_value=mock_extract):
        with TestClient(app) as client:
            res = client.post(
                "/api/v1/prescription/scan",
                files={"file": ("rx.jpg", buf.getvalue(), "image/jpeg")},
                data={"lang": "uz"},
            )
            assert res.status_code == 200
            body = res.json()
            assert len(body["items"]) == 2
            assert body["items"][0]["price_source"] == "verified_db"
            assert body["items"][0]["recommended_analog"]["price_uzs"] == 38000
            assert body["items"][1]["price_source"] == "ai_estimate"
            assert body["items"][1]["recommended_analog"]["trade_name"] == "Ursodez-Nika"
            assert body["total_savings_uzs"] == (122000 - 38000) + (240000 - 95000)


def test_telegram_bot_webhook_and_mini_app_buttons():
    sent_calls: list[tuple[str, dict]] = []

    async def fake_tg_api_call(method: str, payload: dict | None = None, timeout: float = 20.0):
        sent_calls.append((method, payload or {}))
        return {"ok": True, "result": {"message_id": 101}}

    from app.core.config import settings

    with patch("app.services.telegram_bot.tg_api_call", side_effect=fake_tg_api_call):
        with TestClient(app) as client:
            info = client.get("/api/v1/telegram/info")
            assert info.status_code == 200
            assert info.json()["bot_username"] == "VitaGoApp_bot"
            assert "/?tg=1" in info.json()["mini_app_url"]

            # Wrong webhook secret should return 403 when secret is configured
            if settings.TELEGRAM_WEBHOOK_SECRET:
                bad = client.post(
                    "/api/v1/telegram/webhook",
                    json={"update_id": 9001},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
                )
                assert bad.status_code == 403

            headers = (
                {"X-Telegram-Bot-Api-Secret-Token": settings.TELEGRAM_WEBHOOK_SECRET}
                if settings.TELEGRAM_WEBHOOK_SECRET
                else {}
            )

            # 1) /start command should send BOTH persistent web_app ReplyKeyboard and web_app InlineKeyboard
            res_start = client.post(
                "/api/v1/telegram/webhook",
                headers=headers,
                json={
                    "update_id": 9002,
                    "message": {
                        "message_id": 1,
                        "from": {"id": 777001, "first_name": "Husamiddin", "language_code": "uz"},
                        "chat": {"id": 777001, "type": "private"},
                        "text": "/start",
                    },
                },
            )
            assert res_start.status_code == 200
            assert len(sent_calls) == 2
            kb_markup = sent_calls[0][1]["reply_markup"]
            assert "web_app" in kb_markup["keyboard"][0][0]
            inline_markup = sent_calls[1][1]["reply_markup"]
            assert "web_app" in inline_markup["inline_keyboard"][0][0]

            # 2) Free-text drug search ("Sumamed") should return XPN analogs + Mini App button
            sent_calls.clear()
            res_drug = client.post(
                "/api/v1/telegram/webhook",
                headers=headers,
                json={
                    "update_id": 9003,
                    "message": {
                        "message_id": 2,
                        "from": {"id": 777001, "first_name": "Husamiddin", "language_code": "uz"},
                        "chat": {"id": 777001, "type": "private"},
                        "text": "Sumamed",
                    },
                },
            )
            assert res_drug.status_code == 200
            assert len(sent_calls) == 1
            drug_reply = sent_calls[0][1]
            assert "Azitromitsin" in drug_reply["text"]
            assert "21 000" in drug_reply["text"]
            assert "web_app" in drug_reply["reply_markup"]["inline_keyboard"][0][0]


def test_auth_registration_and_profile_flow():
    unique_phone = f"+99890{int(time.time() * 1000) % 10000000:07d}"
    with TestClient(app) as client:
        # 1. Request OTP with First Name, Last Name, and Phone
        res_req = client.post(
            "/api/v1/auth/request-otp",
            json={
                "phone": unique_phone,
                "first_name": "Husamiddin",
                "last_name": "Aminov",
                "telegram_chat_id": 777001,
            },
        )
        assert res_req.status_code == 200
        data_req = res_req.json()
        assert data_req["ok"] is True
        assert "yuborildi" in data_req["message"]
        db = next(get_db())
        otp_record = (
            db.query(AuthOtp)
            .filter(AuthOtp.phone == unique_phone, AuthOtp.is_used == False)  # noqa: E712
            .order_by(AuthOtp.id.desc())
            .first()
        )
        assert otp_record is not None
        code = otp_record.code
        assert len(code) == 6

        # 2. Verify with wrong code should fail
        res_bad = client.post(
            "/api/v1/auth/verify-otp",
            json={
                "phone": unique_phone,
                "code": "000000",
            },
        )
        assert res_bad.status_code == 400

        # 3. Verify with correct 6-digit code
        res_ok = client.post(
            "/api/v1/auth/verify-otp",
            json={
                "phone": unique_phone,
                "code": code,
                "first_name": "Husamiddin",
                "last_name": "Aminov",
            },
        )
        assert res_ok.status_code == 200
        user = res_ok.json()
        assert user["phone"] == unique_phone
        assert user["first_name"] == "Husamiddin"
        assert user["is_profile_complete"] is False
        token = user["token"]
        assert len(token) > 20

        # 4. Fill medical/physical profile (gender, age, height, weight, avatar)
        auth_header = {"Authorization": f"Bearer {token}"}
        res_prof = client.put(
            "/api/v1/auth/profile",
            headers=auth_header,
            json={
                "gender": "male",
                "age": 24,
                "height_cm": 178,
                "weight_kg": 72,
                "avatar_data": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
            },
        )
        assert res_prof.status_code == 200
        updated = res_prof.json()
        assert updated["gender"] == "male"
        assert updated["age"] == 24
        assert updated["height_cm"] == 178
        assert updated["weight_kg"] == 72
        assert updated["is_profile_complete"] is True
        assert updated["avatar_data"].startswith("data:image/")

        # 5. Check /api/v1/auth/me
        res_me = client.get("/api/v1/auth/me", headers=auth_header)
        assert res_me.status_code == 200
        assert res_me.json()["id"] == updated["id"]

        # 6. Logout
        res_out = client.post("/api/v1/auth/logout", headers=auth_header)
        assert res_out.status_code == 200
        res_me_after = client.get("/api/v1/auth/me", headers=auth_header)
        assert res_me_after.status_code == 401


def test_public_drug_catalogue_for_offline_pwa_cache():
    """Verify that public drug catalogue endpoint returns valid non-sensitive data for offline PWA IndexedDB cache."""
    with TestClient(app) as client:
        res = client.get("/api/v1/drugs/public-catalogue", params={"limit": 50})
        assert res.status_code == 200
        data = res.json()
        assert "version" in data
        assert "synced_at" in data
        assert "drugs" in data
        assert len(data["drugs"]) > 0
        first_drug = data["drugs"][0]
        assert "id" in first_drug
        assert "trade_name" in first_drug
        assert "inn" in first_drug
        assert "dosage_and_form" in first_drug
        assert "disclaimer" in data
        # Ensure zero sensitive health/auth fields are exposed
        assert "password" not in str(data).lower()
        assert "session_token" not in str(data).lower()

