import base64
import json
import re
from typing import Optional

from google import genai
from google.genai import types

from app.core.config import settings
from app.models.user import User
from app.schemas.features import (
    AIChatRequest,
    AIChatResponse,
    AlarmMissionVerifyResponse,
    DailyRoutineItem,
    ElderlyAnalysisResponse,
    ExerciseItem,
    FoodScanResponse,
    FoodVerifyRequest,
    FoodVerifyResponse,
    GoalsPlanRequest,
    GoalsPlanResponse,
    HealthAnalysisResponse,
    WorkoutPlanRequest,
    WorkoutPlanResponse,
)


def detect_card_type(card_number: str) -> str:
    digits = re.sub(r"\D", "", card_number)
    if digits.startswith("8600") or digits.startswith("5614"):
        return "uzcard"
    if digits.startswith("9860"):
        return "humo"
    if digits.startswith("4"):
        return "visa"
    if digits.startswith("5"):
        return "mastercard"
    return "bank_card"


def _build_client_for(purpose: str) -> genai.Client:
    key = settings.gemini_key_for(purpose)
    if not key:
        raise ValueError("GEMINI_API_KEY_NOT_CONFIGURED")
    return genai.Client(
        api_key=key,
        http_options=types.HttpOptions(
            timeout=25000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def run_ai_pharmacist_chat(req: AIChatRequest, user: Optional[User]) -> AIChatResponse:
    client = _build_client_for("chat")

    user_info = ""
    if user:
        user_info = (
            f"Foydalanuvchi ma'lumotlari: Ismi: {user.first_name}, Yoshi: {user.age or 'Noma`lum'}, "
            f"Jinsi: {user.gender or 'Noma`lum'}, Bo'yi: {user.height_cm or 'Noma`lum'} sm, "
            f"Vazni: {user.weight_kg or 'Noma`lum'} kg, Reja: {user.plan.upper()}."
        )

    system_prompt = (
        "Siz Tetik AI — professional, xushmuomala va bilimdon klinik farmatsevt maslahatchisisiz. "
        "Foydalanuvchi sizdan dori vositalari, ularning nojo'ya ta'sirlari, to'g'ri ichish tartibi, "
        "bir-biriga mos kelishi (o'zaro ta'siri) va O'zbekistondagi sifatli arzon analoglari haqida so'raydi.\n\n"
        f"{user_info}\n\n"
        "Qoidalar:\n"
        "1. Doimo tushunarli, aniq va xalqona tilda javob bering.\n"
        "2. Dozalash va qabul qilish vaqtini (ovqatdan oldin/keyin) aniq tushuntiring.\n"
        "3. Har doim kerakli dori turlari uchun O'zbekiston bozoridagi arzon va sifatli muqobilini ayting.\n"
        "4. Har qanday jiddiy holatda davolovchi shifokor bilan maslahatlashishni eslatib o'ting.\n"
        "5. Javobingizni o'zbek tilida (yoki foydalanuvchi qaysi tilda yozsa, shu tilda) qaytaring."
    )

    history_text = ""
    for msg in req.history[-6:]:
        speaker = "Foydalanuvchi" if msg.role == "user" else "AI Farmatsevt"
        history_text += f"{speaker}: {msg.text}\n"

    full_prompt = f"{history_text}\nFoydalanuvchi: {req.message}\nAI Farmatsevt:"

    model = settings.GEMINI_MODEL
    try:
        interaction = client.interactions.create(
            model=model,
            system_instruction=system_prompt,
            input=full_prompt,
            generation_config={"temperature": 0.3},
            store=False,
            timeout=22.0,
        )
        reply = getattr(interaction, "output_text", "") or ""
        if not reply:
            for out in reversed(getattr(interaction, "outputs", []) or []):
                t = getattr(out, "text", "")
                if t:
                    reply = t
                    break
        reply = reply.strip() or "Kechirasiz, javob shakllantirilmadi. Iltimos, qayta so'rang."
    except Exception as exc:
        reply = f"AI Farmatsevt bilan aloqa o'rnatishda xatolik yuz berdi: {exc}"

    return AIChatResponse(
        reply=reply,
        voice_url=None,
        voice_gender=req.voice_gender or "female",
    )


def run_ai_sport_coach(prompt_text: str, user: Optional[User], lang: str = "uz") -> str:
    candidate_keys = []
    for k in [
        settings.GEMINI_SPORT_API_KEY,
        settings.GEMINI_CHAT_API_KEY,
        settings.GEMINI_HEALTH_API_KEY,
        settings.GEMINI_FOOD_API_KEY,
        settings.GEMINI_NOTIFY_API_KEY,
        settings.GEMINI_API_KEY,
    ]:
        if k and k not in candidate_keys:
            candidate_keys.append(k)

    lang_rule = (
        "Javobni РУССКОМ языке (Rus tilida) bering."
        if lang == "ru"
        else "Javobni ЎЗБЕК КИРИЛЛ алифбосида bering."
        if lang == "uz_cyrl"
        else "Javobni O'ZBEK LOTIN tilida bering."
    )
    system_prompt = (
        "Siz Tetik ⚡ Sport bo'limining professional AI Sport va Ovqatlanish murabbiyisiz. "
        "Foydalanuvchining haqiqiy yugurgan, yurgan yoki velosipedda bosib o'tgan masofasi (km) va sarflagan kilokaloriyasiga (kkal) qarab, "
        "unga qancha kaloriya, necha gramm oqsil (protein), uglevod, suv va qanday foydali milliy/parhez taomlar iste'mol qilish kerakligini "
        f"aniq va tushunarli hisob-kitob bilan aytib berasiz. {lang_rule}"
    )

    model = settings.GEMINI_MODEL
    for key in candidate_keys:
        try:
            client = genai.Client(
                api_key=key,
                http_options=types.HttpOptions(
                    timeout=20000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
            interaction = client.interactions.create(
                model=model,
                system_instruction=system_prompt,
                input=prompt_text,
                generation_config={"temperature": 0.35},
                store=False,
                timeout=18.0,
            )
            reply = getattr(interaction, "output_text", "") or ""
            if not reply:
                for out in reversed(getattr(interaction, "outputs", []) or []):
                    t = getattr(out, "text", "")
                    if t:
                        reply = t
                        break
            if reply and reply.strip():
                return reply.strip()
        except Exception:
            continue

    # Smart deterministic nutrition calculation if API is unreachable
    weight = (user.weight_kg if user and user.weight_kg else 70)
    protein_g = round(weight * 1.4)
    water_ml = round(weight * 38)
    if lang == "ru":
        return (
            f"🏃‍♂️ Анализ тренировки Tetik ⚡ Sport:\n\n"
            f"• Рекомендуемый белок для восстановления мышц: ~{protein_g} г в день (после тренировки: 25–30 г — отварная куриная грудка, яйца, творог или рыба).\n"
            f"• Сложные углеводы для восполнения энергии: гречка, овсянка, бурый рис и свежие овощи.\n"
            f"• Водный баланс: выпейте 400–500 мл чистой воды сейчас (суточная норма: {water_ml} мл)."
        )
    return (
        f"🏃‍♂️ Tetik ⚡ Sport — Mashg'ulot va Ovqatlanish tahlili:\n\n"
        f"• Mushaklarni tiklash uchun kunlik oqsil me'yoringiz: ~{protein_g} gramm (mashg'ulotdan so'ng 25–30 g oqsil: qaynatilgan tovuq go'shti, 2 ta tuxum, tvorog yoki mol go'shti).\n"
        f"• Sarflangan energiyani to'ldirish uchun murakkab uglevodlar: grechka, suli bo'tqasi (ovsyanka), sabzavotli salat va mevalar.\n"
        f"• Suv balansi: hozirning o'zida 400–500 ml iliq suv iching (kunlik me'yoringiz: {water_ml} ml)."
    )



def run_health_assessment(user: User, notes: Optional[str]) -> HealthAnalysisResponse:
    client = _build_client_for("health")

    age = user.age or 30
    gender = user.gender or "male"
    height = user.height_cm or 175
    weight = user.weight_kg or 70

    # Basic formulas for grounding
    bmi = round(weight / ((height / 100) ** 2), 1)
    base_water = round(weight * 35)  # 35ml per kg
    base_steps = 7000 if age < 50 else 5500
    base_kcal = 450 if gender == "male" else 350

    # ADAPTIVE DAILY PLAN: Calculate performance from previous day's activity
    yesterday_steps = user.daily_steps or 0
    yesterday_target = user.daily_target_steps or base_steps
    steps_deficit = max(0, yesterday_target - yesterday_steps)
    steps_surplus = max(0, yesterday_steps - yesterday_target)

    if steps_deficit > 0:
        # User walked LESS yesterday -> Compensate today by increasing steps
        adaptive_target_steps = min(15000, yesterday_target + min(steps_deficit, 3000))
        adaptive_context = (
            f"DIQQAT (Moslashuvchan Reja): Kecha foydalanuvchi belgilangan {yesterday_target} qadamdan kamroq — faqat {yesterday_steps} qadam yurdi "
            f"({steps_deficit} qadam kam). Bugungi kunlik qadam rejasini aynan {adaptive_target_steps} qadam qilib oshirib bering! "
            f"daily_plan_summary'da 'Kecha {steps_deficit} qadam kam yurganingiz sababli, sog'lom yurak-qon tomir faolligini tiklash uchun bugungi rejangiz {adaptive_target_steps} qadamga oshirildi' deb rag'batlantiruvchi tarzda tushuntiring."
        )
        fallback_summary = (
            f"Kecha {steps_deficit} qadam kam yurganingiz sababli, me'yorni tiklash uchun bugungi rejangiz {adaptive_target_steps} qadam qilib oshirildi. "
            f"Sizning BMI ko'rsatkichingiz {bmi} (vazn: {weight}kg, bo'y: {height}sm)."
        )
    elif steps_surplus >= 1000:
        adaptive_target_steps = yesterday_target
        adaptive_context = (
            f"AJOYIB: Kecha foydalanuvchi faol bo'lib o'z rejasidan ortiqcha {yesterday_steps} qadam yurdi (+{steps_surplus} qadam). "
            f"Uni natija bilan tabriklab, bugun tana charchab qolmasligi uchun {adaptive_target_steps} qadamlik mo''tadil reja bering."
        )
        fallback_summary = (
            f"Ajoyib natija! Kecha rejangizdan ortiqcha {steps_surplus} qadam yurdingiz. "
            f"Bugun me'yordagi {adaptive_target_steps} qadam va organizmni tiklash rejasi tavsiya etiladi."
        )
    else:
        adaptive_target_steps = yesterday_target
        adaptive_context = f"Foydalanuvchi o'z odatiy ritmida bormoqda ({yesterday_steps}/{yesterday_target} qadam)."
        fallback_summary = f"Sizning BMI ko'rsatkichingiz {bmi} (vazn: {weight}kg, bo'y: {height}sm). Kunlik rejangiz: {adaptive_target_steps} qadam."

    system_prompt = (
        "Siz Tetik Premium Salomatlik bo'limi shaxsiy nutrisiolog va sog'lom turmush tarzi murabbiyisiz. "
        "Har bir foydalanuvchining avvalgi kunlik faolligiga qarab har kuni moslashuvchan (adaptive) yangi reja tuzasiz. "
        "Foydalanuvchi kecha kam yurgan bo'lsa, bugun qadam va harakatni kompensatsiya qilib berish shart! "
        "JSON formatda to'liq reja qaytaring."
    )

    user_data = {
        "age": age,
        "gender": gender,
        "height_cm": height,
        "weight_kg": weight,
        "bmi": bmi,
        "yesterday_steps_walked": yesterday_steps,
        "yesterday_steps_target": yesterday_target,
        "calculated_adaptive_steps": adaptive_target_steps,
        "adaptive_notes": adaptive_context,
        "custom_notes": notes or "Kunlik ish faoliyati",
    }

    schema = {
        "type": "object",
        "properties": {
            "daily_plan_summary": {"type": "string"},
            "target_steps": {"type": "integer"},
            "target_water_ml": {"type": "integer"},
            "target_calories_burn": {"type": "integer"},
            "food_recommendations": {"type": "array", "items": {"type": "string"}},
            "activity_routine": {"type": "array", "items": {"type": "string"}},
            "sleep_advice": {"type": "string"},
        },
        "required": [
            "daily_plan_summary",
            "target_steps",
            "target_water_ml",
            "target_calories_burn",
            "food_recommendations",
            "activity_routine",
            "sleep_advice",
        ],
    }

    try:
        interaction = client.interactions.create(
            model=settings.GEMINI_MODEL,
            system_instruction=system_prompt,
            input=f"Foydalanuvchi ma'lumotlari va kechagi faolligi: {json.dumps(user_data, ensure_ascii=False)}",
            generation_config={"temperature": 0.2},
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
            store=False,
            timeout=22.0,
        )
        text = getattr(interaction, "output_text", "")
        data = json.loads(text)
        return HealthAnalysisResponse(**data)
    except Exception:
        return HealthAnalysisResponse(
            daily_plan_summary=fallback_summary,
            target_steps=adaptive_target_steps,
            target_water_ml=base_water,
            target_calories_burn=base_kcal,
            food_recommendations=[
                "Ertalab: Oqsilli to'yimli nonushta (tuxum, tvorog yoki suli bo'tqasi)",
                "Tushlik: Sabzavotlar bilan tayyorlangan yengil sho'rva va dimlangan go'sht",
                "Kechki ovqat: Uxlashdan 3 soat oldin sabzavotli salat yoki kefir",
            ],
            activity_routine=[
                f"Bugun kompensatsiya uchun kamida {adaptive_target_steps} qadam piyoda yurish",
                "Har 45 daqiqada 5 daqiqa o'rindan turib faol harakatlanish",
            ],
            sleep_advice="Kechki soat 23:00 dan kechikmay uxlash va kamida 7-8 soat orom olish.",
        )


def run_elderly_analysis(user: User, notes: Optional[str], blood_img_b64: Optional[str]) -> ElderlyAnalysisResponse:
    client = _build_client_for("health")

    system_prompt = (
        "Siz Tetik Premium 'Yoshi kattalar uchun maxsus' geriatriya va klinik farmakologiya bo'yicha sun'iy intellekt maslahatchisisiz.\n"
        "Vazifangiz:\n"
        "1. Foydalanuvchining yoshi, qon tahlillari (gemoglobin, qand / qandli diabet, xolesterin, ESR/SOE) yoki shikoyatlarini tahlil qiling.\n"
        "2. Qon bosimi (gipertoniya) va qandli diabet xavfini baholang.\n"
        "3. Aynan uning yoshiga mos, qon tomirlariga ortiqcha yuk tushirmaydigan taomlar va nimalar mutlaqo mumkin emasligini yozing.\n"
        "4. Kuniga qancha masofa (km) va qanday sur'atda yurishi kerakligini belgilang.\n"
        "5. O'zbekistonda mavjud bo'lgan, uning salomatligini qo'llab-quvvatlovchi xavfsiz dori va vitaminlar haqida tavsiya bering (lekin shifokor nazoratida bo'lishini qat'iy eslatib o'ting)."
    )

    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "has_diabetes_signs": {"type": "string"},
            "blood_pressure_advice": {"type": "string"},
            "dietary_dos_and_donts": {"type": "array", "items": {"type": "string"}},
            "walking_recommendation_km": {"type": "string"},
            "suggested_safe_medicines": {"type": "array", "items": {"type": "string"}},
            "disclaimer": {"type": "string"},
        },
        "required": [
            "summary",
            "has_diabetes_signs",
            "blood_pressure_advice",
            "dietary_dos_and_donts",
            "walking_recommendation_km",
            "suggested_safe_medicines",
            "disclaimer",
        ],
    }

    inputs = [
        {
            "type": "text",
            "text": f"Foydalanuvchi yoshi: {user.age or 62}, jinsi: {user.gender or 'erkak'}, vazni: {user.weight_kg or 75}kg. Qo'shimcha shikoyat va analizlar: {notes or 'Umumiy holsizlik va qon bosimi ko`tarilishi'}",
        }
    ]

    if blood_img_b64:
        clean_b64 = blood_img_b64.split(",")[-1]
        inputs.append({
            "type": "image",
            "data": clean_b64,
            "mime_type": "image/jpeg",
        })

    try:
        interaction = client.interactions.create(
            model=settings.GEMINI_MODEL,
            system_instruction=system_prompt,
            input=inputs,
            generation_config={"temperature": 0.2},
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
            store=False,
            timeout=25.0,
        )
        text = getattr(interaction, "output_text", "")
        data = json.loads(text)
        return ElderlyAnalysisResponse(**data)
    except Exception:
        return ElderlyAnalysisResponse(
            summary=f"{user.age or 60} yoshdagi organizm uchun qon tomirlar egiluvchanligi va moddalar almashinuvini nazoratda tutish juda muhimdir.",
            has_diabetes_signs="Qon tahlilida glyukoza me'yorida bo'lsa xavf kam, biroq shirinlik va oq nonni cheklash maqsadga muvofiq.",
            blood_pressure_advice="Tuz iste'molini kuniga 4-5 grammgacha kamaytirish, kuniga 2 marta (ertalab va kechqurun) qon bosimini o'lchash tavsiya etiladi.",
            dietary_dos_and_donts=[
                "Tavsiya etiladi: Dimlangan sabzavotlar, qora donli non, yong'oq, yog'siz baliq",
                "Cheklansin: Sho'r tuzlamalar, yog'li qovurma taomlar, xamir ovqatlar va quyuq choy",
            ],
            walking_recommendation_km="Kuniga 2-3 km (o'rtacha 4000-5000 qadam) sokin sur'atda, toza havoda sayr qilish.",
            suggested_safe_medicines=[
                "Magne B6 (Asab va yurak mushaklarini qo'llash uchun)",
                "Kardiomagnil / Trombo ASS (Shifokor bilan qon suyultirish maqsadida)",
                "Omega-3 baliq moyi (Xolesterinni me'yorda saqlash uchun)",
            ],
            disclaimer="Tetik — axborot beruvchi intellektual tizim. Ushbu tavsiyalar shifokor ko'rigi o'rnini bosa olmaydi. Dori vositalarini qabul qilishdan oldin davolovchi shifokoringiz bilan maslahatlashing.",
        )


def run_food_scan(
    image_b64: str,
    portion_grams: Optional[int] = None,
    user_notes: Optional[str] = None,
) -> FoodScanResponse:
    client = _build_client_for("food")
    clean_b64 = image_b64.split(",")[-1]

    portion_text = f"Foydalanuvchi ko'rsatgan porsiya: {portion_grams} gramm. " if portion_grams else ""
    notes_text = f"Foydalanuvchi qo'shimcha izohi: {user_notes}. " if user_notes else ""

    system_prompt = (
        "Siz Tetik Ultra rejimining Ovqatlanish va Kaloriya tahlilchisisiz.\n"
        "Foydalanuvchi yuborgan taom rasmini skaner qilib, undagi kaloriyani, masalliqlarni, "
        "uglevod, yog', oqsil, vitaminlar miqdorini va ushbu ovqatni yegandan so'ng qancha vaqtdan keyin uxlash mumkinligi hamda "
        "kaloriyani yoqish uchun necha kilometr yurish kerakligini hisoblang.\n"
        "MUHIM TIBBIY QOIDA: Foydali va me'yordagi yog'lardan (to'yinmagan yog' kislotalari, zaytun moyi, yong'oqlar, baliq yog'i, tuxum sarig'i) asossiz qo'rqmang va vahima qilmang! Sog'lom yog'lar inson organizmi, gormonlar balansi, hujayra membranasi va energiya uchun biologik jihatdan o'ta zarurdir. Maslahatda me'yordagi yog'larni to'g'ri baholang va foydalanuvchiga muvozanatli yondashuvni tushuntiring.\n"
        f"{portion_text}{notes_text}"
    )

    schema = {
        "type": "object",
        "properties": {
            "food_name": {"type": "string"},
            "calories": {"type": "integer"},
            "ingredients": {"type": "array", "items": {"type": "string"}},
            "sleep_after_hours": {"type": "string"},
            "fat_g": {"type": "string"},
            "carbs_g": {"type": "string"},
            "protein_g": {"type": "string"},
            "vitamins": {"type": "array", "items": {"type": "string"}},
            "walk_distance_km": {"type": "string"},
            "health_advice": {"type": "string"},
        },
        "required": [
            "food_name",
            "calories",
            "ingredients",
            "sleep_after_hours",
            "fat_g",
            "carbs_g",
            "protein_g",
            "vitamins",
            "walk_distance_km",
            "health_advice",
        ],
    }

    try:
        interaction = client.interactions.create(
            model=settings.GEMINI_MODEL,
            system_instruction=system_prompt,
            input=[
                {"type": "text", "text": "Ushbu taomning to'liq ozuqaviy tahlilini JSON shaklida chiqaring."},
                {"type": "image", "data": clean_b64, "mime_type": "image/jpeg"},
            ],
            generation_config={"temperature": 0.1},
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
            store=False,
            timeout=25.0,
        )
        text = getattr(interaction, "output_text", "")
        data = json.loads(text)
        c = int(data.get("calories", 500))
        range_low = max(50, round(c * 0.9))
        range_high = round(c * 1.1)
        data["calorie_range"] = f"{range_low} - {range_high} kkal"
        data["portion_size_g"] = portion_grams or 300
        return FoodScanResponse(**data)
    except Exception:
        c = 680
        if portion_grams:
            c = round(680 * (portion_grams / 350))
        return FoodScanResponse(
            food_name="Osh / Palov porsiyasi",
            calories=c,
            calorie_range=f"{round(c * 0.9)} - {round(c * 1.1)} kkal",
            portion_size_g=portion_grams or 350,
            ingredients=["Guruch", "Go'sht (mol go'shti)", "Sabzi", "Piyoz", "O'simlik yog'i", "Zira"],
            sleep_after_hours="Kamida 3.5 - 4 soat",
            fat_g="28g",
            carbs_g="74g",
            protein_g="24g",
            vitamins=["Vitamin A (Sabzi)", "Vitamin B guruhi", "Temir", "Sink"],
            walk_distance_km="5.5 - 6 km (taxminan 8 000 qadam)",
            health_advice="Ushbu taom yuqori kaloriyali hisoblanadi. Ovqatdan keyin ko'k choy ichish va 30 daqiqadan so'ng piyoda sayr qilish hazm qilishga yordam beradi.",
        )


import hashlib
USED_ALARM_PHOTO_HASHES: set[str] = set()


def compute_photo_hash(b64_str: str) -> str:
    clean = b64_str.split(",")[-1].strip()
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def run_alarm_mission_verify(step: int, image_b64: str, db=None, user_id: int = None) -> AlarmMissionVerifyResponse:
    photo_hash = compute_photo_hash(image_b64)

    # 1. Anti-cheat check: Prevent duplicate/reused photo from previous days
    if photo_hash in USED_ALARM_PHOTO_HASHES:
        return AlarmMissionVerifyResponse(
            step=step,
            passed=False,
            next_step=step,
            message="❌ Ushbu rasm avval foydalanilgan! Qoidaga muvofiq, har kuni faqat kameradan yangi rasm olish shart!",
            mission_completed=False,
        )

    if db:
        try:
            from app.models.payment import ScanRecord
            dup = db.query(ScanRecord).filter(
                ScanRecord.scan_type == f"alarm_mission_step_{step}",
                ScanRecord.image_preview == photo_hash,
            ).first()
            if dup:
                return AlarmMissionVerifyResponse(
                    step=step,
                    passed=False,
                    next_step=step,
                    message="❌ Ushbu rasm avval yuklangan! Har kuni kameradan faqat jonli yangi rasm olish shart!",
                    mission_completed=False,
                )
        except Exception:
            pass

    step_descriptions = {
        1: "xomom yoki oshxonadagi rakvina (cho'mbirak / suv krani / sink)",
        2: "tishini yuvayotgan inson (tish cho'tkasi yoki og'izda tish pastasi)",
        3: "uyqudan to'liq uyg'ongan, ko'zlari ochiq, tetik va hushyor yuz",
    }

    target = step_descriptions.get(step, "cho'mbirak")

    system_prompt = (
        f"Siz Tetik Ultra qat'iy Budilnik tekshiruvchisisiz. "
        f"Vazifangiz rasmni tekshirib, unda aynan: {target} bor-yo'qligini aniqlash. "
        "Agar rasmda ushbu talab bajarilgan bo'lsa passed=true, aks holda passed=false qiling."
    )

    schema = {
        "type": "object",
        "properties": {
            "passed": {"type": "boolean"},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["passed", "confidence", "reason"],
    }

    passed = False
    clean_b64 = image_b64.split(",")[-1].strip()

    try:
        client = _build_client_for("notify")
        interaction = client.interactions.create(
            model=settings.GEMINI_MODEL,
            system_instruction=system_prompt,
            input=[
                {"type": "text", "text": f"Rasmda {target} bormi? Sinchiklab tekshiring."},
                {"type": "image", "data": clean_b64, "mime_type": "image/jpeg"},
            ],
            generation_config={"temperature": 0.1},
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
            store=False,
            timeout=20.0,
        )
        text = getattr(interaction, "output_text", "")
        data = json.loads(text)
        passed = bool(data.get("passed", False))
    except Exception as gemini_err:
        print("Notice Gemini verify error:", gemini_err)
        # If camera photo has data, pass gracefully so user is never trapped in error
        passed = len(clean_b64) > 1000

    if not passed:
        reasons = {
            1: "Rasmda cho'mbirak (rakvina) aniqlanmadi. Iltimos, xomomdagi rakvina va kranni aniqroq ko'rsating!",
            2: "Tish yuvayotgan holat aniqlanmadi. Tish cho'tkangizni ko'rsatib rasmga oling!",
            3: "Yuzingiz aniq ko'rinmadi yoki uyqusiragan ko'rinasiz. Ko'zlaringizni ochib, tetik holatda rasmga oling!",
        }
        return AlarmMissionVerifyResponse(
            step=step,
            passed=False,
            next_step=step,
            message=reasons.get(step, "Vazifa bajarilmadi, qaytadan urinib ko'ring!"),
            mission_completed=False,
        )

    # If passed, register hash so it can never be reused
    USED_ALARM_PHOTO_HASHES.add(photo_hash)
    if db:
        try:
            from app.models.payment import ScanRecord
            rec = ScanRecord(
                user_id=user_id,
                scan_type=f"alarm_mission_step_{step}",
                image_preview=photo_hash,
                summary=f"Budilnik vazifasi {step}-bosqich tasdiqlandi",
            )
            db.add(rec)
            db.commit()
        except Exception:
            pass

    if step == 1:
        return AlarmMissionVerifyResponse(
            step=1,
            passed=True,
            next_step=2,
            message="Ajoyib! Rakvina tasdiqlandi. 2-bosqich: Endi tishingizni yuvayotganingizni rasmga olib tashlang!",
            mission_completed=False,
        )
    elif step == 2:
        return AlarmMissionVerifyResponse(
            step=2,
            passed=True,
            next_step=3,
            message="Barakalla! Tish yuvish tasdiqlandi. 3-bosqich: To'liq tetik yuzingizni kameraga ko'rsating!",
            mission_completed=False,
        )
    else:
        return AlarmMissionVerifyResponse(
            step=3,
            passed=True,
            next_step=4,
            message="Tabriklaymiz! Siz to'liq uyg'ondingiz. Budilnik muvaffaqiyatli o'chirildi! Kuningiz xayrli o'tsin!",
            mission_completed=True,
        )


# --- 1. FITNESS & YOGA AI AGENT ---
def run_fitness_yoga_workout(req: WorkoutPlanRequest, user: Optional[User]) -> WorkoutPlanResponse:
    client = _build_client_for("fitness")

    user_age = req.age or (user.age if user else 30) or 30
    conditions = req.health_conditions or (user.health_plan_json if user else "") or "Yo'q"
    duration = req.duration_min or (25 if req.workout_type == "yoga" else 20)

    is_yoga = req.workout_type.lower() == "yoga"

    if is_yoga:
        system_prompt = (
            "Siz Tetik platformasining sertifikatlangan Yog'a, Nafas va Tana tiklanishi bo'yicha sun'iy intellekt murabbiyisiz.\n"
            "Maxsus e'tibor: Foydalanuvchining yoshi va salomatlik cheklovlariga juda ehtiyotkorlik bilan yondashing. "
            "Keksalar va bo'g'imida muammosi bor odamlar uchun bo'g'imlarga ortiqcha yuk tushirmaydigan, o'murtqa va belni yengillashtiruvchi, "
            "tinchlantiruvchi chuqur nafas (pranayama) va xavfsiz cho'zilish holatlarini bering. "
            "Ko'rsatmalar (instruction) juda tushunarli, aniq, qadamma-qadam va o'zbek tilida ravon bo'lsin.\n"
            "MUHIM: animation_icon maydonida FAQAT bitta chiroyli emoji qo'ying (masalan 🧘, 🫁, 🌸, ⛰️, 🤸). Hech qachon 'warm_up' yoki 'squat' deb yozmang!"
        )
    else:
        system_prompt = (
            "Siz Tetik platformasining Professional Fitness va Jismoniy Tarbiya AI Murabbiyisiz.\n"
            "Vazifangiz: Foydalanuvchining yoshi, jismoniy darajasi va sog'lig'iga qarab xavfsiz, o'z tana vazni yoki oddiy buyumlar bilan bajariladigan "
            "kuch va chidamlilik mashg'ulotini tuzib berish. Bo'g'imlarga shikast yetkazmaydigan to'g'ri texnikani ko'rsating.\n"
            "MUHIM: animation_icon maydonida FAQAT bitta chiroyli emoji qo'ying (masalan 🏃, 🦵, 💪, ⚡, 🤸, 🛡️). Hech qachon 'warm_up' yoki 'squat' deb yozmang!"
        )

    user_prompt = (
        f"Mashg'ulot turi: {'Yog`a va Nafas' if is_yoga else 'Fitness va Kuch'}\n"
        f"Foydalanuvchi yoshi: {user_age} yosh\n"
        f"Davomiyligi: {duration} daqiqa\n"
        f"Darajasi: {req.fitness_level}\n"
        f"Sog'lig'i / cheklovlar: {conditions}\n"
        "Ushbu mezonlar bo'yicha to'liq mashqlar to'plamini JSON shaklida chiqaring."
    )

    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "workout_type": {"type": "string"},
            "total_duration_min": {"type": "integer"},
            "estimated_kcal": {"type": "integer"},
            "target_audience": {"type": "string"},
            "safety_precautions": {"type": "array", "items": {"type": "string"}},
            "exercises": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "duration_or_reps": {"type": "string"},
                        "rest_sec": {"type": "integer"},
                        "instruction": {"type": "string"},
                        "focus_muscle": {"type": "string"},
                        "animation_icon": {"type": "string"},
                    },
                    "required": ["name", "duration_or_reps", "rest_sec", "instruction", "focus_muscle", "animation_icon"],
                },
            },
        },
        "required": ["title", "workout_type", "total_duration_min", "estimated_kcal", "target_audience", "safety_precautions", "exercises"],
    }

    try:
        interaction = client.interactions.create(
            model=settings.GEMINI_MODEL,
            system_instruction=system_prompt,
            input=[{"type": "text", "text": user_prompt}],
            generation_config={"temperature": 0.2},
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
            store=False,
            timeout=25.0,
        )
        data = json.loads(getattr(interaction, "output_text", "{}"))
        return WorkoutPlanResponse(**data)
    except Exception as exc:
        print("run_fitness_yoga_workout fallback:", exc)
        if is_yoga:
            return WorkoutPlanResponse(
                title=f"Xavfsiz Tinchlantiruvchi Yog'a ({user_age} yosh uchun)",
                workout_type="yoga",
                total_duration_min=duration,
                estimated_kcal=90,
                target_audience="Barcha yoshdagilar va bo'g'im salomatligini asrovchilar uchun",
                safety_precautions=[
                    "Mashq paytida nafasni aslo to'xtatmang, chuqur va erkin nafas oling",
                    "Bo'g'imlarda og'riq yoki noqulaylik sezilsa, darhol to'xtating",
                    "To'g'ri tekis joyda yoki gilamchada bajaring",
                ],
                exercises=[
                    ExerciseItem(
                        name="Tinchlantiruvchi Qorin Nafasi (Pranayama)",
                        duration_or_reps="3 daqiqa",
                        rest_sec=20,
                        instruction="Qulay o'tiring. Burun orqali 4 soniya chuqur nafas oling, qorinni shiring, 2 soniya to'xtab, og'iz orqali 6 soniya sekin chiqaring.",
                        focus_muscle="O'pka, diafragma va asab tizimi",
                        animation_icon="🫁",
                    ),
                    ExerciseItem(
                        name="Mushuk-Sigir Holati (Marjaryasana)",
                        duration_or_reps="8-10 marta",
                        rest_sec=20,
                        instruction="To'rt oyoqlab turing. Nafas olib belni pastga tushiring, boshni yuqoriga qarating. Nafas chiqarib orqani kamar qilib bukib, boshni pastga eging.",
                        focus_muscle="Umurtqa pog'onasi va bel",
                        animation_icon="🧘",
                    ),
                    ExerciseItem(
                        name="Bola Holati (Balasana)",
                        duration_or_reps="2 daqiqa dam olish",
                        rest_sec=15,
                        instruction="Tizzalarda o'tirib, tanani oldinga egiltiring, peshanani polga tekkizing. Qo'llaringizni oldinga cho'zing va butunlay bo'shashing.",
                        focus_muscle="Bel, yelkalar va kestirib bo'g'imlari",
                        animation_icon="🌸",
                    ),
                    ExerciseItem(
                        name="Tog' Holati (Tadasana) va Cho'zilish",
                        duration_or_reps="2 daqiqa",
                        rest_sec=20,
                        instruction="Tik turing, oyoqlar birga. Qo'llarni yuqoriga ko'tarib osmonga intiling. Oyoq uchlariga ohista ko'tarilib nafasni muvozanatlang.",
                        focus_muscle="Butun tana va qad-qomat",
                        animation_icon="⛰️",
                    ),
                ],
            )
        else:
            return WorkoutPlanResponse(
                title="Kuch va Chidamlilik Uy Fitnesi",
                workout_type="fitness",
                total_duration_min=duration,
                estimated_kcal=160,
                target_audience="Faol harakat va kaloriyalarni tez yoqish uchun",
                safety_precautions=[
                    "Mashqdan oldin yengil qizib olishni unutmang",
                    "Belni doim to'g'ri tuting, gavdani oldinga egmang",
                    "Mashq oralig'ida 1-2 qultum suv iching",
                ],
                exercises=[
                    ExerciseItem(
                        name="Yengil Qizib Olish va Joyida Yurish",
                        duration_or_reps="3 daqiqa",
                        rest_sec=20,
                        instruction="Joyida ritmik tarzda qadam tashlang, qo'llarni erkin silkitib nafasni me'yorlashtiring.",
                        focus_muscle="Yurak-qon tomir va butun tana",
                        animation_icon="🏃",
                    ),
                    ExerciseItem(
                        name="Xavfsiz Yarim O'tirib-Turish (Squat)",
                        duration_or_reps="12 marta x 3 to'plam",
                        rest_sec=30,
                        instruction="Oyoqlarni yelka kengligida qo'ying. Kursiga o'tirayotgandek tizzalarni buking, tosni orqaga oling, tovon polga mahkam tegib tursin.",
                        focus_muscle="Son, dumba va boldir",
                        animation_icon="🦵",
                    ),
                    ExerciseItem(
                        name="Devorga yoki Stolga Suyangan Holda Push-up",
                        duration_or_reps="10 marta x 3 to'plam",
                        rest_sec=30,
                        instruction="Devordan 1 qadam masofada turing. Qo'llarni devorga tirab ko'krakni devor tomon egiltiring va kuch bilan qaytaring.",
                        focus_muscle="Ko'krak, yelka va qo'llar",
                        animation_icon="💪",
                    ),
                    ExerciseItem(
                        name="Turg'un Plank (Press va Bel)",
                        duration_or_reps="30-40 soniya",
                        rest_sec=30,
                        instruction="Tirsaklarda turing, tana tekis chiziq bo'lsin. Qorin mushaklarini tarang tuting, belni osiltirmang.",
                        focus_muscle="Qorin pressi va orqa",
                        animation_icon="⚡",
                    ),
                ],
            )


# --- 2. GOAL PLANNER (MAQSADLARIM) AI AGENT ---
def run_goals_planning(req: GoalsPlanRequest, user: Optional[User]) -> GoalsPlanResponse:
    client = _build_client_for("goal")

    weight_target = req.weight_target_kg or "Haftasiga -1 kg xavfsiz vazn tashlash"
    current_weight = user.weight_kg if user else 72
    age = user.age if user else 28
    steps_target = req.daily_steps_target or 10000

    system_prompt = (
        "Siz Tetik platformasining Salomatlik Maqsadlari va Rejalashtirish bo'yicha Shaxsiy AI Maslahatchisisiz.\n"
        "Foydalanuvchining haftalik vazn maqsadi, kunlik yurish/yugurish odati va salomatlik cheklovlariga qarab "
        "aniq, erishsa bo'ladigan va tibbiy jihatdan xavfsiz kunlik reja ishlab chiqing. "
        "Kunlik qadamlar soni, suv me'yori (ml) va yoqilishi kerak bo'lgan kaloriyani aniq hisoblab bering."
    )

    user_prompt = (
        f"Foydalanuvchi maqsadi: {req.target_type} ({weight_target})\n"
        f"Hozirgi vazni: {current_weight} kg, Yoshi: {age}\n"
        f"Kunlik qadam maqsadi: {steps_target} qadam\n"
        f"Harakat afzalligi: {req.activity_preference}\n"
        f"Salomatlik qaydlari: {req.health_notes or 'Cheklovlar yo`q'}\n"
        "Bunga mos moslashuvchan kunlik reja, suv va kaloriya normasini JSON shaklida chiqaring."
    )

    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "recommended_daily_steps": {"type": "integer"},
            "recommended_daily_water_ml": {"type": "integer"},
            "recommended_daily_burn_kcal": {"type": "integer"},
            "weekly_milestones": {"type": "array", "items": {"type": "string"}},
            "daily_routine": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "time": {"type": "string"},
                        "action": {"type": "string"},
                    },
                    "required": ["time", "action"],
                },
            },
            "motivation_quote": {"type": "string"},
            "reminder_frequency_min": {"type": "integer"},
        },
        "required": [
            "summary",
            "recommended_daily_steps",
            "recommended_daily_water_ml",
            "recommended_daily_burn_kcal",
            "weekly_milestones",
            "daily_routine",
            "motivation_quote",
            "reminder_frequency_min",
        ],
    }

    try:
        interaction = client.interactions.create(
            model=settings.GEMINI_MODEL,
            system_instruction=system_prompt,
            input=[{"type": "text", "text": user_prompt}],
            generation_config={"temperature": 0.2},
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
            store=False,
            timeout=25.0,
        )
        data = json.loads(getattr(interaction, "output_text", "{}"))
        return GoalsPlanResponse(**data)
    except Exception as exc:
        print("run_goals_planning fallback:", exc)
        return GoalsPlanResponse(
            summary=f"Haftalik sog'lom vazn kamaytirish va {steps_target} qadamli faollik dasturi",
            recommended_daily_steps=steps_target,
            recommended_daily_water_ml=2400,
            recommended_daily_burn_kcal=450,
            weekly_milestones=[
                "1-3 kun: Har kuni ertalab 10 daqiqa yengil gimnastika va 2 stakan iliq suv",
                "4-5 kun: Kechki ovqatdan so'ng 30 daqiqa piyoda sayr va olovcha (streak)ni saqlash",
                "Hafta oxiri: -0.8 kg vazn va tetik umumiy holat",
            ],
            daily_routine=[
                DailyRoutineItem(time="07:30", action="Uyg'onish, 2 stakan iliq suv va 10 min cho'zilish mashqlari"),
                DailyRoutineItem(time="12:30", action="Oqsilga boy tushlik va 15 min toza havoda qadam tashlash"),
                DailyRoutineItem(time="18:30", action="45 daqiqalik faol yurish yoki yugurish mashg'uloti"),
                DailyRoutineItem(time="22:30", action="Telefonni chetga surish va chuqur tinch uyqu"),
            ],
            motivation_quote="Kichik qadamlar har kuni takrorlansa — ulkan natijalarni yaratadi! Davom eting!",
            reminder_frequency_min=45,
        )


# --- 3. FOOD VERIFY & AI MURABBIY AGENT ---
def run_food_verification(req: FoodVerifyRequest, user: Optional[User]) -> FoodVerifyResponse:
    client = _build_client_for("coach")
    clean_b64 = req.image_base64.split(",")[-1]

    system_prompt = (
        "Siz Tetik platformasining Qat'iy va Talabchan AI Murabbiyisiz (Ovqatlanish va Semirishning oldini olish bo'yicha ekspert).\n"
        "Vazifangiz:\n"
        "1. Foydalanuvchi hozir yeymoqchi bo'lgan taom rasmini sinchiklab tahlil qiling.\n"
        "2. Agar taomda ortiqcha xamir, guruch, shakar, o'simlik yoki hayvon yog'lari, qovurilgan narsalar bo'lsa yoki sog'lom ovqatlanish rejasiga to'g'ri kelmasa:\n"
        "   QAT'IY OGOHLANTIRISH BERING: ortiqcha uglerod va yog'lar tanada tezda yog' to'planishiga va semirishga olib kelishini ayting!\n"
        "3. Buning o'rniga nima yeyishi mumkin bo'lgan foydali, oqsil va sabzavotli almashtirish variantini (swap_recommendation) taklif qiling.\n"
        "4. Kaloriya, uglevod, yog', oqsil miqdorini va ushbu kaloriyani yoqish uchun necha km yurish kerakligini hisoblang."
    )

    schema = {
        "type": "object",
        "properties": {
            "food_name": {"type": "string"},
            "calories": {"type": "integer"},
            "carbs_g": {"type": "string"},
            "fat_g": {"type": "string"},
            "protein_g": {"type": "string"},
            "is_healthy": {"type": "boolean"},
            "is_plan_compliant": {"type": "boolean"},
            "warning_alert": {"type": "string"},
            "swap_recommendation": {"type": "string"},
            "health_advice": {"type": "string"},
            "walk_distance_km": {"type": "string"},
            "sleep_after_hours": {"type": "string"},
        },
        "required": [
            "food_name",
            "calories",
            "carbs_g",
            "fat_g",
            "protein_g",
            "is_healthy",
            "is_plan_compliant",
            "health_advice",
            "walk_distance_km",
            "sleep_after_hours",
        ],
    }

    try:
        interaction = client.interactions.create(
            model=settings.GEMINI_MODEL,
            system_instruction=system_prompt,
            input=[
                {"type": "text", "text": f"Vaqt: {req.meal_time}. Ushbu taomni qat'iy tekshiring va agar ortiqcha uglevod/yog' bo'lsa ogohlantiring."},
                {"type": "image", "data": clean_b64, "mime_type": "image/jpeg"},
            ],
            generation_config={"temperature": 0.1},
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
            store=False,
            timeout=25.0,
        )
        data = json.loads(getattr(interaction, "output_text", "{}"))
        return FoodVerifyResponse(**data)
    except Exception as exc:
        print("run_food_verification fallback:", exc)
        return FoodVerifyResponse(
            food_name="Qovurilgan taom porsiyasi",
            calories=620,
            carbs_g="68g (Yuqori)",
            fat_g="31g (Yuqori)",
            protein_g="18g",
            is_healthy=False,
            is_plan_compliant=False,
            warning_alert="⚠️ DIQQAT: Ushbu taom tarkibida ortiqcha uglerod va to'yingan yog'lar juda ko'p! Bunday miqdordagi uglerod tezda tanada yog' qatlamiga aylanib, semirishga va charchoqqa sabab bo'ladi.",
            swap_recommendation="Buning o'rniga: Yog'siz tovuq filesi, qaynatilgan tuxum va ko'katlar bilan yangi bodring-pomidor salatini tanlang. Bu semirtirmaydi va uzoq vaqt to'q tutadi.",
            health_advice="Kechki ovqatda og'ir xamirli va qovurilgan taomlardan saqlaning.",
            walk_distance_km="5.2 km piyoda yurish kerak",
            sleep_after_hours="Kamida 3.5 soatdan keyin",
        )

