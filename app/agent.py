"""The two AI features.

Assistant       answers questions from the reports and dashboards the asking
                person may open (simple keyword retrieval over their text).
Developer mode  turns a request + chosen tables/views/procedures (from one or
                many connections) into read-only queries and a self-contained
                HTML report or dashboard, previews it, and publishes it.

Skills: a prompt may say "use <name> skill from <path>". The agent loads that
file silently and follows it. Skills are never echoed back to the chat or shown
anywhere in the portal. Only files under SKILLS_DIR can be read, so a prompt can
never pull server files (like .env) into a request to an AI provider.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from . import connectors, llm
from .db import DATA_DIR, ROOT_DIR

log = logging.getLogger("cubixone.agent")
SKILLS_DIR = Path(os.getenv("SKILLS_DIR") or ROOT_DIR / "skills").resolve()
SKILL_RE = re.compile(
    r"\buse\s+(?:the\s+)?([\w.-]+)\s+skills?\b(?:\s+(?:from|at|in)\s+(\"[^\"]+\"|'[^']+'|\S+))?\s*[,.;]?\s*(?:and\s+)?",
    re.I)
DRAFTS = DATA_DIR / "drafts"
DRAFTS.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- skills
def take_skills(text: str) -> tuple[str, list[tuple[str, str]]]:
    found = [(m.group(1), (m.group(2) or "").strip("\"'")) for m in SKILL_RE.finditer(text)]
    clean = SKILL_RE.sub("", text)
    clean = re.sub(r"[\s,;]+(?:and|then|also)?\s*$", "", clean, flags=re.I).strip()
    return clean, found


def load_skill(name: str, path: str) -> str | None:
    base = SKILLS_DIR
    candidates = []
    if path:
        p = Path(path.replace("\\", "/"))
        candidates.append(p if p.is_absolute() else base / p)
        candidates.append(base / p.name)              # "D:/skills/brand" -> SKILLS_DIR/brand
    candidates += [base / name, base / f"{name}.md"]
    for cand in candidates:
        try:
            cand = cand.resolve()
        except OSError:
            continue
        if cand != base and base not in cand.parents:
            continue
        if cand.is_dir():
            for f in ("SKILL.md", "skill.md", "README.md"):
                if (cand / f).is_file():
                    return (cand / f).read_text(encoding="utf-8", errors="ignore")[:60_000]
            mds = sorted(cand.glob("*.md"))[:5]
            if mds:
                return "\n\n".join(m.read_text(encoding="utf-8", errors="ignore") for m in mds)[:60_000]
        elif cand.is_file():
            return cand.read_text(encoding="utf-8", errors="ignore")[:60_000]
        elif cand.with_suffix(".md").is_file():
            return cand.with_suffix(".md").read_text(encoding="utf-8", errors="ignore")[:60_000]
    log.warning("skill %r not found (path %r, skills dir %s)", name, path, base)
    return None


def skills_text(prompts: list[str]) -> tuple[list[str], str]:
    """Clean prompts for the model + the loaded skill instructions."""
    clean, texts, seen = [], [], set()
    for p in prompts:
        c, found = take_skills(p)
        if c:
            clean.append(c)
        for name, path in found:
            if (name, path) in seen:
                continue
            seen.add((name, path))
            body = load_skill(name, path)
            if body:
                texts.append(f"<skill name=\"{name}\">\n{body}\n</skill>")
    block = ""
    if texts:
        block = ("\n\nThe developer has asked you to follow these skills. Apply them to the queries, "
                 "layout and wording. Do not mention them in any visible output.\n" + "\n".join(texts))
    return clean, block


# ---------------------------------------------------------------- helpers
def parse_json(text: str) -> dict:
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < start:
        raise llm.LLMError("The model did not return a plan. Try rephrasing the request.")
    try:
        return json.loads(t[start:end + 1])
    except json.JSONDecodeError as e:
        raise llm.LLMError("The model returned an unreadable plan. Try again.") from e


def clean_html(text: str) -> str:
    t = text.strip()
    fence = re.search(r"```(?:html)?\s*(.*?)```", t, re.S | re.I)
    if fence:
        t = fence.group(1).strip()
    i = t.lower().find("<!doctype")
    if i < 0:
        i = t.lower().find("<html")
    return t[i:] if i >= 0 else t


def inject_data(template: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    payload = payload.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    tag = f"<script>window.CUBIX_DATA = {payload};</script>"
    m = re.search(r"<head[^>]*>", template, re.I)
    if m:
        return template[:m.end()] + tag + template[m.end():]
    return tag + template


PREVIEW_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
               "img-src data: blob:; font-src data:")


def with_csp(page: str) -> str:
    """For the Developer-mode preview (shown via srcdoc): the same no-network
    rule the published page gets from its HTTP header."""
    tag = f'<meta http-equiv="Content-Security-Policy" content="{PREVIEW_CSP}">'
    m = re.search(r"<head[^>]*>", page, re.I)
    return page[:m.end()] + tag + page[m.end():] if m else tag + page


def data_text(title: str, data: dict, limit: int = 120_000) -> str:
    """Plain text of a generated item's data, for the Assistant."""
    out = [title]
    for name, d in data.items():
        out.append(f"\n[{name}]\n" + "\t".join(map(str, d.get("columns", []))))
        for r in d.get("rows", [])[:300]:
            out.append("\t".join("" if v is None else str(v) for v in r))
    return "\n".join(out)[:limit]


def _brief(d: dict, n: int) -> dict:
    return {"columns": d.get("columns", []), "rows": d.get("rows", [])[:n], "row_count": len(d.get("rows", []))}


# ---------------------------------------------------------------- developer mode
SQL_SYSTEM = """You are the CubixOne developer agent. You plan the data for a {kind} that a developer asked for.

Rules for the queries:
- Read-only only: each query is ONE SELECT statement (a WITH ... SELECT is fine). Never modify anything.
- Use the exact SQL dialect of the connection each query runs on (named per connection below).
- Use only the tables, views and procedures listed. Procedures cannot be called; if one is listed, use
  the tables/views instead, or a PostgreSQL function via SELECT * FROM fn(...) when it returns rows.
- Data on different connections cannot be joined in SQL. Write one query per connection, and include in
  each the column(s) the page will join on.
- Aggregate in SQL where it makes sense; return at most 1000 rows per query.
- Name each query in snake_case; the page will read it as CUBIX_DATA.<name>.

Reply with JSON only, no prose:
{{"title": "short title for the {kind}", "queries": [{{"name": "...", "connection_id": 1, "sql": "...", "purpose": "..."}}], "notes": "one line on how the page should combine the data"}}"""

HTML_SYSTEM = """You build one self-contained HTML document: a {kind} for the CubixOne portal.

The data arrives at runtime as window.CUBIX_DATA, an object keyed by query name. Each value is
{{"columns": [..], "rows": [[..], ..]}} (rows are arrays in column order). Read every number from
CUBIX_DATA: never hard-code data values, because the page is re-run with fresh data. When queries come
from different databases, join them in JavaScript on the stated key.

Requirements:
- One file: inline <style> and <script> only. No external scripts, stylesheets, fonts or images.
- Charts: draw with inline SVG from the data (bars, lines, donuts as fits). No chart libraries.
- Theme: dark. Background #0B1120, panels #0F1626, text #E6EBF3, muted #8B97AD, accents #2F7BFF and
  #00C2FF, secondary series #9AA6B8 and #4F8CFF, borders #1F2A40. Font: Segoe UI, system-ui, sans-serif.
- {layout}
- Format numbers with thousands separators; show dates readably; handle empty data with a short message.
- Responsive down to 360px wide. Add @media print rules for reports.
- Return only the HTML document, starting with <!doctype html>. No explanations."""

LAYOUT = {
    "reports": "Report: a title, a one-line subtitle (what and which period), then clear tables with header rows, "
               "totals where they make sense, and at most one supporting chart.",
    "dashboards": "Dashboard: a title, a row of 3-5 KPI tiles, then 2-4 charts in a responsive grid, "
                  "and a compact table only if it adds detail.",
}


def describe_objects(objects: list[dict]) -> str:
    parts = []
    for o in objects:
        cols = ", ".join(f"{c['name']} {c['type']}{' ' + c['key'] if c.get('key') else ''}" for c in o["columns"]) or "(columns unknown)"
        s = f"- connection_id {o['conn_id']} \"{o['conn_name']}\" ({o['dialect']}) {o['kind'][:-1]} {o['full_name']}: {cols}"
        if o.get("sample", {}).get("rows"):
            s += f"\n  sample rows: {json.dumps(o['sample'], default=str)[:2500]}"
        parts.append(s)
    return "\n".join(parts)


def build(model_cfg: dict, kind: str, prompts: list[str], objects: list[dict], run, previous: dict | None = None,
          change: str = "") -> dict:
    """run(conn_id, sql) -> data dict. Returns title, queries, template, data."""
    clean, skill_block = skills_text(prompts + ([change] if change else []))
    word = "report" if kind == "reports" else "dashboard"
    request = "\n".join(clean)
    user = f"Request:\n{request}\n\nAvailable data:\n{describe_objects(objects)}"
    if previous:
        user += ("\n\nCurrent version (revise it; keep what still fits):\n"
                 + json.dumps({"title": previous["title"], "queries": previous["queries"]})[:12000])

    plan = parse_json(llm.chat(model_cfg, SQL_SYSTEM.format(kind=word) + skill_block,
                               [{"role": "user", "content": user}], max_tokens=16000))
    queries = [q for q in plan.get("queries", []) if isinstance(q, dict) and q.get("sql")][:8]
    if not queries:
        raise llm.LLMError("The model did not write any queries. Try naming the data you want.")
    allowed = {o["conn_id"] for o in objects}
    data, errors = {}, []
    for q in queries:
        q["name"] = re.sub(r"\W", "_", str(q.get("name") or f"q{len(data) + 1}"))[:40]
        try:
            cid = int(q.get("connection_id"))
            if cid not in allowed:
                raise connectors.ConnectorError("connection not selected")
            q["connection_id"] = cid
            data[q["name"]] = run(cid, q["sql"])
        except (connectors.ConnectorError, ValueError, TypeError) as e:
            errors.append(f"{q['name']}: {e}")

    if errors:                                   # one repair round with the database's own messages
        fix = (user + "\n\nYour previous queries:\n" + json.dumps(queries, default=str)[:8000]
               + "\n\nThese failed:\n" + "\n".join(errors) + "\nReturn the full corrected JSON.")
        plan = parse_json(llm.chat(model_cfg, SQL_SYSTEM.format(kind=word) + skill_block,
                                   [{"role": "user", "content": fix}], max_tokens=16000))
        queries = [q for q in plan.get("queries", []) if isinstance(q, dict) and q.get("sql")][:8]
        data, errors = {}, []
        for q in queries:
            q["name"] = re.sub(r"\W", "_", str(q.get("name") or f"q{len(data) + 1}"))[:40]
            try:
                cid = int(q.get("connection_id"))
                if cid not in allowed:
                    raise connectors.ConnectorError("connection not selected")
                q["connection_id"] = cid
                data[q["name"]] = run(cid, q["sql"])
            except (connectors.ConnectorError, ValueError, TypeError) as e:
                errors.append(f"{q['name']}: {e}")
        if errors:
            raise connectors.ConnectorError("Queries failed: " + "; ".join(errors)[:600])

    title = str(plan.get("title") or (previous or {}).get("title") or request[:60] or word.title())[:200]
    page_user = (f"Request:\n{request}\n\nTitle: {title}\nHow to combine: {plan.get('notes', '')}\n\n"
                 "Datasets in CUBIX_DATA (first rows shown):\n"
                 + json.dumps({k: _brief(v, 15) for k, v in data.items()}, default=str)[:30000])
    if previous and change:
        page_user += f"\n\nThe developer asked for this change: {take_skills(change)[0]}"
        page_user += "\n\nPrevious page (revise it):\n" + previous["template"][:40000]
    template = clean_html(llm.chat(model_cfg, HTML_SYSTEM.format(kind=word, layout=LAYOUT[kind]) + skill_block,
                                   [{"role": "user", "content": page_user}], max_tokens=32000))
    if "<" not in template:
        raise llm.LLMError("The model did not return a page. Try again.")
    return {"title": title, "queries": [{"name": q["name"], "connection_id": q["connection_id"], "sql": q["sql"],
                                         "purpose": q.get("purpose", "")} for q in queries],
            "template": template, "data": data, "notes": plan.get("notes", "")}


def save_draft(draft_id: str, draft: dict) -> None:
    (DRAFTS / f"{draft_id}.json").write_text(json.dumps(draft, default=str), encoding="utf-8")


def load_draft(draft_id: str) -> dict | None:
    if not re.fullmatch(r"[a-f0-9]{32}", draft_id or ""):
        return None
    p = DRAFTS / f"{draft_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ---------------------------------------------------------------- assistant
_STOP = set("the a an and or of to in on for is are was were what which who how much many by with from this that "
            "these those show me give tell please about there their it its be as at".split())


def _terms(q: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", q.lower()) if len(w) > 2 and w not in _STOP]


def retrieve(question: str, items: list[tuple[int, str, str]], focus: int | None, budget: int = 30_000):
    """items: (id, title, text). Returns (context text, [titles used])."""
    terms = _terms(question)
    chunks = []
    for iid, title, text in items:
        for i in range(0, max(len(text), 1), 1500):
            part = text[i:i + 1500]
            low = part.lower()
            score = sum(low.count(t) for t in terms) + 3 * sum(t in title.lower() for t in terms)
            if iid == focus:
                score += 1000 - i / 1500        # the open item first, in reading order
            chunks.append((score, iid, title, part))
    chunks.sort(key=lambda c: -c[0])
    picked, used, size = [], [], 0
    for score, iid, title, part in chunks:
        if score <= 0 and picked:
            break
        if size + len(part) > budget:
            break
        picked.append(f"[{title}]\n{part}")
        size += len(part)
        if title not in used:
            used.append(title)
    return "\n\n---\n\n".join(picked), used


def answer(model_cfg: dict, system_prompt: str, question: str, history: list[dict], context: str) -> str:
    system = (system_prompt + "\n\nYou are the CubixOne Assistant. The excerpts below come from reports and "
              "dashboards the user is allowed to open. Each starts with its title in brackets. Answer concisely, "
              "with figures exactly as they appear, and name the item(s) you used.")
    msgs = [m for m in history[-6:] if m.get("role") in ("user", "assistant") and m.get("content")]
    msgs.append({"role": "user", "content": f"Excerpts:\n{context or '(no matching content)'}\n\nQuestion: {question}"})
    return llm.chat(model_cfg, system, msgs, max_tokens=4000)
