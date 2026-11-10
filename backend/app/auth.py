"""Staff passwords (scrypt) and login sessions.

Staff sign in once with their business's ID and their own password; the dashboard then sends the session token with every
request. Checking a slow password hash on every 2-second poll would be too slow, so the token stands in for it. Only its
SHA-256 is stored, so a leaked database doesn't hand out live sessions.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import delete, select

from .db import LoginSession, Session, Shop, Staff, now

SESSION_DAYS = 30
SCRYPT_N = 2**14  # cost; stored with each hash, so raising it later doesn't break old passwords
WEAK = {"admin", "change-me", "password", "12345678"}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt${SCRYPT_N}${salt.hex()}${hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=8, p=1).hex()}"


def check_password(password: str, stored: str) -> bool:
    _, n, salt, digest = stored.split("$")
    got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=8, p=1).hex()
    return hmac.compare_digest(got, digest)


def strong_enough(password: str) -> str | None:
    """Why a new password isn't acceptable, or None."""
    if len(password) < 8 or password.lower() in WEAK:
        return "Use at least 8 characters, and not a common password."
    return None


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def log_in(db, shop_id: str, password: str) -> tuple[str, Staff] | None:
    """A new session token for the staff member of this business whose password this is."""
    for person in db.scalars(select(Staff).where(Staff.shop_id == shop_id)):
        if check_password(password, person.password_hash):
            token = secrets.token_urlsafe(32)
            db.add(LoginSession(token_hash=_digest(token), staff_id=person.id))
            db.commit()
            return token, person
    return None


def session(token: str) -> tuple[Shop, str] | None:
    """(business, staff name) for a live session token."""
    if not token:
        return None
    with Session() as db:
        row = db.get(LoginSession, _digest(token))
        if not row or row.created_at < now() - timedelta(days=SESSION_DAYS):
            return None
        person = db.get(Staff, row.staff_id)
        return (db.get(Shop, person.shop_id), person.name) if person else None


def log_out(token: str):
    with Session() as db:
        db.execute(delete(LoginSession).where(LoginSession.token_hash == _digest(token)))
        db.commit()
