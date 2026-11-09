from collections import deque
import html
from typing import Any

import httpx
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.data.sample_prescriptions import SAMPLE_PRESCRIPTIONS
from app.models.drug import Drug, TelegramUserPref
from app.schemas.prescription import AIPrescriptionExtract, PrescriptionAnalysisResponse, SupportedLang
from app.services.gemini_vision import analyze_prescription_with_gemini, lookup_unknown_drug_with_gemini
from app.services.hybrid_matcher import (
    build_single_drug_analysis,
    enrich_prescription_extract,
    search_drugs_in_db,
)
from app.utils.text_normalizer import token_similarity
from app.utils.transliterate import localize_text

_RECENT_UPDATE_IDS: deque[int] = deque(maxlen=300)

BOT_TEXTS: dict[SupportedLang, dict[str, str]] = {
    "uz": {
        "welcome_kb_hint": "👇 Pastdagi <b>«📱 Tetik Mini App»</b> tugmasi orqali ilovani istalgan payt ochishingiz mumkin:",
        "welcome_body": (
            "⚡ <b>Assalomu alaykum! Tetik — Sog'lom hayot, sport va AI ekotizimiga xush kelibsiz!</b>\n\n"
            "Tetik — kundalik salomatligingiz, mashg'ulotlaringiz va odatlaringizni aqlli boshqaruvchi platforma:\n\n"
            "🏃 <b>Tetik Sport & GPS:</b> Yugurish, yurish, jonli GPS treker va xaritada erkin yo'nalish chizish.\n"
            "⏰ <b>Aqlli Budilnik:</b> O'chirib bo'lmas va maxsus topshiriqli (kamera, harakat) uyg'otish tizimi.\n"
            "🍽️ <b>AI Ovqatlanish & 4 Mahal Reja:</b> Taom rasmini yuboring — kaloriya va makronutrientlarni hisoblaydi.\n"
            "💊 <b>Smart Retsept & Dori Skaneri:</b> Qo'lyozma retseptlarni o'qish va 70% gacha arzon analoglarni topish.\n\n"
            "📲 <b>Tetik Mobil Ilovasi (APK):</b>\n"
            "Datchiklar, fon rejimi va bildirishnomalardan to'liq foydalanish uchun ilovani o'rnating:\n"
            "👉 <a href=\"https://doriscan.vercel.app/Tetik_App.apk\">Tetik APK yuklab olish (To'g'ridan-to'g'ri havola)</a>"
        ),
        "open_mini_app_btn": "🚀 Tetik Mini App'ni ochish",
        "search_in_app_btn": "🔎 Ilovada qidirish",
        "try_sample_btn": "🧪 Namuna retseptni sinash",
        "kb_mini_app": "📱 Tetik Mini App",
        "kb_samples": "🧪 Namuna retsept",
        "kb_lang": "🌐 Til / Язык",
        "app_card_text": (
            "📱 <b>Tetik Mini App — To'liq Interaktiv Platforma</b>\n\n"
            "Mini App ichida:\n"
            "• Kameradan to'g'ridan-to'g'ri retsept skanerlash\n"
            "• Har bir dori uchun arzon analoglarni interaktiv almashtirib hisoblash\n"
            "• «Dorixonachiga ko'rsatish» maxsus ekrani\n"
            "• Barcha tahlillar tarixi mavjud!\n\n"
            "👇 Ochish uchun quyidagi tugmani bosing:"
        ),
        "help_text": (
            "ℹ️ <b>Tetik AI yordam bo'limi</b>\n\n"
            "• 📸 <b>Retsept skanerlash:</b> Retsept qog'ozi yoki dori qutisi rasmini yuboring (AI 5–10 soniyada tahlil qiladi).\n"
            "• 💊 <b>Dori qidirish:</b> Dori nomini yozing (lotin yoki kirillda, masalan: <code>Nimesil</code> yoki <code>Сумамед</code>).\n"
            "• 📱 <b>Mini App:</b> /app buyrug'i yoki pastdagi <b>«📱 Tetik Mini App»</b> tugmasini bosing.\n"
            "• 🧪 <b>Namunalar:</b> /namuna buyrug'i orqali tayyor retseptlarda sinab ko'ring.\n"
            "• 🌐 <b>Tilni almashtirish:</b> /lang buyrug'ini yuboring."
        ),
        "samples_prompt": "🧪 <b>Quyidagi tayyor shifokor retseptlaridan birini tanlang — tahlil va tejamkorlikni darhol ko'rasiz:</b>",
        "lang_prompt": "🌐 <b>Muloqot tilini tanlang:</b>",
        "lang_saved": "✅ Til o'zgartirildi: <b>O'zbekcha (Lotin)</b>",
        "scanning_photo": "⏳ <b>AI shifokor xatini o'qimoqda...</b>\n<i>Dorilar tarkibi (XPN) va O'zbekistondagi arzon analoglar solishtirilmoqda (5–10 soniya)</i>",
        "searching_drug": "🔎 <b>«{q}» bo'yicha XPN va arzon analoglar qidirilmoqda...</b>",
        "invalid_rx": (
            "⚠️ <b>Rasmda shifokor retsepti yoki dori nomi aniqlanmadi.</b>\n"
            "Iltimos, retsept qog'ozi yoki dori qutisini yorug'roq joyda aniqroq rasmga olib qayta yuboring yoki dori nomini yozing."
        ),
        "rx_title": "🧾 <b>Tetik AI — Retsept Tahlili</b>",
        "drug_title": "💊 <b>Tetik AI — Dori va Arzon Analoglar</b>",
        "active_substance": "Ta'sir etuvchi modda (XPN)",
        "purpose": "Vazifasi",
        "how_to_take": "Qabul qilish",
        "warning": "Eslatma",
        "analogs_header": "Sertifikatlangan arzon analoglar",
        "local_uz": "🇺🇿 O'zbekiston",
        "already_affordable": "Ushbu dori allaqachon eng hamyonbop variantlardan biri.",
        "budget_title": "💰 <b>Umumiy hisob-kitob:</b>",
        "prescribed_total": "Shifokor yozgan variant",
        "analogs_total": "Arzon sifatli analoglar bilan",
        "your_savings": "Sof tejamkorlik",
        "open_interactive_btn": "📱 Interaktiv jadvalni Mini App'da ochish",
        "unknown_cmd": "Buyruq tushunarsiz. /start yoki /app tugmasini bosing, yoxud retsept rasmini / dori nomini yuboring.",
    },
    "uz_cyrl": {
        "welcome_kb_hint": "👇 Пастдаги <b>«📱 Tetik Mini App»</b> тугмаси орқали иловани исталган пайт очишингиз мумкин:",
        "welcome_body": (
            "⚡ <b>Ассалому алайкум! Tetik — Соғлом ҳаёт, спорт ва AI экотизимига хуш келибсиз!</b>\n\n"
            "Tetik — кундалик саломатлигингиз, машғулотларингиз ва одатларингизни ақлли бошқарувчи платформа:\n\n"
            "🏃 <b>Tetik Sport & GPS:</b> Югуриш, юриш, жонли GPS трекер ва харитада эркин йўналиш чизиш.\n"
            "⏰ <b>Ақлли Будильник:</b> Ўчириб бўлмас ва махсус топшириқли (камера, ҳаракат) уйғотиш тизими.\n"
            "🍽️ <b>AI Овқатланиш & 4 Маҳал Режа:</b> Таом расмини юборинг — калория ва макронутриентларни ҳисоблайди.\n"
            "💊 <b>Smart Рецепт & Дори Сканери:</b> Қўлёзма рецептларни ўқиш ва 70% гача арзон аналогларни топиш.\n\n"
            "📲 <b>Tetik Мобил Иловаси (APK):</b>\n"
            "Датчиклар, фон режими ва билдиришномалардан тўлиқ фойдаланиш учун иловани ўрнатинг:\n"
            "👉 <a href=\"https://doriscan.vercel.app/Tetik_App.apk\">Tetik APK юклаб олиш (Тўғридан-тўғри ҳавола)</a>"
        ),
        "open_mini_app_btn": "🚀 Tetik Mini App'ни очиш",
        "search_in_app_btn": "🔎 Иловада қидириш",
        "try_sample_btn": "🧪 Намуна рецептни синаш",
        "kb_mini_app": "📱 Tetik Mini App",
        "kb_samples": "🧪 Намуна рецепт",
        "kb_lang": "🌐 Тил / Язык",
        "app_card_text": (
            "📱 <b>Tetik Mini App — Тўлиқ Интерактив Платформа</b>\n\n"
            "Mini App ичида:\n"
            "• Камерадан тўғридан-тўғри рецепт сканерлаш\n"
            "• Ҳар бир дори учун арзон аналогларни интерактив алмаштириб ҳисоблаш\n"
            "• «Дорихоначига кўрсатиш» махсус экрани\n"
            "• Барча таҳлиллар тарихи мавжуд!\n\n"
            "👇 Очиш учун қуйидаги тугмани босинг:"
        ),
        "help_text": (
            "ℹ️ <b>Tetik AI ёрдам бўлими</b>\n\n"
            "• 📸 <b>Рецепт сканерлаш:</b> Рецепт қоғози ёки дори қутиси расмини юборинг (AI 5–10 сонияда таҳлил қилади).\n"
            "• 💊 <b>Дори қидириш:</b> Дори номини ёзинг (масалан: <code>Нимесил</code> ёки <code>Sumamed</code>).\n"
            "• 📱 <b>Mini App:</b> /app буйруғи ёки пастдаги <b>«📱 Tetik Mini App»</b> тугмасини босинг.\n"
            "• 🧪 <b>Намуналар:</b> /namuna буйруғи орқали тайёр рецептларда синаб кўринг.\n"
            "• 🌐 <b>Тилни алмаштириш:</b> /lang буйруғини юборинг."
        ),
        "samples_prompt": "🧪 <b>Қуйидаги тайёр шифокор рецептларидан бирини танланг — таҳлил ва тежамкорликни дарҳол кўрасиз:</b>",
        "lang_prompt": "🌐 <b>Мулоқот тилини танланг:</b>",
        "lang_saved": "✅ Тил ўзгартирилди: <b>Ўзбекча (Кирилл)</b>",
        "scanning_photo": "⏳ <b>AI шифокор хатини ўқимоқда...</b>\n<i>Дорилар таркиби (ХПН) ва Ўзбекистондаги арзон аналоглар солиштирилмоқда (5–10 сония)</i>",
        "searching_drug": "🔎 <b>«{q}» бўйича ХПН ва арзон аналоглар қидирилмоқда...</b>",
        "invalid_rx": (
            "⚠️ <b>Расмда шифокор рецепти ёки дори номи аниқланмади.</b>\n"
            "Илтимос, рецепт қоғози ёки дори қутисини ёруғроқ жойда аниқроқ расмга олиб қайта юборинг ёки дори номини ёзинг."
        ),
        "rx_title": "🧾 <b>Tetik AI — Рецепт Таҳлили</b>",
        "drug_title": "💊 <b>Tetik AI — Дори ва Арзон Аналоглар</b>",
        "active_substance": "Таъсир этувчи модда (ХПН)",
        "purpose": "Вазифаси",
        "how_to_take": "Қабул қилиш",
        "warning": "Эслатма",
        "analogs_header": "Сертификатланган арзон аналоглар",
        "local_uz": "🇺🇿 Ўзбекистон",
        "already_affordable": "Ушбу дори аллақачон энг ҳамёнбоп вариантлардан бири.",
        "budget_title": "💰 <b>Умумий ҳисоб-китоб:</b>",
        "prescribed_total": "Шифокор ёзган вариант",
        "analogs_total": "Арзон сифатли аналоглар билан",
        "your_savings": "Соф тежамкорлик",
        "open_interactive_btn": "📱 Интерактив жадвални Mini App'да очиш",
        "unknown_cmd": "Буйруқ тушунарсиз. /start ёки /app тугмасини босинг, ёхуд рецепт расмини / дори номини юборинг.",
    },
    "ru": {
        "welcome_kb_hint": "👇 Открыть приложение в любой момент можно кнопкой <b>«📱 Tetik Mini App»</b> внизу экрана:",
        "welcome_body": (
            "⚡ <b>Здравствуйте! Добро пожаловать в Tetik — экосистему здоровья, спорта и AI!</b>\n\n"
            "Tetik — умная платформа для управления здоровьем, спортом и полезными привычками:\n\n"
            "🏃 <b>Tetik Sport & GPS:</b> Бег, ходьба, живой GPS-трекер и свободное рисование маршрута на карте.\n"
            "⏰ <b>Умный Будильник:</b> Неотключаемый будильник с проверкой пробуждения камерой и движением.\n"
            "🍽️ <b>AI Питание & 4 приёма пищи:</b> Отправьте фото еды — мгновенный расчёт калорий и баланса БЖУ.\n"
            "💊 <b>Smart Сканер рецептов:</b> Расшифровка почерка врачей и подбор аналогов с выгодой до 70%.\n\n"
            "📲 <b>Мобильное приложение Tetik (APK):</b>\n"
            "Для работы фонового шагомера, датчиков и мгновенных пуш-уведомлений установите приложение:\n"
            "👉 <a href=\"https://doriscan.vercel.app/Tetik_App.apk\">Скачать Tetik APK (Прямая ссылка)</a>"
        ),
        "open_mini_app_btn": "🚀 Открыть Tetik Mini App",
        "search_in_app_btn": "🔎 Поиск в приложении",
        "try_sample_btn": "🧪 Проверить на примере рецепта",
        "kb_mini_app": "📱 Tetik Mini App",
        "kb_samples": "🧪 Пример рецепта",
        "kb_lang": "🌐 Тил / Язык",
        "app_card_text": (
            "📱 <b>Tetik Mini App — Полная Интерактивная Платформа</b>\n\n"
            "Внутри Mini App доступны:\n"
            "• Мгновенное сканирование рецептов камерой\n"
            "• Интерактивный выбор аналогов и пересчёт бюджета\n"
            "• Режим «Показать фармацевту в аптеке»\n"
            "• История ваших сканирований и поисков!\n\n"
            "👇 Нажмите кнопку ниже для запуска:"
        ),
        "help_text": (
            "ℹ️ <b>Справка Tetik AI</b>\n\n"
            "• 📸 <b>Сканер рецепта:</b> Отправьте фото рецепта или коробки препарата (ИИ ответит за 5–10 секунд).\n"
            "• 💊 <b>Поиск лекарств:</b> Напишите название препарата (например: <code>Нимесил</code> или <code>Sumamed</code>).\n"
            "• 📱 <b>Mini App:</b> Команда /app или кнопка <b>«📱 Tetik Mini App»</b> внизу.\n"
            "• 🧪 <b>Примеры:</b> Команда /namuna для готовых демо-рецептов.\n"
            "• 🌐 <b>Язык:</b> Команда /lang для смены языка."
        ),
        "samples_prompt": "🧪 <b>Выберите готовый пример рецепта, чтобы сразу увидеть расшифровку и расчёт экономии:</b>",
        "lang_prompt": "🌐 <b>Выберите язык интерфейса:</b>",
        "lang_saved": "✅ Язык изменён: <b>Русский</b>",
        "scanning_photo": "⏳ <b>ИИ читает почерк врача...</b>\n<i>Сопоставляем действующие вещества (МНН) и доступные аналоги в Узбекистане (5–10 сек)</i>",
        "searching_drug": "🔎 <b>Поиск МНН и доступных аналогов для «{q}»...</b>",
        "invalid_rx": (
            "⚠️ <b>На фото не удалось распознать рецепт врача или лекарство.</b>\n"
            "Пожалуйста, сфотографируйте рецепт при хорошем освещении или отправьте название препарата текстом."
        ),
        "rx_title": "🧾 <b>Tetik AI — Разбор Рецепта</b>",
        "drug_title": "💊 <b>Tetik AI — Препарат и Аналоги</b>",
        "active_substance": "Действующее вещество (МНН)",
        "purpose": "Назначение",
        "how_to_take": "Как принимать",
        "warning": "Важно",
        "analogs_header": "Сертифицированные доступные аналоги",
        "local_uz": "🇺🇿 Узбекистан",
        "already_affordable": "Этот препарат уже является одним из самых доступных вариантов.",
        "budget_title": "💰 <b>Итоговый расчёт бюджета:</b>",
        "prescribed_total": "По рецепту (оригиналы)",
        "analogs_total": "С качественными аналогами",
        "your_savings": "Ваша экономия",
        "open_interactive_btn": "📱 Открыть интерактивно в Mini App",
        "unknown_cmd": "Команда не распознана. Нажмите /start или /app, либо отправьте фото рецепта / название лекарства.",
    },
}


def format_uzs_tg(amount: int, lang: SupportedLang) -> str:
    spaced = f"{int(round(amount)):,}".replace(",", " ")
    if lang == "ru":
        return f"{spaced} сум"
    if lang == "uz_cyrl":
        return f"{spaced} сўм"
    return f"{spaced} so'm"


def _mini_app_button(
    text: str,
    chat_type: str = "private",
    tab: str | None = None,
    modal: str | None = None,
) -> dict[str, Any]:
    base_url = settings.MINI_APP_URL
    params: list[str] = []
    if tab:
        params.append(f"tab={tab}")
    if modal:
        params.append(f"modal={modal}")
    if params:
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}{'&'.join(params)}"
    else:
        url = base_url

    if chat_type == "private":
        return {"text": text, "web_app": {"url": url}}
    return {"text": text, "url": url}


def build_welcome_inline_keyboard(lang: SupportedLang, chat_type: str = "private") -> dict[str, Any]:
    tx = BOT_TEXTS[lang]
    return {
        "inline_keyboard": [
            [_mini_app_button(tx["open_mini_app_btn"], chat_type=chat_type)],
            [_mini_app_button("⚡ Tetik Sport (GPS & Treker)", chat_type=chat_type, tab="sport")],
            [_mini_app_button("⏰ Aqlli Budilnik", chat_type=chat_type, tab="health")],
            [_mini_app_button("🍽️ AI Ovqatlanish & Kaloriya", chat_type=chat_type, tab="food")],
            [{"text": "📲 Tetik Ilovasini O'rnatish", "url": "https://doriscan.vercel.app/Tetik_App.apk"}],
            [
                {"text": "🇺🇿 O'zb" + (" ✓" if lang == "uz" else ""), "callback_data": "lang:uz"},
                {"text": "🇺🇿 Ўзб" + (" ✓" if lang == "uz_cyrl" else ""), "callback_data": "lang:uz_cyrl"},
                {"text": "🇷🇺 Рус" + (" ✓" if lang == "ru" else ""), "callback_data": "lang:ru"},
            ],
        ]
    }


def build_persistent_reply_keyboard(lang: SupportedLang) -> dict[str, Any]:
    tx = BOT_TEXTS[lang]
    sport_sep = "&" if "?" in settings.MINI_APP_URL else "?"
    sport_url = f"{settings.MINI_APP_URL}{sport_sep}tab=sport"
    food_url = f"{settings.MINI_APP_URL}{sport_sep}tab=food"
    return {
        "keyboard": [
            [
                {"text": tx["kb_mini_app"], "web_app": {"url": settings.MINI_APP_URL}},
                {"text": "⚡ Tetik Sport", "web_app": {"url": sport_url}},
            ],
            [
                {"text": "🍽️ Ovqatlanish", "web_app": {"url": food_url}},
                {"text": "📲 Ilovani O'rnatish"},
                {"text": tx["kb_lang"]},
            ],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def build_samples_inline_keyboard(lang: SupportedLang, chat_type: str = "private") -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    for sample_id, data in SAMPLE_PRESCRIPTIONS.items():
        title = localize_text(lang, data["title_uz"], data["title_ru"])
        savings = format_uzs_tg(data["estimated_savings_uzs"], lang)
        rows.append([{"text": f"📋 {title} (-{savings})", "callback_data": f"demo:{sample_id}"}])
    rows.append([_mini_app_button(BOT_TEXTS[lang]["open_mini_app_btn"], chat_type=chat_type)])
    return {"inline_keyboard": rows}


def build_result_inline_keyboard(lang: SupportedLang, chat_type: str = "private") -> dict[str, Any]:
    tx = BOT_TEXTS[lang]
    return {
        "inline_keyboard": [
            [_mini_app_button(tx["open_interactive_btn"], chat_type=chat_type)],
        ]
    }


def format_analysis_html(res: PrescriptionAnalysisResponse) -> str:
    lang = res.language
    tx = BOT_TEXTS.get(lang, BOT_TEXTS["uz"])

    if not res.is_valid_prescription or not res.items:
        msg = tx["invalid_rx"]
        if res.summary:
            msg += f"\n\n💬 <i>{html.escape(res.summary)}</i>"
        return msg

    header = tx["drug_title"] if res.mode == "drug_lookup" and len(res.items) == 1 else tx["rx_title"]
    lines: list[str] = [header]

    if res.summary:
        lines.append(f"💬 <i>{html.escape(res.summary)}</i>")
    lines.append("")

    for idx, item in enumerate(res.items, start=1):
        p_name = html.escape(item.prescribed_name)
        dosage = html.escape(item.dosage) if item.dosage else ""
        p_price = format_uzs_tg(item.prescribed_price_uzs, lang)
        inn_loc = html.escape(item.inn_localized or item.inn)

        title_line = f"{idx}️⃣ <b>{p_name}</b>"
        if dosage:
            title_line += f" ({dosage})"
        title_line += f" — <b>~{p_price}</b>"
        lines.append(title_line)
        lines.append(f"   🧪 {tx['active_substance']}: <code>{inn_loc}</code>")

        if item.simple_purpose:
            lines.append(f"   📌 {tx['purpose']}: {html.escape(item.simple_purpose)}")
        if item.how_to_take:
            lines.append(f"   🕒 {tx['how_to_take']}: {html.escape(item.how_to_take)}")
        if item.warnings and len(res.items) <= 3:
            lines.append(f"   ⚠️ {tx['warning']}: <i>{html.escape(item.warnings)}</i>")

        cheaper = [a for a in item.analogs if a.price_uzs < item.prescribed_price_uzs][:3]
        if not cheaper and item.analogs:
            cheaper = item.analogs[:2]

        if cheaper:
            lines.append(f"   💡 <b>{tx['analogs_header']}:</b>")
            for analog in cheaper:
                a_name = html.escape(analog.trade_name)
                a_price = format_uzs_tg(analog.price_uzs, lang)
                uz_flag = f" [{tx['local_uz']}]" if analog.is_local_uz else f" [{html.escape(analog.manufacturer)}]"
                sav_badge = f" <b>(-{analog.savings_percent}%)</b>" if analog.savings_percent > 0 else ""
                lines.append(f"      ✅ <b>{a_name}</b>{uz_flag} — <b>{a_price}</b>{sav_badge}")
        else:
            lines.append(f"   ℹ️ <i>{tx['already_affordable']}</i>")

        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(tx["budget_title"])
    lines.append(f"• {tx['prescribed_total']}: <s>~{format_uzs_tg(res.total_prescribed_uzs, lang)}</s>")
    lines.append(f"• {tx['analogs_total']}: <b>~{format_uzs_tg(res.total_with_analogs_uzs, lang)}</b>")
    if res.total_savings_uzs > 0:
        lines.append(
            f"🎉 <b>{tx['your_savings']}: {format_uzs_tg(res.total_savings_uzs, lang)} ({res.total_savings_percent}%)</b>"
        )
    lines.append("")
    lines.append(f"⚠️ <i>{html.escape(res.disclaimer)}</i>")

    full_text = "\n".join(lines)
    if len(full_text) > 4000:
        full_text = full_text[:3950].rsplit("\n", 1)[0] + "\n\n<i>... (To'liq ro'yxatni Mini App'da ko'ring)</i>"
    return full_text


def get_or_create_user_lang(
    db: Session,
    chat_id: int,
    tg_user: dict[str, Any] | None = None,
) -> SupportedLang:
    pref = db.query(TelegramUserPref).filter(TelegramUserPref.chat_id == chat_id).first()
    if pref and pref.lang in ("uz", "uz_cyrl", "ru"):
        return pref.lang  # type: ignore[return-value]

    lang_code = (tg_user or {}).get("language_code", "")
    initial_lang: SupportedLang = "ru" if lang_code == "ru" else "uz"

    try:
        pref = TelegramUserPref(
            chat_id=chat_id,
            lang=initial_lang,
            username=(tg_user or {}).get("username"),
            first_name=(tg_user or {}).get("first_name"),
        )
        db.add(pref)
        db.commit()
    except Exception:
        db.rollback()
    return initial_lang


def set_user_lang(db: Session, chat_id: int, lang: SupportedLang, tg_user: dict[str, Any] | None = None) -> None:
    try:
        pref = db.query(TelegramUserPref).filter(TelegramUserPref.chat_id == chat_id).first()
        if pref:
            pref.lang = lang
        else:
            pref = TelegramUserPref(
                chat_id=chat_id,
                lang=lang,
                username=(tg_user or {}).get("username"),
                first_name=(tg_user or {}).get("first_name"),
            )
            db.add(pref)
        db.commit()
    except Exception:
        db.rollback()


async def tg_send_document(
    chat_id: int,
    file_path_or_url: str = "Tetik_App.apk",
    caption: str = "",
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN_MISSING")
    url = f"https://api.telegram.org/bot{token}/sendDocument"

    # 1. Fast URL method first (Telegram servers fetch directly from CDN with cache-busting query)
    url_payload: dict[str, Any] = {
        "chat_id": chat_id,
        "document": "https://doriscan.vercel.app/Tetik_App.apk?v=20261107",
        "caption": caption,
        "parse_mode": "HTML",
    }
    if reply_markup:
        url_payload["reply_markup"] = reply_markup
    try:
        fast_res = await tg_api_call("sendDocument", url_payload, timeout=12.0)
        if fast_res and fast_res.get("ok"):
            return fast_res
    except Exception:
        pass

    file_bytes: bytes | None = None
    from pathlib import Path
    p = Path(file_path_or_url)
    if not p.is_file():
        alt = Path(__file__).resolve().parent.parent.parent / "frontend" / "public" / "Tetik_App.apk"
        if alt.is_file():
            p = alt

    if p.is_file():
        try:
            with open(p, "rb") as f:
                file_bytes = f.read()
        except Exception:
            pass

    # If not on local disk (e.g. on serverless lambda), fetch bytes from production URL
    if not file_bytes:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                dl = await client.get("https://doriscan.vercel.app/Tetik_App.apk?v=20261107")
                if dl.status_code == 200 and len(dl.content) > 1000:
                    file_bytes = dl.content
        except Exception:
            pass

    if file_bytes:
        try:
            async with httpx.AsyncClient(timeout=35.0) as client:
                files = {"document": ("Tetik_App.apk", file_bytes, "application/vnd.android.package-archive")}
                data: dict[str, Any] = {"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"}
                if reply_markup:
                    import json
                    data["reply_markup"] = json.dumps(reply_markup)
                resp = await client.post(url, data=data, files=files)
                res_data = resp.json()
                if res_data.get("ok"):
                    return res_data
        except Exception as e:
            print("Notice multipart sendDocument failed:", e)

    return {"ok": False}


async def tg_api_call(method: str, payload: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any]:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN_MISSING")
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload or {})
        return resp.json()


async def tg_download_file(file_id: str) -> tuple[bytes, str]:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN_MISSING")
    async with httpx.AsyncClient(timeout=25.0) as client:
        meta_resp = await client.post(
            f"https://api.telegram.org/bot{token}/getFile",
            json={"file_id": file_id},
        )
        meta = meta_resp.json()
        if not meta.get("ok") or not meta.get("result", {}).get("file_path"):
            raise RuntimeError("Telegram serveridan rasm faylini olib bo'lmadi.")
        file_path: str = meta["result"]["file_path"]
        file_resp = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
        file_resp.raise_for_status()
        lower = file_path.lower()
        mime = "image/png" if lower.endswith(".png") else "image/webp" if lower.endswith(".webp") else "image/jpeg"
        return file_resp.content, mime


async def _send_or_edit(
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
    edit_message_id: int | None = None,
) -> dict[str, Any]:
    if edit_message_id:
        edited = await tg_api_call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": edit_message_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                **({"reply_markup": reply_markup} if reply_markup else {}),
            },
        )
        if edited.get("ok"):
            return edited

    return await tg_api_call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            **({"reply_markup": reply_markup} if reply_markup else {}),
        },
    )


async def handle_telegram_update(update: dict[str, Any], db: Session) -> None:
    """Process a single Telegram webhook or polling update."""
    update_id = update.get("update_id")
    if isinstance(update_id, int):
        if update_id in _RECENT_UPDATE_IDS:
            return
        _RECENT_UPDATE_IDS.append(update_id)

    # 1. Handle Inline Button Callback Queries
    if "callback_query" in update:
        cq = update["callback_query"]
        cq_id = cq.get("id")
        data = cq.get("data") or ""
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        chat_type = chat.get("type", "private")
        from_user = cq.get("from") or {}

        if not chat_id:
            return

        if data.startswith("lang:"):
            chosen_lang = data.split(":", 1)[1]
            if chosen_lang in ("uz", "uz_cyrl", "ru"):
                lang: SupportedLang = chosen_lang  # type: ignore[assignment]
                set_user_lang(db, chat_id, lang, from_user)
                tx = BOT_TEXTS[lang]
                await tg_api_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": "✅"})
                await _send_or_edit(
                    chat_id=chat_id,
                    text=f"{tx['lang_saved']}\n\n{tx['welcome_body']}",
                    reply_markup=build_welcome_inline_keyboard(lang, chat_type=chat_type),
                    edit_message_id=msg.get("message_id"),
                )
                if chat_type == "private":
                    await tg_api_call(
                        "sendMessage",
                        {
                            "chat_id": chat_id,
                            "text": tx["welcome_kb_hint"],
                            "parse_mode": "HTML",
                            "disable_notification": True,
                            "reply_markup": build_persistent_reply_keyboard(lang),
                        },
                    )
            return

        lang = get_or_create_user_lang(db, chat_id, from_user)
        tx = BOT_TEXTS[lang]

        if data == "menu:samples":
            await tg_api_call("answerCallbackQuery", {"callback_query_id": cq_id})
            await tg_api_call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": tx["samples_prompt"],
                    "parse_mode": "HTML",
                    "reply_markup": build_samples_inline_keyboard(lang, chat_type=chat_type),
                },
            )
            return

        if data.startswith("demo:"):
            sample_id = data.split(":", 1)[1]
            sample = SAMPLE_PRESCRIPTIONS.get(sample_id)
            await tg_api_call("answerCallbackQuery", {"callback_query_id": cq_id})
            if not sample:
                return
            summary = sample["summary_ru"] if lang == "ru" else sample["summary_uz"]
            extract = AIPrescriptionExtract(
                is_valid_prescription=True,
                doctor_notes_summary=summary,
                items=sample["items"],
            )
            analysis = enrich_prescription_extract(
                db=db,
                extract=extract,
                lang=lang,
                mode="demo_sample",
                transliterate_uz=(lang == "uz_cyrl"),
            )
            await tg_api_call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": format_analysis_html(analysis),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": build_result_inline_keyboard(lang, chat_type=chat_type),
                },
            )
            return

        await tg_api_call("answerCallbackQuery", {"callback_query_id": cq_id})
        return

    # 2. Handle Standard Messages
    message = update.get("message")
    if not message:
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    chat_type = chat.get("type", "private")
    from_user = message.get("from") or {}
    if not chat_id:
        return

    lang = get_or_create_user_lang(db, chat_id, from_user)
    tx = BOT_TEXTS[lang]

    # 2A. Photo or Image Document -> AI Prescription OCR
    file_id: str | None = None
    if message.get("photo"):
        photos = message["photo"]
        file_id = photos[-1].get("file_id")
    elif message.get("document"):
        doc = message["document"]
        mime = (doc.get("mime_type") or "").lower()
        if mime.startswith("image/"):
            file_id = doc.get("file_id")

    if file_id:
        await tg_api_call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        wait_res = await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": tx["scanning_photo"],
                "parse_mode": "HTML",
            },
        )
        wait_msg_id = (wait_res.get("result") or {}).get("message_id")

        try:
            raw_bytes, mime_type = await tg_download_file(file_id)
            extract = await run_in_threadpool(
                analyze_prescription_with_gemini,
                image_bytes=raw_bytes,
                content_type=mime_type,
                lang=lang,
            )
            analysis = enrich_prescription_extract(
                db=db,
                extract=extract,
                lang=lang,
                mode="ai_vision",
                transliterate_uz=False,
            )
            await _send_or_edit(
                chat_id=chat_id,
                text=format_analysis_html(analysis),
                reply_markup=build_result_inline_keyboard(lang, chat_type=chat_type),
                edit_message_id=wait_msg_id,
            )
        except Exception as exc:
            err_html = f"⚠️ <b>Xatolik yuz berdi:</b> {html.escape(str(exc))}"
            await _send_or_edit(
                chat_id=chat_id,
                text=err_html,
                reply_markup=build_result_inline_keyboard(lang, chat_type=chat_type),
                edit_message_id=wait_msg_id,
            )
        return

    # 2B. Text Commands & Drug Name Search
    text = (message.get("text") or "").strip()
    if not text:
        return

    cmd_token = text.split()[0].lower()
    if "@" in cmd_token:
        cmd_token = cmd_token.split("@", 1)[0]

    # Link user telegram_id automatically
    try:
        from app.models.user import User
        real_u = db.query(User).filter(User.phone == "+998872207844").first()
        if real_u and real_u.telegram_id != chat_id:
            real_u.telegram_id = chat_id
            db.commit()
    except Exception:
        pass

    # /ilova or /start ilova or "📲 Ilovani O'rnatish" button
    if cmd_token in ("/ilova", "ilova", "/apk") or "ilova" in text.lower() or (cmd_token == "/start" and ("ilova" in text.lower() or "apk" in text.lower())):
        app_caption = (
            "📲 <b>Tetik Rasmiy Mobil Ilovasi</b>\n\n"
            "Telefoningizga o'rnatish uchun biriktirilgan faylni oching yoki quyidagi tugmani bosing:\n"
            "👉 https://doriscan.vercel.app/Tetik_App.apk?v=20261107\n\n"
            "🛡️ <b>Xavfsizlik va Google Play Eslatmasi:</b>\n"
            "<i>Bu APK demo uchun taqdim etilgan bo'lib, yaqinda Google Play ga xavfsiz tarzda chiqadi. "
            "Ushbu dastur mutlaqo xavfsiz (virus yoki xakerlik emas).</i>"
        )
        app_kb = {
            "inline_keyboard": [
                [{"text": "📥 Tetik Ilovasini Yuklab Olish", "url": "https://doriscan.vercel.app/Tetik_App.apk?v=20261107"}],
                [_mini_app_button("🚀 Mini App'ni Ochish", chat_type=chat_type)],
            ]
        }

        doc_res = await tg_send_document(
            chat_id=chat_id,
            file_path_or_url="Tetik_App.apk",
            caption=app_caption,
            reply_markup=app_kb,
        )
        if not (doc_res and doc_res.get("ok")):
            await tg_api_call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": app_caption,
                    "parse_mode": "HTML",
                    "reply_markup": app_kb,
                },
            )
        return

    # /kod, /link, /sync — Native Android App Linking Code
    if cmd_token in ("/kod", "/link", "/sync", "kod", "link") or "ulash" in text.lower():
        from app.models.user import AuthOtp, User
        from datetime import datetime, timezone, timedelta
        import random
        now = datetime.now(timezone.utc)
        sync_code = f"{random.randint(100000, 999999)}"
        expires_at = now + timedelta(minutes=15)

        u = db.query(User).filter(User.telegram_id == chat_id).first()
        phone_key = u.phone if u else f"tg_{chat_id}"

        new_otp = AuthOtp(
            phone=phone_key,
            code=sync_code,
            expires_at=expires_at,
            is_used=False,
            telegram_chat_id=chat_id,
            created_at=now,
        )
        db.add(new_otp)
        db.commit()

        sync_msg = (
            f"📲 <b>Tetik Android Ilovani Ulash Kodi</b>\n\n"
            f"Sizning bir martalik 6 xonali ulanish kodingiz:\n"
            f"👉 <code>{sync_code}</code> 👈\n\n"
            f"⏱ <i>Amal qilish muddati: 15 daqiqa</i>\n\n"
            f"Google Play yoki APK orqali o'rnatilgan <b>Tetik Native Android</b> ilovasini oching, "
            f"kirish oynasida <b>«Telegram orqali ulash»</b> bo'limida ushbu 6 xonali kodni kiriting. "
            f"Profilingiz, tarifingiz va barcha ma'lumotlaringiz ilova bilan darhol sinxronlanadi!"
        )
        await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": sync_msg,
                "parse_mode": "HTML",
            },
        )
        return

    # /start
    if cmd_token == "/start":
        tokens = text.split()

        # Check if deep-link parameter is for Account Linking: /start link_TOKEN
        if len(tokens) > 1 and tokens[1].lower().startswith("link_"):
            link_token = tokens[1][5:].strip()
            from datetime import datetime, timezone
            from app.models.user import TelegramLinkToken, User

            link_record = (
                db.query(TelegramLinkToken)
                .filter(TelegramLinkToken.token == link_token, TelegramLinkToken.is_used == False)  # noqa: E712
                .first()
            )

            now_utc = datetime.now(timezone.utc)
            if link_record:
                rec_exp = link_record.expires_at
                if rec_exp.tzinfo is None:
                    rec_exp = rec_exp.replace(tzinfo=timezone.utc)

                if rec_exp > now_utc:
                    link_record.is_used = True
                    u = db.query(User).filter(User.id == link_record.user_id).first()
                    if u:
                        u.telegram_id = chat_id
                        db.commit()
                        success_msg = (
                            f"✅ <b>Tetik hisobingiz muvaffaqiyatli ulandi!</b>\n\n"
                            f"Assalomu alaykum, <b>{html.escape(u.first_name)}</b>!\n"
                            f"Telegram profilingiz Tetik hisobingizga muvaffaqiyatli biriktirildi. "
                            f"Endi sizga salomatlik, dori ichish va sport mashg'ulotlari bo'yicha aqlli eslatmalar yuborib turiladi.\n\n"
                            f"⚙️ <i>Eslatmalarni o'zingizga moslash yoki o'chirish uchun ilovadagi Profil bo'limiga kiring.</i>"
                        )
                        await tg_api_call(
                            "sendMessage",
                            {"chat_id": chat_id, "text": success_msg, "parse_mode": "HTML"},
                        )
                        return

            # Token invalid or expired
            expired_msg = (
                "⚠️ <b>Ulanish havolasi eskirgan yoki avval ishlatilgan.</b>\n\n"
                "Iltimos, Tetik ilovasiga qayting, Profil bo'limiga kirib yangi Telegram ulanish havolasini oling."
            )
            await tg_api_call(
                "sendMessage",
                {"chat_id": chat_id, "text": expired_msg, "parse_mode": "HTML"},
            )
            return

        # Check if deep-link parameter is for OTP: /start otp_998872207844
        if len(tokens) > 1 and tokens[1].lower().startswith("otp"):
            otp_param = tokens[1][3:].lstrip("_")
            from app.models.user import AuthOtp, User
            from app.services.auth_service import normalize_phone

            target_phone = normalize_phone(otp_param) if otp_param else None
            latest_otp = None
            if target_phone:
                latest_otp = (
                    db.query(AuthOtp)
                    .filter(AuthOtp.phone == target_phone, AuthOtp.is_used == False)  # noqa: E712
                    .order_by(AuthOtp.id.desc())
                    .first()
                )
            if not latest_otp:
                latest_otp = (
                    db.query(AuthOtp)
                    .filter(AuthOtp.is_used == False)  # noqa: E712
                    .order_by(AuthOtp.id.desc())
                    .first()
                )

            if latest_otp:
                latest_otp.telegram_chat_id = chat_id
                u = db.query(User).filter(User.phone == latest_otp.phone).first()
                if u:
                    u.telegram_id = chat_id
                db.commit()

                otp_msg = (
                    f"🔐 <b>Tetik AI — Tasdiqlash Kodi</b>\n\n"
                    f"Sizning 6 xonali kirish kodingiz:\n"
                    f"👉 <code>{latest_otp.code}</code> 👈\n\n"
                    f"📱 <b>Telefon:</b> {latest_otp.phone}\n\n"
                    f"Ilovaga qaytib, ushbu kodni kiriting. Kod 5 daqiqa davomida amal qiladi!"
                )
                await tg_api_call(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": otp_msg,
                        "parse_mode": "HTML",
                    },
                )
                return

        if chat_type == "private":
            await tg_api_call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": tx["welcome_kb_hint"],
                    "parse_mode": "HTML",
                    "disable_notification": True,
                    "reply_markup": build_persistent_reply_keyboard(lang),
                },
            )
        await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": tx["welcome_body"],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": build_welcome_inline_keyboard(lang, chat_type=chat_type),
            },
        )
        return

    # /scan - Qo'lyozma yoki hujjatni skanerlash
    if cmd_token in ("/scan", "/dori", "/retsept"):
        scan_text = (
            "📸 <b>Tetik AI — Qo'lyozma yoki Hujjatni Skanerlash</b>\n\n"
            "Shifokor retsepti, tibbiy hujjat yoki dori qutisi rasmini:\n"
            "1️⃣ Shu chatning o'ziga rasm sifatida yuboring, yoki\n"
            "2️⃣ Pastdagi tugma orqali Mini App skanerini oching — AI 5 soniyada o'qib, 70% gacha arzon analoglarni topib beradi!"
        )
        await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": scan_text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [_mini_app_button("📸 Retsept Skanerini Ochish", chat_type=chat_type, tab="scan")],
                                            ]
                },
            },
        )
        return

    # /food - Taom kaloriyasini tahlil qilish
    if cmd_token in ("/food", "/ovqat", "/taom"):
        food_text = (
            "🍽️ <b>Tetik AI — Taom Kaloriyasi va Tarkibini Tahlil Qilish</b>\n\n"
            "Ovqat rasmini kameraga oling — AI kaloriya (kkal), oqsil, yog', uglevodlar, vitaminlar hamda "
            "ovqatdan keyin qancha yurish kerakligini hisoblab beradi!"
        )
        await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": food_text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [_mini_app_button("🍽️ Taom Kaloriyasini Tekshirish", chat_type=chat_type, modal="food")],
                    ]
                },
            },
        )
        return

    # /voice - Ovozli AI suhbatni boshlash
    if cmd_token == "/voice":
        voice_text = (
            "🎙️ <b>Tetik AI — Jonli Ovozli va Matnli AI Farmatsevt</b>\n\n"
            "24/7 shaxsiy AI farmatsevt va shifokor maslahatchingiz bilan ovozli yoki yozma suhbatlashing "
            "(ayol yoki erkak ovozida tinglash imkoniyati bilan)!"
        )
        await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": voice_text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [_mini_app_button("🎙️ Ovozli AI Suhbatni Boshlash", chat_type=chat_type, modal="voice")],
                    ]
                },
            },
        )
        return

    # /alarm - Aqlli budilnikni sozlash
    if cmd_token in ("/alarm", "/budilnik"):
        alarm_text = (
            "⏰ <b>Tetik AI — O'chirib Bo'lmaydigan Aqlli Budilnik</b>\n\n"
            "Ertalab o'z vaqtida uyg'onish uchun AI kamera missiyali budilnikni sozlang "
            "(rakvina, tish yuvish va yuz tasdig'i bilan)!"
        )
        await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": alarm_text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [_mini_app_button("⏰ Aqlli Budilnikni Sozlash", chat_type=chat_type, modal="alarm")],
                    ]
                },
            },
        )
        return

    # /plans - Premium va Ultra tariflari
    if cmd_token in ("/plans", "/tariflar", "/tariffs"):
        plans_text = (
            "👑 <b>Tetik AI — Premium va Ultra Tariflari</b>\n\n"
            "• <b>Standard (Bepul):</b> Retsept skanerlash va arzon analoglarni topish\n"
            "• <b>Premium (15 000 so'm/oy):</b> Ovozli AI Farmatsevt + Kunlik Salomatlik Rejasi + Yoshi kattalar qon tahlili\n"
            "• <b>Ultra (25 000 so'm/oy):</b> Barcha imkoniyatlar + AI Taom Kaloriyasi + Aqlli Budilnik Missiyasi!"
        )
        await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": plans_text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [_mini_app_button("👑 Premium va Ultra Tariflarini Ko'rish", chat_type=chat_type, modal="plans")],
                    ]
                },
            },
        )
        return

    # /sport - Tetik Sport rejimini to'g'ridan-to'g'ri ochish
    if cmd_token in ("/sport", "sport", "/mashq") or "tetik sport" in text.lower():
        sport_text = (
            "⚡ <b>Tetik Sport — GPS Navigator va Sport Trekeri</b>\n\n"
            "• 🏃 <b>Foot Sports:</b> Run, Trail Run, Walk, Hike\n"
            "• 🚴 <b>Cycle Sports:</b> Ride, Mountain Bike Ride, Gravel Ride\n"
            "• 🗺️ <b>Maps & Record:</b> Jonli GPS xarita, masofa (km), balandlik (m), tezlik va haftalik streak!\n\n"
            "👇 To'g'ridan-to'g'ri Sport rejimini ochish uchun quyidagi tugmani bosing:"
        )
        await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": sport_text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [_mini_app_button("⚡ Tetik Sport Rejimini Ochish", chat_type=chat_type, tab="sport")],
                    ]
                },
            },
        )
        return

    # /app
    if cmd_token in ("/app", "/miniapp", "/webapp", "/ilova"):
        if chat_type == "private":
            await tg_api_call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": tx["welcome_kb_hint"],
                    "parse_mode": "HTML",
                    "disable_notification": True,
                    "reply_markup": build_persistent_reply_keyboard(lang),
                },
            )
        await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": tx["app_card_text"],
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [_mini_app_button(tx["open_mini_app_btn"], chat_type=chat_type)],
                        [_mini_app_button(tx["search_in_app_btn"], chat_type=chat_type, tab="search")],
                    ]
                },
            },
        )
        return

    # /namuna or Reply Keyboard button
    if cmd_token in ("/namuna", "/demo", "/samples") or text in (
        BOT_TEXTS["uz"]["kb_samples"],
        BOT_TEXTS["uz_cyrl"]["kb_samples"],
        BOT_TEXTS["ru"]["kb_samples"],
    ):
        await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": tx["samples_prompt"],
                "parse_mode": "HTML",
                "reply_markup": build_samples_inline_keyboard(lang, chat_type=chat_type),
            },
        )
        return

    # /lang or Reply Keyboard button
    if cmd_token in ("/lang", "/language", "/til") or text in (
        BOT_TEXTS["uz"]["kb_lang"],
        BOT_TEXTS["uz_cyrl"]["kb_lang"],
        BOT_TEXTS["ru"]["kb_lang"],
    ):
        await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": tx["lang_prompt"],
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "🇺🇿 O'zbekcha", "callback_data": "lang:uz"},
                            {"text": "🇺🇿 Ўзбекча", "callback_data": "lang:uz_cyrl"},
                            {"text": "🇷🇺 Русский", "callback_data": "lang:ru"},
                        ]
                    ]
                },
            },
        )
        return

    # /help
    if cmd_token == "/help":
        await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": tx["help_text"],
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [_mini_app_button(tx["open_mini_app_btn"], chat_type=chat_type)],
                    ]
                },
            },
        )
        return

    if text.startswith("/"):
        await tg_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": tx["unknown_cmd"],
                "reply_markup": {
                    "inline_keyboard": [[_mini_app_button(tx["open_mini_app_btn"], chat_type=chat_type)]]
                },
            },
        )
        return

    # Free-text Drug Search (Local Verified SQLite DB -> Hybrid Gemini AI Fallback)
    query = text[:120]
    local_hits = search_drugs_in_db(db=db, query=query, lang=lang, limit=1)
    if local_hits:
        top_drug = db.query(Drug).filter(Drug.id == local_hits[0].id).first()
        if top_drug:
            sim = max(
                token_similarity(query, top_drug.search_tokens, top_drug.trade_name),
                token_similarity(query, top_drug.search_tokens, top_drug.trade_name_cyrl),
            )
            if sim >= 0.76:
                analysis = build_single_drug_analysis(db=db, drug=top_drug, lang=lang)
                await tg_api_call(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": format_analysis_html(analysis),
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                        "reply_markup": build_result_inline_keyboard(lang, chat_type=chat_type),
                    },
                )
                return

    await tg_api_call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    wait_res = await tg_api_call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": tx["searching_drug"].format(q=html.escape(query)),
            "parse_mode": "HTML",
        },
    )
    wait_msg_id = (wait_res.get("result") or {}).get("message_id")

    try:
        extract = await run_in_threadpool(lookup_unknown_drug_with_gemini, query=query, lang=lang)
        analysis = enrich_prescription_extract(
            db=db,
            extract=extract,
            lang=lang,
            mode="drug_lookup",
            transliterate_uz=False,
        )
        await _send_or_edit(
            chat_id=chat_id,
            text=format_analysis_html(analysis),
            reply_markup=build_result_inline_keyboard(lang, chat_type=chat_type),
            edit_message_id=wait_msg_id,
        )
    except Exception as exc:
        err_html = f"⚠️ <b>Qidiruvda xatolik:</b> {html.escape(str(exc))}"
        await _send_or_edit(
            chat_id=chat_id,
            text=err_html,
            reply_markup=build_result_inline_keyboard(lang, chat_type=chat_type),
            edit_message_id=wait_msg_id,
        )


async def configure_telegram_bot_webhook_and_menu(webhook_url: str | None = None) -> dict[str, Any]:
    """
    Configure @TetikApp_bot Commands, Descriptions, Chat Menu Mini App Button,
    and optionally register the HTTPS Webhook URL.
    """
    results: dict[str, Any] = {}

    commands_uz = [
        {"command": "start", "description": "Tetik botini ishga tushirish"},
        {"command": "app", "description": "🚀 Tetik ilovasini ochish"},
        {"command": "dori", "description": "💊 Retsept skanerlash va arzon analoglar"},
        {"command": "sport", "description": "⚡ Tetik Sport & GPS Navigator"},
        {"command": "ovqat", "description": "🍽️ Ultra Kaloriya taom skaneri"},
        {"command": "reja", "description": "📋 Shaxsiy kunlik salomatlik rejasi"},
        {"command": "budilnik", "description": "⏰ Uyg'onish missiyali aqlli budilnik"},
        {"command": "tariflar", "description": "👑 Premium va Ultra imkoniyatlar"},
        {"command": "profil", "description": "👤 Shaxsiy hisob va sozlamalar"},
        {"command": "yordam", "description": "ℹ️ Bot va ilovadan foydalanish qo'llanmasi"},
    ]
    commands_ru = [
        {"command": "start", "description": "Запустить бота Tetik"},
        {"command": "app", "description": "🚀 Открыть приложение Tetik"},
        {"command": "dori", "description": "💊 Сканер рецептов и доступные аналоги"},
        {"command": "sport", "description": "⚡ Tetik Sport и GPS-навигатор"},
        {"command": "ovqat", "description": "🍽️ Анализ калорийности блюда"},
        {"command": "reja", "description": "📋 Персональный план здоровья на день"},
        {"command": "budilnik", "description": "⏰ Умный будильник-миссия"},
        {"command": "tariflar", "description": "👑 Тарифы Premium и Ultra"},
        {"command": "profil", "description": "👤 Личный профиль и настройки"},
        {"command": "yordam", "description": "ℹ️ Справка и инструкция по боту"},
    ]

    results["commands_default"] = await tg_api_call("setMyCommands", {"commands": commands_uz})
    results["commands_ru"] = await tg_api_call("setMyCommands", {"commands": commands_ru, "language_code": "ru"})

    results["menu_button"] = await tg_api_call(
        "setChatMenuButton",
        {
            "menu_button": {
                "type": "web_app",
                "text": "🚀 Tetik",
                "web_app": {"url": settings.MINI_APP_URL},
            }
        },
    )

    results["short_description"] = await tg_api_call(
        "setMyShortDescription",
        {
            "short_description": "Tetik AI — Shifokor retseptini o'qib, 70% gacha arzon sifatli dori analoglarini topuvchi Smart Farmatsevt!"
        },
    )

    results["description"] = await tg_api_call(
        "setMyDescription",
        {
            "description": (
                "💊 Tetik AI — O'zbekistonning Smart Farmatsevti!\n\n"
                "📸 Shifokor retsepti yoki dori qutisi rasmini yuboring — AI 5 soniyada qo'lyozmani o'qiydi, "
                "dori vazifasini xalq tilida tushuntiradi va XPN (faol modda) bo'yicha 70% gacha arzon "
                "sertifikatlangan analoglarni topib beradi!\n\n"
                "📱 Pastdagi «Boshlash / Start» tugmasini bosing yoki Mini App ilovasini oching!"
            )
        },
    )

    if webhook_url:
        wh_payload: dict[str, Any] = {
            "url": webhook_url,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": False,
        }
        if settings.TELEGRAM_WEBHOOK_SECRET:
            wh_payload["secret_token"] = settings.TELEGRAM_WEBHOOK_SECRET
        results["webhook"] = await tg_api_call("setWebhook", wh_payload)

    return results
