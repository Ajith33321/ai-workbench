"""Runtime configuration. Secrets come from the environment, never from source."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# On Vercel the code folder is read-only, so the demo database lives in /tmp there.
_default_data = Path("/tmp/data") if os.getenv("VERCEL") else BASE_DIR / "data"
DATA_DIR = Path(os.getenv("DATA_DIR", _default_data))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

SECRET_KEY = os.getenv("JWT_SECRET", "dev-secret-change-me-before-deploy")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", str(60 * 24 * 7)))
COOKIE_NAME = "access_token"

_db = os.getenv("DATABASE_URL")
DATABASE_URL = _db if _db else f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_WHISPER_MODEL = os.getenv("OPENAI_WHISPER_MODEL", "whisper-1")
DEMO_MODE = not bool(OPENAI_API_KEY)

APP_NAME = "AI Workbench"
