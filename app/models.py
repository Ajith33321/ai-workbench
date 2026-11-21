import json
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="host")  # host | owner
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    slots: Mapped[list["MeetingSlot"]] = relationship(back_populates="host")
    meetings: Mapped[list["Meeting"]] = relationship(back_populates="host")
    restaurants: Mapped[list["Restaurant"]] = relationship(back_populates="owner")
    brief_packs: Mapped[list["BriefPack"]] = relationship(back_populates="user")


class MeetingSlot(Base):
    __tablename__ = "meeting_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    duration_min: Mapped[int] = mapped_column(Integer, default=30)
    booking_token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    host: Mapped[User] = relationship(back_populates="slots")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="slot")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("meeting_slots.id"))
    guest_name: Mapped[str] = mapped_column(String(120))
    guest_email: Mapped[str] = mapped_column(String(255))
    notes: Mapped[str] = mapped_column(Text, default="")
    booked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    slot: Mapped[MeetingSlot] = relationship(back_populates="bookings")
    meeting: Mapped["Meeting | None"] = relationship(back_populates="booking")


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    slot_id: Mapped[int | None] = mapped_column(ForeignKey("meeting_slots.id"), nullable=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    audio_path: Mapped[str] = mapped_column(String(500), default="")
    transcript: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    checkpoints_json: Mapped[str] = mapped_column(Text, default="[]")
    decisions_json: Mapped[str] = mapped_column(Text, default="[]")
    todos_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending | processed
    source: Mapped[str] = mapped_column(String(20), default="paste")  # paste | whisper | demo
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    host: Mapped[User] = relationship(back_populates="meetings")
    booking: Mapped[Booking | None] = relationship(back_populates="meeting")

    def checkpoints(self) -> list:
        return json.loads(self.checkpoints_json or "[]")

    def decisions(self) -> list:
        return json.loads(self.decisions_json or "[]")

    def todos(self) -> list:
        return json.loads(self.todos_json or "[]")


class Restaurant(Base):
    __tablename__ = "restaurants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    owner: Mapped[User] = relationship(back_populates="restaurants")
    branches: Mapped[list["Branch"]] = relationship(back_populates="restaurant")


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"))
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300), default="")
    public_token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    restaurant: Mapped[Restaurant] = relationship(back_populates="branches")
    reviews: Mapped[list["Review"]] = relationship(back_populates="branch")
    coupons: Mapped[list["Coupon"]] = relationship(back_populates="branch")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"))
    customer_name: Mapped[str] = mapped_column(String(120))
    customer_phone: Mapped[str] = mapped_column(String(30), default="")
    text: Mapped[str] = mapped_column(Text)
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0)  # 1.0–5.0
    sentiment_label: Mapped[str] = mapped_column(String(20), default="neutral")
    themes_json: Mapped[str] = mapped_column(Text, default="{}")
    themes_note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20), default="demo")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    branch: Mapped[Branch] = relationship(back_populates="reviews")
    coupon: Mapped["Coupon | None"] = relationship(back_populates="review")

    def themes(self) -> dict:
        return json.loads(self.themes_json or "{}")


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"))
    review_id: Mapped[int | None] = mapped_column(ForeignKey("reviews.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="coupon")  # coupon | cashback
    value_text: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="issued")  # issued | redeemed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    branch: Mapped[Branch] = relationship(back_populates="coupons")
    review: Mapped[Review | None] = relationship(back_populates="coupon")



class BriefPack(Base):
    """One generation run of Review-to-Reel briefs."""

    __tablename__ = "brief_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(16), default="both")  # en | ta | both
    source: Mapped[str] = mapped_column(String(30), default="saved_reviews")
    reviews_used: Mapped[int] = mapped_column(Integer, default=0)
    source_label: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="generated")  # generated | approved
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="brief_packs")
    briefs: Mapped[list["UgcBrief"]] = relationship(back_populates="pack")


class UgcBrief(Base):
    __tablename__ = "ugc_briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_id: Mapped[int] = mapped_column(ForeignKey("brief_packs.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    theme: Mapped[str] = mapped_column(String(40))
    angle: Mapped[str] = mapped_column(String(20))  # proof | desire | objection
    language: Mapped[str] = mapped_column(String(8), default="en")
    hooks_json: Mapped[str] = mapped_column(Text, default="[]")
    script: Mapped[str] = mapped_column(Text, default="")
    shot_list_json: Mapped[str] = mapped_column(Text, default="[]")
    on_screen_text: Mapped[str] = mapped_column(Text, default="")
    cta: Mapped[str] = mapped_column(String(240), default="")
    quotes_json: Mapped[str] = mapped_column(Text, default="[]")
    source_review_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    caution: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    pack: Mapped[BriefPack] = relationship(back_populates="briefs")

    def hooks(self) -> list:
        return json.loads(self.hooks_json or "[]")

    def shot_list(self) -> list:
        return json.loads(self.shot_list_json or "[]")

    def quotes(self) -> list:
        return json.loads(self.quotes_json or "[]")

    def source_review_ids(self) -> list:
        return json.loads(self.source_review_ids_json or "[]")
