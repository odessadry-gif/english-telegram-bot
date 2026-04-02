import os
import json
import time
import re
import hashlib
import random
import unicodedata
import requests
from openai import OpenAI

# ========= ENV =========
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CHAT_ID = os.getenv("CHAT_ID", "-1003674761753")
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

# MODE=quiz | MODE=postgame
MODE = os.getenv("MODE", "quiz").strip().lower()

GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "@Official_english_every_day")

GAME_URL = os.getenv(
    "GAME_URL",
    "https://odessadry-gif.github.io/english-telegram-bot/docs/"
)

GAME_BUTTON_TEXT = os.getenv(
    "GAME_BUTTON_TEXT",
    "☕ Travel Rush"
)

GAME_POST_TEXT = os.getenv(
    "GAME_POST_TEXT",
    "☕ **Travel Rush** — швидкий квіз з англійської\n\n"
    "Короткі фрази про подорожі, кавʼярню, їжу та everyday English.\n"
    "Обери правильне слово й прокачай англійську за 2 хвилини 👇",
)

# ========= HISTORY =========
HISTORY_FILE = "history.json"
MAX_HISTORY = 1500
RECENT_CORE_LIMIT = 300
RECENT_PATTERN_LIMIT = 220

client = OpenAI(api_key=OPENAI_API_KEY)

ARTICLES_RE = re.compile(r"^(a|an|the)\s+", re.IGNORECASE)

# ========= HARD FILTERS =========
BANNED_HARD_PHRASES = [
    "put it on my tab",
    "confirm the time",
    "touch base",
    "circle back",
    "keep me posted",
    "heads up",
    "it slipped my mind",
    "rain check",
    "piece of cake",
    "under the weather",
    "would you mind",
    "i was wondering if",
    "as soon as possible",
    "get in touch",
    "much appreciated",
    "appreciate it",
    "figure out",
    "work out",
    "sort out",
    "catch up later",
]

BANNED_HARD_WORDS = [
    "venue",
    "schedule",
    "confirm",
    "available",
    "reservation",
    "beverage",
    "purchase",
    "itemized",
    "receipt",
    "account",
    "complimentary",
    "destination",
    "departure",
    "itinerary",
    "accommodation",
    "arrangement",
]

GOOD_SIMPLE_MARKERS = [
    "coffee",
    "tea",
    "water",
    "menu",
    "bill",
    "check",
    "late",
    "hungry",
    "drink",
    "food",
    "friend",
    "table",
    "ticket",
    "hotel",
    "taxi",
    "train",
    "bus",
    "gate",
    "bag",
    "ready",
    "sorry",
    "please",
    "to go",
    "for here",
    "on my way",
    "can i",
    "i'd like",
    "we need",
    "where is",
]

# ========= HELPERS =========
def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def normalize_core(text: str) -> str:
    s = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    s = s.strip("“”\"'`")
    s = re.sub(r"[^a-z0-9\s\-\?']", " ", s)
    s = _norm_spaces(s)
    s = ARTICLES_RE.sub("", s)
    s = _norm_spaces(s)
    return s


def pattern_key(text: str) -> str:
    s = unicodedata.normalize("NFKC", (text or "")).lower()
    s = s.replace("___", " <blank> ")
    s = re.sub(r"\d+", "<num>", s)
    s = re.sub(r"[^a-z<>\s]", " ", s)
    s = _norm_spaces(s)
    return s


def fp_for(core: str, options: list[str]) -> str:
    payload = {
        "core": normalize_core(core),
        "options": [_norm(x) for x in (options or [])],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def core_fp(core: str) -> str:
    raw = json.dumps({"core": normalize_core(core)}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_4_options(options: list[str]) -> list[str]:
    if not isinstance(options, list):
        return []

    cleaned = []
    seen = set()

    for item in options:
        s = _norm_spaces(str(item))
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(s)
        if len(cleaned) == 4:
            break

    return cleaned


def shuffle_options_keep_correct(options: list[str], correct_idx: int) -> tuple[list[str], int]:
    correct_value = options[correct_idx]
    shuffled = options[:]
    random.shuffle(shuffled)
    return shuffled, shuffled.index(correct_value)


def load_history() -> list[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(items: list[dict]) -> None:
    items = (items or [])[-MAX_HISTORY:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def recent_cores(history: list[dict], n: int) -> list[str]:
    out = []
    seen = set()

    for item in reversed(history or []):
        core = normalize_core(item.get("core", ""))
        if not core or core in seen:
            continue
        seen.add(core)
        out.append(core)
        if len(out) >= n:
            break

    return out


def looks_too_hard(text: str) -> bool:
    low = _norm_spaces(text).lower()

    for phrase in BANNED_HARD_PHRASES:
        if phrase in low:
            return True

    words = re.findall(r"[a-z']+", low)
    for w in words:
        if w in BANNED_HARD_WORDS:
            return True

    return False


def looks_simple_enough(text: str) -> bool:
    low = _norm_spaces(text).lower()
    return any(marker in low for marker in GOOD_SIMPLE_MARKERS)


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("No JSON found")
    return json.loads(m.group(0))


# ========= VALIDATION =========
def validate_generated(data: dict) -> tuple[str, str, list[str], int, str]:
    core = str(data.get("core", "")).strip()
    question = str(data.get("question", "")).strip()
    options = ensure_4_options(data.get("options", []))
    correct = int(data.get("correct", 0))
    explanation = str(data.get("explanation_uk", "")).strip()

    if not core:
        raise ValueError("empty core")

    if core.count("___") != 1:
        raise ValueError("core must contain exactly one ___")

    if not question.startswith("💬\nFill the gap:\n"):
        raise ValueError("wrong question format")

    if len(question) > 220:
        raise ValueError("question too long")

    if len(options) != 4:
        raise ValueError("must have exactly 4 options")

    if correct not in (0, 1, 2, 3):
        raise ValueError("correct must be 0..3")

    low_opts = [_norm_spaces(x).lower() for x in options]
    if len(set(low_opts)) != 4:
        raise ValueError("duplicate options")

    if not explanation:
        raise ValueError("empty explanation")

    if len(explanation) > 180:
        raise ValueError("explanation too long")

    if looks_too_hard(core) or looks_too_hard(question):
        raise ValueError("too hard")

    for opt in options:
        if looks_too_hard(opt):
            raise ValueError("hard option")

    if not looks_simple_enough(core):
        raise ValueError("not simple enough")

    # Додаткова перевірка проти двох правильних відповідей:
    # правильна відповідь має бути 1-3 слова, без майже дубля.
    normalized_opts = [normalize_core(x) for x in options]
    if len(set(normalized_opts)) != 4:
        raise ValueError("options too similar")

    return core, question, options, correct, explanation


# ========= TELEGRAM =========
def tg_api(method: str, payload: dict):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram {method} error: {r.status_code} {r.text}")
    return r.json()


def send_quiz_poll(question: str, options: list[str], correct_id: int, explanation: str):
    payload = {
        "chat_id": CHAT_ID,
        "question": question.strip(),
        "options": [str(x).strip() for x in options],
        "type": "quiz",
        "correct_option_id": int(correct_id),
        "is_anonymous": True,
        "allows_multiple_answers": False,
        "explanation": explanation.strip(),
    }
    tg_api("sendPoll", payload)


def send_game_post():
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": GAME_BUTTON_TEXT,
                    "url": GAME_URL
                }
            ]
        ]
    }

    payload = {
        "chat_id": GROUP_CHAT_ID,
        "text": GAME_POST_TEXT.strip(),
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
        "reply_markup": reply_markup,
    }
    tg_api("sendMessage", payload)


# ========= PROMPTS =========
def build_prompt(avoid_items: list[str]) -> str:
    avoid_block = ""
    if avoid_items:
        avoid_block = (
            "Avoid these recent sentence ideas. Do not repeat or make near-copies:\n- "
            + "\n- ".join(avoid_items[:40])
        )

    return f"""
Create ONE Telegram English quiz for beginners.
Return STRICT JSON ONLY.

GOAL:
Make a super clear A1/A2 "fill the gap" poll.
It must be fast, obvious, and fun.
No brain-twister questions.
Only ONE answer must be correct.

MAIN FORMAT:
- complete sentence with ONE blank: ___
- user must choose ONE word or short phrase for the blank

TOPICS TO MIX:
- travel
- food
- ordering food
- coffee shop small talk
- simple everyday phrases
- small talk with a stranger
- being late
- asking for help
- hotel / airport / taxi / cafe basics

LEVEL:
- 70% A1
- 25% A2
- 5% easy B1
Prefer A1 if unsure.

VERY IMPORTANT RULES:
- exactly 4 options
- exactly 1 correct answer
- wrong answers must be clearly wrong
- no second correct answer
- no "all options look okay"
- no advanced vocabulary
- no formal business English
- no idioms
- no perfect tenses
- no modal complexity
- sentence must sound natural in real life
- keep it short and very clear
- explanation_uk must be short and simple
- use American-style everyday English

GOOD EXAMPLES OF SENTENCE STYLE:
- Can I get a ___, please?
- I need a ___ to the airport.
- We want a table for ___.
- I’m running ___.
- Is this seat ___?
- Can I pay by ___?
- My room number is ___.
- I’d like a ___ coffee.
- Where is the train ___?
- I’m on my ___.

BAD STYLE:
- tricky grammar
- abstract situations
- multiple good answers
- long wording
- textbook English
- formal service phrases
- advanced travel vocabulary

OPTION RULES:
- the correct option should be very clearly the best fit
- wrong options can be:
  1) same category but wrong meaning
  2) common learner mistake
  3) funny trap but obviously wrong in context
- do NOT make two near-synonyms that both fit

QUESTION FORMAT MUST BE EXACTLY:
💬
Fill the gap:
<sentence with ___>

JSON schema:
{{
  "core": "sentence with ___",
  "question": "💬\\nFill the gap:\\n<same sentence with ___>",
  "options": ["...", "...", "...", "..."],
  "correct": 0,
  "explanation_uk": "short explanation in Ukrainian"
}}

EXTRA QUALITY CHECK:
Before returning JSON, silently verify:
- only one answer works
- sentence is easy without context
- user can answer in 1-2 seconds
- sentence is useful in real life
- no repeated recent idea

{avoid_block}

Return STRICT JSON ONLY.
""".strip()


def fallback_prompt() -> str:
    return """
Return STRICT JSON ONLY.

Create ONE very easy English fill-the-gap quiz for Telegram.

Rules:
- A1/A2 only
- 4 unique options
- 1 correct answer only
- very clear
- useful in real life
- topics: travel, coffee shop, food, ordering, hotel, taxi, everyday English

Question format exactly:
💬
Fill the gap:
<sentence with ___>

JSON schema:
{
  "core": "sentence with ___",
  "question": "💬\\nFill the gap:\\n<sentence with ___>",
  "options": ["...", "...", "...", "..."],
  "correct": 0,
  "explanation_uk": "short explanation in Ukrainian"
}
""".strip()


def generate_one(avoid_items: list[str]) -> dict:
    try:
        resp = client.responses.create(
            model=MODEL,
            input=build_prompt(avoid_items),
        )
        raw = (getattr(resp, "output_text", None) or "").strip()
        return extract_json(raw)
    except Exception:
        resp = client.responses.create(
            model=MODEL,
            input=fallback_prompt(),
        )
        raw = (getattr(resp, "output_text", None) or "").strip()
        return extract_json(raw)


# ========= MAIN =========
def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")

    if MODE == "postgame":
        send_game_post()
        return

    history = load_history()
    seen_fp = {h.get("fp") for h in history if h.get("fp")}
    seen_core_fp = {h.get("core_fp") for h in history if h.get("core_fp")}

    recent_core_set = set(recent_cores(history, RECENT_CORE_LIMIT))

    recent_patterns = set()
    pattern_count = 0
    for h in reversed(history):
        if pattern_count >= RECENT_PATTERN_LIMIT:
            break
        core = h.get("core", "")
        pk = pattern_key(core)
        if pk:
            recent_patterns.add(pk)
            pattern_count += 1

    avoid_items = recent_cores(history, 40)

    last_err = None

    for _ in range(30):
        try:
            data = generate_one(avoid_items)
            core, question, options, correct, explanation = validate_generated(data)

            cfp = core_fp(core)
            if cfp in seen_core_fp:
                continue

            norm_core = normalize_core(core)
            if norm_core in recent_core_set:
                continue

            pk = pattern_key(core)
            if pk in recent_patterns:
                continue

            fp = fp_for(core, options)
            if fp in seen_fp:
                continue

            shuffled_options, new_correct = shuffle_options_keep_correct(options, correct)
            send_quiz_poll(question, shuffled_options, new_correct, explanation)

            history.append(
                {
                    "ts": int(time.time()),
                    "kind": "phrase_gap",
                    "core": core,
                    "fp": fp_for(core, shuffled_options),
                    "core_fp": cfp,
                }
            )
            save_history(history)
            return

        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Failed to generate quiz. Last error: {last_err}")


if __name__ == "__main__":
    main()
