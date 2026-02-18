import os
import random
import requests

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = -1003674761753  # твой канал

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

def pick_task():
    # 1) Finish the sentence (A1/A2)
    fill = [
        ("Finish the sentence:\nI ___ to school every day.", ["go", "goes", "going"], 0),
        ("Finish the sentence:\nShe ___ coffee in the morning.", ["drink", "drinks", "drinking"], 1),
        ("Finish the sentence:\nThey ___ soccer on Sundays.", ["play", "plays", "playing"], 0),
        ("Finish the sentence:\nHe ___ TV in the evening.", ["watch", "watches", "watching"], 1),
    ]

    # 2) 🇺🇦 → 🇬🇧 (A1/A2)
    ua_en = [
        ("🇺🇦 → 🇬🇧\nШвидкий", ["fast", "slow", "late"], 0),
        ("🇺🇦 → 🇬🇧\nВеликий", ["small", "big", "thin"], 1),
        ("🇺🇦 → 🇬🇧\nГарячий", ["hot", "hat", "hit"], 0),
        ("🇺🇦 → 🇬🇧\nХолодний", ["cold", "close", "cloud"], 0),
    ]

    pool = fill if random.random() < 0.5 else ua_en
    return random.choice(pool)

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing (set it in GitHub Secrets).")

    question, options, correct_id = pick_task()
    send_quiz(question, options, correct_id)

if __name__ == "__main__":
    main()
