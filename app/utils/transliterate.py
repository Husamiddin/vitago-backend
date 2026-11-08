import re

APOSTROPHES = ("‘", "’", "`", "ʼ", "ʻ", "'")

# Digraphs must be processed before single letters
DIGRAPH_MAP = [
    ("O'", "Ў"),
    ("o'", "ў"),
    ("G'", "Ғ"),
    ("g'", "ғ"),
    ("Sh", "Ш"),
    ("SH", "Ш"),
    ("sh", "ш"),
    ("Ch", "Ч"),
    ("CH", "Ч"),
    ("ch", "ч"),
    ("Yo", "Ё"),
    ("YO", "Ё"),
    ("yo", "ё"),
    ("Yu", "Ю"),
    ("YU", "Ю"),
    ("yu", "ю"),
    ("Ya", "Я"),
    ("YA", "Я"),
    ("ya", "я"),
    ("Ts", "Ц"),
    ("TS", "Ц"),
    ("ts", "ц"),
]

SINGLE_CHAR_MAP = {
    "A": "А", "a": "а",
    "B": "Б", "b": "б",
    "D": "Д", "d": "д",
    "F": "Ф", "f": "ф",
    "G": "Г", "g": "г",
    "H": "Ҳ", "h": "ҳ",
    "I": "И", "i": "и",
    "J": "Ж", "j": "ж",
    "K": "К", "k": "к",
    "L": "Л", "l": "л",
    "M": "М", "m": "м",
    "N": "Н", "n": "н",
    "O": "О", "o": "о",
    "P": "П", "p": "п",
    "Q": "Қ", "q": "қ",
    "R": "Р", "r": "р",
    "S": "С", "s": "с",
    "T": "Т", "t": "т",
    "U": "У", "u": "у",
    "V": "В", "v": "в",
    "X": "Х", "x": "х",
    "Y": "Й", "y": "й",
    "Z": "З", "z": "з",
    "'": "ъ",
}

PRESERVE_UNITS = re.compile(r"\b(mg|ml|mkg|g|ME|IU)\b", re.IGNORECASE)


def uz_latn_to_cyrl(text: str) -> str:
    """Convert Uzbek Latin text into grammatically clean Uzbek Cyrillic."""
    if not text:
        return ""

    normalized = text
    for ap in APOSTROPHES:
        normalized = normalized.replace(ap, "'")

    # Word-initial Ye / ye and E / e rules in Uzbek orthography
    normalized = re.sub(r"\bYe", "Е", normalized)
    normalized = re.sub(r"\bYE", "Е", normalized)
    normalized = re.sub(r"\bye", "е", normalized)
    normalized = re.sub(r"\bE", "Э", normalized)
    normalized = re.sub(r"\be", "э", normalized)

    # Protect yo'/yo style combinations (e.g. yo'tal -> йўтал, yo'q -> йўқ)
    normalized = normalized.replace("Yo'", "Йў").replace("yo'", "йў")

    for lat, cyr in DIGRAPH_MAP:
        normalized = normalized.replace(lat, cyr)

    # Remaining E/e inside words -> Е/е
    normalized = normalized.replace("E", "Е").replace("e", "е")

    out_chars: list[str] = []
    for ch in normalized:
        out_chars.append(SINGLE_CHAR_MAP.get(ch, ch))

    result = "".join(out_chars)
    result = result.replace("мг", "мг").replace("мл", "мл")
    return result


def localize_text(lang: str, uz_text: str, ru_text: str, cyrl_override: str | None = None) -> str:
    """Return localized text for 'uz', 'uz_cyrl', or 'ru'."""
    if lang == "ru":
        return ru_text or uz_text
    if lang == "uz_cyrl":
        if cyrl_override:
            return cyrl_override
        return uz_latn_to_cyrl(uz_text)
    return uz_text
