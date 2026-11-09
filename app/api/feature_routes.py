from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.auth_routes import get_current_user
from app.db.database import get_db
from app.models.payment import FoodNutritionRecord, ScanRecord
from app.models.user import SportClub, User, UserNotification
from app.schemas.features import (
    AIChatRequest,
    AIChatResponse,
    AlarmMissionVerifyRequest,
    AlarmMissionVerifyResponse,
    ElderlyAnalysisRequest,
    ElderlyAnalysisResponse,
    FoodScanRequest,
    FoodScanResponse,
    FoodVerifyRequest,
    FoodVerifyResponse,
    GoalsPlanRequest,
    GoalsPlanResponse,
    HealthAnalysisRequest,
    HealthAnalysisResponse,
    WorkoutPlanRequest,
    WorkoutPlanResponse,
)
from app.services.ai_features import (
    run_ai_pharmacist_chat,
    run_ai_sport_coach,
    run_alarm_mission_verify,
    run_elderly_analysis,
    run_fitness_yoga_workout,
    run_food_scan,
    run_food_verification,
    run_goals_planning,
    run_health_assessment,
)
from app.services.telegram_bot import tg_api_call

router = APIRouter(prefix="/api/v1/features", tags=["Plan Features"])


def require_premium_or_ultra(user: User):
    if user.plan not in ("premium", "ultra"):
        raise HTTPException(
            status_code=403,
            detail="Ushbu funksiya faqat Premium va Ultra foydalanuvchilar uchun ochiq. Iltimos, rejangizni yangilang.",
        )


def require_ultra(user: User):
    if user.plan != "ultra":
        raise HTTPException(
            status_code=403,
            detail="Ushbu funksiya faqat Ultra foydalanuvchilar uchun ochiq. Iltimos, Ultra rejasiga o'ting.",
        )


@router.post("/ai-chat", response_model=AIChatResponse)
def api_ai_pharmacist_chat(
    req: AIChatRequest,
    user: User = Depends(get_current_user),
):
    require_premium_or_ultra(user)
    try:
        return run_ai_pharmacist_chat(req=req, user=user)
    except Exception as exc:
        print("api_ai_pharmacist_chat error:", exc)
        return AIChatResponse(
            reply=(
                f"Assalomu alaykum, {user.first_name}! Tetik AI farmatsevti xizmatingizda. "
                "Siz so'ragan savol yoki dori vositasi bo'yicha ma'lumot tayyor. "
                "Iltimos, dori nomini yoki alomatlarni aniqroq yozsangiz, dozasi va arzon analoglarini batafsil tushuntirib beraman."
            ),
            suggested_questions=[
                "Paratsetamol qanday qabul qilinadi?",
                "Bosh og'rig'ida qaysi dori xavfsiz?",
                "Azitromitsin arzon analoglari bormi?",
            ],
            disclaimer="Shifokor bilan maslahatlashing.",
        )


@router.post("/health-assessment", response_model=HealthAnalysisResponse)
def api_health_assessment(
    req: HealthAnalysisRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_premium_or_ultra(user)
    res = run_health_assessment(user=user, notes=req.daily_routine_notes)

    try:
        now_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        user.daily_target_steps = res.target_steps
        user.daily_water_ml = res.target_water_ml
        user.daily_kcal = res.target_calories_burn
        user.health_plan_json = res.model_dump_json()
        user.health_plan_updated_at = now_ms
        user.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        print("api_health_assessment DB save notice:", exc)

    return res


@router.post("/elderly-analysis", response_model=ElderlyAnalysisResponse)
def api_elderly_analysis(
    req: ElderlyAnalysisRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_premium_or_ultra(user)
    res = run_elderly_analysis(
        user=user,
        notes=req.notes_or_symptoms or req.audio_transcription,
        blood_img_b64=req.blood_test_image,
    )

    try:
        log = ScanRecord(
            user_id=user.id,
            user_phone=user.phone,
            user_name=f"{user.first_name} {user.last_name or ''}".strip(),
            scan_type="elderly_blood_analysis",
            image_preview=req.blood_test_image[:200] if req.blood_test_image else None,
            summary=res.summary,
            extracted_drugs=", ".join(res.suggested_safe_medicines[:4]),
            total_prescribed_uzs=0,
            total_savings_uzs=0,
            savings_percent=0,
        )
        db.add(log)
        db.commit()
    except Exception as exc:
        db.rollback()
        print("api_elderly_analysis DB log notice:", exc)

    return res


@router.post("/food-scan", response_model=FoodScanResponse)
def api_food_scan(
    req: FoodScanRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_premium_or_ultra(user)
    res = run_food_scan(
        image_b64=req.image_base64,
        portion_grams=req.portion_grams,
        user_notes=req.user_notes,
    )

    try:
        food_rec = FoodNutritionRecord(
            user_id=user.id,
            image_preview=req.image_base64[:200] if req.image_base64 else None,
            food_name=res.food_name,
            calories=res.calories,
            ingredients=", ".join(res.ingredients),
            fat_g=res.fat_g,
            carbs_g=res.carbs_g,
            protein_g=res.protein_g,
            vitamins=", ".join(res.vitamins),
            sleep_after_hours=res.sleep_after_hours,
            walk_distance_km=res.walk_distance_km,
            health_advice=res.health_advice,
        )
        db.add(food_rec)

        scan_log = ScanRecord(
            user_id=user.id,
            user_phone=user.phone,
            user_name=f"{user.first_name} {user.last_name or ''}".strip(),
            scan_type="food_nutrition",
            image_preview=req.image_base64[:200] if req.image_base64 else None,
            summary=f"{res.food_name} — {res.calories} kkal",
            extracted_drugs="",
            total_prescribed_uzs=0,
            total_savings_uzs=0,
            savings_percent=0,
        )
        db.add(scan_log)
        db.commit()
    except Exception as exc:
        db.rollback()
        print("api_food_scan DB log notice:", exc)

    return res


@router.post("/alarm/verify-mission", response_model=AlarmMissionVerifyResponse)
def api_alarm_verify_mission(
    req: AlarmMissionVerifyRequest,
    db: Session = Depends(get_db),
):
    try:
        return run_alarm_mission_verify(
            step=req.step,
            image_b64=req.image_base64,
            db=db,
            user_id=None,
        )
    except Exception as exc:
        print("Alarm mission verification error caught:", exc)
        return AlarmMissionVerifyResponse(
            step=req.step,
            passed=False,
            next_step=req.step,
            message="Tasvir qabul qilinmadi. Iltimos, kamerani rakvina yoki vazifa obyektiga to'g'irlab qaytadan oling!",
            mission_completed=False,
        )


def _advance_user_streak_today(user: User) -> None:
    today_dt = datetime.now(timezone.utc).date()
    today_str = today_dt.strftime("%Y-%m-%d")
    last_str = getattr(user, "streak_last_date", None)
    if last_str == today_str:
        return
    if last_str:
        try:
            last_dt = datetime.strptime(last_str[:10], "%Y-%m-%d").date()
            diff_days = (today_dt - last_dt).days
            if diff_days == 1:
                user.streak_count = (user.streak_count or 0) + 1
            elif diff_days > 1:
                user.streak_count = 1
        except Exception:
            user.streak_count = (user.streak_count or 0) + 1
    else:
        user.streak_count = max(1, user.streak_count or 0)
    user.streak_last_date = today_str


@router.post("/activity/log")
def api_log_activity(
    steps: int,
    water_ml: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.daily_steps = max(user.daily_steps or 0, max(0, steps))
    if water_ml > 0:
        user.daily_water_ml = max(0, water_ml)

    target = user.daily_target_steps or 6000

    if user.daily_steps >= target:
        _advance_user_streak_today(user)
    else:
        last_str = getattr(user, "streak_last_date", None)
        if last_str:
            try:
                last_dt = datetime.strptime(last_str[:10], "%Y-%m-%d").date()
                if (datetime.now(timezone.utc).date() - last_dt).days > 1:
                    user.streak_count = 0
            except Exception:
                pass

    try:
        db.commit()
        db.refresh(user)
    except Exception:
        db.rollback()

    return {
        "ok": True,
        "daily_steps": user.daily_steps,
        "target_steps": target,
        "streak_count": user.streak_count,
        "streak_active": user.daily_steps >= target,
    }


@router.post("/sport/session-start")
async def api_sport_session_start(
    payload: dict,
    user: User = Depends(get_current_user),
):
    require_ultra(user)
    sport_name = payload.get("sport_name", "Piyoda yurish")
    location_name = payload.get("location_name", "O'zbekiston")
    dest_km = payload.get("dest_distance_km")

    if user.telegram_id:
        try:
            dest_line = f"\n🧭 Manzilgacha masofa: <b>{dest_km} km</b>" if dest_km else ""
            tg_text = (
                f"🏃‍♂️⚡ <b>Tetik Sport — GPS Navigator va Mashg'ulot Faol!</b>\n\n"
                f"🔹 Yo'nalish: <b>{sport_name}</b>\n"
                f"📍 Joylashuv: <b>{location_name}</b>{dest_line}\n\n"
                f"✅ Asosiy bo'limga o'tsangiz yoki ilovani yopsangiz ham GPS Navigator telefoningizda faol ishlab turibdi!"
            )
            await tg_api_call(
                "sendMessage",
                {"chat_id": user.telegram_id, "text": tg_text, "parse_mode": "HTML"},
            )
        except Exception:
            pass

    return {"ok": True}


@router.post("/sport/save-activity")
async def api_save_sport_activity(
    payload: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_ultra(user)
    import json

    existing = []
    if getattr(user, "sport_activities_json", None):
        try:
            existing = json.loads(user.sport_activities_json)
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    new_act = {
        "id": payload.get("id") or f"act_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "sport_type": payload.get("sport_type", "Piyoda yurish"),
        "title": payload.get("title", "Mashg'ulot"),
        "location_name": payload.get("location_name", "O'zbekiston"),
        "distance_km": float(payload.get("distance_km", 0.0)),
        "elev_gain_m": int(payload.get("elev_gain_m", 0)),
        "duration_sec": int(payload.get("duration_sec", 0)),
        "calories": int(payload.get("calories", 0)),
        "kudos": int(payload.get("kudos", 1)),
        "created_at": payload.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "route_coords": payload.get("route_coords", []),
    }
    existing.insert(0, new_act)
    user.sport_activities_json = json.dumps(existing[:50], ensure_ascii=False)

    # Also update user steps & streak
    added_steps = int(new_act["distance_km"] * 1312)
    if added_steps > 0:
        user.daily_steps = (user.daily_steps or 0) + added_steps
    if new_act["distance_km"] >= 0.02:
        _advance_user_streak_today(user)

    try:
        scan_log = ScanRecord(
            user_id=user.id,
            user_phone=user.phone,
            user_name=f"{user.first_name} {user.last_name or ''}".strip(),
            scan_type="sport_activity",
            image_preview=None,
            summary=f"Tetik Sport: {new_act['title']} ({new_act['sport_type']}) — {new_act['distance_km']:.2f} km, {new_act['duration_sec']}s",
            extracted_drugs="",
            total_prescribed_uzs=0,
            total_savings_uzs=0,
            savings_percent=0,
        )
        db.add(scan_log)
        db.commit()
        db.refresh(user)
    except Exception:
        db.rollback()

    if user.telegram_id:
        try:
            tg_text = (
                f"🏁 <b>Tetik ⚡ Sport — Mashg'ulot yakunlandi!</b>\n\n"
                f"🏃 Yo'nalish: <b>{new_act['title']}</b>\n"
                f"📏 Masofa: <b>{new_act['distance_km']:.2f} km</b>\n"
                f"⏱ Vaqt: <b>{new_act['duration_sec']} soniya</b>\n"
                f"🔥 Sarflangan energiya: <b>{new_act['calories']} kkal</b>\n"
                f"🔥 Ketma-ket faollik (Streak): <b>{user.streak_count} kun</b>"
            )
            await tg_api_call(
                "sendMessage",
                {"chat_id": user.telegram_id, "text": tg_text, "parse_mode": "HTML"},
            )
        except Exception:
            pass

    return {
        "ok": True,
        "activity": new_act,
        "sport_activities_json": user.sport_activities_json,
        "daily_steps": user.daily_steps,
        "streak_count": user.streak_count,
    }


@router.get("/sport/leaderboard")
def api_sport_leaderboard(
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.daily_steps.desc()).limit(20).all()
    board = []
    for idx, u in enumerate(users, 1):
        board.append(
            {
                "rank": idx,
                "name": f"{u.first_name} {u.last_name or ''}".strip(),
                "daily_steps": u.daily_steps or 0,
                "streak_count": u.streak_count or 0,
                "distance_km": round((u.daily_steps or 0) / 1312.0, 2),
                "plan": u.plan or "standard",
            }
        )
    return {"ok": True, "leaderboard": board}


@router.get("/sport/clubs")
def api_get_sport_clubs(
    user: User = Depends(get_current_user),
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
                "is_joined": user.id in members,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
        )
    return {"ok": True, "clubs": out}


@router.post("/sport/clubs/{club_id}/toggle-join")
def api_toggle_join_sport_club(
    club_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_ultra(user)
    import json

    club = db.query(SportClub).filter(SportClub.id == club_id).first()
    if not club:
        raise HTTPException(status_code=404, detail="Klub topilmadi.")
    try:
        members = json.loads(club.members_json or "[]")
        if not isinstance(members, list):
            members = []
    except Exception:
        members = []

    if user.id in members:
        members = [m for m in members if m != user.id]
        joined = False
    else:
        members.append(user.id)
        joined = True

    club.members_json = json.dumps(members)
    db.commit()
    return {
        "ok": True,
        "club_id": club.id,
        "is_joined": joined,
        "members_count": len(members),
    }


@router.get("/sport/users")
def api_search_sport_users(
    q: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import json

    # Ultra plan users who use Tetik Sport
    query_str = q.strip().lower()
    ultra_users = db.query(User).filter(User.plan == "ultra").order_by(User.daily_steps.desc()).all()
    results = []
    for u in ultra_users:
        full_name = f"{u.first_name} {u.last_name or ''}".strip()
        if query_str and query_str not in full_name.lower() and query_str not in (u.phone or "").lower():
            continue
        acts = []
        if getattr(u, "sport_activities_json", None):
            try:
                acts = json.loads(u.sport_activities_json)
                if not isinstance(acts, list):
                    acts = []
            except Exception:
                acts = []
        total_km = round(sum(float(a.get("distance_km", 0.0)) for a in acts), 2)
        total_kcal = sum(int(a.get("calories", 0)) for a in acts)
        results.append(
            {
                "id": u.id,
                "name": full_name,
                "avatar_data": u.avatar_data,
                "plan": u.plan or "ultra",
                "daily_steps": u.daily_steps or 0,
                "streak_count": u.streak_count or 0,
                "activities_count": len(acts),
                "total_distance_km": total_km,
                "total_calories": total_kcal,
                "last_activity": acts[0] if acts else None,
            }
        )
    return {"ok": True, "users": results}


@router.get("/sport/notifications")
def api_get_user_notifications(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import json

    notifs = (
        db.query(UserNotification)
        .filter((UserNotification.target_user_id.is_(None)) | (UserNotification.target_user_id == 0) | (UserNotification.target_user_id == user.id))
        .order_by(UserNotification.id.desc())
        .limit(50)
        .all()
    )
    items = []
    unread_count = 0
    for n in notifs:
        try:
            read_by = json.loads(n.read_by_json or "[]")
            if not isinstance(read_by, list):
                read_by = []
        except Exception:
            read_by = []
        is_read = user.id in read_by
        if not is_read:
            unread_count += 1
        items.append(
            {
                "id": n.id,
                "title": n.title,
                "message": n.message,
                "sender_name": n.sender_name or "Tetik Admin",
                "is_target_personal": bool(n.target_user_id and n.target_user_id == user.id),
                "is_read": is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
        )
    return {"ok": True, "notifications": items, "unread_count": unread_count}


@router.post("/sport/notifications/mark-read")
def api_mark_notifications_read(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import json

    notifs = (
        db.query(UserNotification)
        .filter((UserNotification.target_user_id.is_(None)) | (UserNotification.target_user_id == 0) | (UserNotification.target_user_id == user.id))
        .order_by(UserNotification.id.desc())
        .limit(50)
        .all()
    )
    for n in notifs:
        try:
            read_by = json.loads(n.read_by_json or "[]")
            if not isinstance(read_by, list):
                read_by = []
        except Exception:
            read_by = []
        if user.id not in read_by:
            read_by.append(user.id)
            n.read_by_json = json.dumps(read_by)
    db.commit()
    return {"ok": True}


@router.post("/sport/ai-coach")
async def api_sport_ai_coach(
    payload: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_ultra(user)
    import json

    lang = payload.get("lang", "uz")
    user_message = (payload.get("message") or "").strip()
    selected_sport = payload.get("selected_sport", "run")

    acts = []
    if getattr(user, "sport_activities_json", None):
        try:
            acts = json.loads(user.sport_activities_json)
            if not isinstance(acts, list):
                acts = []
        except Exception:
            acts = []

    total_km = round(sum(float(a.get("distance_km", 0.0)) for a in acts[:5]), 2)
    total_kcal = sum(int(a.get("calories", 0)) for a in acts[:5])
    total_sec = sum(int(a.get("duration_sec", 0)) for a in acts[:5])
    latest_act_summary = "Hali mashg'ulot yozilmagan"
    if acts:
        a0 = acts[0]
        latest_act_summary = (
            f"Oxirgi mashg'ulot: {a0.get('title')} ({a0.get('sport_type')}), "
            f"masofa: {a0.get('distance_km')} km, vaqt: {a0.get('duration_sec')} soniya, "
            f"sarflangan energiya: {a0.get('calories')} kkal."
        )

    context_prompt = (
        f"Sen Tetik ⚡ Sport bo'limining shaxsiy AI Sport va Ovqatlanish murabbiyisan.\n"
        f"Foydalanuvchi ma'lumotlari: Ismi: {user.first_name}, Yoshi: {user.age or 25}, "
        f"Bo'yi: {user.height_cm or 172} sm, Vazni: {user.weight_kg or 68} kg, "
        f"Bugungi qadamlari: {user.daily_steps or 0} qadam, Tanlangan sport turi: {selected_sport}.\n"
        f"{latest_act_summary}\n"
        f"Oxirgi mashg'ulotlarda jami masofa: {total_km} km, jami sarflangan kaloriya: {total_kcal} kkal ({total_sec} soniya).\n"
        f"VAZIFANG: Foydalanuvchining haqiqiy yugurgan/yurgan/velosiped haydagan masofasi va sarflagan kaloriyasini tahlil qilib, "
        f"unga HOZIR qancha kaloriya (kkal), qancha oqsil (gramm), uglevod (gramm), suv (ml) va aynan qanday foydali taomlar yeyishi kerakligini "
        f"aniq hisob-kitob bilan tushuntirib ber.\n"
        f"Foydalanuvchi savoli: {user_message or 'Mening sport ko`rsatkichlarimni tahlil qilib, qancha va nima ovqat yeyishim kerakligini aytib bering.'}"
    )

    reply_text = await run_in_threadpool(run_ai_sport_coach, context_prompt, user, lang)
    return {"ok": True, "reply": reply_text}


@router.post("/workout-plan", response_model=WorkoutPlanResponse)
async def api_workout_plan(
    req: WorkoutPlanRequest,
    user: User = Depends(get_current_user),
):
    """
    Dedicated AI Agent for Fitness & Yoga workouts.
    Customized by user age, health conditions and physical constraints.
    """
    return await run_in_threadpool(run_fitness_yoga_workout, req, user)


@router.post("/goals-plan", response_model=GoalsPlanResponse)
async def api_goals_plan(
    req: GoalsPlanRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Dedicated AI Goal Planner (Maqsadlarim).
    Analyzes weekly weight loss targets, daily steps and health limitations.
    Updates the user's daily targets and routine.
    """
    res = await run_in_threadpool(run_goals_planning, req, user)

    # Persist updated targets to user profile
    try:
        user.daily_target_steps = res.recommended_daily_steps
        user.daily_water_ml = res.recommended_daily_water_ml
        user.daily_kcal = res.recommended_daily_burn_kcal
        db.commit()
    except Exception as exc:
        db.rollback()
        print("api_goals_plan persist warning:", exc)

    return res


@router.post("/food-verify", response_model=FoodVerifyResponse)
async def api_food_verify(
    req: FoodVerifyRequest,
    user: User = Depends(get_current_user),
):
    """
    Dedicated AI Murabbiy (Food Verification & Anti-Weight Gain).
    Strictly verifies meal photos, warns about carbs/fats causing obesity,
    and advises healthier alternatives.
    Gated to Premium and Ultra users.
    """
    require_premium_or_ultra(user)
    return await run_in_threadpool(run_food_verification, req, user)


