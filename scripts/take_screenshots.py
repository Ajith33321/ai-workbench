#!/usr/bin/env python3
"""Log into both dashboards and capture PNG screenshots with headless Chrome."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = Path(os.environ.get("SCREENSHOT_DIR", ROOT / "screenshots"))
OUT.mkdir(parents=True, exist_ok=True)
BASE = os.environ.get("APP_URL", "http://127.0.0.1:8000").rstrip("/")
CHROME = os.environ.get("CHROME_PATH", "/usr/bin/google-chrome")

from app.seed import DEMO_HOST_EMAIL, DEMO_OWNER_EMAIL, DEMO_PASSWORD  # noqa: E402


def login(email: str, password: str) -> str:
    r = httpx.post(f"{BASE}/api/auth/login", json={"email": email, "password": password}, timeout=15)
    r.raise_for_status()
    return r.json()["token"]


def _try_playwright(shots: list[tuple[str, Path, str | None]]) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROME,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            headless=True,
        )
        try:
            for url, dest, token in shots:
                context = browser.new_context(
                    viewport={"width": 1440, "height": 1100},
                    device_scale_factor=1.5,
                )
                if token:
                    context.add_cookies(
                        [
                            {
                                "name": "access_token",
                                "value": token,
                                "url": BASE,
                                "httpOnly": True,
                                "sameSite": "Lax",
                            }
                        ]
                    )
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                time.sleep(0.8)
                page.screenshot(path=str(dest), full_page=True)
                print(f"Wrote {dest} ({dest.stat().st_size} bytes)")
                context.close()
        finally:
            browser.close()
    return True


def _cdp_shots(shots: list[tuple[str, Path, str | None]]) -> None:
    import base64
    import json
    import subprocess
    import uuid
    import urllib.request
    import websocket

    port = 9223
    user_data = Path(f"/tmp/chrome-shot-{uuid.uuid4().hex}")
    user_data.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data}",
            "--window-size=1440,1100",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        version = None
        last = None
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as resp:
                    version = json.loads(resp.read().decode())
                    break
            except Exception as exc:
                last = exc
                time.sleep(0.15)
        if not version:
            raise RuntimeError(f"Chrome debug port not ready: {last}")
        ws = websocket.create_connection(version["webSocketDebuggerUrl"], timeout=15)
        mid = 0

        def send(method, params=None, session_id=None):
            nonlocal mid
            mid += 1
            msg = {"id": mid, "method": method}
            if params:
                msg["params"] = params
            if session_id:
                msg["sessionId"] = session_id
            ws.send(json.dumps(msg))
            want = mid
            while True:
                data = json.loads(ws.recv())
                if data.get("id") == want:
                    if "error" in data:
                        raise RuntimeError(data["error"])
                    return data.get("result") or {}

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as resp:
            pages = json.loads(resp.read().decode())
        page = next((item for item in pages if item.get("type") == "page"), pages[0])
        attached = send("Target.attachToTarget", {"targetId": page["id"], "flatten": True})
        sid = attached["sessionId"]
        send("Page.enable", {}, sid)
        send("Network.enable", {}, sid)

        for url, dest, token in shots:
            if token:
                send(
                    "Network.setCookie",
                    {
                        "name": "access_token",
                        "value": token,
                        "url": BASE,
                        "httpOnly": True,
                        "path": "/",
                    },
                    sid,
                )
            send("Page.navigate", {"url": url}, sid)
            time.sleep(2.4)
            shot = send(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True, "captureBeyondViewport": True},
                sid,
            )
            dest.write_bytes(base64.b64decode(shot["data"]))
            print(f"Wrote {dest} ({dest.stat().st_size} bytes)")
        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def main() -> int:
    host = login(DEMO_HOST_EMAIL, DEMO_PASSWORD)
    owner = login(DEMO_OWNER_EMAIL, DEMO_PASSWORD)
    meetings = httpx.get(
        f"{BASE}/api/meetings",
        headers={"Authorization": f"Bearer {host}"},
        timeout=15,
    ).json()
    processed = next((m for m in meetings if m.get("status") == "processed"), None)

    shots: list[tuple[str, Path, str | None]] = [
        (f"{BASE}/", OUT / "home.png", None),
        (f"{BASE}/meetings", OUT / "meetings_dashboard.png", host),
        (f"{BASE}/book/demo-intro-slot", OUT / "booking_page.png", None),
        (f"{BASE}/reviews", OUT / "reviews_dashboard.png", owner),
        (f"{BASE}/r/demo-kora", OUT / "review_form.png", None),
    ]
    if processed:
        shots.insert(2, (f"{BASE}/meetings/{processed['id']}", OUT / "meeting_detail.png", host))

    if not _try_playwright(shots):
        print("Playwright not installed; using Chrome DevTools protocol.")
        _cdp_shots(shots)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
