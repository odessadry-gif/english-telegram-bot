import os
import json
import time
import re
import requests
from openai import OpenAI

# --- ENV ---
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Можно оставить твой канал как ID (самое надежное) или username "@..."
# Если хочешь — потом вынесем в GitHub Secret/Variable.
CHAT_ID = os.getenv("CHAT_ID", "-1003674761753")

HISTORY_FILE = "history.json"
MAX_HISTORY = 500          # сколько последних вопросов держим
MAX_GEN_TRIES = 8          # сколько раз перегенерировать, если повтор


# --- helpers ---
def load_history() -> list[str]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data]
        return []
    except Exception:
        return []


def save_history(items: list[str]) -> None:
    items = items[-MAX_HISTORY:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def normalize_question(q: str) -> str:
    q = q.strip()
    q = re.sub(r"\s+", " ", q)
    return q.lower()


def extract_json(text: str) -> dict:
    """
    Модели иногда оборачивают JSON в текст/markdown.
    Вытащим первый { ... } блок.
    """
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("No JSON object found in model output")
    return json.loads(m.group(0))


# --- Telegram ---
def send_quiz(question: str, options: list[str], correct_id: int) -> None:
    url = f"https://api.telegram.org/bot{TOKEN}/sendPoll"

    payload = {
        "chat_id": CHAT_ID,
        "question": question,
        "options": options,
        "type": "quiz",
        "correct_option_id": int(correct_id),
        # ВАЖНО: для каналов должны быть анонимные квизы
        "is_anonymous": True,
    }

    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram error: {r.status_code} {r.text}")


# --- OpenAI ---
def generate_quiz(client: OpenAI) -> tuple[str, list[str], int, str]:
    """
    Возвращает: (question, options, correct_id, meta_line)
    meta_line — строка для красоты в question.
    """
    prompt = """
Create ONE short A1/A2 English quiz (grammar or basic vocabulary).
Return STRICT JSON ONLY (no markdown, no extra text):
{
  "level": "A1" or "A2",
  "topic": "Grammar" or "Vocabulary",
  "question": "Fill the gap: She ___ coffee in the morning.",
  "options": ["drink", "drinks", "drinking"],
  "correct": 1
}

Rules:
- Exactly 3 options
- correct is 0/1/2
- question must be short and clear
- Avoid repeating the same sentence patterns too often
"""

    resp = client.responses.create(
        model="gpt-5-mini",
        input=prompt,
    )

    raw = (resp.output_text or "").strip()
    data = extract_json(raw)

    level = str(data.get("level", "A1")).strip().upper()
    topic = str(data.get("topic", "Grammar")).strip().title()

    q = str(data["question"]).strip()
    options = [str(x).strip() for x in data["options"]]
    correct = int(data["correct"])

    # Мини-валидация
    if len(options) != 3:
        raise ValueError("options must contain exactly 3 items")
    if correct not in (0, 1, 2):
        raise ValueError("correct must be 0/1/2")
    if not q:
        raise ValueError("question is empty")

    # Премиум-строчка (как ты хотел)
    meta = f"💬 DAILY ENGLISH\nLevel {level} · {topic}"
    return q, options, correct, meta


def main() -> None:
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing (GitHub Secrets -> BOT_TOKEN).")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing (GitHub Secrets -> OPENAI_API_KEY).")

    client = OpenAI(api_key=OPENAI_API_KEY)

    history = load_history()
    history_norm = set(normalize_question(x) for x in history)

    last_error = None

    for attempt in range(1, MAX_GEN_TRIES + 1):
        try:
            q, options, correct, meta = generate_quiz(client)

            # Чтобы вопрос был “брендовый”, но не дублировал "Fill the gap" два раза
            # meta в отдельной строке сверху, сам q уже содержит "Fill the gap: ..."
            final_question = f"{meta}\n\n{q}"

            key = normalize_question(final_question)
            if key in history_norm:
                # повтор — пробуем ещё раз
                continue

            # отправка
            send_quiz(final_question, options, correct)

            # сохраняем в историю
            history.append(final_question)
            save_history(history)

            return

        except Exception as e:
            last_error = e
            # небольшой backoff на случай временных проблем
            time.sleep(min(2 * attempt, 8))

    raise RuntimeError(f"Failed to generate unique quiz after {MAX_GEN_TRIES} tries. Last error: {last_error}")


if __name__ == "__main__":
    main()
