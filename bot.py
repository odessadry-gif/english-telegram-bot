import os
import json
import time
import re
import hashlib
import requests
import unicodedata
from openai import OpenAI

# ========= ENV =========
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CHAT_ID = os.getenv("CHAT_ID", "-1003674761753")  # твой канал (можно оставить так)
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

# ========= HISTORY =========
HISTORY_FILE = "history.json"
MAX_HISTORY = 800
MAX_GEN_TRIES = 12

# ✅ 3 формата
KIND_CYCLE = ["grammar_gap", "ua_en", "guess_word"]

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
    """
    Normalization for dedupe / ban logic.
    - guess_word: global ban regardless of level (umbrella == an umbrella).
    - ua_en: normalize UA core to avoid repeats like 'магазин' 3 times.
    - others: basic normalize.
    """
    if not core:
        return ""

    s = unicodedata.normalize("NFKC", str(core)).strip().lower()
    s = s.strip("“”\"'`")

    if kind == "guess_word":
        # keep letters/numbers/spaces/hyphen
        s = re.sub(r"[^a-z0-9\s\-]", " ", s)
        s = _norm_spaces(s)
        s = ARTICLES_RE.sub("", s)  # remove leading articles
        s = _norm_spaces(s)
        return s

    if kind == "ua_en":
        # Ukrainian word/phrase: trim + normalize spaces only
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _fp(kind: str, core: str, options: list[str]) -> str:
    # оставляем как есть: fp по core+options помогает от “почти одинаковых” дублей
    payload = {
        "kind": _norm(kind),
        "core": _norm(core),
        "options": [_norm(x) for x in (options or [])],
    }
    j = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()


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


def load_history() -> list[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            # поддержка старого формата (список строк)
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
            # новый формат (список dict)
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


# ========= TELEGRAM =========
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

    url = f"https://api.telegram.org/bot{TOKEN}/sendPoll"
    payload = {
        "chat_id": CHAT_ID,
        "question": question,
        "options": options,
        "type": "quiz",
        "correct_option_id": int(correct_id),
        "is_anonymous": True,
        "allows_multiple_answers": False,
        "explanation": explanation,  # лампочка 💡
    }

    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram sendPoll error: {r.status_code} {r.text}")


# ========= PROMPTS =========
def prompt_for(kind: str) -> str:
    # Новый вид шапки: "Level B1 / Riddle" (без DAILY ENGLISH и без ·)
    # Всегда 4 варианта
    # Explanation на укр
    # Для ua_en строго формат B: correct + 2 near + trap (correct всегда index 0)
    return f"""
Create ONE Telegram quiz for English learners (A2/B1). Return STRICT JSON ONLY (no markdown, no extra text).

JSON schema:
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
- Use varied tenses over time: Present Simple/Continuous, Past Simple, Future (will/going to), present perfect basics, comparatives, prepositions, basic conditionals.
- Avoid repeating the same pattern too often.
- question format MUST be exactly:
  Level <A2/B1> / Grammar
  Fill the gap:
  <core>
- options: 4 variants, only ONE correct
- correct: 0..3

2) kind=ua_en:
- core: ONE Ukrainian word/phrase (everyday A2/B1)
- question format MUST be exactly:
  Level <A2/B1> / Vocabulary
  🇺🇦 → 🇬🇧
  <core>
- options MUST be exactly 4 English answers in this exact order:
  1) correct (the best exact translation)
  2) near_1 (close meaning but NOT exact)
  3) near_2 (close meaning but NOT exact)
  4) trap (a common learner mistake / false friend)
- correct MUST be 0

3) kind=guess_word:
- core: correct English word (one word, A2/B1)
- question has 2–3 short riddle lines + "What is it?"
- question format MUST be exactly:
  Level <A2/B1> / Riddle
  <riddle lines>
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


def generate_one(kind: str) -> dict:
    resp = client.responses.create(
        model=MODEL,
        input=prompt_for(kind),
    )
    raw = (getattr(resp, "output_text", None) or "").strip()
    return extract_json(raw)


# ========= MAIN =========
def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN missing (set GitHub Secret BOT_TOKEN).")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing (set GitHub Secret OPENAI_API_KEY).")

    history = load_history()

    # fp-dedupe (как раньше)
    seen_fp = {h.get("fp") for h in history if h.get("fp")}

    # глобальный бан для guess_word по normalized core (НЕ зависит от level)
    guess_word_banned = set()
    for h in history:
        if h.get("kind") == "guess_word":
            guess_word_banned.add(normalize_core("guess_word", h.get("core", "")))

    # анти-повтор для ua_en по core (чтобы не было "магазин" 3 раза)
    ua_en_banned = set()
    for h in history:
        if h.get("kind") == "ua_en":
            ua_en_banned.add(normalize_core("ua_en", h.get("core", "")))

    kind = next_kind(history)
    last_err = None

    for _ in range(MAX_GEN_TRIES):
        try:
            data = generate_one(kind)

            level = str(data.get("level", "A2")).strip().upper()
            if level not in ("A2", "B1"):
                level = "A2"

            topic = str(data.get("topic", "Grammar")).strip()
            question = str(data["question"]).strip()

            options = ensure_4_options(data.get("options", []))
            if len(options) != 4:
                raise ValueError("options must be a list of 4 unique strings")

            correct = int(data.get("correct", 0))
            if correct not in (0, 1, 2, 3):
                raise ValueError("correct must be 0/1/2/3")

            explanation = str(data.get("explanation_uk", "")).strip()
            if not explanation:
                # дефолт на укр, чтобы лампочка не пустая
                explanation = "Перевір підмет і час у реченні."

            core = str(data.get("core", "")).strip() or question

            # --- специальные анти-повторы ---
            if kind == "guess_word":
                norm_word = normalize_core("guess_word", core)
                if not norm_word:
                    continue
                if norm_word in guess_word_banned:
                    # глобально уже было — генерим снова
                    continue

            if kind == "ua_en":
                norm_ua = normalize_core("ua_en", core)
                if norm_ua and norm_ua in ua_en_banned:
                    continue
                # по твоему формату B correct всегда 0
                correct = 0

            # fp как раньше
            fp = _fp(kind, core, options)
            if fp in seen_fp:
                continue

            # ✅ отправляем ОДИН poll
            send_quiz_poll(question, options, correct, explanation)

            # ✅ обновляем бан-сеты сразу
            if kind == "guess_word":
                guess_word_banned.add(normalize_core("guess_word", core))
            if kind == "ua_en":
                ua_en_banned.add(normalize_core("ua_en", core))

            history.append(
                {
                    "ts": int(time.time()),
                    "kind": kind,
                    "level": level,
                    "topic": topic,
                    "core": core,
                    "fp": fp,
                }
            )
            save_history(history)
            return

        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Failed to generate unique quiz for kind={kind}. Last error: {last_err}")


if __name__ == "__main__":
    main()
