from datetime import datetime, timezone
import html
import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.admin_routes import verify_admin_token
from app.api.auth_routes import get_current_user
from app.db.database import get_db
from app.models.user import ScheduledNotification, User, UserNotification
from app.services.telegram_bot import tg_api_call

router = APIRouter(prefix="/api/v1/notifications", tags=["Smart Notifications & Reminders"])


class ScheduleReminderRequest(BaseModel):
    category: str = Field("health", description="'health' | 'sport' | 'general'")
    title: str = Field(..., max_length=200)
    message: Optional[str] = Field(None, description="Optional custom message. If omitted, neutral safe wording is used.")
    scheduled_for: datetime
    delivery_channel: str = Field("both", description="'telegram' | 'in_app' | 'both'")


def _is_in_quiet_hours(now_dt: datetime, start_str: str, end_str: str) -> bool:
    """Checks whether the current time falls inside the user's quiet hours."""
    try:
        cur_hm = now_dt.strftime("%H:%M")
        if start_str < end_str:
            return start_str <= cur_hm < end_str
        else:  # e.g. 22:00 to 08:00
            return cur_hm >= start_str or cur_hm < end_str
    except Exception:
        return False


@router.post("/schedule-reminder")
def schedule_reminder(
    req: ScheduleReminderRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Schedules an opt-in reminder for health, medication, or sport training.
    Uses privacy-safe neutral wording by default.
    """
    cat = req.category.strip().lower()
    if cat not in ("health", "sport", "general"):
        cat = "health"

    chan = req.delivery_channel.strip().lower()
    if chan not in ("telegram", "in_app", "both"):
        chan = "both"

    # Default neutral message if not detailed or omitted
    msg = req.message.strip() if req.message else ""
    if not msg:
        if cat == "sport":
            msg = "VitaGo: Bugungi sport mashg'uloti vaqti keldi! Faollikni davom ettiramiz."
        elif cat == "health":
            msg = "VitaGo: Siz uchun belgilangan salomatlik eslatmasi vaqti keldi."
        else:
            msg = "VitaGo: Siz uchun yangi muhim bildirishnoma mavjud."

    # If user has detailed disabled, keep neutral wording for privacy
    if not getattr(user, "notify_detailed", False) and cat == "health":
        msg = "VitaGo: Siz uchun belgilangan salomatlik eslatmasi vaqti keldi."

    scheduled_dt = req.scheduled_for
    if scheduled_dt.tzinfo is None:
        scheduled_dt = scheduled_dt.replace(tzinfo=timezone.utc)

    rec = ScheduledNotification(
        user_id=user.id,
        category=cat,
        title=req.title.strip()[:200],
        message=msg,
        scheduled_for=scheduled_dt,
        delivery_channel=chan,
        status="pending",
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    return {
        "ok": True,
        "reminder_id": rec.id,
        "category": rec.category,
        "scheduled_for": rec.scheduled_for.isoformat(),
        "delivery_channel": rec.delivery_channel,
        "status": rec.status,
    }


@router.get("/my-reminders")
def list_my_reminders(
    limit: int = Query(20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns recent scheduled and delivered reminders for the current user.
    """
    reminders = (
        db.query(ScheduledNotification)
        .filter(ScheduledNotification.user_id == user.id)
        .order_by(ScheduledNotification.scheduled_for.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "id": r.id,
            "category": r.category,
            "title": r.title,
            "message": r.message,
            "scheduled_for": r.scheduled_for.isoformat(),
            "delivery_channel": r.delivery_channel,
            "status": r.status,
            "delivered_at": r.delivered_at.isoformat() if r.delivered_at else None,
            "created_at": r.created_at.isoformat(),
        }
        for r in reminders
    ]


@router.delete("/reminders/{reminder_id}")
def cancel_reminder(
    reminder_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Cancels a pending reminder.
    """
    rec = (
        db.query(ScheduledNotification)
        .filter(
            ScheduledNotification.id == reminder_id,
            ScheduledNotification.user_id == user.id,
        )
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Eslatma topilmadi.")

    rec.status = "cancelled"
    db.commit()
    return {"ok": True, "message": "Eslatma bekor qilindi."}


@router.post("/dispatch-due")
async def dispatch_due_notifications(db: Session = Depends(get_db)):
    """
    Processes due scheduled reminders (scheduled_for <= now, status == 'pending').
    Respects user opt-ins, quiet hours, and channel configurations.
    Delivers to Telegram and/or In-App notification feed.
    """
    now_utc = datetime.now(timezone.utc)
    due_items = (
        db.query(ScheduledNotification)
        .filter(
            ScheduledNotification.status == "pending",
            ScheduledNotification.scheduled_for <= now_utc,
        )
        .limit(30)
        .all()
    )

    delivered_count = 0
    delayed_quiet_hours = 0
    failed_count = 0

    for item in due_items:
        user = db.query(User).filter(User.id == item.user_id).first()
        if not user:
            item.status = "failed"
            item.last_error = "User not found"
            continue

        # 1. Check Category Opt-In
        if item.category == "health" and not getattr(user, "notify_health", True):
            item.status = "cancelled"
            item.last_error = "User opted out of health reminders"
            continue
        if item.category == "sport" and not getattr(user, "notify_sport", True):
            item.status = "cancelled"
            item.last_error = "User opted out of sport reminders"
            continue
        if item.category == "general" and not getattr(user, "notify_general", True):
            item.status = "cancelled"
            item.last_error = "User opted out of general notices"
            continue

        # 2. Check Quiet Hours
        q_start = getattr(user, "notify_quiet_hours_start", "22:00") or "22:00"
        q_end = getattr(user, "notify_quiet_hours_end", "08:00") or "08:00"
        if _is_in_quiet_hours(now_utc, q_start, q_end):
            # Defer until quiet hours finish (do not disturb user)
            delayed_quiet_hours += 1
            continue

        # 3. Delivery
        chan = item.delivery_channel or "both"
        tg_success = True
        in_app_success = True

        # Telegram Delivery
        if chan in ("telegram", "both"):
            if user.telegram_id:
                tg_text = f"🔔 <b>{html.escape(item.title)}</b>\n\n{html.escape(item.message)}"
                try:
                    res = await tg_api_call("sendMessage", {"chat_id": user.telegram_id, "text": tg_text, "parse_mode": "HTML"})
                    if not (res and res.get("ok")):
                        tg_success = False
                        item.last_error = f"Telegram error: {res}"
                except Exception as ex:
                    tg_success = False
                    item.last_error = f"Telegram exception: {str(ex)[:120]}"
            else:
                tg_success = False
                item.last_error = "Telegram account not linked"

        # In-App Delivery
        if chan in ("in_app", "both") and getattr(user, "notify_in_app", True):
            try:
                notif = UserNotification(
                    target_user_id=user.id,
                    title=item.title,
                    message=item.message,
                    sender_name="VitaGo Eslatma",
                    read_by_json="[]",
                )
                db.add(notif)
            except Exception as ex:
                in_app_success = False
                item.last_error = f"In-app save exception: {str(ex)[:120]}"

        # Evaluate outcome
        if tg_success or (chan == "in_app" and in_app_success):
            item.status = "delivered"
            item.delivered_at = now_utc
            delivered_count += 1
        else:
            item.retry_count = (item.retry_count or 0) + 1
            if item.retry_count >= 3:
                item.status = "failed"
                failed_count += 1

    db.commit()
    return {
        "ok": True,
        "processed": len(due_items),
        "delivered": delivered_count,
        "delayed_quiet_hours": delayed_quiet_hours,
        "failed": failed_count,
    }


@router.get("/admin-stats")
def get_admin_notification_stats(
    _admin: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    """
    Returns administrative operational metrics of notification reminders.
    Strictly NO patient medical text or sensitive private content exposed.
    """
    total_users = db.query(func.count(User.id)).scalar() or 0
    connected_tg = db.query(func.count(User.id)).filter(User.telegram_id.isnot(None)).scalar() or 0

    scheduled_total = db.query(func.count(ScheduledNotification.id)).scalar() or 0
    pending_total = db.query(func.count(ScheduledNotification.id)).filter(ScheduledNotification.status == "pending").scalar() or 0
    delivered_total = db.query(func.count(ScheduledNotification.id)).filter(ScheduledNotification.status == "delivered").scalar() or 0
    failed_total = db.query(func.count(ScheduledNotification.id)).filter(ScheduledNotification.status == "failed").scalar() or 0

    # Category breakdown
    cat_rows = (
        db.query(ScheduledNotification.category, func.count(ScheduledNotification.id))
        .group_by(ScheduledNotification.category)
        .all()
    )
    categories = {row[0]: row[1] for row in cat_rows}

    # Delivery channel breakdown
    chan_rows = (
        db.query(ScheduledNotification.delivery_channel, func.count(ScheduledNotification.id))
        .group_by(ScheduledNotification.delivery_channel)
        .all()
    )
    channels = {row[0]: row[1] for row in chan_rows}

    # Opt-outs
    health_optouts = db.query(func.count(User.id)).filter(User.notify_health == False).scalar() or 0  # noqa: E712
    sport_optouts = db.query(func.count(User.id)).filter(User.notify_sport == False).scalar() or 0  # noqa: E712

    return {
        "connected_telegram_accounts": connected_tg,
        "total_registered_users": total_users,
        "telegram_adoption_rate_percent": round((connected_tg / max(1, total_users)) * 100, 1),
        "total_scheduled": scheduled_total,
        "pending": pending_total,
        "delivered": delivered_total,
        "failed": failed_total,
        "categories": categories,
        "delivery_channels": channels,
        "opt_outs": {
            "health_reminders": health_optouts,
            "sport_reminders": sport_optouts,
        },
    }
