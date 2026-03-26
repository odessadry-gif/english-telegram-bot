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

# food_restaurants | travel | coffee | everyday_english | mixed
THEME = os.getenv("THEME", "mixed").strip().lower()

# MODE=quiz | MODE=postgame
MODE = os.getenv("MODE", "quiz").strip().lower()

GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "@Official_english_every_day")

GAME_URL = os.getenv(
    "GAME_URL",
    "https://odessadry-gif.github.io/english-telegram-bot/docs/"
)

GAME_BUTTON_TEXT = os.getenv(
    "GAME_BUTTON_TEXT",
    "🍔 Food Rush"
)

GAME_POST_TEXT = os.getenv(
    "GAME_POST_TEXT",
    "🍔 **Food Rush** — швидкий квіз про їжу та ресторани\n\n"
    "Вгадай 10 food та restaurant слів за 3 хвилини.\n"
    "Перевір свою англійську та прокачай food vocabulary 👇",
)

# ========= HISTORY =========
HISTORY_FILE = "history.json"
MAX_HISTORY = 1200

# ========= GENERATION =========
MAX_GEN_TRIES_DEFAULT = 16
MAX_GEN_TRIES_BY_KIND = {
    "phrase_gap": 20,
    "situation_quiz": 18,
    "mini_dialogue": 18,
    "what_does_it_mean": 18,
    "ua_en": 22,
}

KIND_CYCLE = [
    "situation_quiz",
    "mini_dialogue",
    "what_does_it_mean",
    "ua_en",
    "phrase_gap",
]

COOLDOWN_LAST_N = {
    "phrase_gap": 180,
    "situation_quiz": 220,
    "mini_dialogue": 220,
    "what_does_it_mean": 220,
    "ua_en": 240,
}

RECENT_PATTERN_LAST_N = 240

client = OpenAI(api_key=OPENAI_API_KEY)

ARTICLES_RE = re.compile(r"^(a|an|the)\s+", re.IGNORECASE)

# ========= A1/A2 SAFETY =========
BANNED_HARD_PHRASES = [
    "put it on my tab",
    "confirm the time",
    "touch base",
    "circle back",
    "keep me posted",
    "heads up",
    "it slipped my mind",
    "rain check",
    "no worries",
    "piece of cake",
    "under the weather",
    "hit the road",
    "figure out",
    "work out",
    "sort out",
    "catch up later",
    "i'm afraid i can't",
    "would you mind",
    "i was wondering if",
]

BANNED_HARD_WORDS = [
    "tab",
    "venue",
    "schedule",
    "confirm",
    "available",
    "reservation" ,
    "beverage",
    "purchase",
    "itemized",
    "receipt",
    "account",
    "charge it",
    "complimentary",
]

EASY_CONVERSATION_MARKERS = [
    "please",
    "sorry",
    "coffee",
    "tea",
    "water",
    "food",
    "menu",
    "bill",
    "check",
    "late",
    "hungry",
    "drink",
    "go",
    "table",
    "friend",
    "wait",
    "come",
    "here",
    "there",
    "ready",
    "want",
    "like",
    "can i",
    "i'd like",
    "for here",
    "to go",
    "on my way",
]

# ========= THEME MAP =========
THEME_INSTRUCTIONS = {
    "food_restaurants": """
Focus on: restaurant, cafe, coffee shop, simple menu words, waiter, order, bill, table,
food, drinks, breakfast, lunch, dinner, dessert, takeaway, delivery.
Use very easy everyday English only.
""".strip(),

    "coffee": """
Focus on: coffee shop, coffee, tea, latte, cappuccino, hot, iced, milk, sugar,
to go, for here, paying, simple barista phrases.
Use very easy everyday English only.
""".strip(),

    "travel": """
Focus on: airport, hotel, taxi, booking, luggage, check-in, gate, ticket,
simple travel phrases and very basic real-life situations.
Use very easy everyday English only.
""".strip(),

    "everyday_english": """
Focus on: daily life, texting, friends, being late, making plans, shopping,
small talk, simple casual spoken English.
Use very easy everyday English only.
""".strip(),

    "mixed": """
Mix these contexts naturally: coffee shop, restaurant, food delivery, travel,
daily life, texting, casual spoken English.
Keep it simple, practical, and easy.
""".strip(),
}


# ========= HELPERS =========
def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def normalize_core(kind: str, core: str) -> str:
    if not core:
        return ""

    s = unicodedata.normalize("NFKC", str(core)).strip().lower()
    s = s.strip("“”\"'`")

    if kind == "ua_en":
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    s = re.sub(r"[^a-z0-9\s\-\?']", " ", s)
    s = _norm_spaces(s)
    s = ARTICLES_RE.sub("", s)
    s = _norm_spaces(s)
    return s


def _fp(kind: str, core: str, options: list[str]) -> str:
    payload = {
        "kind": _norm(kind),
        "core": normalize_core(kind, core),
        "options": [_norm(x) for x in (options or [])],
    }
    j = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()


def core_fp(kind: str, core: str) -> str:
    payload = {"kind": _norm(kind), "core": normalize_core(kind, core)}
    j = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()


def pattern_key(text: str) -> str:
    s = unicodedata.normalize("NFKC", (text or "")).lower()
    s = s.replace("___", " <blank> ")
    s = re.sub(r"\d+", "<num>", s)
    s = re.sub(r"[^a-z<>\s]", " ", s)
    s = _norm_spaces(s)
    return s


def ensure_4_options(options: list[str]) -> list[str]:
    if not isinstance(options, list):
        return []

    cleaned = []
    seen = set()

    for x in options:
        s = _norm_spaces(str(x))
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
    if not options or correct_idx < 0 or correct_idx >= len(options):
        return options, correct_idx

    correct_value = options[correct_idx]
    shuffled = options[:]
    random.shuffle(shuffled)
    new_correct = shuffled.index(correct_value)
    return shuffled, new_correct


def load_history() -> list[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            if data and isinstance(data[0], str):
                out = []
                for x in data:
                    out.append(
                        {
                            "ts": 0,
                            "kind": "legacy",
                            "fp": hashlib.sha256(_norm(x).encode("utf-8")).hexdigest(),
                            "core": x,
                        }
                    )
                return out
            return [x for x in data if isinstance(x, dict)]

        return []
    except Exception:
        return []


def save_history(items: list[dict]) -> None:
    items = (items or [])[-MAX_HISTORY:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def next_kind(history: list[dict]) -> str:
    last_kind = None
    for item in reversed(history or []):
        k = item.get("kind")
        if k in KIND_CYCLE:
            last_kind = k
            break

    if not last_kind:
        return KIND_CYCLE[0]

    idx = KIND_CYCLE.index(last_kind)
    return KIND_CYCLE[(idx + 1) % len(KIND_CYCLE)]


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("No JSON object found in model output")
    return json.loads(m.group(0))


def tries_for_kind(kind: str) -> int:
    return int(MAX_GEN_TRIES_BY_KIND.get(kind, MAX_GEN_TRIES_DEFAULT))


def recent_avoid_list(history: list[dict], kind: str, n: int) -> list[str]:
    out = []
    seen = set()

    for h in reversed(history or []):
        if h.get("kind") != kind:
            continue
        c = normalize_core(kind, h.get("core", ""))
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
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


def looks_easy_and_conversational(text: str) -> bool:
    low = _norm_spaces(text).lower()
    return any(marker in low for marker in EASY_CONVERSATION_MARKERS)


# ========= VALIDATION =========
def validate_common(question: str, options: list[str], correct: int, explanation: str) -> None:
    if not question or len(question.strip()) < 8:
        raise ValueError("question too short")

    if len(question) > 280:
        raise ValueError("question too long for Telegram poll")

    if len(options) != 4:
        raise ValueError("must have exactly 4 options")

    if correct not in (0, 1, 2, 3):
        raise ValueError("correct must be 0..3")

    low = [_norm_spaces(x).lower() for x in options]
    if len(set(low)) != 4:
        raise ValueError("duplicate options")

    if not explanation.strip():
        raise ValueError("empty explanation")

    if len(explanation) > 200:
        raise ValueError("explanation too long")

    if looks_too_hard(question):
        raise ValueError("question too hard")

    for opt in options:
        if looks_too_hard(opt):
            raise ValueError("option too hard")


def validate_phrase_gap(core: str, question: str, options: list[str], correct: int) -> None:
    core_clean = _norm_spaces(core)

    if core_clean.count("___") != 1:
        raise ValueError("phrase_gap core must contain exactly one ___")

    if not question.startswith("💬\nFill the gap:\n"):
        raise ValueError("invalid phrase_gap question format")

    if looks_too_hard(core_clean):
        raise ValueError("phrase_gap too hard")

    if not looks_easy_and_conversational(core_clean):
        raise ValueError("phrase_gap is not conversational enough")


def validate_situation_quiz(core: str, question: str, options: list[str], correct: int) -> None:
    if not question.startswith("💬\nSituation:\n"):
        raise ValueError("invalid situation_quiz format")

    if len(_norm_spaces(core)) < 8:
        raise ValueError("situation_quiz core too short")

    if looks_too_hard(core):
        raise ValueError("situation_quiz too hard")


def validate_mini_dialogue(core: str, question: str, options: list[str], correct: int) -> None:
    if not question.startswith("💬\nMini dialogue:\n"):
        raise ValueError("invalid mini_dialogue format")

    q = question.lower()
    if "you:" not in q and "your reply:" not in q:
        raise ValueError("mini_dialogue must contain reply slot")

    if looks_too_hard(core):
        raise ValueError("mini_dialogue too hard")


def validate_what_does_it_mean(core: str, question: str, options: list[str], correct: int) -> None:
    if not question.startswith("💬\nWhat does it mean?\n"):
        raise ValueError("invalid what_does_it_mean format")

    if len(_norm_spaces(core).split()) > 6:
        raise ValueError("meaning phrase too long")

    if looks_too_hard(core):
        raise ValueError("meaning phrase too hard")


def validate_ua_en(core: str, question: str, options: list[str], correct: int) -> None:
    if not question.startswith("💬\n🇺🇦 → 🇬🇧\n"):
        raise ValueError("invalid ua_en format")

    if correct != 0:
        raise ValueError("ua_en correct must be 0 before shuffle")

    if len(_norm_spaces(core).split()) > 5:
        raise ValueError("ua_en too long")


def validate_generated_quiz(kind: str, data: dict) -> tuple[str, str, list[str], int, str, str, str]:
    level = str(data.get("level", "A2")).strip().upper()
    topic = str(data.get("topic", "")).strip()
    core = str(data.get("core", "")).strip()
    question = str(data.get("question", "")).strip()
    options = ensure_4_options(data.get("options", []))
    correct = int(data.get("correct", 0))
    explanation = str(data.get("explanation_uk", "")).strip()

    validate_common(question, options, correct, explanation)

    if not core:
        core = question

    if kind == "phrase_gap":
        validate_phrase_gap(core, question, options, correct)
    elif kind == "situation_quiz":
        validate_situation_quiz(core, question, options, correct)
    elif kind == "mini_dialogue":
        validate_mini_dialogue(core, question, options, correct)
    elif kind == "what_does_it_mean":
        validate_what_does_it_mean(core, question, options, correct)
    elif kind == "ua_en":
        validate_ua_en(core, question, options, correct)
    else:
        raise ValueError(f"unknown kind: {kind}")

    if level not in ("A1", "A2", "B1"):
        level = "A2"

    return level, topic, question, core, options, correct, explanation


# ========= TELEGRAM =========
def tg_api(method: str, payload: dict):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram {method} error: {r.status_code} {r.text}")
    return r.json()


def send_quiz_poll(question: str, options: list[str], correct_id: int, explanation: str):
    question = (question or "").strip()
    if len(question) > 280:
        question = question[:277] + "..."

    options = [str(x).strip() for x in (options or [])]
    if len(options) != 4:
        raise ValueError("Telegram quiz poll requires exactly 4 options")

    explanation = (explanation or "").strip()
    if len(explanation) > 200:
        explanation = explanation[:200]

    payload = {
        "chat_id": CHAT_ID,
        "question": question,
        "options": options,
        "type": "quiz",
        "correct_option_id": int(correct_id),
        "is_anonymous": True,
        "allows_multiple_answers": False,
        "explanation": explanation,
    }
    tg_api("sendPoll", payload)


def send_game_post():
    text = (GAME_POST_TEXT or "").strip()
    if not text:
        text = "⚡ Word Rush — play now!"

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
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
        "reply_markup": reply_markup,
    }
    tg_api("sendMessage", payload)


# ========= PROMPTS =========
def get_theme_block() -> str:
    if THEME in THEME_INSTRUCTIONS:
        return THEME_INSTRUCTIONS[THEME]
    return THEME_INSTRUCTIONS["mixed"]


def prompt_for(kind: str, avoid_items: list[str]) -> str:
    avoid_line = ""
    if avoid_items:
        avoid_line = (
            "Avoid these recently used cores / ideas. Do NOT repeat them or make near-copies:\n- "
            + "\n- ".join(avoid_items[:30])
        )

    return f"""
Create ONE Telegram English quiz for learners.
Return STRICT JSON ONLY. No markdown. No explanations outside JSON.

CHANNEL STYLE:
- lively
- conversational
- practical
- natural spoken English
- real life situations
- very simple American-style everyday English
- NOT technical grammar
- NOT textbook-style dry sentences

LEVEL STRATEGY (VERY IMPORTANT):
- 55% questions should feel like A1
- 35% questions should feel like A2
- 10% questions may be B1
- prefer A1/A2 if in doubt
- use very common words
- short phrases are better than advanced phrases
- avoid idioms unless extremely obvious
- avoid formal business English
- avoid advanced restaurant or travel expressions

EASY ENGLISH RULES:
- use short sentences
- use common daily words only
- prefer phrases like:
  "Can I get..."
  "I'd like..."
  "I'm late"
  "I'm hungry"
  "Let's go"
  "Are you ready?"
  "For here or to go?"
  "Can we get the bill?"
- avoid phrases like:
  "Put it on my tab"
  "Please confirm the time"
  "Would you mind"
  "I was wondering if"
  "Keep me posted"

THEME:
{get_theme_block()}

ALLOWED CONTEXTS:
- ordering coffee
- ordering food
- restaurant small talk
- asking for the bill
- delivery / takeaway
- texting a friend
- running late
- making plans
- simple travel situations
- casual everyday spoken English

HARD RULES:
- exactly 4 options
- exactly 1 correct answer
- wrong answers must sound plausible but still clearly wrong
- no duplicate options
- keep it short and clean
- practical English only
- no politics, religion, war, medicine, explicit content
- no obscure vocabulary
- no perfect tenses
- no grammar terminology in the question
- no advanced idioms
- no formal phrases
- explanation_uk must be short, useful, natural Ukrainian, max 160 chars

{avoid_line}

JSON schema:
{{
  "level": "A1" or "A2" or "B1",
  "kind": "{kind}",
  "topic": "Coffee" or "Restaurant" or "Everyday English" or "Travel" or "Texting" or "Food",
  "core": "main core phrase / situation / Ukrainian phrase",
  "question": "final Telegram poll text",
  "options": ["A", "B", "C", "D"],
  "correct": 0,
  "explanation_uk": "short explanation in Ukrainian"
}}

FORMAT RULES BY KIND:

1) kind=phrase_gap
- Use a REAL spoken phrase with one blank: ___
- Must feel natural in cafe / restaurant / texting / travel / daily life
- Keep it easy, short, and common
- question format MUST be exactly:
  💬
  Fill the gap:
  <core>
- Good examples:
  "I'd like ___ coffee, please."
  "Can I get this ___ go?"
  "Sorry, I'm running ___."
  "Can we get the ___, please?"
  "I'm on my ___."

2) kind=situation_quiz
- A short real-life situation + ask what you say
- Make it easy and common
- question format MUST be exactly:
  💬
  Situation:
  <1 short situation line>
  What do you say?
- Good examples:
  "You want a coffee.
  What do you say?"
  "You finished dinner and want to pay.
  What do you say?"

3) kind=mini_dialogue
- A tiny dialogue with a reply choice
- Keep it very natural and easy
- question format MUST be exactly:
  💬
  Mini dialogue:
  <line>
  Your reply:
- Good examples:
  "Barista: What can I get for you?"
  "Friend: Are you ready?"
  "Waiter: Still water or sparkling?"

4) kind=what_does_it_mean
- Use a common spoken phrase/text phrase
- Must be easy enough for A1/A2
- question format MUST be exactly:
  💬
  What does it mean?
  "<core>"
- Good examples:
  "I'm hungry."
  "I'm late."
  "I'm on my way."
  "To go."
  "For here."

5) kind=ua_en
- core: one Ukrainian word or phrase from daily real life
- Keep it simple and short
- question format MUST be exactly:
  💬
  🇺🇦 → 🇬🇧
  <core>
- options order MUST be:
  1) exact correct translation
  2) close but not exact
  3) close but not exact
  4) common learner trap
- correct MUST be 0 before shuffle

QUALITY CHECK:
Before returning JSON, silently verify:
- the post feels alive and useful
- a real person could say it in real life
- the English is mostly A1/A2
- only one answer is clearly correct
- no near-duplicate of recent ideas
- no dry technical grammar sentence
- no advanced idiom
- no hard formal phrase

Return STRICT JSON ONLY.
""".strip()


def fallback_prompt_for(kind: str) -> str:
    return f"""
Return STRICT JSON ONLY.

Create ONE very simple Telegram English quiz for learners.
Target level: mostly A1/A2.
Exactly 4 unique options.
Exactly 1 correct answer.
Short Ukrainian explanation.

Kind: {kind}

Use only very easy situations:
- coffee
- restaurant
- food
- being late
- texting
- daily life
- asking for the bill
- takeaway

Avoid:
- idioms
- advanced phrases
- formal English
- difficult travel vocabulary

Question format rules:
- phrase_gap:
  💬
  Fill the gap:
  <easy phrase with ___>

- situation_quiz:
  💬
  Situation:
  <easy situation>
  What do you say?

- mini_dialogue:
  💬
  Mini dialogue:
  <easy line>
  Your reply:

- what_does_it_mean:
  💬
  What does it mean?
  "<easy phrase>"

- ua_en:
  💬
  🇺🇦 → 🇬🇧
  <easy ukrainian phrase>

JSON schema:
{{
  "level": "A1",
  "kind": "{kind}",
  "topic": "Everyday English",
  "core": "...",
  "question": "...",
  "options": ["...", "...", "...", "..."],
  "correct": 0,
  "explanation_uk": "..."
}}
""".strip()


def generate_one(kind: str, avoid_items: list[str]) -> dict:
    try:
        resp = client.responses.create(
            model=MODEL,
            input=prompt_for(kind, avoid_items),
        )
        raw = (getattr(resp, "output_text", None) or "").strip()
        return extract_json(raw)
    except Exception:
        resp = client.responses.create(
            model=MODEL,
            input=fallback_prompt_for(kind),
        )
        raw = (getattr(resp, "output_text", None) or "").strip()
        return extract_json(raw)


# ========= MAIN =========
def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN missing (set GitHub Secret BOT_TOKEN).")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing (set GitHub Secret OPENAI_API_KEY).")

    if MODE == "postgame":
        send_game_post()
        return

    history = load_history()

    seen_fp = {h.get("fp") for h in history if h.get("fp")}
    seen_core = set()
    for h in history:
        k = h.get("kind")
        c = h.get("core")
        if k in KIND_CYCLE and c:
            seen_core.add(h.get("core_fp") or core_fp(k, c))

    recent_core_by_kind = {k: set() for k in COOLDOWN_LAST_N.keys()}
    counts_by_kind = {k: 0 for k in COOLDOWN_LAST_N.keys()}

    for h in reversed(history):
        k = h.get("kind")
        if k in COOLDOWN_LAST_N and counts_by_kind[k] < COOLDOWN_LAST_N[k]:
            c = normalize_core(k, h.get("core", ""))
            if c:
                recent_core_by_kind[k].add(c)
            counts_by_kind[k] += 1

    recent_patterns = set()
    pattern_count = 0
    for h in reversed(history):
        if pattern_count >= RECENT_PATTERN_LAST_N:
            break
        if h.get("kind") in ("phrase_gap", "situation_quiz", "mini_dialogue", "what_does_it_mean"):
            pk = pattern_key(h.get("core", ""))
            if pk:
                recent_patterns.add(pk)
            pattern_count += 1

    start_kind = next_kind(history)
    start_idx = KIND_CYCLE.index(start_kind) if start_kind in KIND_CYCLE else 0

    last_err = None

    for shift in range(len(KIND_CYCLE)):
        kind = KIND_CYCLE[(start_idx + shift) % len(KIND_CYCLE)]
        tries = tries_for_kind(kind)

        avoid_items = recent_avoid_list(history, kind, 35)
        filtered_out = 0

        for _ in range(tries):
            try:
                data = generate_one(kind, avoid_items)

                level, topic, question, core, options, correct, explanation = validate_generated_quiz(kind, data)

                cfp = core_fp(kind, core)
                if cfp in seen_core:
                    filtered_out += 1
                    continue

                norm_core = normalize_core(kind, core)
                if kind in recent_core_by_kind and norm_core in recent_core_by_kind[kind]:
                    filtered_out += 1
                    continue

                pk = pattern_key(core)
                if pk in recent_patterns:
                    filtered_out += 1
                    continue

                if kind == "ua_en":
                    correct = 0

                options, correct = shuffle_options_keep_correct(options, correct)

                fp = _fp(kind, core, options)
                if fp in seen_fp:
                    filtered_out += 1
                    continue

                send_quiz_poll(question, options, correct, explanation)

                seen_fp.add(fp)
                seen_core.add(cfp)

                if kind in recent_core_by_kind and norm_core:
                    recent_core_by_kind[kind].add(norm_core)

                if pk:
                    recent_patterns.add(pk)

                history.append(
                    {
                        "ts": int(time.time()),
                        "kind": kind,
                        "level": level,
                        "topic": topic,
                        "theme": THEME,
                        "core": core,
                        "fp": fp,
                        "core_fp": cfp,
                    }
                )
                save_history(history)
                return

            except Exception as e:
                last_err = e
                continue

        if last_err is None:
            last_err = RuntimeError(f"No unique candidate for kind={kind} (filtered_out={filtered_out})")

    raise RuntimeError(f"Failed to generate quiz for all kinds. Last error: {last_err}")


if __name__ == "__main__":
    main()
