import re
from difflib import SequenceMatcher

CYRILLIC_TO_PHONETIC = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "ғ": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "j",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "қ": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "ў": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "x",
    "ҳ": "x",
    "ц": "s",
    "ч": "ch",
    "ш": "sh",
    "щ": "sh",
    "ъ": "",
    "ы": "i",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def normalize_drug_token(text: str) -> str:
    """
    Convert any Latin / Uzbek Cyrillic / Russian drug string into a unified phonetic key.
    Examples:
      'Цитрамон' / 'Tsitramon' / 'Sitramon' / 'Citramon' -> 'sitramon'
      'Азитромицин' / 'Azitromitsin' / 'Azithromycin' -> 'azitromisin'
      'Но-шпа' / 'No-shpa' / 'Noshpa' -> 'noshpa'
      'Клацид' / 'Klatsid' / 'Clarithromycin' -> 'klasid' / 'klaritromisin'
    """
    if not text:
        return ""

    s = text.lower().strip()
    out = []
    for ch in s:
        if ch in CYRILLIC_TO_PHONETIC:
            out.append(CYRILLIC_TO_PHONETIC[ch])
        else:
            out.append(ch)
    s = "".join(out)

    # Unify medical/pharmaceutical Latin & Uzbek spelling variations
    replacements = [
        ("ph", "f"),
        ("th", "t"),
        ("kh", "x"),
        ("ts", "s"),
        ("tc", "s"),
        ("ck", "k"),
        ("qu", "kv"),
        ("ce", "se"),
        ("ci", "si"),
        ("cy", "si"),
        ("c", "k"),
        ("h", "x"),
        ("q", "k"),
        ("w", "v"),
        ("y", "i"),
        ("o'", "o"),
        ("g'", "g"),
    ]
    for src, dst in replacements:
        s = s.replace(src, dst)

    s = re.sub(r"[^a-z0-9\s]", " ", s)
    # Collapse duplicate adjacent letters (e.g. amoxicillin -> amoksisilin)
    s = re.sub(r"([a-z])\1+", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_search_tokens(*parts: str) -> str:
    tokens: list[str] = []
    for part in parts:
        if part:
            norm = normalize_drug_token(part)
            if norm and norm not in tokens:
                tokens.append(norm)
            compact = norm.replace(" ", "")
            if compact and compact not in tokens:
                tokens.append(compact)
    return " | ".join(tokens)


def token_similarity(query: str, candidate_tokens: str, primary_name: str = "") -> float:
    """Compute fuzzy match score in [0.0, 1.0] between user/AI query and drug record."""
    q_norm = normalize_drug_token(query)
    if not q_norm:
        return 0.0

    # Strip trailing dosage numbers when comparing core brand/INN names if helpful
    q_alpha = re.sub(r"\b\d+\s*(mg|ml|g|mkg|iu|me)?\b", "", q_norm).strip() or q_norm
    q_compact = q_alpha.replace(" ", "")

    p_norm = normalize_drug_token(primary_name) if primary_name else ""
    p_alpha = re.sub(r"\b\d+\s*(mg|ml|g|mkg|iu|me)?\b", "", p_norm).strip() or p_norm

    if q_alpha == p_alpha or q_norm == p_norm:
        return 1.0
    if p_alpha.startswith(q_alpha) and len(q_alpha) >= 3:
        return 0.95

    best = 0.0
    for seg in candidate_tokens.split("|"):
        seg = seg.strip()
        if not seg:
            continue
        if q_compact == seg or q_alpha == seg:
            best = max(best, 0.98)
        elif seg.startswith(q_compact) and len(q_compact) >= 3:
            best = max(best, 0.91)
        elif q_compact in seg and len(q_compact) >= 4:
            best = max(best, 0.85)
        else:
            ratio = SequenceMatcher(None, q_compact, seg.replace(" ", "")).ratio()
            best = max(best, ratio)

    return best
