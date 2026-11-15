"""Data connections: SQL Server, Oracle, PostgreSQL, Supabase, pgvector,
Redshift, MySQL / MariaDB and SQLite through SQLAlchemy. Testing, browsing the
schema (tables, views, procedures, columns) and running read-only queries."""
from __future__ import annotations

import datetime as dt
import decimal
import re
import threading
import time
import uuid

import sqlalchemy as sa
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

TYPES: dict[str, dict] = {
    "sqlserver": {"label": "SQL Server", "abbr": "MS", "port": 1433, "dialect": "Microsoft SQL Server (T-SQL)",
                  "fields": ["host", "port", "database", "user", "password", "schema"]},
    "oracle":    {"label": "Oracle DB", "abbr": "OR", "port": 1521, "dialect": "Oracle SQL",
                  "fields": ["host", "port", "service", "user", "password", "schema"]},
    "postgres":  {"label": "PostgreSQL", "abbr": "PG", "port": 5432, "dialect": "PostgreSQL",
                  "fields": ["host", "port", "database", "user", "password", "sslmode", "schema"]},
    "supabase":  {"label": "Supabase", "abbr": "SB", "port": 5432, "dialect": "PostgreSQL (Supabase)",
                  "fields": ["host", "port", "database", "user", "password", "schema"]},
    "pgvector":  {"label": "pgvector", "abbr": "PV", "port": 5432, "dialect": "PostgreSQL with pgvector",
                  "fields": ["host", "port", "database", "user", "password", "sslmode", "schema"]},
    "redshift":  {"label": "Amazon Redshift", "abbr": "RS", "port": 5439, "dialect": "Amazon Redshift (PostgreSQL-like)",
                  "fields": ["host", "port", "database", "user", "password", "schema"]},
    "mysql":     {"label": "MySQL", "abbr": "MY", "port": 3306, "dialect": "MySQL",
                  "fields": ["host", "port", "database", "user", "password"]},
    "mariadb":   {"label": "MariaDB", "abbr": "MA", "port": 3306, "dialect": "MariaDB",
                  "fields": ["host", "port", "database", "user", "password"]},
    "sqlite":    {"label": "SQLite", "abbr": "SL", "port": 0, "dialect": "SQLite",
                  "fields": ["path"]},
}
COMING_SOON = ["Snowflake", "BigQuery", "Databricks", "ClickHouse", "MongoDB", "REST / OData API"]
SECRET_FIELDS = {"password"}
MAX_ROWS = 1000


class ConnectorError(Exception):
    pass


def _url(kind: str, c: dict) -> URL | str:
    port = int(c.get("port") or TYPES[kind]["port"] or 0) or None
    user, pw, host = c.get("user") or None, c.get("password") or None, c.get("host") or None
    if kind == "sqlserver":
        return URL.create("mssql+pymssql", user, pw, host, port, c.get("database") or None)
    if kind == "oracle":
        return URL.create("oracle+oracledb", user, pw, host, port, query={"service_name": c.get("service") or ""})
    if kind in ("postgres", "supabase", "pgvector", "redshift"):
        ssl = c.get("sslmode") or ("require" if kind in ("supabase", "redshift") else "prefer")
        return URL.create("postgresql+psycopg", user, pw, host, port, c.get("database") or "postgres",
                          query={"sslmode": ssl})
    if kind in ("mysql", "mariadb"):
        return URL.create("mysql+pymysql", user, pw, host, port, c.get("database") or None,
                          query={"charset": "utf8mb4"})
    if kind == "sqlite":
        path = (c.get("path") or "").replace("\\", "/")
        return f"sqlite:///file:{path}?mode=ro&uri=true"       # opened read-only
    raise ConnectorError(f"Unknown connection type: {kind}")


def _connect_args(kind: str) -> dict:
    if kind == "sqlserver":
        return {"login_timeout": 10, "timeout": 120}
    if kind == "oracle":
        return {"tcp_connect_timeout": 10}
    if kind in ("postgres", "pgvector"):
        # the server itself refuses writes on these sessions
        return {"connect_timeout": 10, "options": "-c default_transaction_read_only=on -c statement_timeout=120000"}
    if kind in ("supabase", "redshift"):
        return {"connect_timeout": 10}
    if kind in ("mysql", "mariadb"):
        return {"connect_timeout": 10, "read_timeout": 120}
    return {}


def make_engine(kind: str, cfg: dict, pooled: bool = True) -> sa.Engine:
    if kind not in TYPES:
        raise ConnectorError(f"Unknown connection type: {kind}")
    if kind == "sqlite" and not cfg.get("path"):
        raise ConnectorError("Enter the database file path.")
    if kind != "sqlite" and not cfg.get("host"):
        raise ConnectorError("Enter the host.")
    kw: dict = {"connect_args": _connect_args(kind), "pool_pre_ping": True}
    if pooled:
        kw.update(pool_size=3, max_overflow=2, pool_recycle=1800)
    else:
        kw["poolclass"] = NullPool
    if kind == "sqlite":
        kw = {"poolclass": NullPool, "connect_args": {"uri": True}}
    return sa.create_engine(_url(kind, cfg), **kw)


_engines: dict[int, tuple[str, sa.Engine]] = {}
_lock = threading.Lock()


def engine_for(conn_id: int, kind: str, cfg: dict) -> sa.Engine:
    """One pooled engine per saved connection, rebuilt when its settings change."""
    sig = repr((kind, sorted(cfg.items())))
    with _lock:
        hit = _engines.get(conn_id)
        if hit and hit[0] == sig:
            return hit[1]
        if hit:
            hit[1].dispose()
        eng = make_engine(kind, cfg)
        _engines[conn_id] = (sig, eng)
        return eng


def forget(conn_id: int) -> None:
    with _lock:
        hit = _engines.pop(conn_id, None)
    if hit:
        hit[1].dispose()


def clean_error(e: Exception, cfg: dict | None = None) -> str:
    msg = str(getattr(e, "orig", None) or e)
    msg = msg.split("\n(Background on this error")[0]
    if cfg and cfg.get("password"):
        msg = msg.replace(str(cfg["password"]), "******")
    return re.sub(r"\s+", " ", msg).strip()[:400]


def test(kind: str, cfg: dict) -> dict:
    eng = make_engine(kind, cfg, pooled=False)
    started = time.perf_counter()
    try:
        with eng.connect() as c:
            c.exec_driver_sql("SELECT 1 FROM DUAL" if kind == "oracle" else "SELECT 1")
            ver = c.dialect.server_version_info
        return {"ok": True, "ms": round((time.perf_counter() - started) * 1000),
                "version": ".".join(str(v) for v in ver) if ver else ""}
    except Exception as e:  # noqa: BLE001 - every driver raises its own types
        raise ConnectorError(clean_error(e, cfg)) from e
    finally:
        eng.dispose()


# ---------------------------------------------------------------- schema
def _schema(insp, cfg: dict) -> str | None:
    return (cfg.get("schema") or "").strip() or None


def procedures(eng: sa.Engine, kind: str, schema: str | None) -> list[str]:
    sql, params = None, {}
    if kind == "sqlserver":
        sql = "SELECT name FROM sys.procedures WHERE SCHEMA_NAME(schema_id) = COALESCE(:s, SCHEMA_NAME()) ORDER BY name"
        params = {"s": schema}
    elif kind in ("postgres", "supabase", "pgvector", "redshift"):
        sql = ("SELECT DISTINCT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
               "WHERE n.nspname = COALESCE(:s, current_schema()) ORDER BY 1")
        params = {"s": schema}
    elif kind in ("mysql", "mariadb"):
        sql = "SELECT ROUTINE_NAME FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = DATABASE() ORDER BY 1"
    elif kind == "oracle":
        sql = ("SELECT DISTINCT OBJECT_NAME FROM ALL_PROCEDURES WHERE OWNER = COALESCE(:s, USER) "
               "AND OBJECT_TYPE IN ('PROCEDURE', 'FUNCTION') ORDER BY 1")
        params = {"s": schema.upper() if schema else None}
    if not sql:
        return []
    try:
        with eng.connect() as c:
            return [r[0] for r in c.execute(sa.text(sql), params).fetchmany(1000)]
    except Exception:  # noqa: BLE001 - a login without catalogue rights just sees none
        return []


def schema(eng: sa.Engine, kind: str, cfg: dict) -> dict:
    try:
        insp = sa.inspect(eng)
        sch = _schema(insp, cfg)
        return {
            "schema": sch or insp.default_schema_name or "",
            "tables": sorted(insp.get_table_names(schema=sch))[:1000],
            "views": sorted(insp.get_view_names(schema=sch))[:1000],
            "procs": procedures(eng, kind, sch),
        }
    except Exception as e:  # noqa: BLE001
        raise ConnectorError(clean_error(e, cfg)) from e


def columns(eng: sa.Engine, kind: str, cfg: dict, obj_kind: str, name: str) -> list[dict]:
    sch = (cfg.get("schema") or "").strip() or None
    try:
        if obj_kind == "procs":
            return _proc_params(eng, kind, sch, name)
        insp = sa.inspect(eng)
        cols = insp.get_columns(name, schema=sch)
        pk, fk = set(), set()
        try:
            pk = set(insp.get_pk_constraint(name, schema=sch).get("constrained_columns") or [])
            for f in insp.get_foreign_keys(name, schema=sch):
                fk.update(f.get("constrained_columns") or [])
        except Exception:  # noqa: BLE001 - views have no keys
            pass
        return [{"name": c["name"], "type": str(c["type"]),
                 "key": "PK" if c["name"] in pk else "FK" if c["name"] in fk else ""} for c in cols]
    except Exception as e:  # noqa: BLE001
        raise ConnectorError(clean_error(e, cfg)) from e


def _proc_params(eng, kind, sch, name) -> list[dict]:
    sql, params = None, {"n": name, "s": sch}
    if kind == "sqlserver":
        sql = ("SELECT name, TYPE_NAME(user_type_id), is_output FROM sys.parameters "
               "WHERE object_id = OBJECT_ID(QUOTENAME(COALESCE(:s, SCHEMA_NAME())) + '.' + QUOTENAME(:n)) ORDER BY parameter_id")
    elif kind in ("postgres", "supabase", "pgvector", "redshift"):
        sql = ("SELECT COALESCE(parameter_name, ''), data_type, parameter_mode FROM information_schema.parameters "
               "WHERE specific_schema = COALESCE(:s, current_schema()) AND specific_name LIKE :n || '\\_%' ORDER BY ordinal_position")
    elif kind in ("mysql", "mariadb"):
        sql = ("SELECT PARAMETER_NAME, DATA_TYPE, PARAMETER_MODE FROM information_schema.PARAMETERS "
               "WHERE SPECIFIC_SCHEMA = DATABASE() AND SPECIFIC_NAME = :n AND PARAMETER_NAME IS NOT NULL ORDER BY ORDINAL_POSITION")
        params = {"n": name}
    elif kind == "oracle":
        sql = ("SELECT ARGUMENT_NAME, DATA_TYPE, IN_OUT FROM ALL_ARGUMENTS WHERE OBJECT_NAME = :n "
               "AND OWNER = COALESCE(:s, USER) AND ARGUMENT_NAME IS NOT NULL ORDER BY POSITION")
        params = {"n": name.upper(), "s": sch.upper() if sch else None}
    if not sql:
        return []
    with eng.connect() as c:
        rows = c.execute(sa.text(sql), params).fetchall()
    out = []
    for r in rows:
        mode = r[2]
        mode = ("OUT" if mode else "IN") if isinstance(mode, (bool, int)) else str(mode or "IN").upper()
        out.append({"name": r[0], "type": str(r[1]), "key": mode})
    return out


# ---------------------------------------------------------------- read-only queries
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|upsert|drop|alter|create|truncate|grant|revoke|exec|execute|call|into|"
    r"attach|detach|pragma|copy|vacuum|shutdown|kill|backup|restore|outfile|dumpfile|sp_\w+|xp_\w+|dbms_\w+|"
    r"utl_\w+|pg_sleep|lo_import|lo_export|load_file)\b", re.I)


def check_read_only(sql: str) -> str:
    """Return the statement if it is one SELECT / WITH query; raise otherwise.
    Read-only logins are still the real protection; this is a second fence."""
    stripped = re.sub(r"'(?:[^']|'')*'", "''", sql)              # string literals
    stripped = re.sub(r"--[^\n]*", " ", stripped)
    stripped = re.sub(r"(?s)/\*.*?\*/", " ", stripped)
    body = stripped.strip().rstrip(";").strip()
    if ";" in body:
        raise ConnectorError("Only one statement is allowed.")
    if not re.match(r"(?is)^\s*\(?\s*(select|with)\b", body):
        raise ConnectorError("Only SELECT queries are allowed.")
    bad = _FORBIDDEN.search(body)
    if bad:
        raise ConnectorError(f"'{bad.group(0).upper()}' is not allowed in a read-only query.")
    return sql.strip().rstrip(";").strip()


def to_json(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v)[:64].hex()
    return str(v)


def run_query(eng: sa.Engine, sql: str, max_rows: int = MAX_ROWS) -> dict:
    sql = check_read_only(sql)
    started = time.perf_counter()
    try:
        with eng.connect() as c:
            res = c.exec_driver_sql(sql)
            cols = list(res.keys())
            rows = [[to_json(v) for v in r] for r in res.fetchmany(max_rows + 1)]
            c.rollback()
    except Exception as e:  # noqa: BLE001
        raise ConnectorError(clean_error(e)) from e
    return {"columns": cols, "rows": rows[:max_rows], "truncated": len(rows) > max_rows,
            "ms": round((time.perf_counter() - started) * 1000)}


def sample(eng: sa.Engine, cfg: dict, name: str, n: int = 5) -> dict:
    """A few rows of a table or view, written in the right dialect by SQLAlchemy."""
    sch = (cfg.get("schema") or "").strip() or None
    stmt = sa.select(sa.text("*")).select_from(sa.table(name, schema=sch)).limit(n)
    try:
        with eng.connect() as c:
            res = c.execute(stmt)
            cols = list(res.keys())
            rows = [[to_json(v) for v in r] for r in res.fetchmany(n)]
            c.rollback()
        return {"columns": cols, "rows": rows}
    except Exception:  # noqa: BLE001 - samples are a nice-to-have
        return {"columns": [], "rows": []}
