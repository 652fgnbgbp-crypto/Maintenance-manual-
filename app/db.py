"""SQLite storage: documents, pages (with full-text index), troubleshooting notes, diagnoses."""
import json
import sqlite3
import threading
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '',
    serial      TEXT NOT NULL DEFAULT '',  -- aircraft serial / registration the note applies to
    source      TEXT NOT NULL DEFAULT 'manual',  -- manual | diagnosis
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title, body, tags, tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id          INTEGER PRIMARY KEY,
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
        _conn.executescript(SCHEMA)
    return _conn


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


def add_note(title: str, body: str, tags: str = "", serial: str = "", source: str = "manual") -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO notes(title, body, tags, serial, source) VALUES(?, ?, ?, ?, ?)",
            (title, body, tags, serial, source),
        )
        index_note(conn, cur.lastrowid)
        return cur.lastrowid


def delete_note(note_id: int) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (note_id,))
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))


def save_diagnosis(question: str, serial: str, result: dict, trace: list) -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO diagnoses(question, serial, result, trace) VALUES(?, ?, ?, ?)",
            (question, serial, json.dumps(result), json.dumps(trace)),
        )
        return cur.lastrowid
