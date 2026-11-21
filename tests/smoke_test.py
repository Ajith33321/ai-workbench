#!/usr/bin/env python3
"""End-to-end smoke test for all three modules, always in demo mode.

Run from the repo root:

    /workspace/ai_portfolio/.venv/bin/python tests/smoke_test.py

Exits 0 only if every check passes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Force demo mode before the app is imported.
os.environ.pop("OPENAI_API_KEY", None)
# Use a throwaway database so the test does not pollute a dev DB the second time around.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SMOKE_DIR = ROOT / "data" / "smoke"
SMOKE_DIR.mkdir(parents=True, exist_ok=True)
os.environ["DATA_DIR"] = str(SMOKE_DIR)
os.environ["DATABASE_URL"] = f"sqlite:///{(SMOKE_DIR / 'smoke.db').as_posix()}"

# Fresh file each run
db_path = SMOKE_DIR / "smoke.db"
if db_path.exists():
    db_path.unlink()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.seed import DEMO_HOST_EMAIL, DEMO_OWNER_EMAIL, DEMO_PASSWORD  # noqa: E402


failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        failures.append(msg)


def main() -> int:
    client = TestClient(app)

    print("Health and demo mode")
    r = client.get("/health")
    check(r.status_code == 200, f"/health status {r.status_code}")
    body = r.json()
    check(body.get("ok") is True, "health.ok")
    check(body.get("demo_mode") is True, "demo_mode is True when no API key is set")
    check(client.get("/").status_code == 200, "home page loads")
    check(client.get("/login").status_code == 200, "login page loads")

    print("Book & Record — JWT login, live DB, process a meeting")
    r = client.post("/api/auth/login", json={"email": DEMO_HOST_EMAIL, "password": DEMO_PASSWORD})
    check(r.status_code == 200, f"host login {r.status_code}")
    token = r.json().get("token")
    check(bool(token), "host JWT returned")
    check(r.cookies.get("access_token") == token or "access_token" in r.cookies, "JWT also set as httpOnly cookie")
    h = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/me", headers=h)
    check(me.status_code == 200 and me.json()["email"] == DEMO_HOST_EMAIL, "GET /api/me with Bearer token")
    # TestClient keeps the login cookie; use a fresh client to prove the 401 path.
    bare = TestClient(app)
    check(bare.get("/api/me").status_code == 401, "unauthenticated /api/me is 401")

    seeded = client.get("/api/meetings", headers=h).json()
    check(any(m.get("status") == "processed" and m.get("summary") for m in seeded), "seeded processed meeting is read from SQLite")
    check(any(m.get("todos") for m in seeded if m.get("status") == "processed"), "seeded meeting has to-dos with owners")

    dash = client.get("/meetings", headers=h)
    check(dash.status_code == 200 and "Meetings desk" in dash.text, "meetings dashboard HTML (cookie/auth header)")

    slot = client.post("/api/slots", headers=h, json={"title": "Smoke test slot", "duration_min": 20}).json()
    check("booking_token" in slot, "created a public booking slot")
    book_page = client.get(f"/book/{slot['booking_token']}")
    check(book_page.status_code == 200 and "Smoke test slot" in book_page.text, "public booking page works without login")

    booked = client.post(
        f"/api/book/{slot['booking_token']}",
        json={"guest_name": "Rita Shah", "guest_email": "rita@example.com", "notes": "Please cover next steps."},
    )
    check(booked.status_code == 200, "guest booking via API")
    mid = booked.json()["meeting_id"]

    transcript = (
        "Ajith: Thanks for joining. We will lock the new landing page and pause the old ad.\n"
        "Rita: I will send the copy by Friday.\n"
        "Ajith: Agreed. Decision: shift budget to the clinic-visit creative. I will draft the follow-up script."
    )
    out = client.post(f"/api/meetings/{mid}/process", headers=h, json={"transcript": transcript})
    check(out.status_code == 200, f"process meeting {out.status_code}")
    payload = out.json()
    check(payload.get("status") == "processed", "meeting status is processed")
    check(bool(payload.get("summary")), "summary stored")
    check(isinstance(payload.get("checkpoints"), list), "checkpoints list")
    check(isinstance(payload.get("decisions"), list) and len(payload["decisions"]) >= 1, "at least one decision")
    check(isinstance(payload.get("todos"), list) and len(payload["todos"]) >= 1, "at least one to-do")
    check(any("owner" in t for t in payload.get("todos") or []), "to-dos include an owner field")

    again = client.get("/api/meetings", headers=h).json()
    found = next((m for m in again if m["id"] == mid), None)
    check(found is not None and found.get("summary") == payload["summary"], "processed meeting is persisted and read back from DB")

    detail = client.get(f"/meetings/{mid}", headers=h)
    check(detail.status_code == 200 and "To-dos" in detail.text, "meeting detail page shows notes")

    print("Review Rewards — owner JWT, sentiment, coupon ledger")
    r = client.post("/api/auth/login", json={"email": DEMO_OWNER_EMAIL, "password": DEMO_PASSWORD})
    check(r.status_code == 200, "owner login")
    ot = r.json()["token"]
    oh = {"Authorization": f"Bearer {ot}"}
    check(client.get("/api/reviews", headers=h).status_code == 403, "meeting host cannot open owner API")

    dash = client.get("/api/reviews", headers=oh).json()
    check(dash.get("restaurant") == "Spice Garden", "seeded restaurant name from DB")
    check(len(dash.get("reviews") or []) >= 4, "seeded reviews present")
    check(len(dash.get("coupons") or []) >= 4, "seeded coupons present")
    check(any(b["name"] == "Koramangala" for b in dash.get("branches") or []), "Koramangala branch")

    html = client.get("/reviews", headers=oh)
    check(html.status_code == 200 and "Spice Garden" in html.text, "owner dashboard HTML")
    check("SG-KORA" in html.text or "coupons" in html.text.lower(), "dashboard shows coupon data")

    branch = client.post("/api/branches", headers=oh, json={"name": "HSR Layout", "address": "Bengaluru"})
    check(branch.status_code == 200, "add branch")
    token_b = branch.json()["public_token"]
    form = client.get(f"/r/{token_b}")
    check(form.status_code == 200 and "HSR Layout" in form.text, "public review form, no login required")
    qr = client.get(f"/qr/{token_b}.png")
    check(qr.status_code == 200 and qr.headers["content-type"].startswith("image/png") and len(qr.content) > 100, "QR PNG is served")

    review = client.post(
        f"/api/reviews/{token_b}",
        json={
            "customer_name": "Smoke Tester",
            "text": "The dosa was delicious and fresh. Service was friendly and we were seated quickly.",
        },
    )
    check(review.status_code == 200, f"submit review {review.status_code}")
    rv = review.json()
    check(rv.get("sentiment_label") == "positive", f"positive keyword sentiment, got {rv.get('sentiment_label')}")
    check(rv.get("sentiment_score", 0) >= 3.6, f"score is in the positive band ({rv.get('sentiment_score')})")
    check(isinstance(rv.get("themes"), dict), "theme tags present")
    check((rv.get("coupon") or {}).get("code"), "coupon/cashback code issued")
    check((rv.get("coupon") or {}).get("status") == "issued", "coupon status is issued")

    dash2 = client.get("/api/reviews", headers=oh).json()
    check(any(r["customer_name"] == "Smoke Tester" for r in dash2["reviews"]), "new review is persisted in SQLite")
    check(any(c["code"] == rv["coupon"]["code"] for c in dash2["coupons"]), "new coupon is persisted in SQLite")

    short = client.post(f"/api/reviews/{token_b}", json={"customer_name": "X", "text": "ok"})
    check(short.status_code == 400, "too-short review is rejected")


    print("Review to Reel — briefs from saved and pasted reviews")
    check(TestClient(app).get("/briefs").status_code == 401, "briefs desk requires login")
    seeded_briefs = client.get("/api/briefs", headers=oh).json()
    check(len(seeded_briefs.get("packs") or []) >= 1, "owner has a seeded brief pack from Spice Garden reviews")
    check(len(seeded_briefs.get("briefs") or []) >= 2, "seeded pack contains more than one brief")
    check(any(b.get("language") == "en" for b in seeded_briefs["briefs"]), "English brief present")
    check(any(b.get("language") == "ta" for b in seeded_briefs["briefs"]), "Tamil brief present")
    check(all(b.get("hooks") for b in seeded_briefs["briefs"]), "each seeded brief has hooks")
    check(any(b.get("quotes") and b.get("source_review_ids") for b in seeded_briefs["briefs"]), "briefs attach verbatim quotes and source review ids")
    html_b = client.get("/briefs", headers=oh)
    check(html_b.status_code == 200 and "UGC briefs" in html_b.text, "briefs dashboard HTML")
    pack_id = seeded_briefs["packs"][0]["id"]
    detail = client.get(f"/briefs/{pack_id}", headers=oh)
    check(detail.status_code == 200 and "Hooks" in detail.text, "brief pack detail page")
    # Owner generates a new pack from saved reviews
    gen = client.post(
        "/api/briefs/generate",
        headers=oh,
        json={"title": "Smoke pack from saved reviews", "language": "en", "use_saved_reviews": True},
    )
    check(gen.status_code == 200, f"generate from saved reviews {gen.status_code}")
    g = gen.json()
    check(g.get("reviews_used", 0) >= 4, f"used saved reviews ({g.get('reviews_used')})")
    check(g.get("briefs") and g["briefs"][0].get("hooks"), "generated briefs include hooks")
    check(any(b.get("theme") for b in g["briefs"]), "briefs have a theme")
    # Wait objection should carry a caution, not a fake 'no wait' claim
    wait_briefs = [b for b in g["briefs"] if b.get("theme") == "wait"]
    if wait_briefs:
        check(any("not" in (b.get("caution") or "").lower() or "do not" in (b.get("caution") or "").lower() for b in wait_briefs), "wait briefs include a caution")
        check(all("no wait" not in (b.get("script") or "").lower() and "no queue" not in (b.get("cta") or "").lower() for b in wait_briefs), "wait briefs do not invent a no-wait claim")
    approve_id = g["briefs"][0]["id"]
    ap = client.post(f"/api/briefs/{approve_id}/approve", headers=oh)
    check(ap.status_code == 200 and ap.json().get("status") == "approved", "approve a brief")
    again = client.get("/api/briefs", headers=oh).json()
    check(any(b["id"] == approve_id and b["status"] == "approved" for b in again["briefs"]), "approved status is persisted in SQLite")

    # A host with no restaurant can still paste reviews
    pasted = client.post(
        "/api/briefs/generate",
        headers=h,
        json={
            "title": "Clinic paste pack",
            "language": "en",
            "use_saved_reviews": False,
            "pasted": "The doctor explained the procedure clearly and the front desk was kind.\n\nWe waited almost forty minutes after the appointment time. The nurse did apologise.",
        },
    )
    check(pasted.status_code == 200, f"host can generate briefs from pasted reviews {pasted.status_code}")
    pb = pasted.json()
    check(pb.get("reviews_used") >= 2, "pasted text split into two reviews")
    check(pb.get("briefs"), "pasted reviews produced briefs")
    check(client.get("/api/briefs", headers=h).json().get("packs"), "host pack is scoped to the host account")
    # Owner cannot see the host's pack (and vice versa already implied)
    owner_ids = {p["id"] for p in client.get("/api/briefs", headers=oh).json()["packs"]}
    host_ids = {p["id"] for p in client.get("/api/briefs", headers=h).json()["packs"]}
    check(owner_ids.isdisjoint(host_ids), "brief packs are scoped to the signed-in user")
    empty = client.post("/api/briefs/generate", headers=h, json={"title": "Empty", "pasted": "hi"})
    check(empty.status_code == 400, "too-short paste is rejected")

    print("Signup issues a fresh JWT")
    fresh = client.post(
        "/api/auth/signup",
        json={"name": "New Host", "email": "new.host@example.com", "password": "demo1234", "role": "host"},
    )
    check(fresh.status_code == 200 and fresh.json().get("token"), "signup returns JWT")
    nh = {"Authorization": f"Bearer {fresh.json()['token']}"}
    mine = client.get("/api/meetings", headers=nh).json()
    check(mine == [], "new host sees an empty meeting list (data is scoped to the account)")

    if failures:
        print(f"\n{len(failures)} CHECK(S) FAILED")
        for f in failures:
            print(" -", f)
        return 1
    print("\nALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
