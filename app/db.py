"""SQLite storage: organisations, documents, pages (with full-text index), the Fix Log, diagnoses.

Every document, Fix Log entry and diagnosis belongs to one organisation; all queries that serve a
signed-in user are filtered by that user's organisation.
"""
import json
import sqlite3
import threading
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS organisations (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    aircraft_type TEXT NOT NULL DEFAULT '',
    plan          TEXT NOT NULL DEFAULT 'standard',
    status        TEXT NOT NULL DEFAULT 'active',     -- trial | active | suspended
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
    org_id        INTEGER NOT NULL DEFAULT 1 REFERENCES organisations(id),
    title         TEXT NOT NULL,
    filename      TEXT NOT NULL,
    stored_name   TEXT NOT NULL,
    kind          TEXT NOT NULL,           -- pdf | image | text
    category      TEXT NOT NULL DEFAULT 'manual',
    page_count    INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'processing',  -- processing | ready | error
    enrich_status TEXT NOT NULL DEFAULT 'none',        -- none | running | done | error
    enrich_done   INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    uploaded_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pages (
    id          INTEGER PRIMARY KEY,
    doc_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no     INTEGER NOT NULL,          -- 1-based physical page in the file
    text        TEXT NOT NULL DEFAULT '',  -- OCR / extracted text
    ata         TEXT,                      -- e.g. 24-30-02
    page_label  TEXT,                      -- printed page number, e.g. 101
    subject     TEXT,                      -- circuit / subject name from the footer
    effectivity TEXT,                      -- e.g. SERIAL 0001 TO 0259
    page_type   TEXT,                      -- schematic | text | index | location | other
    ai_summary  TEXT,
    ai_terms    TEXT,                      -- components, wire numbers, breakers found by AI
    width       REAL,
    height      REAL,
    UNIQUE(doc_id, page_no)
);

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    text, subject, ata, ai_summary, ai_terms, title,
    tokenize='unicode61 remove_diacritics 2'
);

-- The Fix Log: problems engineers have solved, and how. Shown in diagnoses as "Previously resolved".
CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY,
    org_id      INTEGER NOT NULL DEFAULT 1 REFERENCES organisations(id),
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '',
    serial      TEXT NOT NULL DEFAULT '',  -- aircraft serial / registration the entry applies to
    source      TEXT NOT NULL DEFAULT 'manual',  -- manual | diagnosis
    symptom     TEXT NOT NULL DEFAULT '',  -- what was reported
    checks      TEXT NOT NULL DEFAULT '',  -- what was checked / found
    fix         TEXT NOT NULL DEFAULT '',  -- what fixed it
    system      TEXT NOT NULL DEFAULT '',  -- system / ATA chapter
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title, body, tags, tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id          INTEGER PRIMARY KEY,
    org_id      INTEGER NOT NULL DEFAULT 1 REFERENCES organisations(id),
    created_by  TEXT NOT NULL DEFAULT '',
    cross_org   INTEGER NOT NULL DEFAULT 0,      -- used every operator's Fix Log: platform admins only
    question    TEXT NOT NULL,
    serial      TEXT NOT NULL DEFAULT '',
    result      TEXT,          -- JSON
    trace       TEXT,          -- JSON list of steps the AI took
    resolution  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.ensure_dirs()
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL")
        _migrate(_conn)
        _conn.executescript(SCHEMA)
        if _conn.execute("SELECT COUNT(*) FROM organisations").fetchone()[0] == 0:
            _conn.execute("INSERT INTO organisations(id, name, aircraft_type) VALUES(1, ?, ?)",
                          (config.DEFAULT_ORG_NAME, config.AIRCRAFT_TYPE))
            _conn.commit()
    return _conn


# Columns added after the first release; older databases get them on start-up.
_NEW_COLUMNS = {
    "documents": [("org_id", "INTEGER NOT NULL DEFAULT 1")],
    "notes": [("org_id", "INTEGER NOT NULL DEFAULT 1"), ("symptom", "TEXT NOT NULL DEFAULT ''"),
              ("checks", "TEXT NOT NULL DEFAULT ''"), ("fix", "TEXT NOT NULL DEFAULT ''"),
              ("system", "TEXT NOT NULL DEFAULT ''"), ("created_by", "TEXT NOT NULL DEFAULT ''")],
    "diagnoses": [("org_id", "INTEGER NOT NULL DEFAULT 1"), ("created_by", "TEXT NOT NULL DEFAULT ''"),
                  ("cross_org", "INTEGER NOT NULL DEFAULT 0")],
    "users": [("org_id", "INTEGER NOT NULL DEFAULT 1"), ("is_platform_admin", "INTEGER NOT NULL DEFAULT 0")],
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in _NEW_COLUMNS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table not created yet; SCHEMA creates it with every column
        for name, decl in cols:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def reset_connection() -> None:
    """Used by tests after changing config.DB_PATH."""
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = None


@contextmanager
def tx():
    with _lock:
        conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def query(sql: str, params=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in connect().execute(sql, params).fetchall()]


def one(sql: str, params=()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


# ---------------------------------------------------------------- full-text index

def _page_fts_values(page: dict, title: str) -> tuple:
    return (
        page.get("text") or "",
        page.get("subject") or "",
        page.get("ata") or "",
        page.get("ai_summary") or "",
        page.get("ai_terms") or "",
        title,
    )


def index_page(conn: sqlite3.Connection, page_id: int) -> None:
    """(Re)index one page in the full-text table."""
    conn.execute("DELETE FROM pages_fts WHERE rowid = ?", (page_id,))
    row = conn.execute(
        "SELECT p.*, d.title AS doc_title FROM pages p JOIN documents d ON d.id = p.doc_id WHERE p.id = ?",
        (page_id,),
    ).fetchone()
    if row is None:
        return
    page = dict(row)
    conn.execute(
        "INSERT INTO pages_fts(rowid, text, subject, ata, ai_summary, ai_terms, title) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (page_id, *_page_fts_values(page, page["doc_title"])),
    )


def index_note(conn: sqlite3.Connection, note_id: int) -> None:
    conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (note_id,))
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is not None:
        conn.execute(
            "INSERT INTO notes_fts(rowid, title, body, tags) VALUES(?, ?, ?, ?)",
            (note_id, row["title"], row["body"], row["tags"]),
        )


def delete_document(doc_id: int) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM pages_fts WHERE rowid IN (SELECT id FROM pages WHERE doc_id = ?)", (doc_id,))
        conn.execute("DELETE FROM pages WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def compose_fix_body(symptom: str, checks: str, fix: str) -> str:
    parts = [f"Symptom: {symptom}"]
    if checks:
        parts.append(f"Checked / found: {checks}")
    parts.append(f"Fixed by: {fix}")
    return "\n\n".join(parts)


def add_fix(org_id: int, symptom: str, fix: str, checks: str = "", system: str = "", aircraft: str = "",
            tags: str = "", created_by: str = "", source: str = "manual", title: str = "") -> int:
    """Add a Fix Log entry: a problem an engineer solved and what solved it."""
    title = title or (symptom if len(symptom) <= 90 else symptom[:87] + "…")
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO notes(org_id, title, body, tags, serial, source, symptom, checks, fix, system, created_by) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (org_id, title, compose_fix_body(symptom, checks, fix), tags, aircraft, source, symptom, checks, fix,
             system, created_by),
        )
        index_note(conn, cur.lastrowid)
        return cur.lastrowid


def add_note(title: str, body: str, tags: str = "", serial: str = "", source: str = "manual", org_id: int = 1) -> int:
    """Free-text entry (older form). New entries should use add_fix."""
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO notes(org_id, title, body, tags, serial, source) VALUES(?, ?, ?, ?, ?, ?)",
            (org_id, title, body, tags, serial, source),
        )
        index_note(conn, cur.lastrowid)
        return cur.lastrowid


def delete_note(note_id: int, org_id: int) -> bool:
    with tx() as conn:
        if conn.execute("SELECT 1 FROM notes WHERE id = ? AND org_id = ?", (note_id, org_id)).fetchone() is None:
            return False
        conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (note_id,))
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return True


def org_document(org_id: int, doc_id: int) -> dict | None:
    """The document, only if it belongs to this organisation."""
    return one("SELECT * FROM documents WHERE id = ? AND org_id = ?", (doc_id, org_id))


def save_diagnosis(question: str, serial: str, result: dict, trace: list, org_id: int = 1, created_by: str = "",
                   cross_org: bool = False) -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO diagnoses(org_id, created_by, cross_org, question, serial, result, trace) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (org_id, created_by, int(cross_org), question, serial, json.dumps(result), json.dumps(trace)),
        )
        return cur.lastrowid
