import uuid
from sqlalchemy.orm import Session

from app.models.drug import Drug
from app.schemas.prescription import (
    AIExtractedDrug,
    AIPrescriptionExtract,
    AnalyzedDrugResult,
    DrugAnalogOption,
    DrugSearchItem,
    PrescriptionAnalysisResponse,
    SupportedLang,
)
from app.utils.text_normalizer import normalize_drug_token, token_similarity
from app.utils.transliterate import localize_text, uz_latn_to_cyrl

DISCLAIMERS = {
    "uz": (
        "VitaGo — axborot beruvchi intellektual tizim. Dori vositalarini yoki ularning "
        "analoglarini qabul qilishdan oldin davolovchi shifokoringiz yoki malakali farmatsevt bilan maslahatlashing."
    ),
    "uz_cyrl": (
        "VitaGo — ахборот берувчи интеллектуал тизим. Дори воситаларини ёки уларнинг "
        "аналогларини қабул қилишдан олдин даволовчи шифокорингиз ёки малакали фармацевт билан маслаҳатлашинг."
    ),
    "ru": (
        "VitaGo — информационная интеллектуальная система. Перед применением лекарственных средств "
        "или заменой на аналоги обязательно проконсультируйтесь с лечащим врачом или фармацевтом."
    ),
}


def format_drug_name(drug: Drug, lang: SupportedLang) -> str:
    if lang in ("ru", "uz_cyrl"):
        return drug.trade_name_cyrl or drug.trade_name
    return drug.trade_name


def format_drug_inn_local(drug: Drug, lang: SupportedLang) -> str:
    return localize_text(lang, drug.inn_uz, drug.inn_ru)


def format_drug_form(drug: Drug, lang: SupportedLang) -> str:
    return localize_text(lang, drug.form_uz, drug.form_ru)


def format_drug_country(drug: Drug, lang: SupportedLang) -> str:
    return localize_text(lang, drug.country_uz, drug.country_ru)


def format_drug_category(drug: Drug, lang: SupportedLang) -> str:
    return localize_text(lang, drug.category_uz, drug.category_ru)


def find_best_drug_match(db: Session, trade_name: str, inn_hint: str = "") -> tuple[Drug | None, list[Drug]]:
    """
    Find the best matching prescribed drug in the local DB, plus all drugs sharing the same INN group.
    """
    all_drugs = db.query(Drug).all()
    if not all_drugs:
        return None, []

    best_drug: Drug | None = None
    best_score = 0.0

    for d in all_drugs:
        score = max(
            token_similarity(trade_name, d.search_tokens, d.trade_name),
            token_similarity(trade_name, d.search_tokens, d.trade_name_cyrl),
        )
        if score > best_score:
            best_score = score
            best_drug = d

    matched_inn: str | None = None
    if best_drug and best_score >= 0.76:
        matched_inn = best_drug.inn
    elif inn_hint:
        inn_norm = normalize_drug_token(inn_hint)
        for d in all_drugs:
            if (
                normalize_drug_token(d.inn) == inn_norm
                or normalize_drug_token(d.inn_uz) == inn_norm
                or normalize_drug_token(d.inn_ru) == inn_norm
                or (len(inn_norm) >= 5 and inn_norm in normalize_drug_token(d.inn))
            ):
                matched_inn = d.inn
                break

    if not matched_inn:
        return None, []

    same_inn_drugs = [d for d in all_drugs if d.inn == matched_inn]
    if best_drug is None or best_drug.inn != matched_inn or best_score < 0.72:
        # If the prescribed brand itself isn't in DB, treat same-INN group as analogs only
        return None, same_inn_drugs

    return best_drug, same_inn_drugs


def build_analog_options_from_db(
    prescribed_price: int,
    prescribed_drug_id: int | None,
    same_inn_drugs: list[Drug],
    lang: SupportedLang,
) -> list[DrugAnalogOption]:
    options: list[DrugAnalogOption] = []
    for cand in same_inn_drugs:
        if prescribed_drug_id is not None and cand.id == prescribed_drug_id:
            continue
        diff = max(0, prescribed_price - cand.avg_price_uzs)
        pct = int(round((diff / prescribed_price) * 100)) if prescribed_price > 0 and diff > 0 else 0
        options.append(
            DrugAnalogOption(
                id=cand.id,
                trade_name=format_drug_name(cand, lang),
                inn=cand.inn,
                inn_localized=format_drug_inn_local(cand, lang),
                dosage=cand.dosage,
                form=format_drug_form(cand, lang),
                manufacturer=cand.manufacturer,
                country=format_drug_country(cand, lang),
                is_local_uz=cand.is_local_uz,
                price_uzs=cand.avg_price_uzs,
                savings_uzs=diff,
                savings_percent=pct,
                source="verified_db",
            )
        )

    # Sort cheapest first, breaking ties in favor of local certified Uzbekistan pharma
    options.sort(key=lambda x: (x.price_uzs, not x.is_local_uz))
    return options


def build_analyzed_item(
    db: Session,
    item: AIExtractedDrug,
    idx: int,
    lang: SupportedLang,
    transliterate_ai_uz: bool = False,
) -> AnalyzedDrugResult:
    matched_drug, same_inn_drugs = find_best_drug_match(
        db, item.recognized_trade_name, item.inn or item.inn_localized
    )

    def adapt_text(txt: str) -> str:
        if transliterate_ai_uz and lang == "uz_cyrl":
            return uz_latn_to_cyrl(txt)
        return txt

    if matched_drug:
        prescribed_price = matched_drug.avg_price_uzs
        prescribed_name = format_drug_name(matched_drug, lang)
        inn_code = matched_drug.inn
        inn_local = format_drug_inn_local(matched_drug, lang)
        dosage = matched_drug.dosage
        form = format_drug_form(matched_drug, lang)
        manufacturer = matched_drug.manufacturer
        country = format_drug_country(matched_drug, lang)
        is_local = matched_drug.is_local_uz
        category = format_drug_category(matched_drug, lang)
        purpose = localize_text(lang, matched_drug.purpose_uz, matched_drug.purpose_ru)
        how_to = localize_text(lang, matched_drug.how_to_take_uz, matched_drug.how_to_take_ru)
        warnings = localize_text(lang, matched_drug.warnings_uz, matched_drug.warnings_ru)
        price_source = "verified_db"
    else:
        prescribed_price = max(5000, item.estimated_brand_price_uzs or 50000)
        prescribed_name = adapt_text(item.recognized_trade_name)
        inn_code = item.inn or item.inn_localized
        inn_local = adapt_text(item.inn_localized or item.inn)
        dosage = adapt_text(item.dosage)
        form = localize_text(lang, "Dori shakli", "Лекарственная форма")
        manufacturer = localize_text(lang, "Xalqaro / Import", "Импортный бренд")
        country = "—"
        is_local = False
        category = localize_text(lang, "Shifokor buyurgan vosita", "Назначенный препарат")
        purpose = adapt_text(item.simple_purpose)
        how_to = adapt_text(item.how_to_take)
        warnings = adapt_text(item.warnings)
        price_source = "ai_estimate"

    if same_inn_drugs:
        analogs = build_analog_options_from_db(
            prescribed_price=prescribed_price,
            prescribed_drug_id=matched_drug.id if matched_drug else None,
            same_inn_drugs=same_inn_drugs,
            lang=lang,
        )
    else:
        analogs = []
        for fb in item.ai_fallback_analogs:
            est_price = max(3000, fb.estimated_price_uzs)
            diff = max(0, prescribed_price - est_price)
            pct = int(round((diff / prescribed_price) * 100)) if prescribed_price > 0 and diff > 0 else 0
            analogs.append(
                DrugAnalogOption(
                    id=None,
                    trade_name=adapt_text(fb.trade_name),
                    inn=inn_code,
                    inn_localized=inn_local,
                    dosage=adapt_text(fb.dosage_and_form),
                    form="",
                    manufacturer=adapt_text(fb.manufacturer),
                    country=localize_text(lang, "O'zbekiston", "Узбекистан") if fb.is_local_uz else "Import",
                    is_local_uz=fb.is_local_uz,
                    price_uzs=est_price,
                    savings_uzs=diff,
                    savings_percent=pct,
                    source="ai_estimate",
                )
            )
        analogs.sort(key=lambda x: (x.price_uzs, not x.is_local_uz))

    cheaper_analogs = [a for a in analogs if a.price_uzs < prescribed_price]
    recommended = cheaper_analogs[0] if cheaper_analogs else None
    max_savings = recommended.savings_uzs if recommended else 0
    max_pct = recommended.savings_percent if recommended else 0

    return AnalyzedDrugResult(
        item_id=f"item-{idx + 1}",
        raw_written_text=item.raw_written_text,
        prescribed_drug_id=matched_drug.id if matched_drug else None,
        prescribed_name=prescribed_name,
        inn=inn_code,
        inn_localized=inn_local,
        dosage=dosage,
        form=form,
        manufacturer=manufacturer,
        country=country,
        is_local_uz=is_local,
        prescribed_price_uzs=prescribed_price,
        regimen=adapt_text(item.regimen),
        confidence=round(min(1.0, max(0.5, item.confidence)), 2),
        category=category,
        simple_purpose=purpose,
        how_to_take=how_to,
        warnings=warnings,
        price_source=price_source,
        analogs=analogs,
        recommended_analog=recommended,
        max_savings_uzs=max_savings,
        max_savings_percent=max_pct,
    )


def enrich_prescription_extract(
    db: Session,
    extract: AIPrescriptionExtract,
    lang: SupportedLang,
    mode: str = "ai_vision",
    transliterate_uz: bool = False,
) -> PrescriptionAnalysisResponse:
    summary = uz_latn_to_cyrl(extract.doctor_notes_summary) if (transliterate_uz and lang == "uz_cyrl") else extract.doctor_notes_summary

    if not extract.is_valid_prescription or not extract.items:
        return PrescriptionAnalysisResponse(
            analysis_id=str(uuid.uuid4())[:8],
            mode=mode,  # type: ignore[arg-type]
            is_valid_prescription=False,
            summary=summary,
            language=lang,
            total_prescribed_uzs=0,
            total_with_analogs_uzs=0,
            total_savings_uzs=0,
            total_savings_percent=0,
            items=[],
            disclaimer=DISCLAIMERS[lang],
        )

    analyzed_items = [
        build_analyzed_item(db, item, idx, lang, transliterate_ai_uz=transliterate_uz)
        for idx, item in enumerate(extract.items)
    ]

    total_prescribed = sum(i.prescribed_price_uzs for i in analyzed_items)
    total_analogs = sum(
        i.recommended_analog.price_uzs if i.recommended_analog else i.prescribed_price_uzs
        for i in analyzed_items
    )
    total_savings = max(0, total_prescribed - total_analogs)
    total_pct = int(round((total_savings / total_prescribed) * 100)) if total_prescribed > 0 else 0

    return PrescriptionAnalysisResponse(
        analysis_id=str(uuid.uuid4())[:8],
        mode=mode,  # type: ignore[arg-type]
        is_valid_prescription=True,
        summary=summary,
        language=lang,
        total_prescribed_uzs=total_prescribed,
        total_with_analogs_uzs=total_analogs,
        total_savings_uzs=total_savings,
        total_savings_percent=total_pct,
        items=analyzed_items,
        disclaimer=DISCLAIMERS[lang],
    )


def search_drugs_in_db(db: Session, query: str, lang: SupportedLang, limit: int = 12) -> list[DrugSearchItem]:
    all_drugs = db.query(Drug).all()
    if not query.strip():
        # Return popular flagship brands when query is empty
        featured_names = {"Sumamed", "Klatsid", "Augmentin", "Nimesil", "Kreon 10000", "No-shpa Forte", "Konkor", "Lineks Forte"}
        scored = [(1.0, d) for d in all_drugs if d.trade_name in featured_names]
    else:
        scored = []
        for d in all_drugs:
            score = max(
                token_similarity(query, d.search_tokens, d.trade_name),
                token_similarity(query, d.search_tokens, d.trade_name_cyrl),
            )
            if score >= 0.58:
                scored.append((score, d))
        scored.sort(key=lambda pair: (-pair[0], pair[1].avg_price_uzs))

    inn_map: dict[str, list[Drug]] = {}
    for d in all_drugs:
        inn_map.setdefault(d.inn, []).append(d)

    results: list[DrugSearchItem] = []
    for _, drug in scored[:limit]:
        peers = [p for p in inn_map.get(drug.inn, []) if p.id != drug.id]
        cheaper_prices = [p.avg_price_uzs for p in peers if p.avg_price_uzs < drug.avg_price_uzs]
        results.append(
            DrugSearchItem(
                id=drug.id,
                trade_name=format_drug_name(drug, lang),
                inn=drug.inn,
                inn_localized=format_drug_inn_local(drug, lang),
                dosage=drug.dosage,
                form=format_drug_form(drug, lang),
                manufacturer=drug.manufacturer,
                country=format_drug_country(drug, lang),
                is_local_uz=drug.is_local_uz,
                avg_price_uzs=drug.avg_price_uzs,
                category=format_drug_category(drug, lang),
                analogs_count=len(peers),
                cheapest_analog_uzs=min(cheaper_prices) if cheaper_prices else None,
            )
        )
    return results


def build_single_drug_analysis(db: Session, drug: Drug, lang: SupportedLang) -> PrescriptionAnalysisResponse:
    synthetic_item = AIExtractedDrug(
        raw_written_text=f"{drug.trade_name} ({drug.dosage})",
        recognized_trade_name=drug.trade_name,
        inn=drug.inn,
        inn_localized=drug.inn_uz,
        dosage=drug.dosage,
        regimen=localize_text(lang, "Yo'riqnoma va shifokor tavsiyasiga binoan", "По инструкции и назначению врача"),
        confidence=1.0,
        simple_purpose=drug.purpose_uz,
        how_to_take=drug.how_to_take_uz,
        warnings=drug.warnings_uz,
        estimated_brand_price_uzs=drug.avg_price_uzs,
    )
    summary = localize_text(
        lang,
        f"«{drug.trade_name}» dorisining faol moddasi ({drug.inn_uz}) bo'yicha ro'yxatdan o'tgan barcha analoglar va narxlar tahlili.",
        f"Анализ действующего вещества препарата «{drug.trade_name_cyrl}» ({drug.inn_ru}) и сравнение цен на сертифицированные аналоги.",
    )
    extract = AIPrescriptionExtract(
        is_valid_prescription=True,
        doctor_notes_summary=summary,
        items=[synthetic_item],
    )
    return enrich_prescription_extract(db, extract, lang=lang, mode="drug_lookup", transliterate_uz=False)
