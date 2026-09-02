"""Email+password auth. Users and sessions persist on the /data disk, not Postgres."""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from analyst.tenant import data_dir, sanitize_tenant_id, warehouse_path
from warehouse.store import Warehouse

COOKIE_NAME = "ca_session"
COOKIE_MAX_AGE = 14 * 24 * 60 * 60
DEMO_EMAIL = "demo@example.clinic"
DEMO_PASSWORD = os.environ.get("CLINIC_ANALYST_DEMO_PASSWORD", "demo-clinic-2026")
DEMO_TENANT_ID = "example-clinic"
ROLES = ("owner", "analyst")


@dataclass
class User:
    user_id: str
    email: str
    tenant_id: str
    role: str
    tenant_name: str


def secret_key() -> str:
    return os.environ.get("CLINIC_ANALYST_SECRET", "dev-only-not-a-production-secret")


def auth_db_path() -> Path:
    path = data_dir() / "auth.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(auth_db_path()))
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
        )
        """
    )
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def _check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key(), salt="clinic-analyst-session")


def sign_session(user: User) -> str:
    return _signer().dumps({"user_id": user.user_id, "tenant_id": user.tenant_id})


def user_from_cookie(token: str | None) -> User | None:
    if not token:
        return None
    try:
        payload = _signer().loads(token, max_age=COOKIE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return get_user(payload.get("user_id", ""))


def get_user(user_id: str) -> User | None:
    with _connect() as con:
        row = con.execute(
            """
            SELECT u.user_id, u.email, u.tenant_id, u.role, t.display_name
            FROM users u JOIN tenants t ON t.tenant_id = u.tenant_id
            WHERE u.user_id = ?
            """,
            [user_id],
        ).fetchone()
    if not row:
        return None
    return User(
        user_id=row["user_id"],
        email=row["email"],
        tenant_id=row["tenant_id"],
        role=row["role"],
        tenant_name=row["display_name"],
    )


def _ensure_warehouse(tenant_id: str) -> None:
    Warehouse(warehouse_path(tenant_id)).close()


def signup(email: str, password: str, clinic_name: str) -> User:
    email = email.strip().lower()
    clinic_name = clinic_name.strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValueError("Enter a valid email.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if len(clinic_name) < 2:
        raise ValueError("Clinic name is required.")
    slug = sanitize_tenant_id(re.sub(r"[^a-z0-9]+", "-", clinic_name.lower()).strip("-"))[:24]
    tenant_id = f"{slug}-{uuid.uuid4().hex[:6]}"
    user_id = uuid.uuid4().hex
    with _connect() as con:
        existing = con.execute("SELECT 1 FROM users WHERE email = ?", [email]).fetchone()
        if existing:
            raise ValueError("An account with that email already exists.")
        con.execute(
            "INSERT INTO tenants (tenant_id, display_name, created_at) VALUES (?, ?, ?)",
            [tenant_id, clinic_name, _now()],
        )
        con.execute(
            """
            INSERT INTO users (user_id, email, password_hash, tenant_id, role, created_at)
            VALUES (?, ?, ?, ?, 'owner', ?)
            """,
            [user_id, email, _hash_password(password), tenant_id, _now()],
        )
        con.commit()
    _ensure_warehouse(tenant_id)
    user = get_user(user_id)
    assert user is not None
    return user


def login(email: str, password: str) -> User:
    email = email.strip().lower()
    with _connect() as con:
        row = con.execute(
            """
            SELECT u.user_id, u.password_hash
            FROM users u
            WHERE u.email = ?
            """,
            [email],
        ).fetchone()
    if not row or not _check_password(password, row["password_hash"]):
        raise ValueError("Unknown email or password.")
    user = get_user(row["user_id"])
    if not user:
        raise ValueError("Unknown email or password.")
    return user


def seed_demo() -> User:
    """Documented demo login. PHI-free. Not a production secret."""
    with _connect() as con:
        row = con.execute("SELECT user_id FROM users WHERE email = ?", [DEMO_EMAIL]).fetchone()
        if row:
            user = get_user(row["user_id"])
            if user:
                _ensure_warehouse(user.tenant_id)
                return user
        con.execute(
            "INSERT OR IGNORE INTO tenants (tenant_id, display_name, created_at) VALUES (?, ?, ?)",
            [DEMO_TENANT_ID, "Example Clinic (synthetic)", _now()],
        )
        user_id = uuid.uuid4().hex
        con.execute(
            """
            INSERT INTO users (user_id, email, password_hash, tenant_id, role, created_at)
            VALUES (?, ?, ?, ?, 'owner', ?)
            """,
            [user_id, DEMO_EMAIL, _hash_password(DEMO_PASSWORD), DEMO_TENANT_ID, _now()],
        )
        con.commit()
    _ensure_warehouse(DEMO_TENANT_ID)
    user = get_user(user_id)
    assert user is not None
    return user
