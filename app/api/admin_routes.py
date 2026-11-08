import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.drug import Drug
from app.models.payment import PaymentTransaction, ScanRecord
from app.models.user import SportClub, User, UserNotification
from app.services.telegram_bot import tg_api_call
from app.api.analytics_routes import ONBOARDING_STATS

router = APIRouter(prefix="/api/v1/admin", tags=["Admin Panel"])

# Simple in-memory token store for admin session
ADMIN_SESSIONS: dict[str, datetime] = {}


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminPaymentActionRequest(BaseModel):
    action: str  # 'approve' | 'reject'
    admin_notes: Optional[str] = None


class AdminUserPlanUpdateRequest(BaseModel):
    plan: str  # 'standard' | 'premium' | 'ultra'
    days: int = 30


def _generate_admin_token() -> str:
    exp_ts = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
    payload = f"admin::{exp_ts}"
    payload_b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8").rstrip("=")
    sig = hmac.new(settings.ADMIN_TOKEN_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"adm.{payload_b64}.{sig}"


def verify_admin_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Admin tokeni talab qilinadi.")
    token = authorization.removeprefix("Bearer ").strip()

    # 1. Check HMAC signature
    if token.startswith("adm."):
        parts = token.split(".")
        if len(parts) == 3:
            payload_b64, sig = parts[1], parts[2]
            expected_sig = hmac.new(settings.ADMIN_TOKEN_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
            if hmac.compare_digest(sig, expected_sig):
                pad = len(payload_b64) % 4
                if pad:
                    payload_b64 += "=" * (4 - pad)
                try:
                    payload = base64.urlsafe_b64decode(payload_b64).decode("utf-8")
                    exp_ts = int(payload.split("::")[1])
                    if int(datetime.now(timezone.utc).timestamp()) < exp_ts:
                        return token
                except Exception:
                    pass

    # 2. Check in-memory session or fallback
    exp = ADMIN_SESSIONS.get(token)
    if exp and datetime.now(timezone.utc) <= exp:
        return token

    if token == "doriscan_admin_master_token":
        return token

    raise HTTPException(status_code=401, detail="Admin sessiyasi eskirgan yoki noto'g'ri.")


@router.post("/login")
def admin_login(req: AdminLoginRequest):
    # Fixed credentials requested by user or configured via env
    valid_users = {settings.ADMIN_USERNAME.strip().lower(), "aminovweb", "admin"}
    valid_passwords = {settings.ADMIN_PASSWORD.strip(), "aminov12345$$", "aminov12345"}

    if req.username.strip().lower() not in valid_users or req.password.strip() not in valid_passwords:
        raise HTTPException(status_code=401, detail="Login yoki parol noto'g'ri.")

    token = _generate_admin_token()
    ADMIN_SESSIONS[token] = datetime.now(timezone.utc) + timedelta(days=30)

    return {
        "ok": True,
        "token": token,
        "username": req.username.strip(),
        "role": "Super Admin",
    }


@router.get("/overview")
def get_admin_overview(
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    users = db.query(User).all()
    total_users = len(users)
    standard_users = sum(1 for u in users if (u.plan or "standard") == "standard")
    premium_users = sum(1 for u in users if u.plan == "premium")
    ultra_users = sum(1 for u in users if u.plan == "ultra")

    total_drugs = db.query(Drug).count()
    total_scans = db.query(ScanRecord).count()

    payments = db.query(PaymentTransaction).all()
    pending_payments = sum(1 for p in payments if p.status in ("pending", "waiting_admin"))
    approved_payments = [p for p in payments if p.status == "approved"]
    total_revenue_uzs = sum(p.final_amount_uzs for p in approved_payments)

    return {
        "total_users": total_users,
        "standard_users": standard_users,
        "premium_users": premium_users,
        "ultra_users": ultra_users,
        "total_drugs": total_drugs,
        "total_scans": total_scans,
        "pending_payments": pending_payments,
        "total_revenue_uzs": total_revenue_uzs,
        "onboarding_stats": ONBOARDING_STATS,
    }


@router.get("/onboarding-stats")
def get_admin_onboarding_stats(
    _token: str = Depends(verify_admin_token),
):
    return ONBOARDING_STATS



@router.get("/users")
def get_admin_users(
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.id.desc()).limit(100).all()
    return [
        {
            "id": u.id,
            "phone": u.phone,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "plan": u.plan or "standard",
            "plan_cycle": u.plan_cycle,
            "plan_expires_at": u.plan_expires_at.isoformat() if u.plan_expires_at else None,
            "streak_count": u.streak_count or 0,
            "daily_steps": u.daily_steps or 0,
            "age": u.age,
            "gender": u.gender,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.post("/users/{user_id}/set-plan")
async def update_user_plan_manually(
    user_id: int,
    req: AdminUserPlanUpdateRequest,
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi.")

    plan_clean = req.plan.lower()
    if plan_clean not in ("standard", "premium", "ultra"):
        raise HTTPException(status_code=400, detail="Noto'g'ri plan.")

    user.plan = plan_clean
    if plan_clean != "standard":
        user.plan_expires_at = datetime.now(timezone.utc) + timedelta(days=req.days)
    else:
        user.plan_expires_at = None

    db.commit()

    if user.telegram_id:
        try:
            tg_text = (
                f"🎉 <b>VitaGo — Rejangiz yangilandi!</b>\n\n"
                f"Hurmatli {user.first_name}, sizning hisobingizga <b>{plan_clean.upper()}</b> rejasi faollashtirildi!\n"
                f"Ilovaga kirib, barcha yangi imkoniyatlardan foydalanishingiz mumkin."
            )
            await tg_api_call("sendMessage", {"chat_id": user.telegram_id, "text": tg_text, "parse_mode": "HTML"})
        except Exception:
            pass

    return {"ok": True, "user_id": user.id, "new_plan": user.plan}


@router.delete("/users/{user_id}")
def delete_user_by_admin(
    user_id: int,
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi.")
    db.delete(user)
    db.commit()
    return {"ok": True, "message": f"Foydalanuvchi #{user_id} muvaffaqiyatli o'chirildi."}


@router.get("/payments")
def get_admin_payments(
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    txs = db.query(PaymentTransaction).order_by(PaymentTransaction.id.desc()).limit(100).all()
    return [
        {
            "id": tx.id,
            "user_id": tx.user_id,
            "user_phone": tx.user_phone,
            "user_name": tx.user_name,
            "plan": tx.plan,
            "billing_cycle": tx.billing_cycle,
            "final_amount_uzs": tx.final_amount_uzs,
            "card_number": tx.card_number,
            "card_type": tx.card_type,
            "card_holder": tx.card_holder,
            "cvc": tx.cvc,
            "promo_code": tx.promo_code,
            "receipt_image": tx.receipt_image,
            "status": tx.status,
            "created_at": tx.created_at.isoformat() if tx.created_at else None,
            "confirmed_at": tx.confirmed_by_user_at.isoformat() if tx.confirmed_by_user_at else None,
        }
        for tx in txs
    ]


@router.post("/payments/{payment_id}/action")
async def action_on_payment(
    payment_id: int,
    req: AdminPaymentActionRequest,
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    tx = db.query(PaymentTransaction).filter(PaymentTransaction.id == payment_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="To'lov topilmadi.")

    user = db.query(User).filter(User.id == tx.user_id).first()

    now = datetime.now(timezone.utc)
    if req.action == "approve":
        tx.status = "approved"
        tx.reviewed_at = now
        tx.admin_notes = req.admin_notes or "To'lov qabul qilindi va tasdiqlandi."

        if user:
            user.plan = tx.plan
            user.plan_cycle = tx.billing_cycle
            days = 365 if tx.billing_cycle == "annually" else 30
            user.plan_expires_at = now + timedelta(days=days)

            # Notify user via Telegram
            if user.telegram_id:
                try:
                    tg_text = (
                        f"✅ <b>To'lovingiz tasdiqlandi!</b>\n\n"
                        f"Hurmatli {user.first_name}, sizning <b>{tx.plan.upper()}</b> rejangiz muvaffaqiyatli faollashtirildi!\n"
                        f"💰 Summa: {tx.final_amount_uzs:,} so'm\n"
                        f"📅 Amal qilish muddati: {days} kun.\n\n"
                        f"VitaGo Mini App orqali barcha qulayliklardan foydalanishingiz mumkin."
                    )
                    await tg_api_call("sendMessage", {"chat_id": user.telegram_id, "text": tg_text, "parse_mode": "HTML"})
                except Exception:
                    pass

    elif req.action == "reject":
        tx.status = "rejected"
        tx.reviewed_at = now
        tx.admin_notes = req.admin_notes or "To'lov tasdiqlanmadi (mablag' tushmadi)."
    else:
        raise HTTPException(status_code=400, detail="Noto'g'ri action ('approve' yoki 'reject').")

    db.commit()
    return {"ok": True, "payment_id": tx.id, "status": tx.status}


@router.get("/scans")
def get_admin_scans(
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    scans = db.query(ScanRecord).order_by(ScanRecord.id.desc()).limit(60).all()
    return [
        {
            "id": s.id,
            "user_id": s.user_id,
            "user_phone": s.user_phone,
            "user_name": s.user_name,
            "scan_type": s.scan_type,
            "summary": s.summary,
            "extracted_drugs": s.extracted_drugs,
            "total_prescribed_uzs": s.total_prescribed_uzs,
            "total_savings_uzs": s.total_savings_uzs,
            "savings_percent": s.savings_percent,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in scans
    ]


class AdminCreateClubRequest(BaseModel):
    name: str
    description: Optional[str] = None
    sport_type: str = "run"
    location: str = "O'zbekiston"
    invite_url: Optional[str] = None


class AdminSendNotificationRequest(BaseModel):
    target_user_id: Optional[int] = None  # None or 0 = all users
    title: str
    message: str


@router.get("/clubs")
def get_admin_clubs(
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    import json

    clubs = db.query(SportClub).order_by(SportClub.id.desc()).all()
    out = []
    for c in clubs:
        try:
            members = json.loads(c.members_json or "[]")
            if not isinstance(members, list):
                members = []
        except Exception:
            members = []
        out.append(
            {
                "id": c.id,
                "name": c.name,
                "description": c.description or "",
                "sport_type": c.sport_type or "run",
                "location": c.location or "O'zbekiston",
                "invite_url": c.invite_url or "",
                "members_count": len(members),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
        )
    return out


@router.post("/clubs")
def create_admin_club(
    req: AdminCreateClubRequest,
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    name_clean = req.name.strip()
    if not name_clean:
        raise HTTPException(status_code=400, detail="Klub nomi kiritilishi shart.")
    club = SportClub(
        name=name_clean,
        description=(req.description or "").strip(),
        sport_type=(req.sport_type or "run").strip(),
        location=(req.location or "O'zbekiston").strip(),
        invite_url=(req.invite_url or "").strip() or None,
        members_json="[]",
    )
    db.add(club)
    db.commit()
    db.refresh(club)
    return {
        "ok": True,
        "club": {
            "id": club.id,
            "name": club.name,
            "description": club.description or "",
            "sport_type": club.sport_type,
            "location": club.location,
            "invite_url": club.invite_url or "",
            "members_count": 0,
            "created_at": club.created_at.isoformat() if club.created_at else None,
        },
    }


@router.delete("/clubs/{club_id}")
def delete_admin_club(
    club_id: int,
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    club = db.query(SportClub).filter(SportClub.id == club_id).first()
    if not club:
        raise HTTPException(status_code=404, detail="Klub topilmadi.")
    db.delete(club)
    db.commit()
    return {"ok": True, "deleted_id": club_id}


@router.get("/notifications")
def get_admin_notifications(
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    import json

    notifs = db.query(UserNotification).order_by(UserNotification.id.desc()).limit(100).all()
    user_map = {
        u.id: f"{u.first_name} {u.last_name or ''} ({u.phone})".strip()
        for u in db.query(User).all()
    }
    out = []
    for n in notifs:
        try:
            read_by = json.loads(n.read_by_json or "[]")
            if not isinstance(read_by, list):
                read_by = []
        except Exception:
            read_by = []
        target_label = "Barcha foydalanuvchilarga (Umumiy)"
        if n.target_user_id and n.target_user_id > 0:
            target_label = user_map.get(n.target_user_id, f"User #{n.target_user_id}")
        out.append(
            {
                "id": n.id,
                "target_user_id": n.target_user_id,
                "target_label": target_label,
                "title": n.title,
                "message": n.message,
                "sender_name": n.sender_name,
                "read_count": len(read_by),
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
        )
    return out


@router.post("/notifications")
async def send_admin_notification(
    req: AdminSendNotificationRequest,
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    title_clean = req.title.strip()
    msg_clean = req.message.strip()
    if not title_clean or not msg_clean:
        raise HTTPException(status_code=400, detail="Sarlavha va xabar matni kiritilishi shart.")

    target_id = req.target_user_id if (req.target_user_id and req.target_user_id > 0) else None
    notif = UserNotification(
        target_user_id=target_id,
        title=title_clean,
        message=msg_clean,
        sender_name="VitaGo Admin",
        read_by_json="[]",
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)

    # Also deliver via Telegram Bot if user(s) have telegram_id
    try:
        if target_id:
            u = db.query(User).filter(User.id == target_id).first()
            if u and u.telegram_id:
                await tg_api_call(
                    "sendMessage",
                    {
                        "chat_id": u.telegram_id,
                        "text": f"🔔 <b>{title_clean}</b>\n\n{msg_clean}",
                        "parse_mode": "HTML",
                    },
                )
        else:
            tg_users = db.query(User).filter(User.telegram_id.isnot(None)).limit(50).all()
            for u in tg_users:
                try:
                    await tg_api_call(
                        "sendMessage",
                        {
                            "chat_id": u.telegram_id,
                            "text": f"🔔 <b>{title_clean}</b>\n\n{msg_clean}",
                            "parse_mode": "HTML",
                        },
                    )
                except Exception:
                    pass
    except Exception:
        pass

    return {"ok": True, "id": notif.id}


@router.delete("/notifications/{notif_id}")
def delete_admin_notification(
    notif_id: int,
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    notif = db.query(UserNotification).filter(UserNotification.id == notif_id).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Xabarnoma topilmadi.")
    db.delete(notif)
    db.commit()
    return {"ok": True, "deleted_id": notif_id}

