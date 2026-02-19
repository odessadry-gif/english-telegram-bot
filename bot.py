import os
import json
import time
import re
import hashlib
import random
import requests
from openai import OpenAI

# =========================
# ENV
# =========================
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Можно оставить твой дефолт, но лучше вынести в Secrets/Env позже
CHAT_ID = os.getenv("CHAT_ID", "-1003674761753")

# =========================
# SETTINGS
# =========================
HISTORY_FILE = "history.json"
MAX_HISTORY = 1200
MAX_GEN_TRIES = 12

# 3 формата (emoji пока выключили)
KIND_CYCLE = ["grammar_gap", "ua_en", "guess_word"]

# Уровни: больше A2/B1
LEVEL_POOL = ["A2", "A2", "A2", "B1", "B1"]

client = OpenAI(api_key=OPENAI_API_KEY)


# =========================
# HELPERS
# =========================
def _norm(s: str) -> str:
    s = (s or "").strip().lower()
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

        # старый формат: список строк
        if isinstance(data, list) and data and isinstance(data[0], str):
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
        if isinstance(data, list):
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


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("No JSON found in model output")
    return json.loads(m.group(0))


# =========================
# TELEGRAM
# =========================
def send_poll(question: str, options: list[str], correct_id: int) -> int:
    """
    Возвращает message_id опубликованного poll.
    """
    question = (question or "").strip()
    if len(question) > 280:
        question = question[:277] + "..."

    url = f"https://api.telegram.org/bot{TOKEN}/sendPoll"
    payload = {
        "chat_id": CHAT_ID,
        "question": question,
        "options": options,
        "type": "quiz",
        "correct_option_id": int(correct_id),
        "is_anonymous": True,  # для каналов обязательно
        "allows_multiple_answers": False,
    }

    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram sendPoll error: {r.status_code} {r.text}")

    data = r.json()
    return int(data["result"]["message_id"])


def send_explanation(text: str, reply_to_message_id: int | None = None) -> None:
    """
    Отправляет короткое explanation отдельным сообщением (можно “ответом” на poll).
    """
    text = (text or "").strip()
    if not text:
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = int(reply_to_message_id)

    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram sendMessage error: {r.status_code} {r.text}")


# =========================
# PROMPTS
# =========================
def prompt_for(kind: str, level: str) -> str:
    # Мы заставляем модель:
    # - делать A2/B1
    # - давать explanation
    # - миксовать времена/структуры
    # - не делать одинаковые "He ___ to school every day" постоянно
    return f"""
Create ONE Telegram quiz for English learners. Return STRICT JSON ONLY (no markdown, no extra text):
{{
  "level": "{level}",
  "kind": "{kind}",
  "topic": "Grammar" or "Vocabulary" or "Riddle",
  "core": "MAIN content without extra labels",
  "question": "FINAL question text (can include line breaks)",
  "options": ["A", "B", "C"],
  "correct": 0,
  "explanation": "1–2 short lines explaining why the correct answer is correct"
}}

Brand style (keep it):
💬 DAILY ENGLISH
Level {level} · <topic>

Rules by kind:

1) kind=grammar_gap
- Make a sentence with exactly one ___ gap
- Focus on variety of tenses/structures (A2/B1):
  Present Simple/Continuous, Past Simple, Present Perfect (basic), Future (will/going to),
  modals (can/should/must), comparatives, prepositions, basic conditionals (if + will)
- Avoid cliché sentences like “He ___ to school every day.”
- question format EXACT:
  💬 DAILY ENGLISH
  Level {level} · Grammar

  Fill the gap:
  <sentence with ___>

- Options: 3 variants, only 1 correct, realistic distractors

2) kind=ua_en
- core: ONE Ukrainian word or short phrase (1–3 words max)
- options: 3 English translations (one correct), A2/B1 vocabulary (common everyday topics)
- question format EXACT:
  💬 DAILY ENGLISH
  Level {level} · Vocabulary

  🇺🇦 → 🇬🇧
  <ukrainian word/phrase>

3) kind=guess_word
- core: the correct English word (one word preferred)
- question: 2–3 short riddle lines (A2/B1) + "What is it?"
- options: 3 words, one correct (=core)
- question format EXACT:
  💬 DAILY ENGLISH
  Level {level} · Riddle

  <riddle lines>
  What is it?

Global rules:
- Exactly 3 options
- correct is 0/1/2
- Keep question short, clean, “premium”
- explanation must NOT repeat the whole rulebook; just the key reason
- Return strict JSON only
""".strip()


def generate_one(kind: str, level: str) -> dict:
    resp = client.responses.create(
        model="gpt-5-mini",
        input=prompt_for(kind, level),
    )
    raw = (resp.output_text or "").strip()
    return extract_json(raw)


# =========================
# MAIN LOGIC
# =========================
def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")

    history = load_history()
    seen = {h.get("fp") for h in history if h.get("fp")}

    kind = next_kind(history)
    level = random.choice(LEVEL_POOL)

    last_err = None

    for _ in range(MAX_GEN_TRIES):
        try:
            data = generate_one(kind, level)

            # validate
            question = str(data["question"]).strip()
            options = data["options"]
            correct = int(data["correct"])
            explanation = str(data.get("explanation", "")).strip()

            if not isinstance(options, list) or len(options) != 3:
                raise ValueError("options must be list of 3")
            options = [str(x).strip() for x in options]
            if correct not in (0, 1, 2):
                raise ValueError("correct must be 0/1/2")

            core = str(data.get("core", question)).strip()
            topic = str(data.get("topic", "")).strip()

            fp = _fp(kind, core, options)
            if fp in seen:
                # повтор — генерим заново
                continue

            # 1) отправляем quiz
            poll_msg_id = send_poll(question, options, correct)

            # 2) отправляем explanation (reply к poll)
            if explanation:
                # компактный “премиум” вид
                # можно легко поменять позже
                exp_text = f"✅ Answer: {options[correct]}\n📝 {explanation}"
                send_explanation(exp_text, reply_to_message_id=poll_msg_id)

            # 3) пишем в историю
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

    raise RuntimeError(
        f"Failed to generate unique quiz for kind={kind}, level={level}. Last error: {last_err}"
    )


if __name__ == "__main__":
    main()
