import os
import json
import time
import re
import hashlib
import requests
from openai import OpenAI

TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CHAT_ID = os.getenv("CHAT_ID", "-1003674761753")

HISTORY_FILE = "history.json"
MAX_HISTORY = 800
MAX_GEN_TRIES = 10

# 4 формата в ротации
KIND_CYCLE = ["grammar_gap", "ua_en", "guess_word", "emoji_quiz"]

client = OpenAI(api_key=OPENAI_API_KEY)


def _norm(s: str) -> str:
    s = str(s).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _fp(kind: str, core: str, options: list[str]) -> str:
    payload = {
        "kind": _norm(kind),
        "core": _norm(core),
        "options": [_norm(x) for x in options],
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
            # поддержка старого формата: список строк
            if data and isinstance(data[0], str):
                return [
                    {
                        "ts": 0,
                        "kind": "legacy",
                        "fp": hashlib.sha256(_norm(x).encode("utf-8")).hexdigest(),
                        "core": x,
                    }
                    for x in data
                ]

            # новый формат: список dict
            return [x for x in data if isinstance(x, dict)]

        return []
    except Exception:
        return []


def save_history(items: list[dict]) -> None:
    items = items[-MAX_HISTORY:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def next_kind(history: list[dict]) -> str:
    last_kind = None
    for item in reversed(history):
        k = item.get("kind")
        if k in KIND_CYCLE:
            last_kind = k
            break

    if not last_kind:
        return KIND_CYCLE[0]

    idx = KIND_CYCLE.index(last_kind)
    return KIND_CYCLE[(idx + 1) % len(KIND_CYCLE)]


def send_quiz(question: str, options: list[str], correct_id: int, explanation: str):
    question = (question or "").strip()
    if len(question) > 280:
        question = question[:277] + "..."

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
        "is_anonymous": True,   # обязательно для каналов
        "explanation": explanation,
    }

    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram error: {r.status_code} {r.text}")


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("No JSON found")
    return json.loads(m.group(0))


def prompt_for(kind: str) -> str:
    return f"""
Create ONE Telegram quiz for English learners (A1/A2). Return STRICT JSON ONLY:

{{
  "level": "A1" or "A2",
  "kind": "{kind}",
  "topic": "Grammar" or "Vocabulary" or "Riddle" or "Emoji",
  "core": "MAIN content without extra labels",
  "question": "FINAL question text (can include line breaks)",
  "options": ["A", "B", "C"],
  "correct": 0,
  "explanation": "Short explanation in simple English (1–2 sentences). No markdown."
}}

Rules for each kind:

1) kind=grammar_gap:
- core: ONLY sentence with exactly one ___
- question must be formatted like:
  💬 DAILY ENGLISH
  Level <A1/A2> · Grammar

  Fill the gap:
  <core>

2) kind=ua_en:
- core: ONLY one Ukrainian word (or short phrase, max 2 words)
- options: 3 English translations (one correct)
- question format:
  💬 DAILY ENGLISH
  Level <A1/A2> · Vocabulary

  🇺🇦 → 🇬🇧
  <core>

3) kind=guess_word:
- core: the correct English word (one word)
- question contains a short A1/A2 riddle (2–3 short lines), then:
  What is it?
- options: 3 words, one correct (=core)
- question format:
  💬 DAILY ENGLISH
  Level <A1/A2> · Riddle

  <riddle lines>
  What is it?

4) kind=emoji_quiz:
- core: the correct English word/phrase (1–2 words max)
- question contains ONLY emojis line + "What is it?"
- options: 3 answers, one correct (=core)
- question format:
  💬 DAILY ENGLISH
  Level <A1/A2> · Emoji

  <emoji line>
  What is it?

Global rules:
- Exactly 3 options
- correct is 0/1/2
- Keep it short and modern
- Vary grammar structures and sentence templates
- Use different tenses over time:
  Present Simple, Present Continuous, Past Simple,
  Future (will / going to),
  basic conditionals, prepositions, comparatives
- Avoid repeating the same tense too often
- Do not generate very similar sentence patterns
- Return strict JSON only. No extra text.
""".strip()


def generate_one(kind: str) -> dict:
    resp = client.responses.create(
        model="gpt-5-mini",
        input=prompt_for(kind),
    )
    raw = (resp.output_text or "").strip()
    return extract_json(raw)


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")

    history = load_history()
    seen = {h.get("fp") for h in history if h.get("fp")}

    kind = next_kind(history)
    last_err = None

    for _ in range(MAX_GEN_TRIES):
        try:
            data = generate_one(kind)

            level = str(data.get("level", "A1")).strip().upper()
            topic = str(data.get("topic", "")).strip()
            question = str(data.get("question", "")).strip()

            options = data.get("options")
            if not isinstance(options, list) or len(options) != 3:
                raise ValueError("options must be list of 3")
            options = [str(x).strip() for x in options]

            correct = int(data.get("correct", 0))
            if correct not in (0, 1, 2):
                raise ValueError("correct must be 0/1/2")

            explanation = str(data.get("explanation", "")).strip()
            if not explanation:
                explanation = "Quick tip: check the tense and the subject."

            core = str(data.get("core", question)).strip()
            fp = _fp(kind, core, options)

            if fp in seen:
                continue

            send_quiz(question, options, correct, explanation)

            history.append(
                {
                    "ts": int(time.time()),
                    "kind": kind,
                    "level": level,
                    "topic": topic,
                    "core": core,
                    "fp": fp,
                    "explanation": explanation,
                }
            )
            save_history(history)
            return

        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(
        f"Failed to generate unique quiz for kind={kind}. Last error: {last_err}"
    )


if __name__ == "__main__":
    main()
