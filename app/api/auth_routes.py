from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.user import User
from app.schemas.user import (
    OtpRequest,
    OtpRequestResponse,
    OtpVerifyRequest,
    ProfileUpdateRequest,
    SyncCodeVerifyRequest,
    UserProfileResponse,
)
from app.services.auth_service import (
    get_user_by_token,
    request_otp_code,
    to_profile_response,
    update_user_profile,
    verify_otp_and_login,
    verify_sync_code_and_login,
)
from app.services.telegram_bot import tg_api_call

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Avtorizatsiya talab qilinadi. Iltimos, ro'yxatdan o'ting.",
        )
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_by_token(db, token)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Sessiya yaroqsiz yoki eskirgan. Qaytadan kiring.",
        )
    return user


@router.post("/request-otp", response_model=OtpRequestResponse)
async def api_request_otp(req: OtpRequest, db: Session = Depends(get_db)):
    try:
        return await request_otp_code(db=db, req=req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/verify-otp", response_model=UserProfileResponse)
async def api_verify_otp(req: OtpVerifyRequest, db: Session = Depends(get_db)):
    try:
        return await verify_otp_and_login(db=db, req=req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/verify-sync-code", response_model=UserProfileResponse)
def api_verify_sync_code(req: SyncCodeVerifyRequest, db: Session = Depends(get_db)):
    try:
        return verify_sync_code_and_login(db=db, code=req.code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/me", response_model=UserProfileResponse)
def api_get_me(user: User = Depends(get_current_user)):
    return to_profile_response(user)


@router.put("/profile", response_model=UserProfileResponse)
def api_update_profile(
    req: ProfileUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return update_user_profile(db=db, user=user, data=req)


@router.post("/logout")
def api_logout(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.session_token = None
    db.commit()
    return {"ok": True, "message": "Tizimdan muvaffaqiyatli chiqildi."}


@router.post("/send-apk-telegram")
async def send_apk_telegram(
    payload: dict,
    db: Session = Depends(get_db),
):
    from app.models.drug import TelegramUserPref
    from app.services.telegram_bot import tg_send_document
    chat_id = payload.get("telegram_chat_id")
    phone = payload.get("phone")
    if not chat_id and phone:
        u = db.query(User).filter(User.phone == phone).first()
        if u and u.telegram_id and u.telegram_id != 777001:
            chat_id = u.telegram_id

    if not chat_id:
        # Check active telegram prefs, strictly excluding dummy 777001
        pref = db.query(TelegramUserPref).filter(TelegramUserPref.chat_id != 777001).order_by(TelegramUserPref.chat_id.desc()).first()
        if pref and pref.chat_id:
            chat_id = pref.chat_id

    if not chat_id:
        u_with_tg = db.query(User).filter(User.telegram_id.isnot(None), User.telegram_id != 777001).first()
        if u_with_tg:
            chat_id = u_with_tg.telegram_id

    caption = (
        "📲 <b>VitaGo Rasmiy Mobil Ilovasi</b>\n\n"
        "Ilovani telefoningizga o'rnatish uchun biriktirilgan faylni oching yoki quyidagi tugmani bosing:\n"
        "👉 https://doriscan.vercel.app/VitaGo_App.apk?v=20261107\n\n"
        "🛡️ <b>Xavfsizlik va Google Play Eslatmasi:</b>\n"
        "<i>Bu APK demo uchun taqdim etilgan bo'lib, yaqinda Google Play ga xavfsiz tarzda chiqadi. "
        "Ilova 100% rasmiy va xavfsiz (virus yoki xakerlik emas).</i>"
    )

    app_kb = {
        "inline_keyboard": [
            [{"text": "📥 VitaGo Ilovasini Yuklab Olish", "url": "https://doriscan.vercel.app/VitaGo_App.apk?v=20261107"}],
            [{"text": "🚀 Mini App'ni Ochish", "web_app": {"url": "https://doriscan.vercel.app/?tg=1"}}],
        ]
    }

    if chat_id and int(chat_id) != 777001:
        sent_doc = False
        try:
            doc_res = await tg_send_document(
                chat_id=int(chat_id),
                file_path_or_url="VitaGo_App.apk",
                caption=caption,
                reply_markup=app_kb,
            )
            if doc_res and doc_res.get("ok"):
                sent_doc = True
        except Exception as e:
            print("Notice tg_send_document failed:", e)

        if not sent_doc:
            try:
                await tg_api_call(
                    "sendMessage",
                    {
                        "chat_id": int(chat_id),
                        "text": caption,
                        "parse_mode": "HTML",
                        "reply_markup": app_kb,
                    },
                )
            except Exception as e:
                print("Notice tg_api_call sendMessage failed:", e)

    return {"ok": True, "chat_id": chat_id, "message": "Ilova fayli Telegram chatingizga yuborildi!"}


class NotificationPreferencesUpdateRequest(BaseModel):
    notify_health: Optional[bool] = None
    notify_sport: Optional[bool] = None
    notify_general: Optional[bool] = None
    notify_in_app: Optional[bool] = None
    notify_web_push: Optional[bool] = None
    notify_quiet_hours_start: Optional[str] = None
    notify_quiet_hours_end: Optional[str] = None
    notify_detailed: Optional[bool] = None


@router.post("/telegram-link-token")
def create_telegram_link_token(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Generates a secure, expiring (15-min) one-time linking token for connecting Telegram account.
    """
    import secrets
    from datetime import datetime, timedelta, timezone
    from app.models.user import TelegramLinkToken

    token = f"tgl_{secrets.token_urlsafe(20)}"
    exp = datetime.now(timezone.utc) + timedelta(minutes=15)
    rec = TelegramLinkToken(user_id=user.id, token=token, expires_at=exp)
    db.add(rec)
    db.commit()

    bot_user = settings.TELEGRAM_BOT_USERNAME
    deep_link = f"https://t.me/{bot_user}?start=link_{token}"
    return {
        "ok": True,
        "token": token,
        "expires_in_seconds": 900,
        "bot_username": bot_user,
        "deep_link": deep_link,
    }


@router.post("/telegram-unlink")
def unlink_telegram_account(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Securely unlinks the connected Telegram identity from the authenticated VitaGo account.
    """
    user.telegram_id = None
    db.commit()
    return {"ok": True, "message": "Telegram hisobi uzildi."}


@router.get("/notification-preferences")
def get_notification_preferences(
    user: User = Depends(get_current_user),
):
    """
    Returns the user's notification preferences and Telegram connection state.
    """
    return {
        "is_telegram_connected": bool(user.telegram_id),
        "telegram_id": user.telegram_id,
        "notify_health": getattr(user, "notify_health", True),
        "notify_sport": getattr(user, "notify_sport", True),
        "notify_general": getattr(user, "notify_general", True),
        "notify_in_app": getattr(user, "notify_in_app", True),
        "notify_web_push": getattr(user, "notify_web_push", True),
        "notify_quiet_hours_start": getattr(user, "notify_quiet_hours_start", "22:00") or "22:00",
        "notify_quiet_hours_end": getattr(user, "notify_quiet_hours_end", "08:00") or "08:00",
        "notify_detailed": getattr(user, "notify_detailed", False),
    }


@router.put("/notification-preferences")
def update_notification_preferences(
    req: NotificationPreferencesUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Updates the user's notification preferences.
    """
    if req.notify_health is not None:
        user.notify_health = req.notify_health
    if req.notify_sport is not None:
        user.notify_sport = req.notify_sport
    if req.notify_general is not None:
        user.notify_general = req.notify_general
    if req.notify_in_app is not None:
        user.notify_in_app = req.notify_in_app
    if req.notify_web_push is not None:
        user.notify_web_push = req.notify_web_push
    if req.notify_quiet_hours_start is not None:
        user.notify_quiet_hours_start = req.notify_quiet_hours_start.strip()[:8]
    if req.notify_quiet_hours_end is not None:
        user.notify_quiet_hours_end = req.notify_quiet_hours_end.strip()[:8]
    if req.notify_detailed is not None:
        user.notify_detailed = req.notify_detailed

    db.commit()
    return {"ok": True, "message": "Eslatmalar sozlamalari muvaffaqiyatli saqlandi."}

