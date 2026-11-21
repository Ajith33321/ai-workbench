"""Seed demo users, a processed meeting, and restaurant reviews when the DB is empty."""

import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.auth import hash_password
from app.models import (
    Booking,
    Branch,
    BriefPack,
    Coupon,
    Meeting,
    MeetingSlot,
    Restaurant,
    Review,
    UgcBrief,
    User,
)

DEMO_HOST_EMAIL = "host@demo.local"
DEMO_OWNER_EMAIL = "owner@demo.local"
DEMO_PASSWORD = "demo1234"

TRANSCRIPT = """Ajith: Thanks for joining. Today we review the healthcare lead-gen campaign and decide next week's creative tests.
Priya: CPL is at one hundred and eighty rupees, down from two hundred and forty last month. Lead quality is better on the clinic-visit creative.
Ajith: Good. Let's pause the generic UGC ad. We'll shift thirty percent of the budget to the clinic-visit set.
Ravi: I'll send the new landing page by Thursday. We also need a WhatsApp follow-up script.
Ajith: Agreed. Priya owns the budget shift today. Ravi owns the landing page by Thursday. I'll draft the WhatsApp script with an LLM and Priya will review it before it goes live.
Priya: I'll also share the weekly Looker Studio snapshot on Friday.
"""


def _token(n: int = 12) -> str:
    return secrets.token_urlsafe(n)[:n]


def seed_if_empty(db: Session) -> None:
    if db.query(User).first():
        return

    host = User(
        email=DEMO_HOST_EMAIL,
        name="Ajith (Demo Host)",
        password_hash=hash_password(DEMO_PASSWORD),
        role="host",
    )
    owner = User(
        email=DEMO_OWNER_EMAIL,
        name="Ajith (Demo Owner)",
        password_hash=hash_password(DEMO_PASSWORD),
        role="owner",
    )
    db.add_all([host, owner])
    db.flush()

    slot = MeetingSlot(
        host_id=host.id,
        title="Intro call — 30 min",
        description="A short intro call. After we meet, the host can paste a transcript or upload audio to generate notes.",
        duration_min=30,
        booking_token="demo-intro-slot",
    )
    filled = MeetingSlot(
        host_id=host.id,
        title="Q4 campaign review",
        description="Weekly performance review.",
        duration_min=45,
        booking_token="demo-q4-slot",
    )
    db.add_all([slot, filled])
    db.flush()

    booking = Booking(
        slot_id=filled.id,
        guest_name="Priya Menon",
        guest_email="priya@example.com",
        notes="Please cover CPL and next-week tests.",
        booked_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    db.add(booking)
    db.flush()

    meeting = Meeting(
        host_id=host.id,
        slot_id=filled.id,
        booking_id=booking.id,
        title="Q4 campaign review with Priya and Ravi",
        transcript=TRANSCRIPT.strip(),
        summary=(
            "The team reviewed healthcare lead-gen performance. CPL improved versus last month, "
            "and the clinic-visit creative is producing better lead quality. They agreed to pause "
            "the generic UGC ad, shift budget, ship a new landing page, and draft a WhatsApp follow-up script."
        ),
        checkpoints_json=json.dumps(
            [
                {"item": "Pause the generic UGC ad", "status": "decided"},
                {"item": "Shift 30% of budget to the clinic-visit set", "status": "decided"},
                {"item": "New landing page", "status": "open"},
                {"item": "WhatsApp follow-up script", "status": "open"},
            ]
        ),
        decisions_json=json.dumps(
            [
                "Pause the generic UGC ad.",
                "Shift 30% of budget to the clinic-visit creative.",
                "Priya reviews the WhatsApp script before it goes live.",
            ]
        ),
        todos_json=json.dumps(
            [
                {"task": "Move 30% of budget to the clinic-visit set", "owner": "Priya", "due": "today"},
                {"task": "Send the new landing page", "owner": "Ravi", "due": "Thursday"},
                {"task": "Draft the WhatsApp follow-up script", "owner": "Ajith", "due": "unspecified"},
                {"task": "Review the WhatsApp script before it goes live", "owner": "Priya", "due": "unspecified"},
                {"task": "Share the weekly Looker Studio snapshot", "owner": "Priya", "due": "Friday"},
            ]
        ),
        status="processed",
        source="demo",
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
        processed_at=datetime.now(timezone.utc) - timedelta(days=2, hours=-1),
    )
    pending = Meeting(
        host_id=host.id,
        title="Pending — paste a transcript to try the summariser",
        transcript="",
        status="pending",
        source="paste",
    )
    db.add_all([meeting, pending])

    resto = Restaurant(owner_id=owner.id, name="Spice Garden")
    db.add(resto)
    db.flush()

    kora = Branch(
        restaurant_id=resto.id,
        name="Koramangala",
        address="5th Block, Koramangala, Bengaluru",
        public_token="demo-kora",
    )
    indi = Branch(
        restaurant_id=resto.id,
        name="Indiranagar",
        address="100 Feet Road, Indiranagar, Bengaluru",
        public_token="demo-indi",
    )
    db.add_all([kora, indi])
    db.flush()

    samples = [
        (
            kora,
            "Ananya Rao",
            "The biryani was fragrant and the raita was fresh. Staff were friendly and we were seated quickly. Will come back.",
            4.7,
            "positive",
            {"food": "pos", "service": "pos", "wait": "pos"},
            "Food, service and wait all mentioned positively.",
            "coupon",
            "10% off next visit",
            "SG-KORA-A1",
        ),
        (
            kora,
            "Vikram Shah",
            "Food was decent but we waited almost forty minutes for the mains. The waiter was apologetic. Please look at kitchen timing at dinner.",
            2.6,
            "negative",
            {"food": "neu", "service": "pos", "wait": "neg"},
            "Wait time was the main complaint; service recovered somewhat.",
            "coupon",
            "10% off next visit",
            "SG-KORA-B2",
        ),
        (
            indi,
            "Meera Iyer",
            "Excellent filter coffee and crisp dosa. Service was warm. A little wait on a Sunday evening, which I expected.",
            4.5,
            "positive",
            {"food": "pos", "service": "pos", "wait": "neu"},
            "Strong food and service; wait treated as acceptable.",
            "cashback",
            "₹50 cashback",
            "SG-INDI-C3",
        ),
        (
            indi,
            "John Mathew",
            "The paneer was cold and the naan arrived late. Manager did come to the table, but the meal was disappointing.",
            2.1,
            "negative",
            {"food": "neg", "service": "neu", "wait": "neg"},
            "Food quality and late bread drove a low score.",
            "coupon",
            "10% off next visit",
            "SG-INDI-D4",
        ),
    ]

    for branch, name, text, score, label, themes, note, kind, value, code in samples:
        review = Review(
            branch_id=branch.id,
            customer_name=name,
            text=text,
            sentiment_score=score,
            sentiment_label=label,
            themes_json=json.dumps(themes),
            themes_note=note,
            source="demo",
            created_at=datetime.now(timezone.utc) - timedelta(days=samples.index((branch, name, text, score, label, themes, note, kind, value, code)) + 1),
        )
        db.add(review)
        db.flush()
        db.add(
            Coupon(
                branch_id=branch.id,
                review_id=review.id,
                code=code,
                kind=kind,
                value_text=value,
                status="issued",
            )
        )

    db.commit()
    seed_briefs_if_empty(db)



def seed_briefs_if_empty(db: Session) -> None:
    """Add a demo Review-to-Reel pack for the owner if none exist yet."""
    if db.query(BriefPack).first():
        return
    owner = db.query(User).filter(User.email == DEMO_OWNER_EMAIL).first()
    if not owner:
        return
    reviews = (
        db.query(Review)
        .join(Branch, Review.branch_id == Branch.id)
        .join(Restaurant, Branch.restaurant_id == Restaurant.id)
        .filter(Restaurant.owner_id == owner.id)
        .all()
    )
    if not reviews:
        return
    from app import llm

    payload = [
        {
            "id": r.id,
            "name": r.customer_name,
            "text": r.text,
            "sentiment_label": r.sentiment_label,
        }
        for r in reviews
    ]
    items, source = llm.generate_ugc_briefs(payload, language="both")
    pack = BriefPack(
        user_id=owner.id,
        title="Spice Garden — Review to Reel (demo)",
        language="both",
        source="saved_reviews",
        reviews_used=len(reviews),
        source_label="Spice Garden reviews already in the database",
        status="generated",
    )
    db.add(pack)
    db.flush()
    for item in items:
        db.add(
            UgcBrief(
                pack_id=pack.id,
                user_id=owner.id,
                theme=item["theme"],
                angle=item["angle"],
                language=item["language"],
                hooks_json=json.dumps(item["hooks"]),
                script=item["script"],
                shot_list_json=json.dumps(item["shot_list"]),
                on_screen_text=item["on_screen_text"],
                cta=item["cta"],
                quotes_json=json.dumps(item["quotes"]),
                source_review_ids_json=json.dumps(item["source_review_ids"]),
                caution=item.get("caution") or "",
                status="draft",
            )
        )
    db.commit()
