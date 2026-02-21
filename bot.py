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
CHAT_ID = os.getenv("CHAT_ID", "-1003674761753")
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

# ========= HISTORY =========
HISTORY_FILE = "history.json"
MAX_HISTORY = 800
MAX_GEN_TRIES = 12

# ✅ 3 формата
KIND_CYCLE = ["grammar_gap", "ua_en", "guess_word"]

# ========= ANTI-REPEAT TUNING =========
# Сколько последних постов данного вида считаем "окном запрета" для повторов core
COOLDOWN_LAST_N = {
    "ua_en": 200,        # ~16 дней при 12 постов/день и 1/3 ua_en
    "grammar_gap": 120,  # ~10 дней (по циклу)
}
# Для grammar_gap дополнительно запретим повторять "шаблон" в окне
GRAMMAR_PATTERN_LAST_N = 180

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
    - grammar_gap: keep basic normalization (pattern is handled separately).
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
    # fp по core+options — полезен, но НЕ достаточен
    payload = {
        "kind": _norm(kind),
        "core": _norm(core),
        "options": [_norm(x) for x in (options or [])],
    }
    j = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()


def core_fp(kind: str, core: str) -> str:
    """
    Железный дедуп: только kind + normalized(core).
    Срабатывает даже если options/explanation другие.
    """
    payload = {
        "kind": _norm(kind),
        "core": normalize_core(kind, core),
    }
    j = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()


def grammar_pattern_key(core: str) -> str:
    """
    Анти-повтор для грамматических шаблонов.
    Пример: "I ___ here since 2010." и "She ___ in London since 2019."
    -> схожий паттерн " <blank> since <num> "
    """
    s = unicodedata.normalize("NFKC", (core or "")).lower()
    s = s.replace("___", " <blank> ")
    # цифры и простые числительные → <num>
    s = re.sub(r"\d+", "<num>", s)
    s = re.sub(r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\b", "<num>", s)
    # оставим только латиницу и спец-токены
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
        "explanation": explanation,
    }

    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram sendPoll error: {r.status_code} {r.text}")


# ========= PROMPTS =========
def prompt_for(kind: str) -> str:
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

    # ---------- sets from history ----------
    seen_fp = {h.get("fp") for h in history if h.get("fp")}

    # core-level dedupe (железный)
    seen_core = set()
    for h in history:
        k = h.get("kind")
        c = h.get("core")
        if k in KIND_CYCLE and c:
            # поддержка старых записей без core_fp
            seen_core.add(h.get("core_fp") or core_fp(k, c))

    # guess_word: глобальный бан по слову (навсегда)
    guess_word_banned = set()
    for h in history:
        if h.get("kind") == "guess_word":
            guess_word_banned.add(normalize_core("guess_word", h.get("core", "")))

    # cooldown по последним N (ua_en / grammar_gap)
    recent_core_by_kind = {k: set() for k in COOLDOWN_LAST_N.keys()}
    counts_by_kind = {k: 0 for k in COOLDOWN_LAST_N.keys()}
    # идем с конца истории, набираем окно
    for h in reversed(history):
        k = h.get("kind")
        if k in COOLDOWN_LAST_N and counts_by_kind[k] < COOLDOWN_LAST_N[k]:
            c = normalize_core(k, h.get("core", ""))
            if c:
                recent_core_by_kind[k].add(c)
            counts_by_kind[k] += 1

    # grammar patterns cooldown
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

    # ---------- generation ----------
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
                explanation = "Перевір підмет і час у реченні."

            core = str(data.get("core", "")).strip() or question

            # ---------- hard anti-repeat: core_fp ----------
            cfp = core_fp(kind, core)
            if cfp in seen_core:
                continue

            # ---------- guess_word global ban ----------
            if kind == "guess_word":
                norm_word = normalize_core("guess_word", core)
                if not norm_word:
                    continue
                if norm_word in guess_word_banned:
                    continue

            # ---------- ua_en cooldown + format B correct always 0 ----------
            if kind == "ua_en":
                norm_ua = normalize_core("ua_en", core)
                if not norm_ua:
                    continue
                # cooldown window
                if norm_ua in recent_core_by_kind["ua_en"]:
                    continue
                correct = 0

            # ---------- grammar_gap cooldown + pattern cooldown ----------
            if kind == "grammar_gap":
                norm_g = normalize_core("grammar_gap", core)
                if norm_g and norm_g in recent_core_by_kind["grammar_gap"]:
                    continue
                pk = grammar_pattern_key(core)
                if pk and pk in recent_grammar_patterns:
                    continue

            # ---------- fp dedupe as extra ----------
            fp = _fp(kind, core, options)
            if fp in seen_fp:
                continue

            # ✅ send poll
            send_quiz_poll(question, options, correct, explanation)

            # ✅ update in-memory sets immediately
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

            # ✅ write history
            history.append(
                {
                    "ts": int(time.time()),
                    "kind": kind,
                    "level": level,
                    "topic": topic,
                    "core": core,
                    "fp": fp,
                    "core_fp": cfp,  # IMPORTANT: for iron dedupe
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
