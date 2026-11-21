"""Password hashing (PBKDF2) and JWT issue / verify."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import ALGORITHM, COOKIE_NAME, SECRET_KEY, ACCESS_TOKEN_EXPIRE_MINUTES
from app.database import get_db
from app.models import User

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters, salt, hexhash = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hexhash)
    except (ValueError, TypeError):
        return False


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "name": user.name,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return request.cookies.get(COOKIE_NAME)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _token_from_request(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    payload = decode_token(token)
    user = db.get(User, int(payload["sub"]))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = _token_from_request(request)
    if not token:
        return None
    try:
        payload = decode_token(token)
        return db.get(User, int(payload["sub"]))
    except HTTPException:
        return None


def require_owner(user: User = Depends(get_current_user)) -> User:
    if user.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Restaurant owner login required")
    return user
