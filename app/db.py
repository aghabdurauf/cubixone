"""The portal's own data: users, folders, items, data connections, AI models,
audit trail and settings.

DATABASE_URL picks where it lives:
  * the built-in MySQL container   mysql+pymysql://cubix:...@mysql:3306/cubixone
  * the customer's own database    postgresql+psycopg://... | mssql+pymssql://... | mysql+pymysql://...
  * nothing set (local testing)    a SQLite file in DATA_DIR
Tables are created on first start; all names start with cx_ so they never
collide with anything else in a shared database.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR") or ROOT_DIR / "data").resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{(DATA_DIR / 'cubixone.db').as_posix()}"


def now() -> dt.datetime:
    """Naive UTC, the same in every database."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "cx_user"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(16), default="viewer")          # admin | developer | viewer
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    must_change: Mapped[bool] = mapped_column(Boolean, default=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1)        # bump = sign out everywhere
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Folder(Base):
    """Subfolders under the two fixed sections (root = reports | dashboards)."""
    __tablename__ = "cx_folder"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    root: Mapped[str] = mapped_column(String(16))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("cx_folder.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    visibility: Mapped[str] = mapped_column(String(16), default="everyone")  # everyone | restricted
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Item(Base):
    """A report or dashboard. kind: upload (HTML / zip), live (registered URL),
    generated (built by Developer mode: a template + read-only queries)."""
    __tablename__ = "cx_item"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    root: Mapped[str] = mapped_column(String(16))
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("cx_folder.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    visibility: Mapped[str] = mapped_column(String(16), default="everyone")
    entry: Mapped[str | None] = mapped_column(String(400), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str] = mapped_column(String(400), default="")
    text_index: Mapped[str] = mapped_column(Text, default="")              # what the Assistant reads
    meta: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Grant(Base):
    """Who may open a restricted item or folder."""
    __tablename__ = "cx_grant"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("cx_user.id"))
    item_id: Mapped[int | None] = mapped_column(ForeignKey("cx_item.id"), nullable=True)
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("cx_folder.id"), nullable=True)


class Connection(Base):
    """A data source. All settings (password included) are stored encrypted."""
    __tablename__ = "cx_connection"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    type: Mapped[str] = mapped_column(String(32))
    config_enc: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="untested")   # ok | bad | untested
    last_error: Mapped[str] = mapped_column(String(1000), default="")
    tested_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Model(Base):
    """An AI model the Assistant and Developer mode can call. The key is encrypted."""
    __tablename__ = "cx_llm"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(500), default="")
    key_enc: Mapped[str] = mapped_column(Text, default="")
    key_hint: Mapped[str] = mapped_column(String(16), default="")
    extra: Mapped[str] = mapped_column(Text, default="{}")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="untested")
    last_error: Mapped[str] = mapped_column(String(1000), default="")
    tested_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Audit(Base):
    """Append-only: the application never updates or deletes these rows."""
    __tablename__ = "cx_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, index=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str] = mapped_column(String(300), default="")
    outcome: Mapped[str] = mapped_column(String(16), default="allow")      # allow | deny | error
    detail: Mapped[str] = mapped_column(String(1000), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")


class Setting(Base):
    __tablename__ = "cx_setting"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


DEFAULT_SETTINGS = {
    "brand": "CubixOne",
    "session_hours": "8",
    "idle_minutes": "60",
    "password_min_length": "10",
    "login_max_failures": "5",
    "login_lock_minutes": "15",
    "max_upload_mb": "50",
    "max_package_mb": "200",
    "bot_enabled": "1",
    "bot_prompt": ("Answer only from the reports and dashboards provided to you. "
                   "Name the item each answer comes from. If the answer is not in them, say so plainly."),
}

_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}
else:
    _kwargs["pool_recycle"] = 1800
engine = create_engine(DATABASE_URL, **_kwargs)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def settings(db) -> dict[str, str]:
    out = dict(DEFAULT_SETTINGS)
    for row in db.scalars(select(Setting)):
        out[row.key] = row.value
    return out


def setting_int(db, key: str) -> int:
    try:
        return int(settings(db)[key])
    except (KeyError, ValueError):
        return int(DEFAULT_SETTINGS[key])


def save_settings(db, values: dict[str, str]) -> None:
    for key, value in values.items():
        if key not in DEFAULT_SETTINGS:
            continue
        row = db.get(Setting, key)
        if row:
            row.value = str(value)
        else:
            db.add(Setting(key=key, value=str(value)))
    db.commit()
