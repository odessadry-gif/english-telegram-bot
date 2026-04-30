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

# ========= HISTORY =========
HISTORY_FILE = "history.json"
MAX_HISTORY = 1500
RECENT_CORE_LIMIT = 300
RECENT_PATTERN_LIMIT = 220
RECENT_RIDDLE_ANSWER_LIMIT = 120

QUIZ_TYPE_WEIGHTS = {
    "grammar_gap": 50,
    "ua_en": 30,
    "riddle": 20,
}

LEVEL_WEIGHTS = {
    "A1": 20,
    "A2": 35,
    "B1": 45,
}

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
    "home",
    "work",
    "school",
    "room",
    "phone",
    "book",
    "chair",
    "window",
    "door",
    "to go",
    "for here",
    "on my way",
    "can i",
    "i'd like",
    "we need",
    "where is",
    "how much",
    "what time",
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
    s = re.sub(r"[^a-z0-9а-яіїєґ\s\-\?']", " ", s)
    s = _norm_spaces(s)
    s = ARTICLES_RE.sub("", s)
    s = _norm_spaces(s)
    return s


def pattern_key(text: str) -> str:
    s = unicodedata.normalize("NFKC", (text or "")).lower()
    s = s.replace("___", " <blank> ")
    s = re.sub(r"\d+", "<num>", s)
    s = re.sub(r"[^a-zа-яіїєґ<>\s]", " ", s)
    s = _norm_spaces(s)
    return s


def fp_for(kind: str, core: str, options: list[str]) -> str:
    payload = {
        "kind": kind,
        "core": normalize_core(core),
        "options": [_norm(x) for x in (options or [])],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def core_fp(kind: str, core: str) -> str:
    raw = json.dumps(
        {"kind": kind, "core": normalize_core(core)},
        ensure_ascii=False,
        sort_keys=True,
    )
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


def recent_cores(history: list[dict], n: int, kind: str | None = None) -> list[str]:
    out = []
    seen = set()

    for item in reversed(history or []):
        if kind and item.get("kind") != kind:
            continue

        core = normalize_core(item.get("core", ""))
        if not core or core in seen:
            continue

        seen.add(core)
        out.append(core)
        if len(out) >= n:
            break

    return out


def recent_riddle_answers(history: list[dict], n: int) -> list[str]:
    out = []
    seen = set()

    for item in reversed(history or []):
        if item.get("kind") != "riddle":
            continue

        answer = normalize_core(item.get("answer", ""))
        if not answer or answer in seen:
            continue

        seen.add(answer)
        out.append(answer)
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


def pick_quiz_type() -> str:
    pool = (
        ["grammar_gap"] * QUIZ_TYPE_WEIGHTS["grammar_gap"] +
        ["ua_en"] * QUIZ_TYPE_WEIGHTS["ua_en"] +
        ["riddle"] * QUIZ_TYPE_WEIGHTS["riddle"]
    )
    return random.choice(pool)


def pick_level() -> str:
    pool = []
    for level, weight in LEVEL_WEIGHTS.items():
        pool.extend([level] * weight)
    return random.choice(pool)


# ========= VALIDATION =========
def validate_generated(data: dict, quiz_type: str) -> tuple[str, str, list[str], int, str, str]:
    core = str(data.get("core", "")).strip()
    question = str(data.get("question", "")).strip()
    options = ensure_4_options(data.get("options", []))
    correct = int(data.get("correct", 0))
    explanation = str(data.get("explanation_uk", "")).strip()

    if not core:
        raise ValueError("empty core")

    if len(question) > 220:
        raise ValueError("question too long")

    if len(options) != 4:
        raise ValueError("must have exactly 4 options")

    if correct not in (0, 1, 2, 3):
        raise ValueError("correct must be 0..3")

    if not explanation:
        raise ValueError("empty explanation")

    if len(explanation) > 180:
        raise ValueError("explanation too long")

    low_opts = [_norm_spaces(x).lower() for x in options]
    if len(set(low_opts)) != 4:
        raise ValueError("duplicate options")

    normalized_opts = [normalize_core(x) for x in options]
    if len(set(normalized_opts)) != 4:
        raise ValueError("options too similar")

    if looks_too_hard(core) or looks_too_hard(question):
        raise ValueError("too hard")

    for opt in options:
        if looks_too_hard(opt):
            raise ValueError("hard option")

    # ===== grammar_gap =====
    if quiz_type == "grammar_gap":
        if core.count("___") != 1:
            raise ValueError("grammar_gap: core must contain exactly one ___")

        if not question.startswith("💬\nFill the gap:\n"):
            raise ValueError("grammar_gap: wrong question format")

        if not looks_simple_enough(core):
            raise ValueError("grammar_gap: not simple enough")

        return core, question, options, correct, explanation, ""

    # ===== ua_en =====
    if quiz_type == "ua_en":
        if "___" in core:
            raise ValueError("ua_en: core must not contain blank")

        if not question.startswith("💬\n🇺🇦 → 🇬🇧\n"):
            raise ValueError("ua_en: wrong question format")

        if len(core.split()) > 8:
            raise ValueError("ua_en: core too long")

        correct_option = options[correct].strip()
        if len(correct_option.split()) > 8:
            raise ValueError("ua_en: correct option too long")

        return core, question, options, correct, explanation, ""

    # ===== riddle =====
    if quiz_type == "riddle":
        answer = str(data.get("answer", "")).strip()

        if "___" in core:
            raise ValueError("riddle: no blank allowed")

        if not answer:
            raise ValueError("riddle: empty answer")

        if len(answer.split()) != 1:
            raise ValueError("riddle: answer must be one word")

        if not question.startswith("💬\n"):
            raise ValueError("riddle: wrong question prefix")

        if "What is it?" not in question:
            raise ValueError("riddle: question must contain 'What is it?'")

        if len(core.splitlines()) < 2:
            raise ValueError("riddle: core should have 2-3 short clue lines")

        if normalize_core(answer) != normalize_core(options[correct]):
            raise ValueError("riddle: answer must equal correct option")

        return core, question, options, correct, explanation, answer

    raise ValueError("unknown quiz type")


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


# ========= PROMPTS =========
def build_prompt(quiz_type: str, target_level: str, avoid_items: list[str], avoid_answers: list[str]) -> str:
    avoid_block = ""
    if avoid_items:
        avoid_block = (
            "Avoid these recent ideas. Do not repeat or make near-copies:\n- "
            + "\n- ".join(avoid_items[:40])
        )

    avoid_answers_block = ""
    if quiz_type == "riddle" and avoid_answers:
        avoid_answers_block = (
            "Avoid these recent riddle answers. Do not repeat them:\n- "
            + "\n- ".join(avoid_answers[:40])
        )

    common_rules = """
LEVEL:
- Target level for this poll: {target_level}
- A1 = very basic words and simple present / be
- A2 = practical everyday phrases and simple travel/food situations
- B1 = natural everyday English, short useful phrases, still clear and not advanced

GLOBAL RULES:
- exactly 4 options
- exactly 1 correct answer
- no second correct answer
- wrong answers must be clearly wrong
- no perfect tenses
- no idioms
- no business English
- no tricky grammar
- no advanced service phrases
- very clear and useful in real life
- explanation_uk must be short and simple
- use natural everyday English
""".format(target_level=target_level).strip()

    if quiz_type == "grammar_gap":
        return f"""
Create ONE Telegram English poll for A1-B1 learners.
Return STRICT JSON ONLY.

TYPE:
grammar_gap

GOAL:
Make a super clear {target_level} fill-the-gap question.
Only ONE answer must fit.
No doubtful phrasing.
Avoid slippery cases like article + drink/food if it may confuse beginners.

TOPICS:
- travel
- food
- ordering food
- coffee shop
- hotel
- taxi
- airport
- train
- simple daily life
- asking for help
- being late

{common_rules}

FORMAT:
💬
Fill the gap:
<sentence with ___>

RULES FOR SENTENCE:
- exactly one blank: ___
- short sentence
- answer in 1-2 seconds
- one obvious best option
- include enough context
- good for the target level: {target_level}
- prefer verbs, basic nouns, pronouns, time markers, simple prepositions
- avoid phrases where two words can sound okay

GOOD EXAMPLES:
- I am ___ now.
- She ___ at home.
- We need a ___ for two.
- Where is my ___?
- He goes to work by ___.
- I’m on my ___.
- Can I pay by ___?
- The train is at ___ 5.

BAD EXAMPLES:
- I’d like a ___ coffee.
- anything with two natural answers
- anything too textbook
- anything without context

JSON schema:
{{
  "core": "sentence with ___",
  "question": "💬\\nFill the gap:\\n<same sentence with ___>",
  "options": ["...", "...", "...", "..."],
  "correct": 0,
  "explanation_uk": "short explanation in Ukrainian"
}}

EXTRA CHECK:
- only one answer works
- no ambiguity
- fast to solve
- simple enough for beginners

{avoid_block}

Return STRICT JSON ONLY.
""".strip()

    if quiz_type == "ua_en":
        return f"""
Create ONE Telegram English poll for A1-B1 learners.
Return STRICT JSON ONLY.

TYPE:
ua_en

GOAL:
Make a very clear Ukrainian-to-English multiple choice quiz.
Target level: {target_level}.
Only ONE translation must be correct.

TOPICS:
- travel
- food
- coffee shop
- hotel
- airport
- taxi
- daily life
- simple phrases
- basic communication

{common_rules}

FORMAT:
💬
🇺🇦 → 🇬🇧
<Ukrainian phrase>

RULES:
- Ukrainian phrase must be short: 2-8 words
- English correct answer must be short and natural
- exactly one correct translation
- 2 wrong options should be close-ish learner mistakes
- 1 wrong option may be a trap
- no two English options may both fit
- avoid formal language
- avoid long sentences

GOOD EXAMPLES:
- Я голодний.
- Де мій квиток?
- Мені потрібне таксі.
- Я вже йду.
- Скільки це коштує?

JSON schema:
{{
  "core": "Ukrainian phrase",
  "question": "💬\\n🇺🇦 → 🇬🇧\\n<Ukrainian phrase>",
  "options": ["...", "...", "...", "..."],
  "correct": 0,
  "explanation_uk": "short explanation in Ukrainian"
}}

EXTRA CHECK:
- exactly one correct translation
- no optional synonyms that also fit
- simple and useful

{avoid_block}

Return STRICT JSON ONLY.
""".strip()

    if quiz_type == "riddle":
        return f"""
Create ONE Telegram English poll for A1-B1 learners.
Return STRICT JSON ONLY.

TYPE:
riddle

GOAL:
Make a clear one-word English riddle for {target_level}.
It must be fun and simple.

TOPICS:
- basic everyday objects
- food
- transport
- travel items
- home items
- simple nature words
- body parts
- school / cafe / hotel basics

{common_rules}

FORMAT:
💬
<2 or 3 very short clue lines>
What is it?

RULES:
- answer must be ONE English word
- clues must be very easy
- user should solve in 2-4 seconds
- no poetic clues
- no abstract words
- no rare nouns
- options must be 4 one-word answers if possible
- only one answer fits clearly

GOOD EXAMPLES:
It is yellow.
Monkeys like it.
What is it?

You sleep on it.
It is in your bedroom.
What is it?

JSON schema:
{{
  "core": "clue line 1\\nclue line 2",
  "question": "💬\\nclue line 1\\nclue line 2\\nWhat is it?",
  "options": ["...", "...", "...", "..."],
  "correct": 0,
  "answer": "oneword",
  "explanation_uk": "short explanation in Ukrainian"
}}

EXTRA CHECK:
- answer is one word
- no repeated recent answers
- only one option matches
- very easy

{avoid_block}
{avoid_answers_block}

Return STRICT JSON ONLY.
""".strip()

    raise ValueError("Unknown quiz type")


def fallback_prompt(quiz_type: str, target_level: str) -> str:
    if quiz_type == "grammar_gap":
        return f"""
Return STRICT JSON ONLY.

Create ONE clear English fill-the-gap quiz for Telegram.

Rules:
- target level: {target_level}
- 4 unique options
- 1 correct answer only
- very clear
- no ambiguity
- useful in real life

Question format exactly:
💬
Fill the gap:
<sentence with ___>

JSON schema:
{{
  "core": "sentence with ___",
  "question": "💬\\nFill the gap:\\n<sentence with ___>",
  "options": ["...", "...", "...", "..."],
  "correct": 0,
  "explanation_uk": "short explanation in Ukrainian"
}}
""".strip()

    if quiz_type == "ua_en":
        return f"""
Return STRICT JSON ONLY.

Create ONE clear Ukrainian-to-English quiz for Telegram.

Rules:
- target level: {target_level}
- 4 unique options
- 1 correct translation only
- short phrase
- very clear

Question format exactly:
💬
🇺🇦 → 🇬🇧
<Ukrainian phrase>

JSON schema:
{{
  "core": "Ukrainian phrase",
  "question": "💬\\n🇺🇦 → 🇬🇧\\n<Ukrainian phrase>",
  "options": ["...", "...", "...", "..."],
  "correct": 0,
  "explanation_uk": "short explanation in Ukrainian"
}}
""".strip()

    if quiz_type == "riddle":
        return f"""
Return STRICT JSON ONLY.

Create ONE clear one-word English riddle for Telegram.

Rules:
- target level: {target_level}
- 4 unique options
- 1 correct answer only
- answer = one word
- very easy and clear

Question format exactly:
💬
<clue line 1>
<clue line 2>
What is it?

JSON schema:
{{
  "core": "clue line 1\\nclue line 2",
  "question": "💬\\nclue line 1\\nclue line 2\\nWhat is it?",
  "options": ["...", "...", "...", "..."],
  "correct": 0,
  "answer": "oneword",
  "explanation_uk": "short explanation in Ukrainian"
}}
""".strip()

    raise ValueError("Unknown quiz type")


def generate_one(quiz_type: str, target_level: str, avoid_items: list[str], avoid_answers: list[str]) -> dict:
    try:
        resp = client.responses.create(
            model=MODEL,
            input=build_prompt(quiz_type, target_level, avoid_items, avoid_answers),
        )
        raw = (getattr(resp, "output_text", None) or "").strip()
        return extract_json(raw)
    except Exception:
        resp = client.responses.create(
            model=MODEL,
            input=fallback_prompt(quiz_type, target_level),
        )
        raw = (getattr(resp, "output_text", None) or "").strip()
        return extract_json(raw)


# ========= MAIN =========
def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")

    history = load_history()

    seen_fp = {h.get("fp") for h in history if h.get("fp")}
    seen_core_fp = {h.get("core_fp") for h in history if h.get("core_fp")}

    recent_patterns_by_kind = {
        "grammar_gap": set(),
        "ua_en": set(),
        "riddle": set(),
    }

    pattern_counts = {
        "grammar_gap": 0,
        "ua_en": 0,
        "riddle": 0,
    }

    for h in reversed(history):
        kind = h.get("kind")
        if kind not in recent_patterns_by_kind:
            continue
        if pattern_counts[kind] >= RECENT_PATTERN_LIMIT:
            continue

        core = h.get("core", "")
        pk = pattern_key(core)
        if pk:
            recent_patterns_by_kind[kind].add(pk)
            pattern_counts[kind] += 1

    recent_core_sets = {
        "grammar_gap": set(recent_cores(history, RECENT_CORE_LIMIT, "grammar_gap")),
        "ua_en": set(recent_cores(history, RECENT_CORE_LIMIT, "ua_en")),
        "riddle": set(recent_cores(history, RECENT_CORE_LIMIT, "riddle")),
    }

    recent_riddle_answer_set = set(recent_riddle_answers(history, RECENT_RIDDLE_ANSWER_LIMIT))

    last_err = None

    for _ in range(40):
        try:
            quiz_type = pick_quiz_type()
            target_level = pick_level()
            avoid_items = recent_cores(history, 40, quiz_type)
            avoid_answers = recent_riddle_answers(history, 40) if quiz_type == "riddle" else []

            data = generate_one(quiz_type, target_level, avoid_items, avoid_answers)
            core, question, options, correct, explanation, answer = validate_generated(data, quiz_type)

            cfp = core_fp(quiz_type, core)
            if cfp in seen_core_fp:
                continue

            norm_core = normalize_core(core)
            if norm_core in recent_core_sets[quiz_type]:
                continue

            pk = pattern_key(core)
            if pk in recent_patterns_by_kind[quiz_type]:
                continue

            if quiz_type == "riddle":
                norm_answer = normalize_core(answer)
                if norm_answer in recent_riddle_answer_set:
                    continue

            fp = fp_for(quiz_type, core, options)
            if fp in seen_fp:
                continue

            shuffled_options, new_correct = shuffle_options_keep_correct(options, correct)
            send_quiz_poll(question, shuffled_options, new_correct, explanation)

            history_item = {
                "ts": int(time.time()),
                "kind": quiz_type,
                "level": target_level,
                "core": core,
                "fp": fp_for(quiz_type, core, shuffled_options),
                "core_fp": cfp,
            }

            if quiz_type == "riddle":
                history_item["answer"] = answer

            history.append(history_item)
            save_history(history)
            return

        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Failed to generate quiz. Last error: {last_err}")


if __name__ == "__main__":
    main()
