"""Natural-language understanding: intent + entity extraction.

Default engine is deterministic and offline (bilingual char-n-gram TF-IDF
retrieval over exemplars + concept lexicon). An optional LLM adapter
(``humain.llm``) can be plugged in; its output is always validated against
the closed intent set, so the LLM can never invent a service.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

INTENTS = [
    "report_stolen_vehicle",
    "replace_lost_licence",
    "close_business",
    "hire_employee",
    "end_employee_contract",
    "leave_job",
    "case_status",
]

# Exemplars used to build the retrieval index (the "training" set).
# Held-out evaluation utterances in tests/ are deliberately NOT copied here.
EXEMPLARS = {
    "report_stolen_vehicle": [
        "my car was stolen", "someone stole my car", "my vehicle has been stolen",
        "I need to report a stolen car", "car theft report", "my car is missing from the parking",
        "they took my car last night", "report vehicle theft",
        "سيارتي انسرقت", "تمت سرقة سيارتي", "ابغى ابلغ عن سرقة سيارة", "بلاغ سرقة مركبة",
    ],
    "replace_lost_licence": [
        "I lost my driving licence", "I lost my driver's license", "replace my licence",
        "my licence is lost what do I do", "I need a new driving licence because I lost it",
        "replacement driving license", "my license was damaged",
        "ضاعت رخصتي", "فقدت رخصة القيادة", "ابغى بدل فاقد للرخصة", "رخصة القيادة تالفة",
    ],
    "close_business": [
        "I want to close my business", "close my company", "cancel my commercial registration",
        "shut down my shop", "I want to stop my business permanently",
        "ابغى اقفل مؤسستي", "شطب السجل التجاري", "اغلاق الشركة",
    ],
    "hire_employee": [
        "I need to hire someone", "hire a new employee", "I want to employ a worker",
        "recruit staff for my company", "add a new employee", "I need to create a contract for an employee",
        "create an employment contract", "new employment contract for my staff",
        "ابغى اوظف موظف", "توظيف موظف جديد", "احتاج عامل جديد", "ابغى اسوي عقد لموظف", "انشاء عقد عمل",
    ],
    "end_employee_contract": [
        "I need to end an employee's contract", "terminate an employee", "fire my employee",
        "end the contract of a worker", "dismiss a staff member",
        "انهاء عقد موظف", "ابغى افصل موظف", "انهاء خدمات عامل",
    ],
    "leave_job": [
        "I want to leave my job", "I want to resign", "submit my resignation",
        "I am quitting my job", "how do I resign from work",
        "ابغى استقيل", "تقديم استقالة", "ابغى اترك شغلي",
    ],
    "case_status": [
        "what is the status of my case", "any update on my report", "track my request",
        "where is my application", "what happened with my case",
        "وش صار على بلاغي", "حالة الطلب", "متابعة الطلب",
    ],
}

# Concept lexicon: an intent fires strongly when *both* concept groups match.
CONCEPTS = {
    "VEHICLE": ["car", "vehicle", "sedan", "suv", "truck", "camry", "elantra", "sportage", "patrol",
                 "toyota", "hyundai", "kia", "nissan", "motor", "ride", "سيار", "مركب", "جيب", "ونيت"],
    "THEFT": ["stolen", "stole", "steal", "theft", "nicked", "took", "taken", "drove off", "drove away",
               "missing", "gone", "broke into", "hijack", "سرق", "انسرق", "مسروق", "اختف", "ماخذ", "اخذو"],
    "LICENCE": ["licence", "license", "licance", "lisence", "liscense", "driving permit", "driver permit",
                 "رخص"],
    "LOSS": ["lost", "lose", "losing", "misplac", "can't find", "cant find", "cannot find", "replace",
              "replacement", "new one", "gone", "fell out", "missing", "damaged", "broken", "torn", "stolen", "stole", "renew", "ضاع", "ضيع", "فقد",
              "بدل", "تالف", "انسرق", "مسروق", "سرق"],
    "BUSINESS": ["business", "company", "shop", "store", "commercial registration", "cr ", "firm", "cafe",
                  "restaurant", "establishment", "enterprise", "سجل", "شرك", "مؤسس", "محل", "نشاط"],
    "CLOSE": ["close", "closing", "shut", "cancel", "wind down", "wind up", "stop", "liquidat", "dissolve",
               "deregister", "شطب", "اقفل", "قفل", "اغلاق", "سكر", "الغاء", "تصفي"],
    "HIRE": ["hire", "hiring", "recruit", "employ ", "onboard", "new employee", "new staff", "new worker",
              "take on", "bring on", "vacancy", "new hire", "create a contract", "create an employment", "employment contract",
             "new contract", "contract for an employee", "contract for my employee", "عقد عمل", "انشاء عقد", "اسوي عقد", "اوظف", "توظيف", "استقدام", "عامل جديد", "موظف جديد"],
    "TERMINATE": ["fire ", "fire my", "firing", "terminat", "end an employee", "end the contract", "end contract",
                   "end my employee", "dismiss", "let go", "let him go", "let her go", "sack", "lay off",
                   "افصل", "فصل", "انهاء عقد", "انهاء خدمات", "انهي عقد", "انهي خدمات", "الاستغناء"],
    "RESIGN": ["resign", "quit", "leave my job", "leaving my job", "leave work", "leave the company",
                "notice period", "hand in my notice", "give notice", "new job", "استقال", "استقيل", "اترك شغل", "اترك العمل", "اطلع من الشغل"],
    "STATUS": ["status", "update", "progress", "track", "where is", "what happened", "any news", "heard back",
                "حال", "وش صار", "متابع", "وين وصل"],
}

PAIRS = {
    "report_stolen_vehicle": ("VEHICLE", "THEFT"),
    "replace_lost_licence": ("LICENCE", "LOSS"),
    "close_business": ("BUSINESS", "CLOSE"),
    "hire_employee": ("HIRE", None),
    "end_employee_contract": ("TERMINATE", None),
    "leave_job": ("RESIGN", None),
    "case_status": ("STATUS", None),
}

AR_RE = re.compile(r"[؀-ۿ]")


_AR_WORD = re.compile(r"[\u0600-\u06FF]+")
_LAT_WORD = re.compile(r"[A-Za-z]{2,}")
_OTHER_SCRIPT = re.compile(r"[\u0400-\u04FF\u0900-\u097F\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF\u0E00-\u0E7F\u0590-\u05FF]")
_PLATE = re.compile(r"\b[A-Z]{3}\s?\d{4}\b")


def detect_language(text: str) -> dict:
    """Detect Arabic / English / mixed (code-switching) and the dominant language.
    Plates and model names don't decide the language on their own."""
    t = _PLATE.sub(" ", text or "")
    ar = len(_AR_WORD.findall(t))
    en = len(_LAT_WORD.findall(t))
    other = len(_OTHER_SCRIPT.findall(t))
    if ar == 0 and en == 0:
        return {"lang": None, "mixed": False, "supported": other == 0, "ar_words": 0, "en_words": 0}
    if other > max(ar, en):
        return {"lang": None, "mixed": False, "supported": False, "ar_words": ar, "en_words": en}
    if ar == en:   # tie: the script the citizen started with
        first = re.search(r"[\u0600-\u06FF]|[A-Za-z]", t)
        dominant = "ar" if first and AR_RE.match(first.group(0)) else "en"
    else:
        dominant = "ar" if ar > en else "en"
    return {"lang": dominant, "mixed": ar > 0 and en > 0, "supported": True, "ar_words": ar, "en_words": en}


def detect_lang(text: str) -> str:
    return detect_language(text)["lang"] or "en"


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = re.sub(r"[ً-ْـ]", "", t)          # Arabic diacritics + tatweel
    t = re.sub(r"[إأآا]", "ا", t)
    t = t.replace("ى", "ي").replace("ة", "ه")
    t = t.replace("’", "'")
    t = re.sub(r"[^\w\s']", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _ngrams(text: str, lo: int = 3, hi: int = 5) -> Counter:
    grams: Counter = Counter()
    for w in normalise(text).split():
        w = f" {w} "
        for n in range(lo, hi + 1):
            for i in range(max(0, len(w) - n + 1)):
                grams[w[i:i + n]] += 1
    return grams


class _Index:
    def __init__(self, exemplars: dict[str, list[str]]):
        docs = [(intent, _ngrams(s)) for intent, ss in exemplars.items() for s in ss]
        df: Counter = Counter()
        for _, g in docs:
            df.update(g.keys())
        n = len(docs)
        self.idf = {k: math.log((1 + n) / (1 + v)) + 1 for k, v in df.items()}
        self.docs = [(intent, self._vec(g)) for intent, g in docs]

    def _vec(self, g: Counter) -> dict:
        v = {k: c * self.idf.get(k, 1.0) for k, c in g.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {k: x / norm for k, x in v.items()}

    def scores(self, text: str) -> dict[str, float]:
        q = self._vec(_ngrams(text))
        best: dict[str, float] = {}
        for intent, d in self.docs:
            s = sum(w * d.get(k, 0.0) for k, w in q.items())
            best[intent] = max(best.get(intent, 0.0), s)
        return best


_INDEX = _Index(EXEMPLARS)


def _edit1(a: str, b: str, k: int) -> bool:
    """True if Levenshtein(a, b) <= k (small k, early exit)."""
    if abs(len(a) - len(b)) > k:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > k:
            return False
        prev = cur
    return prev[-1] <= k


_FUZZY = {c: [w for w in ws if w.isalpha() and len(w) >= 5] for c, ws in CONCEPTS.items()}
_ROLE_RE = re.compile(r"\b(need|want|looking for|find)\s+(a|an)\s+(new\s+)?(driver|cashier|accountant|receptionist|"
                      r"engineer|manager|assistant|cleaner|chef|barista|salesperson|technician|nurse|worker|employee)\b"
                      r"(?!.*\b(stolen|lost|stole)\b)")


def _concept_hits(text: str) -> set[str]:
    t = " " + normalise(text) + " "
    raw = " " + (text or "").lower() + " "
    hits = set()
    for c, words in CONCEPTS.items():
        for w in words:
            wn = normalise(w) if AR_RE.search(w) else w
            if (wn in t) or (w in raw):
                hits.add(c)
                break
    # typo tolerance: edit distance 1 for words >= 5 chars, 2 for >= 8
    for tok in t.split():
        if len(tok) < 5 or not tok.isalpha():
            continue
        for c, words in _FUZZY.items():
            if c not in hits and any(_edit1(tok, w, 1 if len(w) < 8 else 2) for w in words):
                hits.add(c)
    if _ROLE_RE.search(raw):
        hits.add("HIRE")
    return hits


@dataclass
class IntentResult:
    intent: str | None
    confidence: float
    scores: dict = field(default_factory=dict)
    secondary: list = field(default_factory=list)
    concepts: list = field(default_factory=list)
    engine: str = "deterministic-tfidf+lexicon"


THRESHOLD = 0.42


def classify(text: str, extra_concepts: set[str] | None = None) -> IntentResult:
    """extra_concepts: concepts grounded in the citizen's authenticated context
    (e.g. they named one of their own registered businesses or vehicles)."""
    tf = _INDEX.scores(text)
    hits = _concept_hits(text) | (extra_concepts or set())
    combined = {}
    for intent in INTENTS:
        a, b = PAIRS[intent]
        concept = 0.0
        if a in hits and (b is None or b in hits):
            concept = 1.0
        elif a in hits or (b and b in hits):
            concept = 0.35
        combined[intent] = round(0.45 * tf.get(intent, 0.0) + 0.55 * concept, 4)
    # Disambiguation: licence words outrank vehicle words ("my licence was stolen").
    if "LICENCE" in hits and "VEHICLE" not in hits:
        combined["report_stolen_vehicle"] *= 0.5
    ranked = sorted(combined.items(), key=lambda kv: kv[1], reverse=True)
    top, conf = ranked[0]
    secondary = [i for i, s in ranked[1:] if s >= 0.6 and i != "case_status"]
    if conf < THRESHOLD:
        return IntentResult(None, conf, combined, [], sorted(hits))
    return IntentResult(top, conf, combined, secondary, sorted(hits))


# ---------------------------------------------------------------- entities
_TIME_PATTERNS = [
    (r"\blast night\b", "last night"), (r"\byesterday\b", "yesterday"), (r"\bthis morning\b", "this morning"),
    (r"\btonight\b", "tonight"), (r"\btoday\b", "today"), (r"\b(an|one|two|few) hours? ago\b", None),
    (r"\b\d+\s*(hours?|hrs?|minutes?|mins?|days?)\s*ago\b", None),
    (r"\b(a|an|one|two|three|four|five|few|couple of)\s+(days?|weeks?|hours?)\s+ago\b", None),
    (r"\b(at|around|about)\s+\d{1,2}(:\d{2})?\s*(am|pm)?\b", None), (r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b", None),
    (r"\b(on\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", None),
    (r"\b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b", None),
    (r"\bend of (the |next |this )?(month|week)\b", None), (r"\b(next|this)\s+(month|week)\b", None), (r"\bin\s+\d+\s+(days|weeks)\b", None),
    (r"\b(next|this) (monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", None),
    (r"قبل\s+(?:\d+\s+)?(?:يومين|يوم|ايام|اسبوعين|اسبوع|ساعتين|ساعات|ساعه|دقايق|دقيقه|شهرين|شهر)", None),
    (r"امس|البارحه|اليوم|الصبح|الليله|الفجر|قبل\s+\S+\s*ساع\S*|نهايه الشهر|الشهر الجاي|بعد\s+\S+", None),
    (r"الساعه\s*\d{1,2}(?:[:.]\d{2})?(?:\s*(?:الصبح|الظهر|العصر|المغرب|الليل|بالليل))?", None),
]


def extract_time(text: str) -> str | None:
    t = normalise(text)
    found = []
    for pat, label in _TIME_PATTERNS:
        m = re.search(pat, t)
        if m:
            val = (label or m.group(0)).strip()
            if not any(val in f for f in found):
                found.append(val)
    return ", ".join(dict.fromkeys(found)) if found else None


_LOC_RE = re.compile(
    r"\b(?:near|outside|in front of|at|in|from|by|opposite)\s+(?:the\s+)?([a-z][\w' ]{2,40}?)"
    r"(?=\s+(?:at|around|about|last|yesterday|this|today|on|tonight|and|but|when|i|it|\d)\b|[.,!?]|$)",
    re.I)
_LOC_STOP = {"night", "morning", "evening", "the morning", "pm", "am", "time", "all", "work"}
_AR_LOC_RE = re.compile(r"(?:في|عند|قرب|جنب|قدام|من)\s+([؀-ۿ ]{2,30})")


PLACE_RE = re.compile(r"\b(mall|street|road|park|station|airport|parking|car park|office|home|mosque|souq|market|hospital|gym|school|university)\b|"
                      r"مول|شارع|طريق|حي |موقف|مواقف|البيت|المطار|الدوام|المسجد|السوق|محطه|بارك|المستشفي|الجامعه|المدرسه|النادي")


def extract_location(text: str) -> str | None:
    for m in _LOC_RE.finditer(text or ""):
        cand = m.group(1).strip()
        if cand.lower() in _LOC_STOP or re.fullmatch(r"\d.*", cand):
            continue
        if cand.lower().split()[0] in {"my", "a", "an", "about", "around"}:
            continue
        return cand
    m = _AR_LOC_RE.search(text or "")
    if m:
        cand = m.group(1).strip()
        cand = re.split(r"\s+(?:امس|البارحه|البارحة|اليوم|الساعه|الساعة)", cand)[0]
        if cand:
            return cand
    return None


def match_option(text: str, options: list[dict], keys: list[str]) -> int | None:
    """Return index of option the user refers to (by ordinal, number or attribute)."""
    t = normalise(text)
    ordinals = {0: ["first", "1st", "one", "الاول", "الأول", "1"], 1: ["second", "2nd", "two", "الثاني", "الثانيه", "2"]}
    hits = []
    for i, o in enumerate(options):
        for k in keys:
            v = str(o.get(k, "")).lower()
            if v and (v in t or normalise(v) in t or v.replace(" ", "") in t.replace(" ", "")):
                hits.append(i)
                break
    if len(set(hits)) == 1:
        return hits[0]
    words = set(t.split())
    for i, ws in ordinals.items():
        if i < len(options) and any(w in words for w in ws):
            return i
    return None


YES = {"yes", "y", "yeah", "yep", "confirm", "confirmed", "submit", "go ahead", "proceed", "ok", "okay", "sure",
       "do it", "please do", "approve", "approved", "نعم", "ايوه", "ايه", "اي", "موافق", "تمام", "اكد", "ارسل", "يب"}
NO = {"no", "n", "nope", "cancel", "stop", "don't", "dont", "abort", "not now", "لا", "الغاء", "الغي", "وقف", "لاحقا"}


def yes_no(text: str) -> bool | None:
    t = normalise(text)
    words = t.split()
    joined = " " + t + " "
    if any(f" {n} " in joined for n in NO if " " in n) or any(w in NO for w in words):
        return False
    if any(f" {y} " in joined for y in YES if " " in y) or any(w in YES for w in words):
        return True
    return None


def extract_enum(text: str, slot_type: str) -> str | None:
    t = normalise(text)
    if slot_type == "enum:lost|stolen|damaged":
        if re.search(r"damag|broke|torn|تالف|انكسر|مقطوع", t):
            return "damaged"
        if re.search(r"stol|theft|سرق|مسروق", t):
            return "stolen"
        if re.search(r"lost|lose|misplac|can'?t find|cannot find|ضاع|فقد|ضيع", t):
            return "lost"
    if slot_type == "enum:pickup|delivery":
        if re.search(r"deliver|home|send|post|courier|توصيل|البيت|المنزل|ارسل", t):
            return "delivery"
        if re.search(r"pick|collect|branch|office|myself|استلم|الفرع|بنفسي", t):
            return "pickup"
    return None


def extract_money(text: str) -> str | None:
    m = re.search(r"(\d[\d,\.]*)\s*(k\b|sar|riyal|ريال)?", (text or "").lower())
    if not m:
        return None
    val = m.group(1).replace(",", "")
    try:
        amount = float(val) * (1000 if m.group(2) == "k" else 1)
    except ValueError:
        return None
    return f"{int(amount)} SAR"
