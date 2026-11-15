"""Passwords, encryption of stored secrets, viewer tickets and access rules."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .db import DATA_DIR, Folder, Item, User


def _secret_key() -> str:
    key = os.getenv("SECRET_KEY", "").strip()
    if key:
        return key
    path = DATA_DIR / "secret.key"           # local testing: made once, kept in the data volume
    if not path.exists():
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip()


SECRET_KEY = _secret_key()
_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"cubixone/enc:" + SECRET_KEY.encode()).digest()))
_tickets = URLSafeTimedSerializer(SECRET_KEY, salt="cubixone/view")
TICKET_SECONDS = 8 * 3600


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=64)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=64)
        return hmac.compare_digest(test.hex(), digest)
    except (ValueError, AttributeError):
        return False


def password_problem(password: str, username: str, min_length: int) -> str | None:
    if len(password) < min_length:
        return f"The password must be at least {min_length} characters."
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 3:
        return "Use at least three of: lower case, capitals, digits, symbols."
    if username and username.lower() in password.lower():
        return "The password may not contain the username."
    return None


# ---------------------------------------------------------------- secrets at rest
def encrypt(value: dict) -> str:
    return _fernet.encrypt(json.dumps(value).encode()).decode()


def decrypt(token: str) -> dict:
    if not token:
        return {}
    try:
        return json.loads(_fernet.decrypt(token.encode()))
    except (InvalidToken, ValueError):
        # SECRET_KEY changed: stored secrets can no longer be read and must be re-entered.
        return {}


# ---------------------------------------------------------------- viewer tickets
# An uploaded report runs in a sandboxed frame with an opaque origin, so the
# browser sends no cookie with its own image/CSS/JS requests. The page that
# opens the item mints a short-lived ticket and puts it in the frame's URL
# path; every asset request carries it.
def make_ticket(item_id: int, user_id: int) -> str:
    return _tickets.dumps({"i": item_id, "u": user_id})


def read_ticket(ticket: str) -> dict | None:
    try:
        return _tickets.loads(ticket, max_age=TICKET_SECONDS)
    except BadSignature:
        return None


# ---------------------------------------------------------------- access rules
class Access:
    """Everything needed to decide what one person may open, read once per request.

    * admin sees everything.
    * A restricted folder closes everything beneath it, at every level, except
      for people granted that folder.
    * A restricted item is open only to people granted that item.
    * A developer also sees the items they published themselves.
    """

    def __init__(self, user: User, folders: list[Folder], item_grants: set[int], folder_grants: set[int]):
        self.user = user
        self.folders = {f.id: f for f in folders}
        self.item_grants = item_grants
        self.folder_grants = folder_grants

    @property
    def admin(self) -> bool:
        return self.user.role == "admin"

    def folder_open(self, folder_id: int | None) -> bool:
        if self.admin:
            return True
        seen = set()
        while folder_id and folder_id not in seen:
            seen.add(folder_id)
            f = self.folders.get(folder_id)
            if not f:
                return False
            if f.visibility == "restricted" and f.id not in self.folder_grants:
                return False
            folder_id = f.parent_id
        return True

    def item_open(self, item: Item) -> bool:
        if self.admin:
            return True
        if self.user.role == "developer" and item.created_by == self.user.username:
            return True
        if not self.folder_open(item.folder_id):
            return False
        return item.visibility == "everyone" or item.id in self.item_grants

    def path(self, folder_id: int | None) -> list[Folder]:
        out, seen = [], set()
        while folder_id and folder_id not in seen:
            seen.add(folder_id)
            f = self.folders.get(folder_id)
            if not f:
                break
            out.insert(0, f)
            folder_id = f.parent_id
        return out
