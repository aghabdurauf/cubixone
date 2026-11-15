"""Uploaded report files: saving a .html or a .zip package safely, and pulling
plain text out of HTML for the Assistant."""
from __future__ import annotations

import html as htmllib
import re
import shutil
import zipfile
from pathlib import Path

from .db import DATA_DIR

ITEMS_DIR = DATA_DIR / "items"
ITEMS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED = {".html", ".htm", ".css", ".js", ".mjs", ".json", ".csv", ".txt", ".svg", ".png", ".jpg", ".jpeg",
           ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".map", ".xml", ".pdf"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".idea", ".vscode", "__MACOSX"}


class UploadError(ValueError):
    pass


def item_dir(item_id: int) -> Path:
    return ITEMS_DIR / str(item_id)


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\- ]", "_", name).strip() or "file"


def store(upload: Path, filename: str, dest: Path, max_package_bytes: int) -> str:
    """Put an upload into dest and return the entry page's path inside it."""
    dest.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lower()
    if ext in (".html", ".htm"):
        name = _safe_name(Path(filename).name)
        shutil.copyfile(upload, dest / name)
        return name
    if ext != ".zip":
        raise UploadError("Upload an .html file or a .zip package.")
    try:
        zf = zipfile.ZipFile(upload)
    except zipfile.BadZipFile as e:
        raise UploadError("That .zip file could not be read.") from e
    with zf:
        if sum(i.file_size for i in zf.infolist()) > max_package_bytes:
            raise UploadError(f"The package is larger than {max_package_bytes // 1048576} MB once extracted.")
        root = dest.resolve()
        pages: list[str] = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = [p for p in info.filename.replace("\\", "/").split("/") if p not in ("", ".")]
            if not parts or any(p == ".." or p in SKIP_DIRS or p.startswith(".") for p in parts):
                continue                      # zip-slip guard + junk a zipped project would carry
            if Path(parts[-1]).suffix.lower() not in ALLOWED:
                continue
            target = root.joinpath(*parts).resolve()
            if root not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            rel = "/".join(parts)
            if rel.lower().endswith((".html", ".htm")):
                pages.append(rel)
    if not pages:
        raise UploadError("The package has no .html page.")
    for p in pages:
        if p.lower() in ("index.html", "index.htm"):
            return p
    return sorted(pages, key=lambda p: (p.count("/"), p))[0]


def safe_path(item_id: int, rel: str) -> Path | None:
    root = item_dir(item_id).resolve()
    target = (root / rel).resolve()
    return target if root in target.parents and target.is_file() else None


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6]|table)>", "\n", raw)
    raw = re.sub(r"(?i)</t[dh]>", " \t ", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = htmllib.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    return re.sub(r"\n\s*\n+", "\n", raw).strip()


def extract_text(dest: Path, limit: int = 300_000) -> str:
    chunks, total = [], 0
    for page in sorted(dest.rglob("*.htm*"))[:30]:
        text = html_to_text(page.read_text(encoding="utf-8", errors="ignore"))
        chunks.append(text)
        total += len(text)
        if total > limit:
            break
    return "\n\n".join(chunks)[:limit]


def remove(item_id: int) -> None:
    shutil.rmtree(item_dir(item_id), ignore_errors=True)
