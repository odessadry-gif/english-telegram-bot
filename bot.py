import os
import requests
import json
from openai import OpenAI

# 🔹 Токены из GitHub Secrets
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 🔹 ID твоего канала
CHAT_ID = -1003674761753

# 🔹 Инициализация OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)


def send_quiz(question: str, options: list[str], correct_id: int):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPoll"

    payload = {
        "chat_id": CHAT_ID,
        "question": question,
        "options": options,
        "type": "quiz",
        "correct_option_id": correct_id,
        "is_anonymous": True
    }

    r = requests.post(url, json=payload, timeout=20)

    if not r.ok:
        raise RuntimeError(f"Telegram error: {r.status_code} {r.text}")


def generate_quiz():
    prompt = """
    Create one A1/A2 English quiz.
    Return strictly JSON in this format:

    {
      "level": "A1 or A2",
      "category": "Grammar or Vocabulary",
      "question": "sentence with ___ OR Ukrainian word",
      "options": ["...", "...", "..."],
      "correct": 0
    }

    If it's Grammar, use format:
    Fill the gap:
    She ___ coffee in the morning.

    If it's Vocabulary, use a Ukrainian word only.
    """

    response = client.responses.create(
        model="gpt-5-mini",
        input=prompt
    )

    text = response.output_text.strip()
    data = json.loads(text)

    if data["category"] == "Grammar":
        task_text = f"Fill the gap:\n{data['question']}"
    else:
        task_text = f"Translate:\n{data['question']}"

    formatted_question = f"""💬 DAILY ENGLISH
Level {data['level']} · {data['category']}

{task_text}"""

    return formatted_question, data["options"], data["correct"]


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    question, options, correct_id = generate_quiz()
    send_quiz(question, options, correct_id)


if __name__ == "__main__":
    main()
