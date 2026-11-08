from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.cache import get_cached, set_cached
from app.core.config import settings
from app.data.sample_prescriptions import SAMPLE_PRESCRIPTIONS
from app.db.database import get_db
from app.models.drug import Drug
from app.schemas.prescription import (
    AIPrescriptionExtract,
    DrugSearchItem,
    PlatformStatsResponse,
    PrescriptionAnalysisResponse,
    SamplePrescriptionMeta,
    SupportedLang,
)
from app.services.gemini_vision import analyze_prescription_with_gemini, lookup_unknown_drug_with_gemini
from app.services.hybrid_matcher import (
    build_single_drug_analysis,
    enrich_prescription_extract,
    search_drugs_in_db,
)
from app.services.telegram_bot import handle_telegram_update
from app.utils.transliterate import localize_text

router = APIRouter(prefix="/api/v1", tags=["VitaGo"])


@router.get("/stats", response_model=PlatformStatsResponse)
def get_platform_stats(db: Session = Depends(get_db)):
    cached = get_cached("stats:platform")
    if cached:
        return PlatformStatsResponse(**cached)

    drugs = db.query(Drug).all()
    inn_groups = {d.inn for d in drugs}
    local_count = sum(1 for d in drugs if d.is_local_uz)
    res = {
        "total_drugs": len(drugs),
        "total_inn_groups": len(inn_groups),
        "local_uz_drugs": local_count,
        "avg_savings_percent": 72,
        "ai_vision_configured": bool(settings.GEMINI_API_KEY),
        "ai_model": settings.GEMINI_MODEL,
    }
    set_cached("stats:platform", res, ttl_seconds=300)
    return PlatformStatsResponse(**res)


@router.get("/prescription/samples", response_model=list[SamplePrescriptionMeta])
def list_sample_prescriptions(lang: SupportedLang = Query("uz")):
    samples: list[SamplePrescriptionMeta] = []
    for sample_id, data in SAMPLE_PRESCRIPTIONS.items():
        samples.append(
            SamplePrescriptionMeta(
                id=sample_id,
                title=localize_text(lang, data["title_uz"], data["title_ru"]),
                subtitle=localize_text(lang, data["subtitle_uz"], data["subtitle_ru"]),
                badge=localize_text(lang, data["badge_uz"], data["badge_ru"]),
                drugs_preview=data["drugs_preview"],
                estimated_savings_uzs=data["estimated_savings_uzs"],
            )
        )
    return samples


@router.post("/prescription/demo/{sample_id}", response_model=PrescriptionAnalysisResponse)
def run_demo_prescription(
    sample_id: str,
    lang: SupportedLang = Query("uz"),
    db: Session = Depends(get_db),
):
    sample = SAMPLE_PRESCRIPTIONS.get(sample_id)
    if not sample:
        raise HTTPException(status_code=404, detail="Namuna retsept topilmadi.")

    summary = sample["summary_ru"] if lang == "ru" else sample["summary_uz"]
    extract = AIPrescriptionExtract(
        is_valid_prescription=True,
        doctor_notes_summary=summary,
        items=sample["items"],
    )
    return enrich_prescription_extract(
        db=db,
        extract=extract,
        lang=lang,
        mode="demo_sample",
        transliterate_uz=(lang == "uz_cyrl"),
    )


@router.post("/prescription/scan", response_model=PrescriptionAnalysisResponse)
async def scan_prescription_image(
    file: UploadFile = File(...),
    lang: SupportedLang = Form("uz"),
    db: Session = Depends(get_db),
):
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Yuklangan rasm fayli bo'sh.")
    if len(raw_bytes) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Rasm hajmi 15 MB dan oshmasligi kerak.")

    try:
        extract = await run_in_threadpool(
            analyze_prescription_with_gemini,
            image_bytes=raw_bytes,
            content_type=file.content_type,
            lang=lang,
        )
    except ValueError as exc:
        if "GEMINI_API_KEY_MISSING" in str(exc):
            msg = localize_text(
                lang,
                "Haqiqiy rasmni AI orqali o'qish uchun backend/.env fayliga GEMINI_API_KEY kiriting. Hozircha pastdagi tayyor Namuna Retseptlardan yoki Dori Qidiruvidan foydalanishingiz mumkin!",
                "Для распознавания фото рецепта через ИИ укажите GEMINI_API_KEY в файле backend/.env. Пока вы можете протестировать готовые примеры рецептов или поиск лекарств!",
            )
            raise HTTPException(status_code=503, detail=msg)
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        msg = localize_text(
            lang,
            f"Retseptni AI tahlil qilishda xatolik yuz berdi: {exc}",
            f"Ошибка при ИИ-анализе рецепта: {exc}",
        )
        raise HTTPException(status_code=502, detail=msg)

    res = enrich_prescription_extract(db=db, extract=extract, lang=lang, mode="ai_vision", transliterate_uz=False)
    try:
        from app.models.payment import ScanRecord
        drug_names = [it.prescribed_name for it in res.items]
        rec = ScanRecord(
            scan_type="prescription_ai",
            summary=res.summary,
            extracted_drugs=", ".join(drug_names[:6]),
            total_prescribed_uzs=res.total_prescribed_uzs,
            total_savings_uzs=res.total_savings_uzs,
            savings_percent=res.total_savings_percent,
        )
        db.add(rec)
        db.commit()
    except Exception:
        pass
    return res


@router.get("/drugs/search", response_model=list[DrugSearchItem])
def search_drugs(
    q: str = Query("", max_length=120),
    lang: SupportedLang = Query("uz"),
    limit: int = Query(12, ge=1, le=30),
    db: Session = Depends(get_db),
):
    clean_q = q.strip().lower()
    cache_key = f"drugs:search:{lang}:{limit}:{clean_q}"
    cached = get_cached(cache_key)
    if cached is not None:
        return [DrugSearchItem(**item) for item in cached]

    results = search_drugs_in_db(db=db, query=q, lang=lang, limit=limit)
    set_cached(cache_key, [item.model_dump() for item in results], ttl_seconds=180)
    return results


@router.get("/drugs/{drug_id}/analogs", response_model=PrescriptionAnalysisResponse)
def get_drug_analogs_by_id(
    drug_id: int,
    lang: SupportedLang = Query("uz"),
    db: Session = Depends(get_db),
):
    cache_key = f"drugs:analogs:{lang}:{drug_id}"
    cached = get_cached(cache_key)
    if cached is not None:
        return PrescriptionAnalysisResponse(**cached)

    drug = db.query(Drug).filter(Drug.id == drug_id).first()
    if not drug:
        raise HTTPException(status_code=404, detail="Dori vositasi topilmadi.")
    analysis = build_single_drug_analysis(db=db, drug=drug, lang=lang)
    set_cached(cache_key, analysis.model_dump(), ttl_seconds=300)
    return analysis


@router.post("/drugs/ai-lookup", response_model=PrescriptionAnalysisResponse)
async def hybrid_ai_drug_lookup(
    q: str = Query(..., min_length=2, max_length=120),
    lang: SupportedLang = Query("uz"),
    db: Session = Depends(get_db),
):
    """
    Hybrid lookup: first checks local verified DB; if not found, asks Gemini
    for the drug's INN (XPN) and Uzbekistan analogs.
    """
    local_hits = search_drugs_in_db(db=db, query=q, lang=lang, limit=1)
    if local_hits and q.strip():
        top_drug = db.query(Drug).filter(Drug.id == local_hits[0].id).first()
        if top_drug:
            # Verify strong match before short-circuiting
            from app.utils.text_normalizer import token_similarity
            sim = max(
                token_similarity(q, top_drug.search_tokens, top_drug.trade_name),
                token_similarity(q, top_drug.search_tokens, top_drug.trade_name_cyrl),
            )
            if sim >= 0.78:
                return build_single_drug_analysis(db=db, drug=top_drug, lang=lang)

    try:
        extract = await run_in_threadpool(
            lookup_unknown_drug_with_gemini,
            query=q,
            lang=lang,
        )
    except ValueError as exc:
        if "GEMINI_API_KEY_MISSING" in str(exc):
            msg = localize_text(
                lang,
                f"«{q}» mahalliy bazada topilmadi. Bazada yo'q dorilarni AI orqali topish uchun backend/.env fayliga GEMINI_API_KEY kiriting.",
                f"Препарат «{q}» не найден в локальной базе. Для ИИ-поиска редких лекарств добавьте GEMINI_API_KEY в backend/.env.",
            )
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return enrich_prescription_extract(db=db, extract=extract, lang=lang, mode="drug_lookup", transliterate_uz=False)


@router.get("/telegram/info")
def telegram_bot_info():
    """Public diagnostic info about the connected Telegram Bot & Mini App."""
    return {
        "bot_configured": bool(settings.TELEGRAM_BOT_TOKEN),
        "bot_username": settings.TELEGRAM_BOT_USERNAME,
        "bot_url": f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}",
        "mini_app_url": settings.MINI_APP_URL,
        "webhook_secret_enabled": bool(settings.TELEGRAM_WEBHOOK_SECRET),
    }


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    """Serverless webhook endpoint receiving updates from Telegram Bot API."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN sozlanmagan.")

    expected_secret = settings.TELEGRAM_WEBHOOK_SECRET
    if expected_secret and x_telegram_bot_api_secret_token and x_telegram_bot_api_secret_token != expected_secret:
        raise HTTPException(status_code=403, detail="Noto'g'ri webhook secret token.")

    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Noto'g'ri JSON payload.")

    try:
        await handle_telegram_update(update=update, db=db)
    except Exception:
        # Always return 200 OK to Telegram so transient errors never block the webhook queue
        pass

    return {"ok": True}
