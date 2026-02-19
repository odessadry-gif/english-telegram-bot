import os
import json
import time
import re
import hashlib
import requests
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

# ✅ 3 формата (эмодзи пока НЕ трогаем)
KIND_CYCLE = ["grammar_gap", "ua_en", "guess_word"]

client = OpenAI(api_key=OPENAI_API_KEY)


# ========= HELPERS =========
def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _fp(kind: str, core: str, options: list[str]) -> str:
    payload = {
        "kind": _norm(kind),
        "core": _norm(core),
        "options": [_norm(x) for x in (options or [])],
    }
    j = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()


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
    # берём первый JSON-объект в тексте
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
    if len(options) != 3:
        raise ValueError("Telegram poll requires exactly 3 options")

    explanation = (explanation or "").strip()
    # Telegram explanation ограничен (на практике лучше держать коротко)
    if len(explanation) > 200:
        explanation = explanation[:200]

    url = f"https://api.telegram.org/bot{TOKEN}/sendPoll"
    payload = {
        "chat_id": CHAT_ID,
        "question": question,
        "options": options,
        "type": "quiz",
        "correct_option_id": int(correct_id),
        "is_anonymous": True,   # ✅ обязательно для каналов
        "allows_multiple_answers": False,
        "explanation": explanation,  # ✅ будет лампочка 💡 (без второго сообщения)
    }

    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram sendPoll error: {r.status_code} {r.text}")


# ========= PROMPTS =========
def prompt_for(kind: str) -> str:
    # Акцент A2/B1 + разнообразие времен + короткое explanation
    return f"""
Create ONE Telegram quiz for English learners (A2/B1). Return STRICT JSON ONLY (no markdown, no extra text).

JSON schema:
{{
  "level": "A2" or "B1",
  "kind": "{kind}",
  "topic": "Grammar" or "Vocabulary" or "Riddle",
  "core": "MAIN content (sentence/word) without extra labels",
  "question": "Final question text (can include line breaks)",
  "options": ["A", "B", "C"],
  "correct": 0,
  "explanation": "SHORT explanation (<=160 chars), plain text"
}}

Format rules:

1) kind=grammar_gap:
- core: ONE sentence with exactly one blank: ___
- Use different tenses over time: Present Simple/Continuous, Past Simple, Future (will/going to), present perfect basics, comparatives, prepositions, conditionals (basic).
- Avoid repeating the same pattern like "He ___ to school" too often.
- question must be exactly:
  💬 DAILY ENGLISH
  Level <A2/B1> · Grammar

  Fill the gap:
  <core>

2) kind=ua_en:
- core: ONE Ukrainian word/phrase (everyday A2/B1)
- options: 3 English translations (one correct)
- question:
  💬 DAILY ENGLISH
  Level <A2/B1> · Vocabulary

  🇺🇦 → 🇬🇧
  <core>

3) kind=guess_word:
- core: correct English word (one word, A2/B1)
- question has 2–3 short riddle lines + "What is it?"
- options: 3 words, one correct (=core)
- question:
  💬 DAILY ENGLISH
  Level <A2/B1> · Riddle

  <riddle lines>
  What is it?

Global rules:
- Exactly 3 options
- correct is 0/1/2
- Keep question short + clean (premium minimal)
- explanation: short and useful, no "Answer:" prefix, no extra emojis
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
    seen = {h.get("fp") for h in history if h.get("fp")}

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

            options = data["options"]
            if not isinstance(options, list) or len(options) != 3:
                raise ValueError("options must be a list of 3 strings")
            options = [str(x).strip() for x in options]

            correct = int(data["correct"])
            if correct not in (0, 1, 2):
                raise ValueError("correct must be 0/1/2")

            explanation = str(data.get("explanation", "")).strip()
            if not explanation:
                # безопасный дефолт, чтобы лампочка не выглядела пусто
                explanation = "Check the tense and subject."

            core = str(data.get("core", "")).strip() or question
            fp = _fp(kind, core, options)

            if fp in seen:
                # повтор — генерим снова
                continue

            # ✅ отправляем ОДИН poll, explanation будет лампочкой
            send_quiz_poll(question, options, correct, explanation)

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
