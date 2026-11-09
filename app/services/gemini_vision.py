import base64
import io
from PIL import Image
from google import genai
from google.genai import types

from app.core.config import settings
from app.schemas.prescription import AIPrescriptionExtract, SupportedLang

LANG_INSTRUCTIONS = {
    "uz": "Barcha izohlar, dori tushuntirishlari va xulosalarni O'ZBEK LOTIN alifbosida sodda, xalqona va tushunarli tilda yozing.",
    "uz_cyrl": "Барча изоҳлар, дори тушунтиришлари ва хулосаларни ЎЗБЕК КИРИЛЛ алифбосида содда, халқона ва тушунарли тилда ёзинг.",
    "ru": "Все пояснения, описания препаратов, способ приёма и резюме пишите на простом и понятном РУССКОМ языке.",
}


def normalize_image_bytes(raw_bytes: bytes, content_type: str | None) -> tuple[bytes, str]:
    """Ensure uploaded image is clean JPEG within optimal resolution for fast Vision OCR."""
    if len(raw_bytes) < 600:
        raise ValueError("Rasm hajmi juda kichik yoki fayl bo'sh. Iltimos, haqiqiy retsept suratini yuklang.")
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            if img.width < 100 or img.height < 100:
                raise ValueError("Rasm o'lchami juda kichik (kamida 100x100px talab etiladi). Retseptni yaqindan suratga oling.")
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            max_side = 1500
            if max(img.size) > max_side:
                img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue(), "image/jpeg"
    except ValueError:
        raise
    except Exception:
        mime = content_type if content_type in ("image/jpeg", "image/png", "image/webp") else "image/jpeg"
        return raw_bytes, mime


def build_vision_system_prompt(lang: SupportedLang) -> str:
    lang_rule = LANG_INSTRUCTIONS.get(lang, LANG_INSTRUCTIONS["uz"])
    return (
        "Siz O'zbekistondagi eng tajribali klinik farmatsevt va shifokor qo'lyozmalarini "
        "(lotin, rus va o'zbek kirill/lotin yozuvidagi retseptlarni, shuningdek dori qutisi rasmlarini) "
        "yuqori aniqlikda o'quvchi intellektual tizimsiz (Tetik AI).\n\n"
        "Vazifangiz:\n"
        "1. Rasmdagi shifokor retseptini yoki dori qutisini diqqat bilan o'qing ('Rp.:', 'Tab.', 'Caps.', 'Sol.', 'Pulv.' qisqartmalarini to'g'ri yeching).\n"
        "2. Har bir dorining aniq savdo nomini va Xalqaro Patentlanmagan Nomini (INN / XPN, inglizcha standart nomini 'inn' maydoniga, masalan 'Azithromycin', 'Pantoprazole', 'Bisoprolol') aniqlang.\n"
        "3. Har bir dori uchun oddiy odam tushunadigan tilda vazifasini, qabul qilish tartibini va muhim ogohlantirishlarni yozing.\n"
        "4. O'zbekiston dorixonalarida uchraydigan 2–4 ta sifatli va arzon analoglarini (ayniqsa Jurabek Laboratories, Nobel Pharmsanoat, Nika Pharm, Radiks, Dentafill kabi mahalliy hamda ishonchli Yevropa/Hindiston generiklarini) taxminiy so'mdagi narxi bilan 'ai_fallback_analogs' ro'yxatiga kiriting.\n"
        "5. QAT'IY ISHONCHLILIK QOIDASI: Agar shifokor qo'lyozmasi juda xira, o'qib bo'lmaydigan yoki tushunarsiz bo'lsa, ASLO o'zingizdan dori nomini to'qimang! Bunday holda 'recognized_trade_name' ga 'Noma'lum qo'lyozma' deb yozing va 'confidence' ko'rsatkichini 0.3 qilib belgilang.\n"
        "6. Agar rasmda umuman retsept yoki dori bo'lmasa, 'is_valid_prescription' ni false qiling.\n"
        f"7. Til talabi: {lang_rule}"
    )


def _get_candidate_models() -> list[str]:
    """
    Fast, high-quota Gemini 3.x multi-model failover chain.
    Starts with gemini-3.5-flash-lite (~5s OCR, high Free Tier quota, works on all new & existing keys),
    then instantly falls back to gemini-3.6-flash, gemini-3.8-flash, and gemini-3.1-flash-lite.
    """
    configured = settings.GEMINI_MODEL
    ordered = [
        configured,
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
        "gemini-3.8-flash",
        "gemini-3.1-flash-lite",
    ]
    seen: set[str] = set()
    result: list[str] = []
    for m in ordered:
        if m and not m.startswith(("gemini-2.", "gemini-1.")) and m not in seen:
            seen.add(m)
            result.append(m)
    return result


def _generation_config_for_model(model_name: str) -> dict:
    """Return valid minimal-latency thinking configuration for each specific Gemini 3.x model."""
    if model_name in ("gemini-3.5-flash-lite", "gemini-3.6-flash"):
        return {"thinking_level": "minimal", "temperature": 0.1}
    if model_name.startswith(("gemini-3.8", "gemini-4")):
        return {"thinking_level": "low", "temperature": 0.1}
    return {"temperature": 0.1}


def _build_fast_client(api_key: str) -> genai.Client:
    """
    Build a Gemini client with zero internal backoff sleep so 429/503 errors on any single model
    fail in <1s and immediately switch to the next fallback model instead of hanging 50-100s.
    """
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=22000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    try:
        client.interactions.sdk_configuration.retry_config = None
    except Exception:
        pass
    return client


def _extract_interaction_text(interaction) -> str:
    txt = getattr(interaction, "output_text", None)
    if txt and txt.strip():
        return txt.strip()
    for out in reversed(getattr(interaction, "outputs", None) or []):
        out_txt = getattr(out, "text", None)
        if out_txt and out_txt.strip():
            return out_txt.strip()
    return ""


def _format_gemini_error(exc: Exception) -> str:
    raw = str(exc)
    lower = raw.lower()
    if "api_key_invalid" in lower or "api key not valid" in lower or "403" in lower:
        return "GEMINI_API_KEY noto'g'ri yoki muddati tugagan. Iltimos, Google AI Studio kalitini tekshiring."
    if "429" in lower or "rate limit" in lower or "quota" in lower:
        return "AI API so'rovlar limiti vaqtincha to'ldi (429). Iltimos, 15-20 soniyadan keyin qayta urinib ko'ring."
    if "503" in lower or "unavailable" in lower or "timeout" in lower or "timed out" in lower:
        return "Google AI serverida vaqtinchalik yuqori yuklama kuzatildi. Iltimos, qayta urinib ko'ring."
    return raw


def analyze_prescription_with_gemini(
    image_bytes: bytes,
    content_type: str | None,
    lang: SupportedLang = "uz",
) -> AIPrescriptionExtract:
    api_key = (
        settings.GEMINI_API_KEY
        or settings.GEMINI_CHAT_API_KEY
        or settings.GEMINI_HEALTH_API_KEY
        or settings.GEMINI_FOOD_API_KEY
        or settings.GEMINI_NOTIFY_API_KEY
    )
    if not api_key:
        raise ValueError("GEMINI_API_KEY_MISSING")

    clean_bytes, mime_type = normalize_image_bytes(image_bytes, content_type)
    b64_image = base64.b64encode(clean_bytes).decode("ascii")
    schema = AIPrescriptionExtract.model_json_schema()
    system_prompt = build_vision_system_prompt(lang)
    client = _build_fast_client(api_key)

    last_error: Exception | None = None
    for model_name in _get_candidate_models():
        try:
            interaction = client.interactions.create(
                model=model_name,
                system_instruction=system_prompt,
                input=[
                    {
                        "type": "text",
                        "text": "Ushbu retsept yoki dori rasmini tahlil qilib, barcha dorilar, XPN va arzon analoglarini JSON sxemada qaytaring.",
                    },
                    {
                        "type": "image",
                        "data": b64_image,
                        "mime_type": mime_type,
                    },
                ],
                generation_config=_generation_config_for_model(model_name),
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema,
                },
                store=False,
                timeout=22.0,
            )
            text = _extract_interaction_text(interaction)
            if text:
                return AIPrescriptionExtract.model_validate_json(text)
        except Exception as exc:
            last_error = exc
            continue

    if last_error:
        raise RuntimeError(_format_gemini_error(last_error))
    raise RuntimeError("AI modeldan bo'sh javob qaytdi.")


def lookup_unknown_drug_with_gemini(query: str, lang: SupportedLang = "uz") -> AIPrescriptionExtract:
    """Hybrid fallback when a manually searched drug is not yet in the local SQLite database."""
    api_key = (
        settings.GEMINI_API_KEY
        or settings.GEMINI_CHAT_API_KEY
        or settings.GEMINI_HEALTH_API_KEY
        or settings.GEMINI_FOOD_API_KEY
        or settings.GEMINI_NOTIFY_API_KEY
    )
    if not api_key:
        raise ValueError("GEMINI_API_KEY_MISSING")

    lang_rule = LANG_INSTRUCTIONS.get(lang, LANG_INSTRUCTIONS["uz"])
    system_prompt = (
        "Siz O'zbekiston farmatsevtika bo'yicha ma'lumotnoma tizimisiz (Tetik AI). "
        "Foydalanuvchi kiritgan dori vositasining Xalqaro Patentlanmagan Nomini (INN/XPN), "
        "vazifasini va O'zbekiston dorixonalarida mavjud bo'lgan arzon hamda sifatli analoglarini "
        f"(so'mdagi taxminiy narxlari bilan) qaytaring. Agar bunday dori umuman mavjud bo'lmasa, is_valid_prescription=false qiling. {lang_rule}"
    )
    schema = AIPrescriptionExtract.model_json_schema()
    client = _build_fast_client(api_key)

    last_error: Exception | None = None
    for model_name in _get_candidate_models():
        try:
            interaction = client.interactions.create(
                model=model_name,
                system_instruction=system_prompt,
                input=f"Dori nomi yoki faol moddasi: {query}",
                generation_config=_generation_config_for_model(model_name),
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema,
                },
                store=False,
                timeout=18.0,
            )
            text = _extract_interaction_text(interaction)
            if text:
                return AIPrescriptionExtract.model_validate_json(text)
        except Exception as exc:
            last_error = exc
            continue

    if last_error:
        raise RuntimeError(_format_gemini_error(last_error))
    raise RuntimeError("AI qidiruvidan bo'sh javob qaytdi.")
