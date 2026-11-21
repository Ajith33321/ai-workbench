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
