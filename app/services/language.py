"""Conservative multilingual query handling for Mongolian and English."""
import re
import unicodedata

CYRILLIC = re.compile(r"[\u0400-\u04ff]")
LATIN_WORD = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)?")
TOKEN = re.compile(r"https?://\S+|[A-Za-zА-Яа-яЁёӨөҮү]+|\d+(?:[./-]\d+)*", re.UNICODE)
# Match identifiers before matching words, so a reviewed word inside a code is
# never transliterated (for example policy_ajiltan_2026 or AJILTAN-04).
PROTECTED_SPAN = re.compile(r"https?://\S+|\b[A-Za-z0-9]+(?:[_./:@-][A-Za-z0-9]+)+\b|\b[A-Za-z]+\d[A-Za-z0-9]*\b|\b[A-Z]{2,}\b|\b\d+(?:[./-]\d+)*\b")

MN_LATIN = {
    "ajiltan": "ажилтан", "ajiltnii": "ажилтны", "ajiltand": "ажилтанд",
    "eeljiin": "ээлжийн", "amralt": "амралт", "amraltiin": "амралтын",
    "heden": "хэдэн", "honog": "хоног", "ve": "вэ", "uu": "уу", "u": "үү",
    "sain": "сайн", "baina": "байна", "bainaa": "байна", "tsalin": "цалин",
    "hezee": "хэзээ", "olgodog": "олгодог", "chuluu": "чөлөө", "chuluunii": "чөлөөний",
    "huseltiig": "хүсэлтийг", "huselt": "хүсэлт", "honogiin": "хоногийн", "umnu": "өмнө", "uguh": "өгөх",
    "avah": "авах", "juram": "журам", "yu": "юу", "baiguullaga": "байгууллага",
    "baiguullagiin": "байгууллагын", "dotood": "дотоод", "ajliin": "ажлын",
    "tsag": "цаг", "hedees": "хэдээс", "eheldeg": "эхэлдэг", "herhen": "хэрхэн",
    "policy": "policy", "annual": "annual", "leave": "leave", "employee": "employee",
    "employees": "employees", "days": "days", "salary": "salary", "when": "when",
    "what": "what", "how": "how", "many": "many", "is": "is", "are": "are",
    "the": "the", "a": "a", "of": "of", "for": "for", "in": "in", "and": "and", "request": "request",
}
MN_LATIN_CUES = {"ajiltan", "ajiltnii", "ajiltand", "eeljiin", "amralt", "amraltiin", "heden", "honog", "honogiin", "ve", "uu", "sain", "baina", "bainaa", "tsalin", "hezee", "olgodog", "chuluu", "chuluunii", "huseltiig", "huselt", "umnu", "uguh", "avah", "juram", "yu", "baiguullaga", "baiguullagiin", "dotood", "ajliin", "tsag", "hedees", "eheldeg", "herhen"}
EN_WORDS = {"the", "is", "are", "how", "many", "days", "employee", "employees", "annual", "leave", "policy", "salary", "what", "when", "does", "do", "get", "of", "for", "and", "in", "a", "an", "request"}


def normalize_text(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(text or "")).replace("\x00", " ")).strip()


def detect_language(text):
    original = normalize_text(text)
    words = {word.casefold() for word in LATIN_WORD.findall(original)}
    cyrillic = len(CYRILLIC.findall(original))
    latin_mn = len(words & MN_LATIN_CUES)
    latin_en = len(words & EN_WORDS)
    if cyrillic and latin_mn + latin_en:
        label = "mixed"
        confidence = min(.99, .7 + min(cyrillic, 12) / 100 + min(latin_mn + latin_en, 5) / 20)
    elif cyrillic:
        label, confidence = "mn_cyrl", min(.99, .75 + min(cyrillic, 20) / 100)
    elif latin_mn and latin_en:
        label, confidence = "mixed", min(.94, .62 + .08 * min(latin_mn + latin_en, 4))
    elif latin_mn >= 2 and latin_mn >= latin_en:
        label, confidence = "mn_latn", min(.96, .58 + .12 * latin_mn)
    elif latin_en >= 2 and latin_en > latin_mn:
        label, confidence = "en", min(.96, .6 + .1 * latin_en)
    elif latin_mn:
        label, confidence = "mn_latn", .55
    elif words:
        label, confidence = "unknown", .35
    else:
        label, confidence = "unknown", .0
    return dict(language=label, confidence=round(confidence, 3))


def normalize_romanized(text):
    """Replace only whole words in a reviewed lexicon; retain unknowns/IDs."""
    original = normalize_text(text)
    replacements = 0
    def convert(match):
        nonlocal replacements
        token = match.group(0)
        replacement = MN_LATIN.get(token.casefold()) if token.isascii() and token.isalpha() else None
        if replacement:
            replacements += 1
            if token[:1].isupper():
                replacement = replacement[:1].upper() + replacement[1:]
        return replacement or token
    parts, cursor = [], 0
    for match in PROTECTED_SPAN.finditer(original):
        parts.extend((TOKEN.sub(convert, original[cursor:match.start()]), match.group(0)))
        cursor = match.end()
    parts.append(TOKEN.sub(convert, original[cursor:]))
    candidate = "".join(parts)
    confidence = replacements / max(1, len(LATIN_WORD.findall(original)))
    return dict(text=candidate, confidence=round(confidence, 3), replacements=replacements)


def query_candidates(text):
    original = normalize_text(text)
    detected = detect_language(original)
    candidates = [original] if original else []
    if detected["language"] in ("mn_latn", "mixed", "unknown"):
        normalized = normalize_romanized(original)
        if normalized["replacements"] and normalized["text"] not in candidates:
            candidates.append(normalized["text"])
    return dict(original=original, language=detected["language"], confidence=detected["confidence"],
        candidates=candidates[:3], normalized_confidence=normalize_romanized(original)["confidence"])


MN_OUTPUT_CUES = {"байна", "болно", "болох", "биш", "үгүй", "юм", "мөн", "гэж", "нь", "энэ", "тухай", "дагуу", "ажлын", "ажилтан", "ажилтны", "өдөр", "хоног", "журам", "журмын", "баримт", "баримтын", "нотолгоо", "хангалтгүй", "хариулт", "хүсэлт", "чөлөө", "авах", "өгөх", "шаардлагатай", "таван"}
RU_OUTPUT_CUES = {"это", "для", "что", "или", "должен", "нужно", "можно", "согласно", "правила", "сотрудник", "сотрудника", "является", "политика", "дней", "отпуск"}


def validate_output_language(text, protected=()):
    """Conservative policy check, not a language-identification quality metric.

    Only caller-verified exact titles/names/identifiers may be exempted. English
    prose must remain visible to the check; ambiguous Cyrillic is uncertain.
    """
    content = normalize_text(text)
    for exact in sorted({str(p) for p in protected if p}, key=len, reverse=True):
        content = re.sub(r"(?<!\w)" + re.escape(exact) + r"(?!\w)", " ", content)
    content = re.sub(r"\[S\d+\]", " ", content)
    words = re.findall(r"[A-Za-zА-Яа-яЁёӨөҮү]+", content)
    if not words:
        return "uncertain"
    latin = sum(len(w) for w in words if w.isascii())
    total = sum(map(len, words))
    if latin / total > .10:
        return "fail"
    tokens = {w.casefold() for w in words}
    mn, ru = len(tokens & MN_OUTPUT_CUES), len(tokens & RU_OUTPUT_CUES)
    if ru >= 2 and ru > mn:
        return "fail"
    if len(words) < 3 or mn < 2:
        return "uncertain"
    return "pass"


def answer_is_mongolian(text, protected=()):
    """Compatibility wrapper: uncertain output does not pass the policy."""
    return validate_output_language(text, protected) == "pass"
