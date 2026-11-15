"""Sign-in for when Vantage is hosted online.

Users are stored in the library database with PBKDF2-hashed passwords. Sign-in is required as soon
as at least one user exists. The first user can be created from the command line
(`python -m app.cli add-user`) or from the VANTAGE_ADMIN_EMAIL / VANTAGE_ADMIN_PASSWORD environment
variables, which suits cloud hosts that have no shell.
"""
import hashlib
import hmac
import os
import secrets
import time

from . import db

SESSION_DAYS = 30
COOKIE = "vantage_session"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name       TEXT NOT NULL DEFAULT '',
    pw_hash    TEXT NOT NULL,
    is_admin   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires    REAL NOT NULL
);
"""


def init() -> None:
    with db.tx() as conn:
        conn.executescript(SCHEMA)
    email, password = os.environ.get("VANTAGE_ADMIN_EMAIL"), os.environ.get("VANTAGE_ADMIN_PASSWORD")
    if email and password and not db.one("SELECT id FROM users WHERE email = ?", (email,)):
        add_user(email, password, name="Administrator", is_admin=True)


def _hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def _verify(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        test = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(test.hex(), digest)
    except ValueError:
        return False


def add_user(email: str, password: str, name: str = "", is_admin: bool = False) -> int:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    with db.tx() as conn:
        cur = conn.execute("INSERT INTO users(email, name, pw_hash, is_admin) VALUES(?, ?, ?, ?)",
                           (email.strip(), name.strip(), _hash(password), int(is_admin)))
        return cur.lastrowid


def set_password(email: str, password: str) -> bool:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    with db.tx() as conn:
        cur = conn.execute("UPDATE users SET pw_hash = ? WHERE email = ?", (_hash(password), email.strip()))
        conn.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email = ?)", (email.strip(),))
        return cur.rowcount > 0


def login_required() -> bool:
    return db.one("SELECT COUNT(*) AS n FROM users")["n"] > 0


def login(email: str, password: str) -> str | None:
    user = db.one("SELECT * FROM users WHERE email = ?", (email.strip(),))
    if user is None or not _verify(password, user["pw_hash"]):
        time.sleep(0.8)  # slow down password guessing
        return None
    token = secrets.token_urlsafe(32)
    with db.tx() as conn:
        conn.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        conn.execute("INSERT INTO sessions(token, user_id, expires) VALUES(?, ?, ?)",
                     (token, user["id"], time.time() + SESSION_DAYS * 86400))
    return token


def logout(token: str | None) -> None:
    if token:
        with db.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def user_for(token: str | None) -> dict | None:
    if not token:
        return None
    return db.one(
        "SELECT u.id, u.email, u.name, u.is_admin FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ? AND s.expires > ?", (token, time.time()))
