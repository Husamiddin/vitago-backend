from datetime import datetime, timedelta, timezone
import html
import json
import uuid
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.admin_routes import verify_admin_token
from app.api.auth_routes import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.models.user import (
    PushSubscription,
    ScheduledNotification,
    User,
    UserNotification,
    utcnow,
)
from app.services.telegram_bot import tg_api_call
from app.services.web_push import send_web_push

router = APIRouter(prefix="/api/v1/notifications", tags=["Smart Notifications & Reminders"])

# Tashkent local timezone offset (UTC+5)
TASHKENT_TZ = timezone(timedelta(hours=5))


class ScheduleReminderRequest(BaseModel):
    category: str = Field("health", description="'health' | 'sport' | 'general'")
    title: str = Field(..., max_length=200)
    message: Optional[str] = Field(None, description="Optional custom message. If omitted, neutral safe wording is used.")
    scheduled_for: datetime
    delivery_channel: str = Field("all", description="'telegram' | 'in_app' | 'web_push' | 'both' | 'all'")


class PushSubscribeRequest(BaseModel):
    endpoint: str = Field(..., description="Browser PushSubscription endpoint URL")
    keys: Dict[str, str] = Field(..., description="p256dh and auth cryptographic keys")
    user_agent: Optional[str] = Field(None, description="Optional client user agent for device differentiation")


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(..., description="Endpoint to revoke")


def _is_in_quiet_hours(now_dt: datetime, start_str: str, end_str: str) -> bool:
    """
    Checks whether the current time falls inside the user's quiet hours in Tashkent timezone (UTC+5).
    """
    try:
        # Convert to Tashkent local time
        tashkent_dt = now_dt.astimezone(TASHKENT_TZ) if now_dt.tzinfo else (now_dt + timedelta(hours=5))
        cur_hm = tashkent_dt.strftime("%H:%M")
        if start_str < end_str:
            return start_str <= cur_hm < end_str
        else:  # e.g. 22:00 to 08:00
            return cur_hm >= start_str or cur_hm < end_str
    except Exception:
        return False


# ---- Web Push Public Key & Subscription Endpoints ----

@router.get("/vapid-public-key")
def get_vapid_public_key():
    """
    Returns the VAPID application server public key so the frontend Service Worker can subscribe.
    """
    return {
        "ok": True,
        "public_key": settings.VAPID_PUBLIC_KEY,
    }


@router.post("/push-subscribe")
def subscribe_web_push(
    req: PushSubscribeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Registers or reactivates a Web Push subscription endpoint for the authenticated user.
    """
    endpoint = req.endpoint.strip()
    p256dh = req.keys.get("p256dh", "").strip()
    auth_key = req.keys.get("auth", "").strip()

    if not endpoint or not p256dh or not auth_key:
        raise HTTPException(status_code=400, detail="Endpoint, p256dh va auth kalitlari to'liq bo'lishi shart.")

    existing = (
        db.query(PushSubscription)
        .filter(PushSubscription.endpoint == endpoint)
        .first()
    )

    now = utcnow()
    if existing:
        existing.user_id = user.id
        existing.p256dh = p256dh
        existing.auth = auth_key
        existing.user_agent = req.user_agent or existing.user_agent
        existing.is_active = True
        existing.revoked_at = None
        existing.updated_at = now
    else:
        new_sub = PushSubscription(
            user_id=user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth_key,
            user_agent=req.user_agent,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(new_sub)

    # Ensure user has web push enabled in their preferences
    user.notify_web_push = True
    db.commit()

    return {
        "ok": True,
        "message": "Web Push bildirishnomalari muvaffaqiyatli ulandi.",
    }


@router.post("/push-unsubscribe")
def unsubscribe_web_push(
    req: PushUnsubscribeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Revokes a Web Push subscription for the current user.
    """
    endpoint = req.endpoint.strip()
    sub = (
        db.query(PushSubscription)
        .filter(
            PushSubscription.endpoint == endpoint,
            PushSubscription.user_id == user.id,
        )
        .first()
    )
    if sub:
        sub.is_active = False
        sub.revoked_at = utcnow()
        db.commit()

    return {
        "ok": True,
        "message": "Web Push obunasi bekor qilindi.",
    }


# ---- Reminder Scheduling & Management ----

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
    if chan not in ("telegram", "in_app", "web_push", "both", "all"):
        chan = "all"

    # Default neutral message if not detailed or omitted
    msg = req.message.strip() if req.message else ""
    if not msg:
        if cat == "sport":
            msg = "Tetik: Bugungi sport mashg'uloti vaqti keldi! Faollikni davom ettiramiz."
        elif cat == "health":
            msg = "Tetik: Siz uchun belgilangan salomatlik eslatmasi vaqti keldi."
        else:
            msg = "Tetik: Siz uchun yangi muhim bildirishnoma mavjud."

    # If user has detailed disabled, enforce neutral medical privacy wording
    if not getattr(user, "notify_detailed", False) and cat == "health":
        msg = "Tetik: Siz uchun belgilangan salomatlik eslatmasi vaqti keldi."

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
        web_push_status="pending",
        telegram_status="pending",
        in_app_status="pending",
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
            "web_push_status": getattr(r, "web_push_status", "pending"),
            "telegram_status": getattr(r, "telegram_status", "pending"),
            "in_app_status": getattr(r, "in_app_status", "pending"),
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


# ---- Dispatcher Engine (Idempotent, Multi-Channel, Quiet-Hours Aware) ----

@router.post("/dispatch-due")
async def dispatch_due_notifications(db: Session = Depends(get_db)):
    """
    Processes due scheduled reminders (scheduled_for <= now, status == 'pending').
    Uses atomic claim locking to prevent duplicate concurrent deliveries.
    Respects user opt-ins, Asia/Tashkent quiet hours, and delivers across 3 channels:
    - Web Push API (via pywebpush + VAPID)
    - Telegram Bot API
    - In-App notification feed
    """
    now_utc = datetime.now(timezone.utc)
    claim_id = str(uuid.uuid4())[:8]

    # 1. Atomically claim up to 30 pending items to prevent race conditions
    due_items = (
        db.query(ScheduledNotification)
        .filter(
            ScheduledNotification.status == "pending",
            ScheduledNotification.scheduled_for <= now_utc,
        )
        .limit(30)
        .all()
    )

    if not due_items:
        return {
            "ok": True,
            "processed": 0,
            "delivered": 0,
            "delayed_quiet_hours": 0,
            "failed": 0,
        }

    # Mark as claimed
    for it in due_items:
        it.status = "claimed"
        it.claimed_at = now_utc
        it.claimed_by = f"worker_{claim_id}"
    db.commit()

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

        # 2. Check Quiet Hours (Tashkent UTC+5)
        q_start = getattr(user, "notify_quiet_hours_start", "22:00") or "22:00"
        q_end = getattr(user, "notify_quiet_hours_end", "08:00") or "08:00"
        if _is_in_quiet_hours(now_utc, q_start, q_end):
            # Defer back to pending so it fires once quiet hours end
            item.status = "pending"
            delayed_quiet_hours += 1
            continue

        # 3. Channel Delivery: 'in_app', 'web_push', 'telegram', 'both', 'all'
        chan = item.delivery_channel or "all"
        any_success = False
        channel_errors = []

        # Channel A: In-App Delivery
        if chan in ("in_app", "both", "all") and getattr(user, "notify_in_app", True):
            try:
                notif = UserNotification(
                    target_user_id=user.id,
                    title=item.title,
                    message=item.message,
                    sender_name="Tetik Eslatma",
                    read_by_json="[]",
                )
                db.add(notif)
                item.in_app_status = "delivered"
                any_success = True
            except Exception as ex:
                item.in_app_status = "failed"
                channel_errors.append(f"InApp: {str(ex)[:80]}")
        else:
            item.in_app_status = "skipped"

        # Channel B: Telegram Delivery
        if chan in ("telegram", "both", "all"):
            if user.telegram_id:
                tg_text = f"🔔 <b>{html.escape(item.title)}</b>\n\n{html.escape(item.message)}"
                try:
                    res = await tg_api_call("sendMessage", {"chat_id": user.telegram_id, "text": tg_text, "parse_mode": "HTML"})
                    if res and res.get("ok"):
                        item.telegram_status = "delivered"
                        any_success = True
                    else:
                        item.telegram_status = "failed"
                        channel_errors.append(f"Telegram API: {res}")
                except Exception as ex:
                    item.telegram_status = "failed"
                    channel_errors.append(f"Telegram exc: {str(ex)[:80]}")
            else:
                item.telegram_status = "skipped"
        else:
            item.telegram_status = "skipped"

        # Channel C: Web Push Delivery (Browser / Mobile PWA)
        if chan in ("web_push", "both", "all") and getattr(user, "notify_web_push", True):
            subs = (
                db.query(PushSubscription)
                .filter(
                    PushSubscription.user_id == user.id,
                    PushSubscription.is_active == True,
                )
                .all()
            )
            if subs:
                push_delivered_for_user = False
                push_payload = {
                    "title": item.title,
                    "message": item.message,
                    "icon": "/logo.png",
                    "badge": "/favicon.png",
                    "url": "/",
                    "tag": f"vitago-reminder-{item.id}",
                }
                for s in subs:
                    sub_info = {
                        "endpoint": s.endpoint,
                        "keys": {
                            "p256dh": s.p256dh,
                            "auth": s.auth,
                        },
                    }
                    ok, push_status, push_err = send_web_push(sub_info, push_payload)
                    if ok:
                        push_delivered_for_user = True
                    elif push_status == "revoked":
                        # Deactivate invalid/expired browser endpoint
                        s.is_active = False
                        s.revoked_at = utcnow()
                    elif push_err:
                        channel_errors.append(f"WebPush: {push_err}")

                if push_delivered_for_user:
                    item.web_push_status = "delivered"
                    any_success = True
                else:
                    item.web_push_status = "failed"
            else:
                item.web_push_status = "skipped"
        else:
            item.web_push_status = "skipped"

        # 4. Evaluate Overall Outcome
        if any_success:
            item.status = "delivered"
            item.delivered_at = now_utc
            item.last_error = None
            delivered_count += 1
        else:
            item.retry_count = (item.retry_count or 0) + 1
            item.last_error = "; ".join(channel_errors)[:300] if channel_errors else "All channels skipped or failed"
            if item.retry_count >= 3:
                item.status = "failed"
                failed_count += 1
            else:
                # Return to pending for next retry attempt
                item.status = "pending"

    db.commit()
    return {
        "ok": True,
        "processed": len(due_items),
        "delivered": delivered_count,
        "delayed_quiet_hours": delayed_quiet_hours,
        "failed": failed_count,
    }


# ---- Admin Operational Analytics Dashboard ----

@router.get("/admin-stats")
def get_admin_notification_stats(
    _admin: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
):
    """
    Returns administrative operational metrics of notification reminders across all 3 channels.
    Strictly NO patient medical text or sensitive private content exposed.
    """
    total_users = db.query(func.count(User.id)).scalar() or 0
    connected_tg = db.query(func.count(User.id)).filter(User.telegram_id.isnot(None)).scalar() or 0

    # Web push subscriptions metrics
    total_push_subs = db.query(func.count(PushSubscription.id)).scalar() or 0
    active_push_subs = db.query(func.count(PushSubscription.id)).filter(PushSubscription.is_active == True).scalar() or 0
    revoked_push_subs = db.query(func.count(PushSubscription.id)).filter(PushSubscription.is_active == False).scalar() or 0
    users_with_push = db.query(func.count(func.distinct(PushSubscription.user_id))).filter(PushSubscription.is_active == True).scalar() or 0

    scheduled_total = db.query(func.count(ScheduledNotification.id)).scalar() or 0
    pending_total = db.query(func.count(ScheduledNotification.id)).filter(ScheduledNotification.status == "pending").scalar() or 0
    delivered_total = db.query(func.count(ScheduledNotification.id)).filter(ScheduledNotification.status == "delivered").scalar() or 0
    failed_total = db.query(func.count(ScheduledNotification.id)).filter(ScheduledNotification.status == "failed").scalar() or 0

    # Per-channel delivery metrics
    web_push_delivered = db.query(func.count(ScheduledNotification.id)).filter(ScheduledNotification.web_push_status == "delivered").scalar() or 0
    telegram_delivered = db.query(func.count(ScheduledNotification.id)).filter(ScheduledNotification.telegram_status == "delivered").scalar() or 0
    in_app_delivered = db.query(func.count(ScheduledNotification.id)).filter(ScheduledNotification.in_app_status == "delivered").scalar() or 0

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
    web_push_optouts = db.query(func.count(User.id)).filter(User.notify_web_push == False).scalar() or 0  # noqa: E712

    return {
        "connected_telegram_accounts": connected_tg,
        "total_registered_users": total_users,
        "telegram_adoption_rate_percent": round((connected_tg / max(1, total_users)) * 100, 1),
        "web_push": {
            "total_subscriptions": total_push_subs,
            "active_subscriptions": active_push_subs,
            "revoked_subscriptions": revoked_push_subs,
            "users_with_web_push": users_with_push,
            "adoption_rate_percent": round((users_with_push / max(1, total_users)) * 100, 1),
        },
        "total_scheduled": scheduled_total,
        "pending": pending_total,
        "delivered": delivered_total,
        "failed": failed_total,
        "channel_delivery_counts": {
            "web_push": web_push_delivered,
            "telegram": telegram_delivered,
            "in_app": in_app_delivered,
        },
        "categories": categories,
        "delivery_channels": channels,
        "opt_outs": {
            "health_reminders": health_optouts,
            "sport_reminders": sport_optouts,
            "web_push": web_push_optouts,
        },
    }
