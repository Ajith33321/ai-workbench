"""FastAPI entry point: HTML pages + JSON API for Book & Record and Review Rewards."""

from __future__ import annotations

import io
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import qrcode
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app import llm
from app.auth import (
    create_access_token,
    get_current_user,
    get_current_user_optional,
    hash_password,
    require_owner,
    verify_password,
)
from app.config import APP_NAME, COOKIE_NAME, DATA_DIR, DEMO_MODE, UPLOAD_DIR
from app.database import Base, SessionLocal, engine, get_db
from app.models import Booking, Branch, Coupon, Meeting, MeetingSlot, Restaurant, Review, User
from app.seed import DEMO_HOST_EMAIL, DEMO_OWNER_EMAIL, DEMO_PASSWORD, seed_if_empty

Base.metadata.create_all(bind=engine)
with SessionLocal() as _db:
    seed_if_empty(_db)

app = FastAPI(title=APP_NAME, docs_url="/api/docs", redoc_url=None)
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _ctx(request: Request, user: User | None = None, **extra):
    data = {
        "request": request,
        "user": user,
        "app_name": APP_NAME,
        "demo_mode": DEMO_MODE,
        "demo_host": DEMO_HOST_EMAIL,
        "demo_owner": DEMO_OWNER_EMAIL,
        "demo_password": DEMO_PASSWORD,
    }
    data.update(extra)
    return data


def _set_login_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )


def _new_token() -> str:
    return secrets.token_urlsafe(9)


def _coupon_code(branch: Branch) -> str:
    prefix = "".join(ch for ch in branch.name.upper() if ch.isalnum())[:4] or "BR"
    return f"SG-{prefix}-{secrets.token_hex(2).upper()}"


def _public_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "demo_mode": DEMO_MODE, "db": str(DATA_DIR / "app.db")}


# ---------------------------------------------------------------------------
# Pages — marketing / auth
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request, user: User | None = Depends(get_current_user_optional)):
    return templates.TemplateResponse(request, "index.html", _ctx(request, user))


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: User | None = Depends(get_current_user_optional), next: str = ""):
    if user:
        return RedirectResponse(_default_next(user), status_code=302)
    return templates.TemplateResponse(request, "login.html", _ctx(request, user, next=next, error=None))


@app.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(func.lower(User.email) == email.strip().lower()).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(request, 
            "login.html",
            _ctx(request, None, next=next, error="Email or password is not right."),
            status_code=401,
        )
    dest = next if next.startswith("/") else _default_next(user)
    resp = RedirectResponse(dest, status_code=302)
    _set_login_cookie(resp, create_access_token(user))
    return resp


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if user:
        return RedirectResponse(_default_next(user), status_code=302)
    return templates.TemplateResponse(request, "signup.html", _ctx(request, user, error=None))


@app.post("/signup")
def signup_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("host"),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    role = "owner" if role == "owner" else "host"
    if db.query(User).filter(func.lower(User.email) == email).first():
        return templates.TemplateResponse(request, 
            "signup.html",
            _ctx(request, None, error="That email already has an account. Try signing in."),
            status_code=400,
        )
    if len(password) < 6:
        return templates.TemplateResponse(request, 
            "signup.html",
            _ctx(request, None, error="Please use a password of at least 6 characters."),
            status_code=400,
        )
    user = User(email=email, name=name.strip() or email.split("@")[0], password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    if role == "owner":
        db.add(Restaurant(owner_id=user.id, name=f"{user.name}'s restaurant"))
    db.commit()
    db.refresh(user)
    resp = RedirectResponse(_default_next(user), status_code=302)
    _set_login_cookie(resp, create_access_token(user))
    return resp


@app.post("/logout")
@app.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


def _default_next(user: User) -> str:
    return "/reviews" if user.role == "owner" else "/meetings"


# ---------------------------------------------------------------------------
# Pages — Book & Record
# ---------------------------------------------------------------------------
@app.get("/meetings", response_class=HTMLResponse)
def meetings_dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    meetings = (
        db.query(Meeting)
        .filter(Meeting.host_id == user.id)
        .order_by(Meeting.created_at.desc())
        .all()
    )
    slots = (
        db.query(MeetingSlot)
        .filter(MeetingSlot.host_id == user.id)
        .order_by(MeetingSlot.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(request, "meetings/dashboard.html", _ctx(request, user, meetings=meetings, slots=slots, base=_public_base(request)),
    )


@app.get("/meetings/new", response_class=HTMLResponse)
def new_meeting_page(user: User = Depends(get_current_user)):
    return RedirectResponse("/meetings", status_code=302)


@app.post("/meetings/slots")
def create_slot_form(
    user: User = Depends(get_current_user),
    title: str = Form(...),
    description: str = Form(""),
    duration_min: int = Form(30),
    db: Session = Depends(get_db),
):
    slot = MeetingSlot(
        host_id=user.id,
        title=title.strip(),
        description=description.strip(),
        duration_min=max(10, min(duration_min, 240)),
        booking_token=_new_token(),
    )
    db.add(slot)
    db.commit()
    return RedirectResponse("/meetings", status_code=302)


@app.post("/meetings/manual")
def create_manual_meeting(
    user: User = Depends(get_current_user),
    title: str = Form(...),
    transcript: str = Form(""),
    db: Session = Depends(get_db),
):
    meeting = Meeting(
        host_id=user.id,
        title=title.strip() or "Untitled meeting",
        transcript=transcript.strip(),
        status="pending",
        source="paste",
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return RedirectResponse(f"/meetings/{meeting.id}", status_code=302)


@app.get("/meetings/{meeting_id}", response_class=HTMLResponse)
def meeting_detail(
    meeting_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    error: str | None = None,
):
    meeting = db.get(Meeting, meeting_id)
    if not meeting or meeting.host_id != user.id:
        raise HTTPException(404, "Meeting not found")
    return templates.TemplateResponse(request, 
        "meetings/detail.html",
        _ctx(request, user, meeting=meeting, error=error),
    )


@app.post("/meetings/{meeting_id}/process")
async def process_meeting_form(
    meeting_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    transcript: str = Form(""),
    audio: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    meeting = db.get(Meeting, meeting_id)
    if not meeting or meeting.host_id != user.id:
        raise HTTPException(404, "Meeting not found")
    text = (transcript or "").strip()
    source = "paste"
    if audio and audio.filename:
        suffix = Path(audio.filename).suffix.lower() or ".webm"
        dest = UPLOAD_DIR / f"meeting-{meeting.id}-{secrets.token_hex(4)}{suffix}"
        content = await audio.read()
        if len(content) > 25 * 1024 * 1024:
            return templates.TemplateResponse(request, 
                "meetings/detail.html",
                _ctx(request, user, meeting=meeting, error="Audio must be 25 MB or smaller."),
                status_code=400,
            )
        dest.write_bytes(content)
        meeting.audio_path = str(dest)
        text, source = llm.transcribe_audio(dest)
    if not text:
        return templates.TemplateResponse(request, 
            "meetings/detail.html",
            _ctx(request, user, meeting=meeting, error="Paste a transcript or upload an audio file."),
            status_code=400,
        )
    payload, llm_source = llm.summarise_meeting(text)
    meeting.transcript = text
    meeting.summary = str(payload.get("summary") or "")
    meeting.checkpoints_json = json.dumps(payload.get("checkpoints") or [])
    meeting.decisions_json = json.dumps(payload.get("decisions") or [])
    meeting.todos_json = json.dumps(payload.get("todos") or [])
    meeting.status = "processed"
    meeting.source = source if source == "whisper" else llm_source
    meeting.processed_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(f"/meetings/{meeting.id}", status_code=302)


@app.get("/book/{token}", response_class=HTMLResponse)
def book_page(token: str, request: Request, db: Session = Depends(get_db), done: int = 0):
    slot = db.query(MeetingSlot).filter(MeetingSlot.booking_token == token).first()
    if not slot:
        raise HTTPException(404, "This booking link is not valid.")
    host = db.get(User, slot.host_id)
    return templates.TemplateResponse(request, 
        "meetings/book.html",
        _ctx(request, None, slot=slot, host=host, done=bool(done), error=None),
    )


@app.post("/book/{token}")
def book_submit(
    token: str,
    request: Request,
    guest_name: str = Form(...),
    guest_email: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    slot = db.query(MeetingSlot).filter(MeetingSlot.booking_token == token).first()
    if not slot:
        raise HTTPException(404, "This booking link is not valid.")
    booking = Booking(
        slot_id=slot.id,
        guest_name=guest_name.strip(),
        guest_email=guest_email.strip(),
        notes=notes.strip(),
    )
    db.add(booking)
    db.flush()
    meeting = Meeting(
        host_id=slot.host_id,
        slot_id=slot.id,
        booking_id=booking.id,
        title=f"{slot.title} — {booking.guest_name}",
        status="pending",
        source="paste",
    )
    db.add(meeting)
    db.commit()
    return RedirectResponse(f"/book/{token}?done=1", status_code=302)


# ---------------------------------------------------------------------------
# Pages — Review Rewards
# ---------------------------------------------------------------------------
@app.get("/reviews", response_class=HTMLResponse)
def reviews_dashboard(
    request: Request,
    user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    resto = db.query(Restaurant).filter(Restaurant.owner_id == user.id).first()
    branches = []
    reviews = []
    coupons = []
    stats = []
    if resto:
        branches = db.query(Branch).filter(Branch.restaurant_id == resto.id).order_by(Branch.name).all()
        branch_ids = [b.id for b in branches]
        if branch_ids:
            reviews = (
                db.query(Review)
                .options(joinedload(Review.branch), joinedload(Review.coupon))
                .filter(Review.branch_id.in_(branch_ids))
                .order_by(Review.created_at.desc())
                .all()
            )
            coupons = (
                db.query(Coupon)
                .options(joinedload(Coupon.branch))
                .filter(Coupon.branch_id.in_(branch_ids))
                .order_by(Coupon.created_at.desc())
                .all()
            )
            for b in branches:
                rows = [r for r in reviews if r.branch_id == b.id]
                avg = round(sum(r.sentiment_score for r in rows) / len(rows), 2) if rows else None
                stats.append(
                    {
                        "branch": b,
                        "count": len(rows),
                        "avg": avg,
                        "positive": sum(1 for r in rows if r.sentiment_label == "positive"),
                        "neutral": sum(1 for r in rows if r.sentiment_label == "neutral"),
                        "negative": sum(1 for r in rows if r.sentiment_label == "negative"),
                        "coupons": sum(1 for c in coupons if c.branch_id == b.id),
                    }
                )
    return templates.TemplateResponse(request, 
        "reviews/dashboard.html",
        _ctx(
            request,
            user,
            restaurant=resto,
            branches=branches,
            reviews=reviews,
            coupons=coupons,
            stats=stats,
            base=_public_base(request),
        ),
    )


@app.post("/reviews/branches")
def add_branch(
    user: User = Depends(require_owner),
    name: str = Form(...),
    address: str = Form(""),
    db: Session = Depends(get_db),
):
    resto = db.query(Restaurant).filter(Restaurant.owner_id == user.id).first()
    if not resto:
        resto = Restaurant(owner_id=user.id, name=f"{user.name}'s restaurant")
        db.add(resto)
        db.flush()
    branch = Branch(
        restaurant_id=resto.id,
        name=name.strip(),
        address=address.strip(),
        public_token=_new_token(),
    )
    db.add(branch)
    db.commit()
    return RedirectResponse("/reviews", status_code=302)


@app.get("/qr/{token}.png")
def branch_qr(token: str, request: Request, db: Session = Depends(get_db)):
    branch = db.query(Branch).filter(Branch.public_token == token).first()
    if not branch:
        raise HTTPException(404, "Unknown branch")
    url = f"{_public_base(request)}/r/{token}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@app.get("/r/{token}", response_class=HTMLResponse)
def review_form(token: str, request: Request, db: Session = Depends(get_db)):
    branch = db.query(Branch).options(joinedload(Branch.restaurant)).filter(Branch.public_token == token).first()
    if not branch:
        raise HTTPException(404, "This review link is not valid.")
    return templates.TemplateResponse(request, 
        "reviews/form.html",
        _ctx(request, None, branch=branch, error=None),
    )


@app.post("/r/{token}")
def review_submit(
    token: str,
    request: Request,
    customer_name: str = Form(...),
    customer_phone: str = Form(""),
    text: str = Form(...),
    db: Session = Depends(get_db),
):
    branch = db.query(Branch).options(joinedload(Branch.restaurant)).filter(Branch.public_token == token).first()
    if not branch:
        raise HTTPException(404, "This review link is not valid.")
    body = (text or "").strip()
    if len(body) < 8:
        return templates.TemplateResponse(request, 
            "reviews/form.html",
            _ctx(request, None, branch=branch, error="Please write a little more so we can score the review fairly."),
            status_code=400,
        )
    payload, source = llm.score_review(body)
    review = Review(
        branch_id=branch.id,
        customer_name=customer_name.strip() or "Guest",
        customer_phone=customer_phone.strip(),
        text=body,
        sentiment_score=float(payload.get("sentiment_score") or 0),
        sentiment_label=str(payload.get("sentiment_label") or "neutral"),
        themes_json=json.dumps(payload.get("themes") or {}),
        themes_note=str(payload.get("themes_note") or ""),
        source=source,
    )
    db.add(review)
    db.flush()
    coupon = None
    cinfo = payload.get("coupon") or {}
    kind = (cinfo.get("kind") or "none").lower()
    if kind in {"coupon", "cashback"} and cinfo.get("value_text"):
        coupon = Coupon(
            branch_id=branch.id,
            review_id=review.id,
            code=_coupon_code(branch),
            kind=kind,
            value_text=str(cinfo["value_text"]),
            status="issued",
        )
        db.add(coupon)
    db.commit()
    if coupon:
        db.refresh(coupon)
    return templates.TemplateResponse(request, 
        "reviews/thanks.html",
        _ctx(request, None, branch=branch, review=review, coupon=coupon),
    )


# ---------------------------------------------------------------------------
# JSON API (used by the smoke test, also handy in interviews)
# ---------------------------------------------------------------------------
@app.post("/api/auth/signup")
def api_signup(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    name = str(payload.get("name", "")).strip() or email.split("@")[0]
    role = "owner" if payload.get("role") == "owner" else "host"
    if not email or len(password) < 6:
        raise HTTPException(400, "Email and a password of at least 6 characters are required")
    if db.query(User).filter(func.lower(User.email) == email).first():
        raise HTTPException(400, "Email already registered")
    user = User(email=email, name=name, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    if role == "owner":
        db.add(Restaurant(owner_id=user.id, name=f"{name}'s restaurant"))
    db.commit()
    db.refresh(user)
    token = create_access_token(user)
    resp = JSONResponse({"token": token, "user": {"id": user.id, "email": user.email, "role": user.role, "name": user.name}})
    _set_login_cookie(resp, token)
    return resp


@app.post("/api/auth/login")
def api_login(payload: dict = Body(...), db: Session = Depends(get_db)):
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    user = db.query(User).filter(func.lower(User.email) == email).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    token = create_access_token(user)
    resp = JSONResponse({"token": token, "user": {"id": user.id, "email": user.email, "role": user.role, "name": user.name}})
    _set_login_cookie(resp, token)
    return resp


@app.get("/api/me")
def api_me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "role": user.role, "name": user.name}


@app.post("/api/slots")
def api_create_slot(payload: dict = Body(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    slot = MeetingSlot(
        host_id=user.id,
        title=str(payload.get("title") or "Meeting").strip(),
        description=str(payload.get("description") or ""),
        duration_min=int(payload.get("duration_min") or 30),
        booking_token=_new_token(),
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return {"id": slot.id, "title": slot.title, "booking_token": slot.booking_token, "path": f"/book/{slot.booking_token}"}


@app.post("/api/book/{token}")
def api_book(token: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    slot = db.query(MeetingSlot).filter(MeetingSlot.booking_token == token).first()
    if not slot:
        raise HTTPException(404, "Unknown booking token")
    booking = Booking(
        slot_id=slot.id,
        guest_name=str(payload.get("guest_name") or "Guest"),
        guest_email=str(payload.get("guest_email") or "guest@example.com"),
        notes=str(payload.get("notes") or ""),
    )
    db.add(booking)
    db.flush()
    meeting = Meeting(
        host_id=slot.host_id,
        slot_id=slot.id,
        booking_id=booking.id,
        title=f"{slot.title} — {booking.guest_name}",
        status="pending",
        source="paste",
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return {"booking_id": booking.id, "meeting_id": meeting.id, "title": meeting.title}


@app.post("/api/meetings/{meeting_id}/process")
def api_process_meeting(
    meeting_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    meeting = db.get(Meeting, meeting_id)
    if not meeting or meeting.host_id != user.id:
        raise HTTPException(404, "Meeting not found")
    text = str(payload.get("transcript") or "").strip()
    if not text:
        raise HTTPException(400, "transcript is required")
    result, source = llm.summarise_meeting(text)
    meeting.transcript = text
    meeting.summary = str(result.get("summary") or "")
    meeting.checkpoints_json = json.dumps(result.get("checkpoints") or [])
    meeting.decisions_json = json.dumps(result.get("decisions") or [])
    meeting.todos_json = json.dumps(result.get("todos") or [])
    meeting.status = "processed"
    meeting.source = source
    meeting.processed_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "id": meeting.id,
        "status": meeting.status,
        "source": meeting.source,
        "summary": meeting.summary,
        "checkpoints": meeting.checkpoints(),
        "decisions": meeting.decisions(),
        "todos": meeting.todos(),
    }


@app.get("/api/meetings")
def api_list_meetings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Meeting).filter(Meeting.host_id == user.id).order_by(Meeting.created_at.desc()).all()
    return [
        {
            "id": m.id,
            "title": m.title,
            "status": m.status,
            "summary": m.summary,
            "todos": m.todos(),
            "checkpoints": m.checkpoints(),
        }
        for m in rows
    ]


@app.post("/api/branches")
def api_add_branch(payload: dict = Body(...), user: User = Depends(require_owner), db: Session = Depends(get_db)):
    resto = db.query(Restaurant).filter(Restaurant.owner_id == user.id).first()
    if not resto:
        resto = Restaurant(owner_id=user.id, name=f"{user.name}'s restaurant")
        db.add(resto)
        db.flush()
    branch = Branch(
        restaurant_id=resto.id,
        name=str(payload.get("name") or "New branch").strip(),
        address=str(payload.get("address") or ""),
        public_token=_new_token(),
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return {"id": branch.id, "name": branch.name, "public_token": branch.public_token, "path": f"/r/{branch.public_token}"}


@app.post("/api/reviews/{token}")
def api_submit_review(token: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    branch = db.query(Branch).filter(Branch.public_token == token).first()
    if not branch:
        raise HTTPException(404, "Unknown branch token")
    body = str(payload.get("text") or "").strip()
    if len(body) < 8:
        raise HTTPException(400, "Review text is too short")
    result, source = llm.score_review(body)
    review = Review(
        branch_id=branch.id,
        customer_name=str(payload.get("customer_name") or "Guest"),
        customer_phone=str(payload.get("customer_phone") or ""),
        text=body,
        sentiment_score=float(result.get("sentiment_score") or 0),
        sentiment_label=str(result.get("sentiment_label") or "neutral"),
        themes_json=json.dumps(result.get("themes") or {}),
        themes_note=str(result.get("themes_note") or ""),
        source=source,
    )
    db.add(review)
    db.flush()
    coupon = None
    cinfo = result.get("coupon") or {}
    kind = (cinfo.get("kind") or "none").lower()
    if kind in {"coupon", "cashback"} and cinfo.get("value_text"):
        coupon = Coupon(
            branch_id=branch.id,
            review_id=review.id,
            code=_coupon_code(branch),
            kind=kind,
            value_text=str(cinfo["value_text"]),
            status="issued",
        )
        db.add(coupon)
    db.commit()
    db.refresh(review)
    return {
        "review_id": review.id,
        "sentiment_score": review.sentiment_score,
        "sentiment_label": review.sentiment_label,
        "themes": review.themes(),
        "source": source,
        "coupon": None
        if not coupon
        else {"code": coupon.code, "kind": coupon.kind, "value_text": coupon.value_text, "status": coupon.status},
    }


@app.get("/api/reviews")
def api_list_reviews(user: User = Depends(require_owner), db: Session = Depends(get_db)):
    resto = db.query(Restaurant).filter(Restaurant.owner_id == user.id).first()
    if not resto:
        return {"restaurant": None, "reviews": [], "coupons": [], "branches": []}
    branches = db.query(Branch).filter(Branch.restaurant_id == resto.id).all()
    ids = [b.id for b in branches]
    reviews = (
        db.query(Review).filter(Review.branch_id.in_(ids)).order_by(Review.created_at.desc()).all() if ids else []
    )
    coupons = db.query(Coupon).filter(Coupon.branch_id.in_(ids)).all() if ids else []
    return {
        "restaurant": resto.name,
        "branches": [{"id": b.id, "name": b.name, "token": b.public_token} for b in branches],
        "reviews": [
            {
                "id": r.id,
                "branch_id": r.branch_id,
                "customer_name": r.customer_name,
                "text": r.text,
                "sentiment_score": r.sentiment_score,
                "sentiment_label": r.sentiment_label,
                "themes": r.themes(),
            }
            for r in reviews
        ],
        "coupons": [
            {"code": c.code, "kind": c.kind, "value_text": c.value_text, "branch_id": c.branch_id, "status": c.status}
            for c in coupons
        ],
    }
