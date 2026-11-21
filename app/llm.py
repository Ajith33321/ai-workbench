"""LLM helpers: OpenAI when OPENAI_API_KEY is set, otherwise deterministic demo mode.

Demo mode is intentional. The full product path (upload/paste → store → summarise
or score → write back to SQLite) works without a paid API key, so a recruiter
can click through the app. With a key, the same functions call Whisper and
a chat model and persist those results instead.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import (
    DEMO_MODE,
    OPENAI_API_KEY,
    OPENAI_CHAT_MODEL,
    OPENAI_WHISPER_MODEL,
)

MEETING_SYSTEM_PROMPT = """You are a careful meeting-notes assistant.
Read the transcript and return ONLY valid JSON with this shape:
{
  "summary": "3 to 6 sentences covering what was discussed and why it matters",
  "checkpoints": [{"item": "short status or milestone", "status": "decided|open|blocked"}],
  "decisions": ["a decision the group actually made"],
  "todos": [{"task": "clear action", "owner": "name or unspecified", "due": "date or unspecified"}]
}
Rules:
- Use only facts present in the transcript. Do not invent attendees, numbers or deadlines.
- If an owner or due date is not stated, use "unspecified".
- Prefer short, concrete wording a busy manager can scan.
- If the transcript is empty or unusable, say so in the summary and return empty lists.
"""

REVIEW_SYSTEM_PROMPT = """You are scoring a restaurant guest review so the owner can see branch-level sentiment.
Return ONLY valid JSON with this shape:
{
  "sentiment_score": 3.0,
  "sentiment_label": "positive|neutral|negative",
  "themes": {"food": "pos|neu|neg|na", "service": "pos|neu|neg|na", "wait": "pos|neu|neg|na"},
  "themes_note": "one short sentence on why you tagged it that way",
  "coupon": {"kind": "coupon|cashback|none", "value_text": "10% off next visit"}
}
Rules:
- sentiment_score is a number from 1.0 to 5.0.
- Tag a theme "na" if the review does not mention it.
- Reward genuine reviews, including critical ones. Do not reward empty, spam or one-word reviews (kind=none).
- Typical reward: "10% off next visit". Use a small cashback (for example "₹50 cashback") only for a detailed, constructive review.
- Never increase a score because the guest asked for a discount.
"""

# Keyword lists for deterministic demo sentiment (no API key).
_POS = {
    "good", "great", "love", "loved", "amazing", "delicious", "tasty", "excellent",
    "friendly", "fast", "quick", "fresh", "perfect", "wonderful", "kind", "warm",
    "crispy", "juicy", "recommended", "best", "nice", "polite", "clean",
}
_NEG = {
    "bad", "poor", "slow", "cold", "rude", "worst", "stale", "dirty", "late",
    "waited", "overcooked", "undercooked", "bland", "salty", "expensive", "delay",
    "delayed", "long", "terrible", "awful", "soggy", "ignored", "overpriced",
}
_FOOD = {
    "food", "dish", "biryani", "curry", "dosa", "idli", "naan", "rice", "taste",
    "tasty", "delicious", "spicy", "bland", "stale", "fresh", "dessert", "menu",
    "paneer", "chicken", "mutton", "thali", "sambar",
}
_SERVICE = {
    "service", "staff", "waiter", "waitress", "server", "friendly", "rude",
    "polite", "kind", "ignored", "manager", "hospitality",
}
_WAIT = {
    "wait", "waited", "waiting", "slow", "quick", "fast", "delay", "delayed",
    "long", "time", "late", "queue", "seated",
}


def is_demo_mode() -> bool:
    return DEMO_MODE


def transcribe_audio(path: Path) -> tuple[str, str]:
    """Return (transcript, source) where source is 'whisper' or 'demo'."""
    if DEMO_MODE or not path.exists():
        name = path.name if path else "audio"
        return (
            f"[Demo transcription — no OPENAI_API_KEY is set.]\n"
            f"This stands in for a Whisper transcript of the uploaded file '{name}'. "
            "In live mode the OpenAI Whisper API reads the same audio. "
            "Paste a real transcript in the text box if you want the summariser to work on your own words.\n\n"
            "Host: Thanks for joining. We will review last week's leads and lock next steps.\n"
            "Guest: Agreed. I will send the revised deck by Thursday.\n"
            "Host: Decision: we pause the generic ad and shift budget to the better-performing creative. "
            "I will draft the follow-up script; please review it before it goes live.",
            "demo",
        )
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    with path.open("rb") as fh:
        result = client.audio.transcriptions.create(model=OPENAI_WHISPER_MODEL, file=fh)
    return (result.text or "").strip(), "whisper"


def summarise_meeting(transcript: str) -> tuple[dict, str]:
    """Return (payload, source) with summary / checkpoints / decisions / todos."""
    text = (transcript or "").strip()
    if DEMO_MODE:
        return _demo_summarise(text), "demo"
    return _llm_json(MEETING_SYSTEM_PROMPT, text, _demo_summarise(text)), "llm"


def score_review(review_text: str) -> tuple[dict, str]:
    text = (review_text or "").strip()
    if DEMO_MODE:
        return _demo_score(text), "demo"
    fallback = _demo_score(text)
    return _llm_json(REVIEW_SYSTEM_PROMPT, text, fallback), "llm"


def _llm_json(system: str, user: str, fallback: dict) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        resp = client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user or "(empty)"},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        if not isinstance(data, dict):
            return fallback
        return data
    except Exception:
        return fallback


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _demo_summarise(transcript: str) -> dict:
    if not transcript.strip():
        return {
            "summary": "No transcript was provided, so no notes could be generated.",
            "checkpoints": [],
            "decisions": [],
            "todos": [],
        }
    sents = _sentences(transcript)
    summary = " ".join(sents[:4]) if sents else transcript[:400]
    checkpoints = []
    decisions = []
    todos = []
    for s in sents:
        low = s.lower()
        if any(w in low for w in ("decid", "agreed", "we will", "we'll", "lock", "pause", "shift")):
            decisions.append(s if len(s) < 180 else s[:177] + "...")
            checkpoints.append({"item": s if len(s) < 140 else s[:137] + "...", "status": "decided"})
        if any(w in low for w in ("i will", "i'll", "please", "by thursday", "by friday", "action", "send", "draft", "review")):
            owner = "unspecified"
            m = re.search(r"\b([A-Z][a-z]{2,15})\b(?:\s+will|\s+owns|:)", s)
            if "i will" in low or "i'll" in low:
                owner = "Speaker"
            elif m:
                owner = m.group(1)
            todos.append({"task": s if len(s) < 160 else s[:157] + "...", "owner": owner, "due": "unspecified"})
    if not checkpoints:
        checkpoints = [{"item": "Notes generated in demo mode from the transcript", "status": "open"}]
    if not decisions:
        decisions = ["Demo mode: no explicit decision phrase was found; see the summary."]
    if not todos:
        todos = [{"task": "Review these draft notes and add owners", "owner": "Host", "due": "unspecified"}]
    return {
        "summary": summary,
        "checkpoints": checkpoints[:6],
        "decisions": decisions[:6],
        "todos": todos[:8],
    }


def _theme(words: set[str], tokens: set[str], pos: int, neg: int) -> str:
    if not (words & tokens):
        return "na"
    if pos > neg:
        return "pos"
    if neg > pos:
        return "neg"
    return "neu"


def _demo_score(text: str) -> dict:
    raw = text.strip()
    tokens = set(re.findall(r"[a-z']+", raw.lower()))
    if len(raw) < 8 or len(tokens) < 2:
        return {
            "sentiment_score": 0.0,
            "sentiment_label": "neutral",
            "themes": {"food": "na", "service": "na", "wait": "na"},
            "themes_note": "Review too short to score; no reward issued.",
            "coupon": {"kind": "none", "value_text": ""},
        }
    pos = len(tokens & _POS)
    neg = len(tokens & _NEG)
    # Map a simple pos-neg margin onto 1–5.
    margin = pos - neg
    score = 3.0 + max(-2.0, min(2.0, margin * 0.7))
    score = round(max(1.0, min(5.0, score)), 1)
    if score >= 3.6:
        label = "positive"
    elif score <= 2.4:
        label = "negative"
    else:
        label = "neutral"
    themes = {
        "food": _theme(_FOOD, tokens, pos, neg) if tokens & _FOOD else "na",
        "service": _theme(_SERVICE, tokens, pos, neg) if tokens & _SERVICE else "na",
        "wait": _theme(_WAIT, tokens, pos, neg) if tokens & _WAIT else "na",
    }
    # Theme polarity uses local keywords where possible.
    def local(theme_words: set[str]) -> str:
        if not (tokens & theme_words):
            return "na"
        p = len(tokens & theme_words & _POS) + (1 if tokens & theme_words & (_POS | _NEG) == tokens & theme_words & _POS and tokens & _POS else 0)
        n = len(tokens & theme_words & _NEG)
        # If the sentence mentions the theme and overall tone is clear, inherit it.
        if n > p:
            return "neg"
        if p > n:
            return "pos"
        if margin > 0:
            return "pos"
        if margin < 0:
            return "neg"
        return "neu"

    themes = {"food": local(_FOOD), "service": local(_SERVICE), "wait": local(_WAIT)}
    note = f"Demo keyword score {score}/5 ({label}) from {pos} positive and {neg} negative words."
    if len(raw) < 20:
        coupon = {"kind": "none", "value_text": ""}
    elif score <= 2.4 and len(raw) >= 80:
        coupon = {"kind": "coupon", "value_text": "10% off next visit"}
    elif score >= 4.0 and len(raw) >= 80:
        coupon = {"kind": "cashback", "value_text": "₹50 cashback"}
    else:
        coupon = {"kind": "coupon", "value_text": "10% off next visit"}
    return {
        "sentiment_score": score,
        "sentiment_label": label,
        "themes": themes,
        "themes_note": note,
        "coupon": coupon,
    }


BRIEF_SYSTEM_PROMPT = """You are a performance-marketing creative lead writing UGC briefs for a 15-second Meta or Instagram Reel, for an Indian local business (restaurant, clinic or D2C brand).
You are given a list of guest reviews, each with an optional id, name and text. Return ONLY valid JSON:
{
  "briefs": [
    {
      "theme": "food|service|wait|value|ambience|trust",
      "angle": "proof|desire|objection",
      "language": "en|ta",
      "hooks": ["0-3 second hook one", "hook two", "hook three"],
      "script": "a spoken 15-second script a creator can read",
      "shot_list": ["shot one", "shot two", "shot three"],
      "on_screen_text": "short text that works with the sound off",
      "cta": "a specific next step (book, WhatsApp, visit, reserve)",
      "quotes": ["a verbatim line from a review"],
      "source_review_ids": [1],
      "caution": "optional warning, or an empty string"
    }
  ]
}
Rules:
- Use only facts and wording present in the reviews. Do not invent a result, offer, dish, wait time, or cure.
- One idea per brief. Prefer clearly positive lines for proof or desire. Use mixed or critical reviews as objection briefs.
- If guests complain about something that is not shown as fixed, write an objection brief with a caution. Never turn a complaint into a fake success story (for example, do not say "no wait" if people waited).
- Attach the source_review_ids you actually used. If a review has no id, omit it from that list, but still quote the line.
- If the requested language is "ta", write hooks, script, on-screen text and CTA in Tamil. Keep quotes in the guest's original language.
- If the requested language is "both", return separate English and Tamil briefs (not one mixed paragraph).
- Do not offer a discount or coupon in exchange for a review. Do not ask only happy guests to review.
- Three hooks per brief. Keep the CTA specific.
"""

_THEME_WORDS = {
    "food": {
        "food", "dish", "biryani", "curry", "dosa", "idli", "naan", "rice", "taste",
        "tasty", "delicious", "spicy", "bland", "stale", "fresh", "dessert", "menu",
        "paneer", "chicken", "mutton", "thali", "sambar", "coffee", "filter", "raita",
        "crispy", "juicy", "fragrant", "overcooked", "undercooked",
    },
    "service": {
        "service", "staff", "waiter", "waitress", "server", "friendly", "rude",
        "polite", "kind", "ignored", "manager", "hospitality", "apologetic", "warm",
    },
    "wait": {
        "wait", "waited", "waiting", "slow", "quick", "fast", "delay", "delayed",
        "long", "late", "queue", "seated", "minutes",
    },
    "value": {
        "price", "priced", "expensive", "cheap", "worth", "value", "costly",
        "overpriced", "affordable", "combo",
    },
    "ambience": {
        "ambience", "atmosphere", "music", "rooftop", "clean", "dirty", "vibe",
        "seating", "decor", "noisy", "quiet",
    },
}


def parse_pasted_reviews(text: str) -> list[dict]:
    """Split a pasted block into individual reviews (blank lines, or dash/number bullets)."""
    raw = (text or "").strip()
    if not raw:
        return []
    chunks = re.split(r"\n\s*\n", raw)
    if len(chunks) == 1:
        lined = re.split(r"\n(?=\s*(?:[-*•]|\d+[.)])\s+)", raw)
        if len(lined) > 1:
            chunks = lined
    out: list[dict] = []
    for chunk in chunks:
        body = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", chunk.strip())
        body = re.sub(r"^(?:reviewer|guest|name)\s*:\s*[^\n]+\n", "", body, flags=re.I).strip()
        if len(body) >= 8:
            out.append({"id": None, "name": "Pasted guest", "text": body, "sentiment_label": ""})
    return out


def reviews_to_prompt_block(reviews: list[dict]) -> str:
    lines = []
    for r in reviews:
        rid = r.get("id")
        name = r.get("name") or "Guest"
        prefix = f"[{rid}] {name}: " if rid is not None else f"{name}: "
        lines.append(prefix + (r.get("text") or "").strip())
    return "\n\n".join(lines)


def generate_ugc_briefs(reviews: list[dict], language: str = "both") -> tuple[list[dict], str]:
    """Return (briefs, source) from a list of {id, name, text, sentiment_label?}."""
    language = (language or "both").lower()
    if language not in {"en", "ta", "both"}:
        language = "both"
    clean = [r for r in reviews if (r.get("text") or "").strip()]
    if not clean:
        return [], "demo" if DEMO_MODE else "llm"
    if DEMO_MODE:
        return _demo_briefs(clean, language), "demo"
    user = (
        f"Requested language: {language}\n\n"
        f"Reviews:\n{reviews_to_prompt_block(clean)}"
    )
    fallback = _demo_briefs(clean, language)
    data = _llm_json(BRIEF_SYSTEM_PROMPT, user, {"briefs": fallback})
    briefs = data.get("briefs") if isinstance(data, dict) else None
    if not isinstance(briefs, list) or not briefs:
        return fallback, "demo"
    normalised = []
    for item in briefs:
        if isinstance(item, dict) and (item.get("hooks") or item.get("script")):
            normalised.append(_normalise_brief(item))
    return (normalised or fallback), "llm"


def _normalise_brief(item: dict) -> dict:
    hooks = item.get("hooks") or []
    if isinstance(hooks, str):
        hooks = [hooks]
    shots = item.get("shot_list") or item.get("shots") or []
    if isinstance(shots, str):
        shots = [shots]
    quotes = item.get("quotes") or []
    if isinstance(quotes, str):
        quotes = [quotes]
    ids = item.get("source_review_ids") or []
    clean_ids = []
    for i in ids:
        try:
            clean_ids.append(int(i))
        except (TypeError, ValueError):
            continue
    return {
        "theme": str(item.get("theme") or "general"),
        "angle": str(item.get("angle") or "proof"),
        "language": "ta" if item.get("language") == "ta" else "en",
        "hooks": [str(h) for h in hooks][:4],
        "script": str(item.get("script") or ""),
        "shot_list": [str(s) for s in shots][:8],
        "on_screen_text": str(item.get("on_screen_text") or item.get("onscreen") or ""),
        "cta": str(item.get("cta") or ""),
        "quotes": [str(q) for q in quotes][:6],
        "source_review_ids": clean_ids,
        "caution": str(item.get("caution") or ""),
    }


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", (text or "").lower()))


def _cluster_reviews(reviews: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {k: [] for k in _THEME_WORDS}
    unused = []
    for r in reviews:
        toks = _tokens(r.get("text") or "")
        hit = False
        for theme, words in _THEME_WORDS.items():
            if toks & words:
                buckets[theme].append(r)
                hit = True
        if not hit:
            unused.append(r)
    if unused:
        buckets["trust"] = unused
    return {k: v for k, v in buckets.items() if v}


def _is_negative(review: dict) -> bool:
    label = (review.get("sentiment_label") or "").lower()
    if label == "negative":
        return True
    if label == "positive":
        return False
    toks = _tokens(review.get("text") or "")
    return len(toks & _NEG) > len(toks & _POS)


def _quotes_for(reviews: list[dict], limit: int = 3) -> list[str]:
    quotes = []
    for r in reviews:
        sents = _sentences(r.get("text") or "")
        pick = sents[0] if sents else (r.get("text") or "")
        pick = pick.strip()
        if pick and pick not in quotes:
            quotes.append(pick if len(pick) < 180 else pick[:177] + "...")
        if len(quotes) >= limit:
            break
    return quotes


def _ids_for(reviews: list[dict]) -> list[int]:
    out = []
    for r in reviews:
        if r.get("id") is not None:
            try:
                out.append(int(r["id"]))
            except (TypeError, ValueError):
                pass
    return out


# Short Tamil templates written for this project (not copied from another repo).
_TA = {
    "food_proof": {
        "hooks": [
            "பிரியாணி வாசனையிலேயே உள்ளே நடந்து வந்துட்டேன்.",
            "இது மெனுவின் வாக்கியம் இல்ல. விருந்தினர் சொன்ன வார்த்தை.",
            "கிரிஸ்ப் தோசை, வெதுவெதுப்பான ஃபில்டர் காபி — அதே வரியை ரீல்-ல சொல்லுங்க.",
        ],
        "script": "விருந்தினர் சொன்னாங்க: பிரியாணி வாசனையா இருந்துச்சு, தோசை கிரிஸ்ப், காபி நல்லா இருந்துச்சு. நான் இங்கே ருசியை காட்டுறேன். நீங்களும் வாங்க, டேபிள் முன்பதிவு பண்ணுங்க.",
        "on_screen_text": "விருந்தினர் வார்த்தையிலேயே.",
        "cta": "இன்று மாலை டேபிள் முன்பதிவு — லிங்க் பயோ-ல.",
        "shots": [
            "தியாகும் பிரியாணி பானையின் க்ளோஸ்-அப்",
            "தவையில் தோசை சுடும் காட்சி",
            "முதல் கடியில் உருவாக்கரின் எதிர்வினை",
        ],
    },
    "service_proof": {
        "hooks": [
            "பணியாளர்கள் அன்பா இருந்தாங்க — அது விருந்தினர் குரல்.",
            "மேலாளர் மேசைக்கு வந்தார். அதை மறைக்காமல் காட்டுங்க.",
            "சேவை தான் இந்த ஊருக்கு நம்பிக்கை.",
        ],
        "script": "விருந்தினர்கள் சொல்கிறார்கள்: பணியாளர்கள் அன்பாக இருந்தார்கள், மேலாளர் மேசைக்கு வந்தார். ஒரு உண்மையான வரவேற்பை 15 வினாடியில் காட்டுங்க. பிறகு WhatsApp-ல டேபிள் கேளுங்க.",
        "on_screen_text": "அன்பான சேவை. நேரடி விருந்தினர் வரி.",
        "cta": "WhatsApp-ல் இன்றைய டேபிள் கேளுங்கள்.",
        "shots": [
            "கதவு அருகே வரவேற்பு",
            "மேலாளர் மேசைக்கு வரும் காட்சி",
            "மேசை மீது தண்ணீர் / மெனு வைக்கும் கை",
        ],
    },
    "wait_objection": {
        "hooks": [
            "கிட்டத்தட்ட நாற்பது நிமிடம் காத்திருந்தோம் என்று ஒரு விருந்தினர் எழுதியிருக்கார்.",
            "இதை மறைக்காதீங்க. 'காத்திருப்பு இல்ல'னு பொய்யும் சொல்லாதீங்க.",
            "வீக்டே லஞ்ச் அல்லது முன்பதிவு — அதுதான் நேர்மையான CTA.",
        ],
        "script": "சில விருந்தினர்கள் இரவு நேரத்தில் காத்திருப்பு அதிகம் என்று சொல்லியிருக்கிறார்கள். அதை மறுக்க வேண்டாம். வீக்டே லஞ்சை காட்டுங்கள், அல்லது முன்பதிவு செய்யச் சொல்லுங்கள்.",
        "on_screen_text": "முன்பதிவு செய்து வாருங்கள்.",
        "cta": "வீக்டே லஞ்ச் அல்லது முன்பதிவு — லிங்க் பயோ-ல.",
        "shots": [
            "பகல் நேர டேபிள், காலி இருக்கை இல்லாமல் நேர்மையாக",
            "முன்பதிவு செய்யும் ஃபோன் / WhatsApp ஸ்க்ரீன்",
            "உணவு வந்ததும் ஸ்டீம் க்ளோஸ்-அப்",
        ],
    },
}


def _en_templates(theme: str, angle: str, quotes: list[str]) -> dict:
    q1 = quotes[0] if quotes else "A guest left us this line."
    if theme == "food" and angle != "objection":
        return {
            "hooks": [
                f"A guest wrote: “{q1[:90]}”",
                "This is not a slogan. It is a line from a real review.",
                "Show the first bite before you show the logo.",
            ],
            "script": (
                f"Open on the dish. Then say, in the guest's words: “{q1[:140]}” "
                "Keep it to one idea — the taste — and end with a clear way to book a table."
            ),
            "on_screen_text": "In a guest's words.",
            "cta": "Reserve a table today — link in bio or WhatsApp us.",
            "shots": [
                "Hero-dish close-up with steam or a crisp edge",
                "Creator's first bite, handheld, no heavy music",
                "End card with the booking or WhatsApp link",
            ],
        }
    if theme == "service" and angle != "objection":
        return {
            "hooks": [
                f"“{q1[:90]}”",
                "People remember how they were treated. Film that, not a stock smile.",
                "If a manager came to the table, show that moment.",
            ],
            "script": (
                f"A guest said: “{q1[:140]}” Walk in with the creator, show the greeting, "
                "and close with how to book."
            ),
            "on_screen_text": "Real service. Real words.",
            "cta": "WhatsApp us for tonight's table.",
            "shots": [
                "Doorway welcome or water being poured",
                "Staff at the table, not posed",
                "WhatsApp chat or reserve button as the last frame",
            ],
        }
    if theme == "wait":
        return {
            "hooks": [
                f"A guest wrote: “{q1[:90]}”",
                "Don't hide the wait. Don't claim there isn't one.",
                "A honest CTA is 'reserve ahead' or 'weekday lunch' — not 'no queue'.",
            ],
            "script": (
                f"Guests have mentioned the wait. One of them said: “{q1[:140]}” "
                "Do not say the wait is gone unless a later review says so. "
                "Film a quieter sitting, or ask people to reserve."
            ),
            "on_screen_text": "Reserve ahead. Skip the guesswork.",
            "cta": "Book a slot on WhatsApp before you start out.",
            "shots": [
                "A weekday or off-peak table, labelled honestly if you can",
                "Phone booking / WhatsApp reservation",
                "Food arriving — no fake empty restaurant claim",
            ],
        }
    if angle == "objection":
        return {
            "hooks": [
                f"A guest was honest: “{q1[:90]}”",
                "This is an objection. Answer it. Don't paper over it.",
                "If you cannot show a fix, don't invent one on camera.",
            ],
            "script": (
                f"A review said: “{q1[:140]}” Use this Reel to address that worry with something "
                "you can actually film, or to set a fair expectation."
            ),
            "on_screen_text": "An honest answer to a real review.",
            "cta": "Message us if you want to know what to expect today.",
            "shots": [
                "The specific thing the review was about, filmed clearly",
                "A staff or owner line that does not over-promise",
                "A simple next step on screen",
            ],
        }
    return {
        "hooks": [
            f"“{q1[:90]}”",
            "Start with the guest's words, not the brand name.",
            "One idea. Fifteen seconds. A clear next step.",
        ],
        "script": f"A guest wrote: “{q1[:140]}” Show that moment, then tell people how to visit or book.",
        "on_screen_text": "From a real review.",
        "cta": "Visit or WhatsApp us to book.",
        "shots": [
            "Open on the thing the guest mentioned",
            "A real person on camera",
            "End card with the next step",
        ],
    }


def _demo_briefs(reviews: list[dict], language: str) -> list[dict]:
    briefs: list[dict] = []
    langs = ["en", "ta"] if language == "both" else [language]
    clusters = _cluster_reviews(reviews)
    # Keep a stable, useful order.
    order = ["food", "service", "wait", "value", "ambience", "trust"]
    for theme in order:
        group = clusters.get(theme) or []
        if not group:
            continue
        neg = [r for r in group if _is_negative(r)]
        pos = [r for r in group if not _is_negative(r)]
        if theme == "wait" and neg:
            angle, used = "objection", neg
        elif pos:
            angle, used = "proof", pos
        else:
            angle, used = "objection", group
        quotes = _quotes_for(used)
        ids = _ids_for(used)
        caution = ""
        if angle == "objection" and theme == "wait":
            caution = "Do not claim there is no wait. Guests reported long waits. Film a quieter sitting or ask people to reserve."
        elif angle == "objection":
            caution = "This brief answers a complaint. Do not invent a fix the reviews do not support."
        en = _en_templates(theme, angle, quotes)
        for lang in langs:
            if lang == "ta":
                key = f"{theme}_{angle}" if f"{theme}_{angle}" in _TA else ("wait_objection" if angle == "objection" else "food_proof")
                ta = _TA.get(key) or _TA["food_proof"]
                item = {
                    "theme": theme,
                    "angle": angle,
                    "language": "ta",
                    "hooks": ta["hooks"],
                    "script": ta["script"],
                    "shot_list": ta["shots"],
                    "on_screen_text": ta["on_screen_text"],
                    "cta": ta["cta"],
                    "quotes": quotes,
                    "source_review_ids": ids,
                    "caution": caution,
                }
            else:
                item = {
                    "theme": theme,
                    "angle": angle,
                    "language": "en",
                    "hooks": en["hooks"],
                    "script": en["script"],
                    "shot_list": en["shots"],
                    "on_screen_text": en["on_screen_text"],
                    "cta": en["cta"],
                    "quotes": quotes,
                    "source_review_ids": ids,
                    "caution": caution,
                }
            briefs.append(item)
        if len(briefs) >= 8:
            break
    if not briefs:
        # Fall back to one English brief from the whole set.
        quotes = _quotes_for(reviews)
        en = _en_templates("trust", "proof", quotes)
        briefs.append(
            {
                "theme": "trust",
                "angle": "proof",
                "language": "en" if language != "ta" else "ta",
                "hooks": en["hooks"] if language != "ta" else _TA["food_proof"]["hooks"],
                "script": en["script"] if language != "ta" else _TA["food_proof"]["script"],
                "shot_list": en["shots"] if language != "ta" else _TA["food_proof"]["shots"],
                "on_screen_text": en["on_screen_text"] if language != "ta" else _TA["food_proof"]["on_screen_text"],
                "cta": en["cta"] if language != "ta" else _TA["food_proof"]["cta"],
                "quotes": quotes,
                "source_review_ids": _ids_for(reviews),
                "caution": "",
            }
        )
    return briefs
