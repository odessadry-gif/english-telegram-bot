import os
import json
import time
import re
import hashlib
import requests
import unicodedata
import random
from openai import OpenAI

# ========= ENV =========
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CHAT_ID = os.getenv("CHAT_ID", "-1003674761753")  # куда постятся квизы (как было)
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

# ✅ НОВОЕ: тема квизов (по умолчанию travel)
THEME = os.getenv("THEME", "travel").strip().lower()

# ✅ НОВОЕ: режимы
# MODE=quiz      -> как раньше (по умолчанию)
# MODE=postgame  -> отдельный пост с кнопкой
MODE = os.getenv("MODE", "quiz").strip().lower()

# ✅ НОВОЕ: куда постить кнопку (группа/канал)
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "@Official_english_every_day")

# ✅ НОВОЕ: ссылка на мини-игру
GAME_URL = os.getenv("GAME_URL", "https://odessadry-gif.github.io/english-telegram-bot/")

# ✅ НОВОЕ: подпись на кнопке и текст поста
GAME_BUTTON_TEXT = os.getenv("GAME_BUTTON_TEXT", "⚡ Word Rush")
GAME_POST_TEXT = os.getenv(
    "GAME_POST_TEXT",
    "⚡ **Word Rush** — 2 хвилини на швидкий англійський челендж\n\n"
    "Вгадай 20 слів за 2 хвилини. Спробуй побити топ-5 👇",
)

# ========= HISTORY =========
HISTORY_FILE = "history.json"
MAX_HISTORY = 800

# Базовое число попыток
MAX_GEN_TRIES_DEFAULT = 12
# Больше попыток для сложных случаев
MAX_GEN_TRIES_BY_KIND = {
    "guess_word": 40,
    "grammar_gap": 18,
    "ua_en": 18,
}

# ✅ 3 формата
KIND_CYCLE = ["grammar_gap", "ua_en", "guess_word"]

# ========= ANTI-REPEAT TUNING =========
COOLDOWN_LAST_N = {
    "ua_en": 200,
    "grammar_gap": 120,
}
GRAMMAR_PATTERN_LAST_N = 180
GUESS_WORD_AVOID_LAST_N = 80

client = OpenAI(api_key=OPENAI_API_KEY)

ARTICLES_RE = re.compile(r"^(a|an|the)\s+", re.IGNORECASE)


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

    if kind == "guess_word":
        s = re.sub(r"[^a-z0-9\s\-]", " ", s)
        s = _norm_spaces(s)
        s = ARTICLES_RE.sub("", s)
        s = _norm_spaces(s)
        return s

    if kind == "ua_en":
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _fp(kind: str, core: str, options: list[str]) -> str:
    payload = {
        "kind": _norm(kind),
        "core": _norm(core),
        "options": [_norm(x) for x in (options or [])],
    }
    j = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()


def core_fp(kind: str, core: str) -> str:
    payload = {"kind": _norm(kind), "core": normalize_core(kind, core)}
    j = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()


def grammar_pattern_key(core: str) -> str:
    s = unicodedata.normalize("NFKC", (core or "")).lower()
    s = s.replace("___", " <blank> ")
    s = re.sub(r"\d+", "<num>", s)
    s = re.sub(r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\b", "<num>", s)
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


def get_guess_word_avoid_list(history: list[dict], n: int) -> list[str]:
    out = []
    seen = set()
    for h in reversed(history or []):
        if h.get("kind") != "guess_word":
            continue
        w = normalize_core("guess_word", h.get("core", ""))
        if not w or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= n:
            break
    return out


def tries_for_kind(kind: str) -> int:
    return int(MAX_GEN_TRIES_BY_KIND.get(kind, MAX_GEN_TRIES_DEFAULT))


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


# ✅ НОВОЕ: пост с кнопкой (в группу/канал)
def send_game_post():
    text = (GAME_POST_TEXT or "").strip()
    if not text:
        text = "⚡ Word Rush — play now!"

    reply_markup = {
        "inline_keyboard": [
            [{"text": GAME_BUTTON_TEXT, "url": GAME_URL}],
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
def prompt_for(kind: str, guess_word_avoid: list[str]) -> str:
    avoid_line = ""
    if kind == "guess_word" and guess_word_avoid:
        avoid_line = "Avoid these words (do NOT use them as the answer): " + ", ".join(guess_word_avoid) + "\n"

    theme_block = ""
    if THEME == "travel":
        theme_block = """
THEME (HARD RULE): Travel & everyday trips only.
Use contexts like: airport, boarding, passport, luggage, hotel, check-in/check-out, reservation, city transport, taxi, directions, tickets, sightseeing, money exchange, travel problems, polite requests.
Avoid: politics, medicine, religion, explicit content, violence, war.
""".strip()
    elif THEME:
        # fallback: если ты потом захочешь другую тему одним env
        theme_block = f"THEME (HARD RULE): {THEME}. Keep everything in this theme.\n"

    return f"""
Create ONE Telegram quiz for English learners (A2/B1). Return STRICT JSON ONLY (no markdown, no extra text).

{theme_block}
{avoid_line}JSON schema:
{{
  "level": "A2" or "B1",
  "kind": "{kind}",
  "topic": "Grammar" or "Vocabulary" or "Riddle",
  "core": "MAIN content (sentence/word) without extra labels",
  "question": "Final question text (can include line breaks)",
  "options": ["A", "B", "C", "D"],
  "correct": 0,
  "explanation_uk": "SHORT Ukrainian explanation (<=160 chars), plain text"
}}

Format rules:

1) kind=grammar_gap:
- core: ONE sentence with exactly one blank: ___
- Use ONLY these tenses:
  Present Simple, Past Simple, Future Simple (will), Present Continuous, Past Continuous.
- DO NOT use: any Perfect tenses, modals (should/might/could), conditionals, comparatives.
- Keep sentences short and clear (A2/B1), everyday situations.
- MUST follow the theme above.
- question format MUST be exactly:
  💬
  Fill the gap:
  <core>
- options: 4 variants, only ONE correct
- correct: 0..3

2) kind=ua_en:
- core: ONE Ukrainian word/phrase (everyday A2/B1) in the theme above
- question format MUST be exactly:
  💬
  🇺🇦 → 🇬🇧
  <core>
- options MUST be exactly 4 English answers in this exact order:
  1) correct (the best exact translation)
  2) near_1 (close meaning but NOT exact)
  3) near_2 (close meaning but NOT exact)
  4) trap (a common learner mistake / false friend)
- correct MUST be 0

3) kind=guess_word:
- core: correct English word (one word, A2/B1) in the theme above.
- Use less obvious everyday nouns/objects (NOT the most common ones).
- question has 2–3 short riddle lines + "What is it?"
- question format MUST be exactly:
  💬
  <riddle line 1>
  <riddle line 2>
  (optional riddle line 3)
  What is it?
- options: 4 words, one correct (=core)
- correct: 0..3

Global rules:
- Exactly 4 options
- Options must be UNIQUE
- Keep question short + clean (premium minimal)
- explanation_uk: short and useful Ukrainian, no emojis, no "Answer:" prefix
Return STRICT JSON ONLY.
""".strip()


def generate_one(kind: str, guess_word_avoid: list[str]) -> dict:
    resp = client.responses.create(
        model=MODEL,
        input=prompt_for(kind, guess_word_avoid),
    )
    raw = (getattr(resp, "output_text", None) or "").strip()
    return extract_json(raw)


# ========= MAIN =========
def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN missing (set GitHub Secret BOT_TOKEN).")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing (set GitHub Secret OPENAI_API_KEY).")

    # ✅ режим поста с кнопкой (без генерации квиза)
    if MODE == "postgame":
        send_game_post()
        return

    # ====== ниже всё как у тебя работало ======
    history = load_history()
    seen_fp = {h.get("fp") for h in history if h.get("fp")}

    seen_core = set()
    for h in history:
        k = h.get("kind")
        c = h.get("core")
        if k in KIND_CYCLE and c:
            seen_core.add(h.get("core_fp") or core_fp(k, c))

    guess_word_banned = set()
    for h in history:
        if h.get("kind") == "guess_word":
            guess_word_banned.add(normalize_core("guess_word", h.get("core", "")))

    recent_core_by_kind = {k: set() for k in COOLDOWN_LAST_N.keys()}
    counts_by_kind = {k: 0 for k in COOLDOWN_LAST_N.keys()}
    for h in reversed(history):
        k = h.get("kind")
        if k in COOLDOWN_LAST_N and counts_by_kind[k] < COOLDOWN_LAST_N[k]:
            c = normalize_core(k, h.get("core", ""))
            if c:
                recent_core_by_kind[k].add(c)
            counts_by_kind[k] += 1

    recent_grammar_patterns = set()
    gp_count = 0
    for h in reversed(history):
        if gp_count >= GRAMMAR_PATTERN_LAST_N:
            break
        if h.get("kind") == "grammar_gap":
            pk = grammar_pattern_key(h.get("core", ""))
            if pk:
                recent_grammar_patterns.add(pk)
            gp_count += 1

    guess_word_avoid = get_guess_word_avoid_list(history, GUESS_WORD_AVOID_LAST_N)

    start_kind = next_kind(history)
    start_idx = KIND_CYCLE.index(start_kind) if start_kind in KIND_CYCLE else 0

    last_err = None
    for shift in range(len(KIND_CYCLE)):
        kind = KIND_CYCLE[(start_idx + shift) % len(KIND_CYCLE)]
        tries = tries_for_kind(kind)
        filtered_out = 0

        for _ in range(tries):
            try:
                data = generate_one(kind, guess_word_avoid)

                question = str(data["question"]).strip()

                options = ensure_4_options(data.get("options", []))
                if len(options) != 4:
                    raise ValueError("options must be a list of 4 unique strings")

                correct = int(data.get("correct", 0))
                if correct not in (0, 1, 2, 3):
                    raise ValueError("correct must be 0..3")

                explanation = str(data.get("explanation_uk", "")).strip()
                if not explanation:
                    explanation = "Перевір підмет і час у реченні."

                core = str(data.get("core", "")).strip() or question

                cfp = core_fp(kind, core)
                if cfp in seen_core:
                    filtered_out += 1
                    continue

                if kind == "guess_word":
                    norm_word = normalize_core("guess_word", core)
                    if not norm_word:
                        filtered_out += 1
                        continue
                    if norm_word in guess_word_banned:
                        filtered_out += 1
                        continue

                if kind == "ua_en":
                    norm_ua = normalize_core("ua_en", core)
                    if not norm_ua:
                        filtered_out += 1
                        continue
                    if norm_ua in recent_core_by_kind["ua_en"]:
                        filtered_out += 1
                        continue
                    correct = 0  # фиксировано по правилу ua_en

                if kind == "grammar_gap":
                    norm_g = normalize_core("grammar_gap", core)
                    if norm_g and norm_g in recent_core_by_kind["grammar_gap"]:
                        filtered_out += 1
                        continue
                    pk = grammar_pattern_key(core)
                    if pk and pk in recent_grammar_patterns:
                        filtered_out += 1
                        continue

                # ✅ shuffle (как у тебя)
                options, correct = shuffle_options_keep_correct(options, correct)

                fp = _fp(kind, core, options)
                if fp in seen_fp:
                    filtered_out += 1
                    continue

                send_quiz_poll(question, options, correct, explanation)

                seen_fp.add(fp)
                seen_core.add(cfp)

                if kind == "guess_word":
                    guess_word_banned.add(normalize_core("guess_word", core))

                if kind in recent_core_by_kind:
                    normc = normalize_core(kind, core)
                    if normc:
                        recent_core_by_kind[kind].add(normc)

                if kind == "grammar_gap":
                    pk = grammar_pattern_key(core)
                    if pk:
                        recent_grammar_patterns.add(pk)

                history.append(
                    {
                        "ts": int(time.time()),
                        "kind": kind,
                        "level": str(data.get("level", "A2")).strip().upper(),
                        "topic": str(data.get("topic", "")),
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

    raise RuntimeError(f"Failed to generate quiz for ALL kinds. Last error: {last_err}")


if __name__ == "__main__":
    main()
