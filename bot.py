import os
import json
import random
import requests
from openai import OpenAI

TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# твой канал (ID начинается с -100...)
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "-1003674761753"))

HISTORY_FILE = "history.json"
MAX_HISTORY = 500  # чтобы файл не рос бесконечно
MAX_RETRIES = 6    # сколько раз пробовать сгенерировать уникальный вопрос

client = OpenAI(api_key=OPENAI_API_KEY)


def load_history() -> list[str]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            # храним только строки
            return [str(x) for x in data]
        return []
    except Exception:
        return []


def save_history(history: list[str]) -> None:
    history = history[-MAX_HISTORY:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def send_quiz(question: str, options: list[str], correct_id: int):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPoll"
    payload = {
        "chat_id": CHAT_ID,
        "question": question,
        "options": options,
        "type": "quiz",
        "correct_option_id": int(correct_id),
        "is_anonymous": True,
    }
    r = requests.post(url, json=payload, timeout=20)
    if not r.ok:
        raise RuntimeError(f"Telegram error: {r.status_code} {r.text}")


def build_prompt(history: list[str]) -> str:
    # чтобы модель не повторяла — даём ей последние вопросы
    recent = history[-40:]  # достаточно
    recent_block = "\n".join([f"- {q}" for q in recent]) if recent else "- (none)"

    return f"""
You create A1/A2 English quiz questions for Telegram.

IMPORTANT:
- Must NOT repeat any of these recent questions:
{recent_block}

Return STRICT JSON ONLY (no markdown, no comments):
{{
  "title": "💬 DAILY ENGLISH",
  "level": "A1",
  "topic": "Grammar",
  "gap_sentence": "She ___ coffee in the morning.",
  "options": ["drink", "drinks", "drinking"],
  "correct": 1
}}

Rules:
- 3 options only
- correct is 0/1/2
- gap_sentence must contain exactly one "___"
- Keep it short and clear.
""".strip()


def generate_unique_quiz(history: list[str]):
    history_set = set([h.strip().lower() for h in history])

    for _ in range(MAX_RETRIES):
        prompt = build_prompt(history)

        resp = client.responses.create(
            model="gpt-5-mini",
            input=prompt,
        )

        text = resp.output_text.strip()

        try:
            data = json.loads(text)
        except Exception:
            continue

        title = str(data.get("title", "💬 DAILY ENGLISH")).strip()
        level = str(data.get("level", "A1")).strip()
        topic = str(data.get("topic", "Grammar")).strip()
        gap_sentence = str(data.get("gap_sentence", "")).strip()
        options = data.get("options", [])
        correct = data.get("correct", 0)

        if not gap_sentence or "___" not in gap_sentence:
            continue
        if not isinstance(options, list) or len(options) != 3:
            continue
        options = [str(x).strip() for x in options]
        if any(not x for x in options):
            continue
        if correct not in [0, 1, 2]:
            continue

        # ключ уникальности: само предложение с пропуском
        uniq_key = gap_sentence.strip().lower()

        if uniq_key in history_set:
            continue

        # Форматируем вопрос (красиво/премиум как ты хотел)
        question_text = f"{title}\nLevel {level} · {topic}\n\nFill the gap:\n{gap_sentence}"

        return question_text, options, int(correct), uniq_key

    # запасной вариант если ИИ тупит: простая банка вопросов
    fallback = [
        ("💬 DAILY ENGLISH\nLevel A1 · Grammar\n\nFill the gap:\nHe ___ to school every day.", ["go", "goes", "going"], 1),
        ("💬 DAILY ENGLISH\nLevel A1 · Grammar\n\nFill the gap:\nThey ___ soccer on Sundays.", ["play", "plays", "playing"], 0),
        ("💬 DAILY ENGLISH\nLevel A1 · Grammar\n\nFill the gap:\nI ___ coffee in the morning.", ["drink", "drinks", "drinking"], 0),
    ]
    q, opts, c = random.choice(fallback)
    return q, opts, c, q.split("\n")[-1].strip().lower()


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing (set it in GitHub Secrets).")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing (set it in GitHub Secrets).")

    history = load_history()

    question, options, correct_id, uniq_key = generate_unique_quiz(history)

    send_quiz(question, options, correct_id)

    history.append(uniq_key)
    save_history(history)


if __name__ == "__main__":
    main()
