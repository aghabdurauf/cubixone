"""CubixOne web application: the JSON API, the sandboxed report viewer and the
single-page portal. Run: uvicorn app.main:app"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import tempfile
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from starlette.middleware.sessions import SessionMiddleware

from . import __version__, agent, connectors, llm, storage
from .db import (Audit, Connection, Folder, Grant, Item, Model, SessionLocal, User, init_db, now, save_settings,
                 setting_int, settings)
from .security import (SECRET_KEY, Access, decrypt, encrypt, hash_password, make_ticket, password_problem,
                       read_ticket, verify_password)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cubixone")
STATIC = Path(__file__).resolve().parent / "static"
ROOTS = ("reports", "dashboards")
ROLES = ("admin", "developer", "viewer")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
SERVICE_KEY = os.getenv("SERVICE_AUTH_KEY", "")
DUMMY_HASH = hash_password(uuid.uuid4().hex)
UPLOAD_SANDBOX = "sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads"
# Pages written by Developer mode get nothing from outside: no network, inline code only.
GENERATED_CSP = ("sandbox allow-scripts allow-modals allow-downloads; default-src 'none'; script-src 'unsafe-inline'; "
                 "style-src 'unsafe-inline'; img-src data: blob:; font-src data:")


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="CubixOne", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="cubixone", same_site="lax",
                   https_only=COOKIE_SECURE, max_age=7 * 24 * 3600)


@app.middleware("http")
async def guard(request: Request, call_next):
    # State-changing API calls must come from the portal's own pages: a browser
    # only lets another site send this custom header after a CORS preflight,
    # and CubixOne never approves one.
    if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE") \
            and request.headers.get("x-cubixone") != "1":
        return JSONResponse({"detail": "Request refused."}, status_code=403)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return resp


# ================================================================== plumbing
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def audit(db, request: Request, who, action: str, target="", outcome="allow", detail="") -> None:
    name = who if isinstance(who, str) else (who.username if who else "")
    db.add(Audit(username=name[:64], action=action, target=str(target)[:300], outcome=outcome,
                 detail=str(detail)[:1000], ip=client_ip(request)))
    db.commit()


def load_user(request: Request, db) -> User | None:
    s = request.session
    uid = s.get("uid")
    if not uid:
        return None
    u = db.get(User, uid)
    t = time.time()
    if (not u or not u.is_active or s.get("sv") != u.session_version
            or t - s.get("iat", 0) > setting_int(db, "session_hours") * 3600
            or t - s.get("seen", 0) > setting_int(db, "idle_minutes") * 60):
        s.clear()
        return None
    if t - s.get("seen", 0) > 30:
        s["seen"] = int(t)
    return u


def user_dep(request: Request, db=Depends(get_db)) -> User:
    u = load_user(request, db)
    if not u:
        raise HTTPException(401, "Please sign in.")
    if u.must_change and request.url.path not in ("/api/account/password", "/api/logout"):
        raise HTTPException(403, "Change your password first.")
    return u


def admin_dep(u: User = Depends(user_dep)) -> User:
    if u.role != "admin":
        raise HTTPException(403, "Administrators only.")
    return u


def builder_dep(u: User = Depends(user_dep)) -> User:
    if u.role not in ("admin", "developer"):
        raise HTTPException(403, "Administrators and developers only.")
    return u


def access(db, u: User) -> Access:
    grants = db.scalars(select(Grant).where(Grant.user_id == u.id)).all()
    return Access(u, db.scalars(select(Folder)).all(),
                  {g.item_id for g in grants if g.item_id}, {g.folder_id for g in grants if g.folder_id})


def iso(v: dt.datetime | None) -> str | None:
    return v.replace(tzinfo=dt.timezone.utc).isoformat() if v else None


def user_json(u: User) -> dict:
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "email": u.email, "role": u.role,
            "must_change": u.must_change, "is_active": u.is_active,
            "locked": bool(u.locked_until and u.locked_until > now()), "last_login_at": iso(u.last_login_at)}


def item_json(i: Item, acc: Access) -> dict:
    meta = json.loads(i.meta or "{}")
    return {"id": i.id, "title": i.title, "root": i.root, "folder_id": i.folder_id,
            "folder_path": " / ".join(f.name for f in acc.path(i.folder_id)), "kind": i.kind,
            "visibility": i.visibility, "source": i.source, "created_by": i.created_by,
            "updated_at": iso(i.updated_at), "url": i.url if i.kind == "live" else None,
            "refresh": meta.get("refresh")}


def default_model(db) -> Model | None:
    return db.scalar(select(Model).where(Model.is_default.is_(True)))


def model_cfg(m: Model) -> dict:
    extra = json.loads(m.extra or "{}")
    return {"provider": m.provider, "model": m.model, "base_url": m.base_url,
            "api_key": decrypt(m.key_enc).get("k", ""), "api_version": extra.get("api_version", "")}


def conn_engine(db, conn_id: int):
    c = db.get(Connection, conn_id)
    if not c:
        raise HTTPException(404, "Connection not found.")
    cfg = decrypt(c.config_enc)
    return c, cfg, connectors.engine_for(c.id, c.type, cfg)


# ================================================================== state, setup, sign-in
@app.get("/api/state")
def state(request: Request, db=Depends(get_db)):
    u = load_user(request, db)
    st = settings(db)
    m = default_model(db)
    return {"setup": not db.scalar(select(func.count(User.id))), "brand": st["brand"], "version": __version__,
            "user": user_json(u) if u else None,
            "bot": st["bot_enabled"] == "1" and m is not None,
            "model": f"{llm.PROVIDERS.get(m.provider, {}).get('label', m.provider)} · {m.model}" if m else "",
            "password_min_length": int(st["password_min_length"])}


class SetupIn(BaseModel):
    brand: str = "CubixOne"
    username: str = Field(min_length=2, max_length=64)
    full_name: str = ""
    password: str


@app.post("/api/setup")
def setup(body: SetupIn, request: Request, db=Depends(get_db)):
    if db.scalar(select(func.count(User.id))):
        raise HTTPException(409, "Setup is already done.")
    problem = password_problem(body.password, body.username, setting_int(db, "password_min_length"))
    if problem:
        raise HTTPException(400, problem)
    u = User(username=body.username.strip(), full_name=body.full_name.strip(), role="admin",
             password_hash=hash_password(body.password), last_login_at=now())
    db.add(u)
    db.commit()
    save_settings(db, {"brand": body.brand.strip() or "CubixOne"})
    request.session.update(uid=u.id, sv=u.session_version, iat=int(time.time()), seen=int(time.time()))
    audit(db, request, u, "setup", detail="first administrator created")
    return {"user": user_json(u)}


class LoginIn(BaseModel):
    username: str
    password: str


_ip_attempts: dict[str, deque] = defaultdict(deque)


@app.post("/api/login")
def login(body: LoginIn, request: Request, db=Depends(get_db)):
    ip, t = client_ip(request), time.time()
    q = _ip_attempts[ip]
    while q and t - q[0] > 900:
        q.popleft()
    if len(q) >= 30:
        audit(db, request, body.username, "sign_in", outcome="deny", detail="too many attempts from this address")
        raise HTTPException(429, "Too many sign-in attempts. Try again in a few minutes.")
    q.append(t)
    u = db.scalar(select(User).where(User.username == body.username.strip()))
    if u and u.locked_until and u.locked_until > now():
        audit(db, request, u, "sign_in", outcome="deny", detail="account locked")
        raise HTTPException(401, "This account is locked for a few minutes after too many failed attempts.")
    ok = verify_password(body.password, u.password_hash if u else DUMMY_HASH)
    if not u or not ok or not u.is_active:
        if u and not ok:
            u.failed_logins += 1
            if u.failed_logins >= setting_int(db, "login_max_failures"):
                u.locked_until = now() + dt.timedelta(minutes=setting_int(db, "login_lock_minutes"))
                u.failed_logins = 0
            db.commit()
        audit(db, request, body.username, "sign_in", outcome="deny",
              detail="disabled" if u and ok else "wrong username or password")
        raise HTTPException(401, "Wrong username or password.")
    u.failed_logins, u.locked_until, u.last_login_at = 0, None, now()
    db.commit()
    request.session.clear()
    request.session.update(uid=u.id, sv=u.session_version, iat=int(t), seen=int(t))
    q.clear()
    audit(db, request, u, "sign_in")
    return {"user": user_json(u)}


@app.post("/api/logout")
def logout(request: Request, db=Depends(get_db)):
    u = load_user(request, db)
    request.session.clear()
    if u:
        audit(db, request, u, "sign_out")
    return {"ok": True}


class PasswordIn(BaseModel):
    current: str
    new: str


@app.post("/api/account/password")
def change_password(body: PasswordIn, request: Request, u: User = Depends(user_dep), db=Depends(get_db)):
    if not verify_password(body.current, u.password_hash):
        audit(db, request, u, "password_change", outcome="deny", detail="current password wrong")
        raise HTTPException(400, "The current password is wrong.")
    problem = password_problem(body.new, u.username, setting_int(db, "password_min_length"))
    if problem:
        raise HTTPException(400, problem)
    if verify_password(body.new, u.password_hash):
        raise HTTPException(400, "Choose a password different from the current one.")
    u.password_hash, u.must_change = hash_password(body.new), False
    u.session_version += 1
    db.commit()
    request.session["sv"] = u.session_version
    audit(db, request, u, "password_change")
    return {"user": user_json(u)}


# ================================================================== catalogue
@app.get("/api/home")
def home(u: User = Depends(user_dep), db=Depends(get_db)):
    acc = access(db, u)
    vis = [i for i in db.scalars(select(Item)).all() if acc.item_open(i)]
    vis.sort(key=lambda i: i.updated_at, reverse=True)
    return {"counts": {r: sum(i.root == r for i in vis) for r in ROOTS}, "recent": [item_json(i, acc) for i in vis[:8]]}


@app.get("/api/catalog")
def catalog(root: str = "reports", folder_id: int | None = None, q: str = "", u: User = Depends(user_dep),
            db=Depends(get_db)):
    acc = access(db, u)
    folder = None
    if folder_id:
        folder = acc.folders.get(folder_id)
        if not folder or not acc.folder_open(folder_id):
            raise HTTPException(404, "Not found.")
        root = folder.root
    if root not in ROOTS and not q:
        raise HTTPException(404, "Not found.")
    items = [i for i in db.scalars(select(Item)).all() if acc.item_open(i)]
    if q:
        ql = q.lower()
        return {"items": [item_json(i, acc) for i in items if ql in i.title.lower()]}
    items = [i for i in items if i.root == root]
    subs = [f for f in acc.folders.values() if f.root == root and f.parent_id == folder_id and acc.folder_open(f.id)]
    return {
        "root": root,
        "folder": {"id": folder.id, "name": folder.name, "visibility": folder.visibility} if folder else None,
        "path": [{"id": f.id, "name": f.name} for f in acc.path(folder_id)],
        "folders": [{"id": f.id, "name": f.name, "visibility": f.visibility,
                     "count": sum(i.folder_id == f.id for i in items)} for f in sorted(subs, key=lambda f: f.name.lower())],
        "items": [item_json(i, acc) for i in sorted(items, key=lambda i: i.title.lower())
                  if not folder or i.folder_id == folder.id],
        "all_folders": [{"id": f.id, "path": " / ".join(x.name for x in acc.path(f.id))}
                        for f in acc.folders.values() if f.root == root and acc.folder_open(f.id)],
    }


@app.get("/api/items/{item_id}")
def open_item(item_id: int, request: Request, u: User = Depends(user_dep), db=Depends(get_db)):
    i = db.get(Item, item_id)
    acc = access(db, u)
    if not i or not acc.item_open(i):
        audit(db, request, u, "view", target=f"item {item_id}", outcome="deny")
        raise HTTPException(404, "This item does not exist, or you do not have access to it.")
    out = item_json(i, acc)
    if i.kind == "live":
        out["view_url"] = i.url
    else:
        out["view_url"] = f"/r/{i.id}/{make_ticket(i.id, u.id)}/{i.entry if i.kind == 'upload' else 'index.html'}"
    audit(db, request, u, "view", target=i.title)
    return out


def render_generated(db, i: Item, force: bool = False) -> str:
    """A Developer-mode item: re-run its read-only queries (per its refresh
    setting) and put the data into its page."""
    meta = json.loads(i.meta or "{}")
    folder = storage.item_dir(i.id)
    template = (folder / "template.html").read_text(encoding="utf-8")
    cache = folder / "data.json"
    ttl = {"open": 0, "hourly": 3600, "daily": 86400}.get(meta.get("refresh", "open"), 0)
    cached = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else None
    if force or not cached or time.time() - cached.get("at", 0) > ttl:
        data, failed = {}, []
        for q in meta.get("queries", []):
            try:
                _c, _cfg, eng = conn_engine(db, q["connection_id"])
                data[q["name"]] = connectors.run_query(eng, q["sql"])
            except (connectors.ConnectorError, HTTPException) as e:
                failed.append(f"{q['name']}: {getattr(e, 'detail', e)}")
        if failed and cached:
            log.warning("refresh of item %s failed, showing last data: %s", i.id, failed)
        else:
            if failed:
                data["_errors"] = failed
            cached = {"at": time.time(), "data": data}
            cache.write_text(json.dumps(cached, default=str), encoding="utf-8")
            i.text_index = agent.data_text(i.title, data)
            db.commit()
    return agent.inject_data(template, cached["data"])


@app.get("/r/{item_id}/{ticket}/{path:path}")
def item_content(item_id: int, ticket: str, path: str, db=Depends(get_db)):
    t = read_ticket(ticket)
    if not t or t.get("i") != item_id:
        raise HTTPException(404, "Not found.")
    u = db.get(User, t.get("u"))
    i = db.get(Item, item_id)
    if not u or not u.is_active or not i or not access(db, u).item_open(i):
        raise HTTPException(404, "Not found.")
    if i.kind == "generated":
        if path != "index.html":
            raise HTTPException(404, "Not found.")
        return HTMLResponse(render_generated(db, i), headers={"Content-Security-Policy": GENERATED_CSP,
                                                              "Cache-Control": "no-store"})
    p = storage.safe_path(i.id, path)
    if not p:
        raise HTTPException(404, "Not found.")
    headers = {"Cache-Control": "private, max-age=300"}
    if p.suffix.lower() in (".html", ".htm", ".svg", ".xml"):
        headers["Content-Security-Policy"] = UPLOAD_SANDBOX
    return FileResponse(p, headers=headers)


# ================================================================== folders and items (admin / developer)
class FolderIn(BaseModel):
    root: str
    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    visibility: str = "everyone"


@app.post("/api/folders")
def create_folder(body: FolderIn, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    if body.root not in ROOTS or body.visibility not in ("everyone", "restricted"):
        raise HTTPException(400, "Unknown section or visibility.")
    if body.parent_id:
        parent = db.get(Folder, body.parent_id)
        if not parent or parent.root != body.root:
            raise HTTPException(400, "The parent folder is in a different section.")
    name = body.name.strip()
    if db.scalar(select(Folder).where(Folder.root == body.root, Folder.parent_id == body.parent_id, Folder.name == name)):
        raise HTTPException(409, "A folder with that name already exists here.")
    f = Folder(root=body.root, parent_id=body.parent_id, name=name, visibility=body.visibility)
    db.add(f)
    db.commit()
    audit(db, request, u, "folder_create", target=name)
    return {"id": f.id}


class FolderPatch(BaseModel):
    name: str | None = None
    visibility: str | None = None


@app.patch("/api/folders/{folder_id}")
def update_folder(folder_id: int, body: FolderPatch, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    f = db.get(Folder, folder_id)
    if not f:
        raise HTTPException(404, "Not found.")
    if body.name:
        f.name = body.name.strip()[:200]
    if body.visibility in ("everyone", "restricted"):
        f.visibility = body.visibility
    db.commit()
    audit(db, request, u, "folder_update", target=f.name, detail=f"visibility={f.visibility}")
    return {"ok": True}


@app.delete("/api/folders/{folder_id}")
def delete_folder(folder_id: int, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    f = db.get(Folder, folder_id)
    if not f:
        raise HTTPException(404, "Not found.")
    if db.scalar(select(func.count(Folder.id)).where(Folder.parent_id == f.id)) or \
            db.scalar(select(func.count(Item.id)).where(Item.folder_id == f.id)):
        raise HTTPException(409, "Only an empty folder can be removed.")
    db.execute(delete(Grant).where(Grant.folder_id == f.id))
    db.delete(f)
    db.commit()
    audit(db, request, u, "folder_delete", target=f.name)
    return {"ok": True}


class GrantsIn(BaseModel):
    user_ids: list[int]


def _grants(db, column, obj_id: int) -> list[int]:
    return [g.user_id for g in db.scalars(select(Grant).where(column == obj_id))]


@app.get("/api/folders/{folder_id}/grants")
def folder_grants(folder_id: int, u: User = Depends(admin_dep), db=Depends(get_db)):
    return {"user_ids": _grants(db, Grant.folder_id, folder_id)}


@app.put("/api/folders/{folder_id}/grants")
def set_folder_grants(folder_id: int, body: GrantsIn, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    f = db.get(Folder, folder_id)
    if not f:
        raise HTTPException(404, "Not found.")
    db.execute(delete(Grant).where(Grant.folder_id == folder_id))
    for uid in set(body.user_ids):
        if db.get(User, uid):
            db.add(Grant(user_id=uid, folder_id=folder_id))
    db.commit()
    audit(db, request, u, "grant_folder", target=f.name, detail=f"{len(set(body.user_ids))} people")
    return {"ok": True}


@app.get("/api/items/{item_id}/grants")
def item_grants(item_id: int, u: User = Depends(admin_dep), db=Depends(get_db)):
    return {"user_ids": _grants(db, Grant.item_id, item_id)}


@app.put("/api/items/{item_id}/grants")
def set_item_grants(item_id: int, body: GrantsIn, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    i = db.get(Item, item_id)
    if not i:
        raise HTTPException(404, "Not found.")
    db.execute(delete(Grant).where(Grant.item_id == item_id))
    for uid in set(body.user_ids):
        if db.get(User, uid):
            db.add(Grant(user_id=uid, item_id=item_id))
    db.commit()
    audit(db, request, u, "grant_item", target=i.title, detail=f"{len(set(body.user_ids))} people")
    return {"ok": True}


def check_target_folder(db, u: User, root: str, folder_id: int | None) -> None:
    if root not in ROOTS:
        raise HTTPException(400, "Unknown section.")
    if folder_id:
        f = db.get(Folder, folder_id)
        if not f or f.root != root:
            raise HTTPException(400, "That folder is not in this section.")
        if not access(db, u).folder_open(folder_id):
            raise HTTPException(403, "You cannot publish into that folder.")


@app.post("/api/items/upload")
def upload_item(request: Request, root: str = Form(...), title: str = Form(...), visibility: str = Form("everyone"),
                folder_id: str = Form(""), file: UploadFile = File(...), u: User = Depends(builder_dep),
                db=Depends(get_db)):
    fid = int(folder_id) if folder_id.strip() else None
    check_target_folder(db, u, root, fid)
    limit = setting_int(db, "max_upload_mb") * 1048576
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        size = 0
        while chunk := file.file.read(1048576):
            size += len(chunk)
            if size > limit:
                tmp.close()
                os.unlink(tmp.name)
                raise HTTPException(413, f"The file is larger than {limit // 1048576} MB.")
            tmp.write(chunk)
    i = Item(root=root, folder_id=fid, title=title.strip()[:200] or file.filename, kind="upload",
             visibility="restricted" if visibility == "restricted" else "everyone", created_by=u.username)
    db.add(i)
    db.flush()
    try:
        i.entry = storage.store(Path(tmp.name), file.filename or "report.html", storage.item_dir(i.id),
                                setting_int(db, "max_package_mb") * 1048576)
        i.text_index = storage.extract_text(storage.item_dir(i.id))
        db.commit()
    except storage.UploadError as e:
        db.rollback()
        storage.remove(i.id)
        raise HTTPException(400, str(e)) from e
    finally:
        os.unlink(tmp.name)
    audit(db, request, u, "upload", target=i.title, detail=file.filename or "")
    return {"id": i.id}


class LiveIn(BaseModel):
    root: str
    folder_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    url: str
    visibility: str = "everyone"


@app.post("/api/items/live")
def register_live(body: LiveIn, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    check_target_folder(db, u, body.root, body.folder_id)
    if urlparse(body.url).scheme not in ("http", "https"):
        raise HTTPException(400, "The address must start with http:// or https://")
    i = Item(root=body.root, folder_id=body.folder_id, title=body.title.strip(), kind="live", url=body.url.strip(),
             visibility="restricted" if body.visibility == "restricted" else "everyone", created_by=u.username)
    db.add(i)
    db.commit()
    audit(db, request, u, "register", target=i.title, detail=i.url)
    return {"id": i.id}


class ItemPatch(BaseModel):
    title: str | None = None
    folder_id: int | None = None
    move: bool = False
    visibility: str | None = None


@app.patch("/api/items/{item_id}")
def update_item(item_id: int, body: ItemPatch, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    i = db.get(Item, item_id)
    if not i:
        raise HTTPException(404, "Not found.")
    if body.title:
        i.title = body.title.strip()[:200]
    if body.move:
        check_target_folder(db, u, i.root, body.folder_id)       # never across sections
        i.folder_id = body.folder_id
    if body.visibility in ("everyone", "restricted"):
        i.visibility = body.visibility
    i.updated_at = now()
    db.commit()
    audit(db, request, u, "item_update", target=i.title, detail=f"visibility={i.visibility}")
    return {"ok": True}


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    i = db.get(Item, item_id)
    if not i:
        raise HTTPException(404, "Not found.")
    db.execute(delete(Grant).where(Grant.item_id == i.id))
    db.delete(i)
    db.commit()
    storage.remove(item_id)
    audit(db, request, u, "delete", target=i.title)
    return {"ok": True}


# ================================================================== users (admin)
@app.get("/api/users")
def list_users(u: User = Depends(admin_dep), db=Depends(get_db)):
    users = db.scalars(select(User).order_by(User.username)).all()
    grants = db.scalars(select(Grant)).all()
    titles = {i.id: i.title for i in db.scalars(select(Item))}
    names = {f.id: f.name for f in db.scalars(select(Folder))}
    out = []
    for x in users:
        mine = [g for g in grants if g.user_id == x.id]
        out.append({**user_json(x), "grants": [titles.get(g.item_id) or names.get(g.folder_id) or "?" for g in mine]})
    return {"users": out}


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    full_name: str = ""
    email: str = ""
    role: str = "viewer"
    password: str


@app.post("/api/users")
def add_user(body: UserIn, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(400, "Unknown role.")
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(409, "That username is taken.")
    problem = password_problem(body.password, body.username, setting_int(db, "password_min_length"))
    if problem:
        raise HTTPException(400, problem)
    x = User(username=body.username, full_name=body.full_name.strip(), email=body.email.strip(), role=body.role,
             password_hash=hash_password(body.password), must_change=True)
    db.add(x)
    db.commit()
    audit(db, request, u, "user_add", target=x.username, detail=f"role={x.role}")
    return {"id": x.id}


class UserPatch(BaseModel):
    full_name: str | None = None
    email: str | None = None
    role: str | None = None
    is_active: bool | None = None
    unlock: bool = False


def _other_admins(db, uid: int) -> int:
    return db.scalar(select(func.count(User.id)).where(User.role == "admin", User.is_active.is_(True), User.id != uid))


@app.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UserPatch, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    x = db.get(User, user_id)
    if not x:
        raise HTTPException(404, "Not found.")
    if body.role and body.role not in ROLES:
        raise HTTPException(400, "Unknown role.")
    losing_admin = (body.role and body.role != "admin" and x.role == "admin") or (body.is_active is False and x.role == "admin")
    if losing_admin and not _other_admins(db, x.id):
        raise HTTPException(400, "Keep at least one active administrator.")
    if body.full_name is not None:
        x.full_name = body.full_name.strip()[:200]
    if body.email is not None:
        x.email = body.email.strip()[:200]
    if body.role:
        x.role = body.role
    if body.is_active is not None:
        x.is_active = body.is_active
        x.session_version += 1
    if body.unlock:
        x.locked_until, x.failed_logins = None, 0
    db.commit()
    audit(db, request, u, "user_update", target=x.username,
          detail=f"role={x.role} active={x.is_active}{' unlocked' if body.unlock else ''}")
    return {"ok": True}


class ResetIn(BaseModel):
    password: str


@app.post("/api/users/{user_id}/password")
def reset_password(user_id: int, body: ResetIn, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    x = db.get(User, user_id)
    if not x:
        raise HTTPException(404, "Not found.")
    problem = password_problem(body.password, x.username, setting_int(db, "password_min_length"))
    if problem:
        raise HTTPException(400, problem)
    x.password_hash, x.must_change, x.locked_until, x.failed_logins = hash_password(body.password), True, None, 0
    x.session_version += 1
    db.commit()
    audit(db, request, u, "password_reset", target=x.username)
    return {"ok": True}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    x = db.get(User, user_id)
    if not x:
        raise HTTPException(404, "Not found.")
    if x.id == u.id:
        raise HTTPException(400, "You cannot delete your own account.")
    if x.role == "admin" and not _other_admins(db, x.id):
        raise HTTPException(400, "Keep at least one active administrator.")
    db.execute(delete(Grant).where(Grant.user_id == x.id))
    db.delete(x)
    db.commit()
    audit(db, request, u, "user_delete", target=x.username)
    return {"ok": True}


# ================================================================== data connections
@app.get("/api/connection-types")
def connection_types(u: User = Depends(builder_dep)):
    return {"types": connectors.TYPES, "coming_soon": connectors.COMING_SOON}


def conn_json(c: Connection, with_config: bool = False) -> dict:
    cfg = decrypt(c.config_enc)
    out = {"id": c.id, "name": c.name, "type": c.type, "label": connectors.TYPES.get(c.type, {}).get("label", c.type),
           "host": cfg.get("host") or cfg.get("path") or "", "database": cfg.get("database") or cfg.get("service") or "",
           "status": c.status, "last_error": c.last_error, "tested_at": iso(c.tested_at)}
    if with_config:
        out["config"] = {k: ("" if k in connectors.SECRET_FIELDS else v) for k, v in cfg.items()}
        out["has_password"] = bool(cfg.get("password"))
    return out


@app.get("/api/connections")
def list_connections(u: User = Depends(builder_dep), db=Depends(get_db)):
    return {"connections": [conn_json(c, with_config=u.role == "admin")
                            for c in db.scalars(select(Connection).order_by(Connection.name))]}


class ConnIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str
    config: dict


def _clean_cfg(kind: str, cfg: dict, old: dict | None = None) -> dict:
    if kind not in connectors.TYPES:
        raise HTTPException(400, "Unknown connection type.")
    fields = connectors.TYPES[kind]["fields"]
    out = {k: str(cfg.get(k, "") or "").strip() for k in fields}
    if old and not out.get("password") and "password" in fields:
        out["password"] = old.get("password", "")        # blank on edit = keep the stored password
    return out


@app.post("/api/connections")
def add_connection(body: ConnIn, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    if db.scalar(select(Connection).where(Connection.name == body.name.strip())):
        raise HTTPException(409, "A connection with that name exists.")
    c = Connection(name=body.name.strip(), type=body.type, config_enc=encrypt(_clean_cfg(body.type, body.config)))
    db.add(c)
    db.commit()
    audit(db, request, u, "connection_add", target=c.name, detail=body.type)
    return {"id": c.id}


@app.patch("/api/connections/{conn_id}")
def update_connection(conn_id: int, body: ConnIn, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    c = db.get(Connection, conn_id)
    if not c:
        raise HTTPException(404, "Not found.")
    c.name, c.type = body.name.strip(), body.type
    c.config_enc = encrypt(_clean_cfg(body.type, body.config, decrypt(c.config_enc)))
    c.status, c.last_error = "untested", ""
    db.commit()
    connectors.forget(c.id)
    audit(db, request, u, "connection_update", target=c.name)
    return {"ok": True}


@app.delete("/api/connections/{conn_id}")
def delete_connection(conn_id: int, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    c = db.get(Connection, conn_id)
    if not c:
        raise HTTPException(404, "Not found.")
    db.delete(c)
    db.commit()
    connectors.forget(conn_id)
    audit(db, request, u, "connection_delete", target=c.name)
    return {"ok": True}


class ConnTestIn(BaseModel):
    type: str
    config: dict
    id: int | None = None


@app.post("/api/connections/test")
def test_connection_draft(body: ConnTestIn, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    old = decrypt(db.get(Connection, body.id).config_enc) if body.id and db.get(Connection, body.id) else None
    cfg = _clean_cfg(body.type, body.config, old)
    try:
        res = connectors.test(body.type, cfg)
        audit(db, request, u, "connection_test", target=cfg.get("host") or cfg.get("path"), detail="ok")
        return res
    except connectors.ConnectorError as e:
        audit(db, request, u, "connection_test", target=cfg.get("host") or cfg.get("path"), outcome="error", detail=str(e))
        return {"ok": False, "error": str(e)}


@app.post("/api/connections/{conn_id}/test")
def test_connection(conn_id: int, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    c = db.get(Connection, conn_id)
    if not c:
        raise HTTPException(404, "Not found.")
    try:
        res = connectors.test(c.type, decrypt(c.config_enc))
        c.status, c.last_error = "ok", ""
    except connectors.ConnectorError as e:
        res = {"ok": False, "error": str(e)}
        c.status, c.last_error = "bad", str(e)[:1000]
    c.tested_at = now()
    db.commit()
    audit(db, request, u, "connection_test", target=c.name, outcome="allow" if res.get("ok") else "error",
          detail=res.get("error", ""))
    return res


@app.get("/api/connections/{conn_id}/schema")
def connection_schema(conn_id: int, u: User = Depends(builder_dep), db=Depends(get_db)):
    c, cfg, eng = conn_engine(db, conn_id)
    try:
        return connectors.schema(eng, c.type, cfg)
    except connectors.ConnectorError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/connections/{conn_id}/columns")
def connection_columns(conn_id: int, kind: str, name: str, u: User = Depends(builder_dep), db=Depends(get_db)):
    c, cfg, eng = conn_engine(db, conn_id)
    try:
        return {"columns": connectors.columns(eng, c.type, cfg, kind, name)}
    except connectors.ConnectorError as e:
        raise HTTPException(400, str(e)) from e


# ================================================================== AI models (admin)
@app.get("/api/llm-providers")
def llm_providers(u: User = Depends(admin_dep)):
    return {"providers": llm.PROVIDERS}


def model_json(m: Model) -> dict:
    return {"id": m.id, "name": m.name, "provider": m.provider,
            "provider_label": llm.PROVIDERS.get(m.provider, {}).get("label", m.provider), "model": m.model,
            "base_url": m.base_url, "key_hint": m.key_hint, "is_default": m.is_default, "status": m.status,
            "last_error": m.last_error, "tested_at": iso(m.tested_at),
            "api_version": json.loads(m.extra or "{}").get("api_version", "")}


@app.get("/api/llms")
def list_llms(u: User = Depends(admin_dep), db=Depends(get_db)):
    return {"models": [model_json(m) for m in db.scalars(select(Model).order_by(Model.id))]}


class ModelIn(BaseModel):
    name: str = ""
    provider: str
    model: str = Field(min_length=1, max_length=200)
    base_url: str = ""
    api_key: str = ""
    api_version: str = ""
    is_default: bool = False


def _apply_model(m: Model, body: ModelIn) -> None:
    if body.provider not in llm.PROVIDERS:
        raise HTTPException(400, "Unknown provider.")
    m.name = (body.name or llm.PROVIDERS[body.provider]["label"]).strip()[:200]
    m.provider, m.model, m.base_url = body.provider, body.model.strip(), body.base_url.strip()
    if body.api_key:
        m.key_enc = encrypt({"k": body.api_key.strip()})
        m.key_hint = "••••" + body.api_key.strip()[-4:]
    m.extra = json.dumps({"api_version": body.api_version.strip()})


@app.post("/api/llms")
def add_llm(body: ModelIn, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    m = Model()
    _apply_model(m, body)
    first = not db.scalar(select(func.count(Model.id)))
    m.is_default = first or body.is_default
    if m.is_default:
        for other in db.scalars(select(Model)):
            other.is_default = False
    db.add(m)
    db.commit()
    audit(db, request, u, "model_add", target=m.name, detail=f"{m.provider} {m.model}")
    return {"id": m.id}


@app.patch("/api/llms/{model_id}")
def update_llm(model_id: int, body: ModelIn, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404, "Not found.")
    _apply_model(m, body)
    m.status = "untested"
    db.commit()
    audit(db, request, u, "model_update", target=m.name)
    return {"ok": True}


@app.post("/api/llms/{model_id}/default")
def default_llm(model_id: int, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404, "Not found.")
    for other in db.scalars(select(Model)):
        other.is_default = other.id == m.id
    db.commit()
    audit(db, request, u, "model_default", target=m.name)
    return {"ok": True}


@app.delete("/api/llms/{model_id}")
def delete_llm(model_id: int, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404, "Not found.")
    was_default = m.is_default
    db.delete(m)
    db.commit()
    if was_default:
        nxt = db.scalar(select(Model).order_by(Model.id))
        if nxt:
            nxt.is_default = True
            db.commit()
    audit(db, request, u, "model_delete", target=m.name)
    return {"ok": True}


class ModelTestIn(ModelIn):
    id: int | None = None


@app.post("/api/llms/test")
def test_llm_draft(body: ModelTestIn, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    key = body.api_key
    if not key and body.id and db.get(Model, body.id):
        key = decrypt(db.get(Model, body.id).key_enc).get("k", "")
    cfg = {"provider": body.provider, "model": body.model, "base_url": body.base_url, "api_key": key,
           "api_version": body.api_version}
    try:
        return llm.test(cfg)
    except llm.LLMError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/llms/{model_id}/test")
def test_llm(model_id: int, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404, "Not found.")
    try:
        res = llm.test(model_cfg(m))
        m.status, m.last_error = "ok", ""
    except llm.LLMError as e:
        res = {"ok": False, "error": str(e)}
        m.status, m.last_error = "bad", str(e)[:1000]
    m.tested_at = now()
    db.commit()
    audit(db, request, u, "model_test", target=m.name, outcome="allow" if res.get("ok") else "error",
          detail=res.get("error", ""))
    return res


# ================================================================== settings, audit
@app.get("/api/settings")
def get_settings(u: User = Depends(admin_dep), db=Depends(get_db)):
    st = settings(db)
    return {"settings": st, "user_store": _store_label(), "skills_dir": str(agent.SKILLS_DIR)}


def _store_label() -> str:
    from .db import DATABASE_URL
    scheme = DATABASE_URL.split(":", 1)[0]
    if scheme.startswith("sqlite"):
        return "SQLite file (local testing)"
    if "@mysql:" in DATABASE_URL or "@mysql/" in DATABASE_URL:
        return "Built-in MySQL container"
    return {"mysql+pymysql": "External MySQL / MariaDB", "postgresql+psycopg": "External PostgreSQL",
            "mssql+pymssql": "External SQL Server"}.get(scheme, f"External ({scheme})")


@app.put("/api/settings")
def put_settings(body: dict, request: Request, u: User = Depends(admin_dep), db=Depends(get_db)):
    ints = ("session_hours", "idle_minutes", "password_min_length", "login_max_failures", "login_lock_minutes",
            "max_upload_mb", "max_package_mb")
    clean = {}
    for k, v in body.items():
        if k in ints:
            try:
                clean[k] = str(max(1, min(int(v), 100000)))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} must be a number.") from None
        elif k == "bot_enabled":
            clean[k] = "1" if v in (True, "1", 1, "true") else "0"
        elif k in ("brand", "bot_prompt"):
            clean[k] = str(v)[:4000]
    save_settings(db, clean)
    audit(db, request, u, "settings", detail=", ".join(sorted(clean)))
    return {"ok": True}


@app.get("/api/audit")
def get_audit(limit: int = 300, u: User = Depends(admin_dep), db=Depends(get_db)):
    rows = db.scalars(select(Audit).order_by(Audit.id.desc()).limit(min(limit, 2000))).all()
    return {"rows": [{"at": iso(r.at), "user": r.username, "action": r.action, "target": r.target,
                      "outcome": r.outcome, "detail": r.detail, "ip": r.ip} for r in rows]}


# ================================================================== Assistant
class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    item_id: int | None = None
    history: list[dict] = []


@app.post("/api/assistant")
def ask(body: AskIn, request: Request, u: User = Depends(user_dep), db=Depends(get_db)):
    st = settings(db)
    m = default_model(db)
    if st["bot_enabled"] != "1" or not m:
        raise HTTPException(400, "The Assistant is not set up. An administrator can connect a model under LLM & Bot.")
    acc = access(db, u)
    items = [i for i in db.scalars(select(Item)).all() if i.kind != "live" and acc.item_open(i)]
    focus = body.item_id if any(i.id == body.item_id for i in items) else None
    context, used = agent.retrieve(body.question, [(i.id, i.title, i.text_index or "") for i in items], focus)
    try:
        text = agent.answer(model_cfg(m), st["bot_prompt"], body.question, body.history, context)
    except llm.LLMError as e:
        audit(db, request, u, "bot_question", target=f"item {focus}" if focus else "all", outcome="error", detail=str(e))
        raise HTTPException(502, str(e)) from e
    audit(db, request, u, "bot_question", target=next((i.title for i in items if i.id == focus), "all items"),
          detail=body.question[:300])
    return {"answer": text, "sources": used}


# ================================================================== Developer mode
class DevObject(BaseModel):
    conn_id: int
    kind: str          # tables | views | procs
    name: str


class BuildIn(BaseModel):
    kind: str = "reports"
    prompts: list[str] = []
    objects: list[DevObject] = []
    draft_id: str | None = None
    change: str = ""


@app.post("/api/dev/build")
def dev_build(body: BuildIn, request: Request, u: User = Depends(builder_dep), db=Depends(get_db)):
    m = default_model(db)
    if not m:
        raise HTTPException(400, "Connect an AI model under LLM & Bot first.")
    prev = None
    if body.draft_id:
        prev = agent.load_draft(body.draft_id)
        if not prev or prev.get("owner") != u.username:
            raise HTTPException(404, "That draft is no longer available. Start again.")
    kind = (prev or {}).get("kind") or body.kind
    if kind not in ROOTS:
        raise HTTPException(400, "Choose Report or Dashboard.")
    raw = [o.model_dump() for o in body.objects] or (prev or {}).get("objects", [])
    prompts = (prev or {}).get("prompts", []) if prev else body.prompts
    if not raw:
        raise HTTPException(400, "Tick at least one table, view or procedure.")
    objects = []
    for o in raw[:12]:
        c, cfg, eng = conn_engine(db, o["conn_id"])
        cols = []
        try:
            cols = connectors.columns(eng, c.type, cfg, o["kind"], o["name"])
        except connectors.ConnectorError as e:
            raise HTTPException(400, f"{c.name}: {e}") from e
        sch = (cfg.get("schema") or "").strip()
        objects.append({"conn_id": c.id, "conn_name": c.name, "dialect": connectors.TYPES[c.type]["dialect"],
                        "kind": o["kind"], "name": o["name"], "full_name": f"{sch}.{o['name']}" if sch else o["name"],
                        "columns": cols,
                        "sample": connectors.sample(eng, cfg, o["name"]) if o["kind"] != "procs" else {}})

    def run(conn_id: int, sql: str) -> dict:
        _c, _cfg, eng = conn_engine(db, conn_id)
        return connectors.run_query(eng, sql)

    try:
        result = agent.build(model_cfg(m), kind, prompts, objects, run, previous=prev, change=body.change)
    except llm.LLMError as e:
        audit(db, request, u, "dev_build", outcome="error", detail=str(e))
        raise HTTPException(502, str(e)) from e
    except connectors.ConnectorError as e:
        audit(db, request, u, "dev_build", outcome="error", detail=str(e))
        raise HTTPException(400, str(e)) from e
    draft_id = uuid.uuid4().hex
    agent.save_draft(draft_id, {"owner": u.username, "kind": kind, "prompts": prompts + ([body.change] if body.change else []),
                                "objects": raw, "title": result["title"], "queries": result["queries"],
                                "template": result["template"], "notes": result["notes"], "created": time.time()})
    names = {c.id: c.name for c in db.scalars(select(Connection))}
    audit(db, request, u, "dev_build", target=result["title"], detail=f"{len(result['queries'])} queries")
    return {"draft_id": draft_id, "title": result["title"], "kind": kind,
            "queries": [{"name": q["name"], "connection": names.get(q["connection_id"], "?"), "sql": q["sql"]}
                        for q in result["queries"]],
            "html": agent.with_csp(agent.inject_data(result["template"], result["data"]))}


class PublishIn(BaseModel):
    draft_id: str
    title: str = Field(min_length=1, max_length=200)
    folder_id: int | None = None
    visibility: str = "everyone"
    refresh: str = "open"


@app.post("/api/dev/publish")
def dev_publish(body: PublishIn, request: Request, u: User = Depends(builder_dep), db=Depends(get_db)):
    d = agent.load_draft(body.draft_id)
    if not d or d.get("owner") != u.username:
        raise HTTPException(404, "That draft is no longer available. Build it again.")
    check_target_folder(db, u, d["kind"], body.folder_id)
    names = {c.id: c.name for c in db.scalars(select(Connection))}
    sources = sorted({names.get(q["connection_id"], "?") for q in d["queries"]})
    i = Item(root=d["kind"], folder_id=body.folder_id, title=body.title.strip(), kind="generated",
             visibility="restricted" if body.visibility == "restricted" else "everyone", source=" + ".join(sources),
             created_by=u.username,
             meta=json.dumps({"queries": d["queries"], "refresh": body.refresh if body.refresh in ("open", "hourly", "daily") else "open",
                              "objects": d["objects"], "notes": d.get("notes", "")}))
    db.add(i)
    db.flush()
    folder = storage.item_dir(i.id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "template.html").write_text(d["template"], encoding="utf-8")
    db.commit()
    render_generated(db, i, force=True)          # first data + text for the Assistant
    audit(db, request, u, "publish_generated", target=i.title, detail=i.source)
    return {"id": i.id}


# ================================================================== live services, health, portal
@app.get("/api/auth/check")
def auth_check(request: Request, item: int | None = None, db=Depends(get_db)):
    """For a live report service behind the same host: is the browser in front
    of it signed in and allowed to open item <item>? Needs SERVICE_AUTH_KEY."""
    given = request.headers.get("x-service-key", "")
    if not SERVICE_KEY or not secrets_equal(given, SERVICE_KEY):
        raise HTTPException(404, "Not found.")
    u = load_user(request, db)
    if not u:
        return Response(status_code=401, headers={"X-Auth-Reason": "not-signed-in"})
    i = db.get(Item, item) if item else None
    if not i:
        return Response(status_code=403, headers={"X-Auth-Reason": "not-registered"})
    if not access(db, u).item_open(i):
        audit(db, request, u, "service_check", target=i.title, outcome="deny")
        return Response(status_code=403, headers={"X-Auth-Reason": "not-granted"})
    return Response(status_code=204, headers={"X-Auth-User": u.username})


def secrets_equal(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())


@app.get("/health")
def health(db=Depends(get_db)):
    db.execute(select(1))
    return {"ok": True, "version": __version__}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def portal():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
