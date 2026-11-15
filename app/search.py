"""Full-text search over manual pages and troubleshooting notes (SQLite FTS5, BM25 ranking)."""
import re

from . import db

STOP = {
    "the", "a", "an", "and", "or", "is", "are", "was", "were", "be", "to", "of", "in", "on", "at", "for",
    "with", "when", "it", "its", "this", "that", "not", "no", "does", "do", "did", "has", "have", "had",
    "but", "if", "then", "from", "by", "as", "will", "won't", "wont", "doesn't", "after", "before", "i",
    "we", "my", "our", "aircraft", "plane",
}


def fts_query(text: str) -> str:
    """Turn free text into an FTS5 OR-query with prefix matching on longer words."""
    terms = []
    for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", text):
        t = raw.lower()
        if t in STOP or len(t) < 2:
            continue
        if "-" in t:  # ATA numbers like 24-30-02 -> phrase
            terms.append('"' + t.replace("-", " ") + '"')
            continue
        terms.append(f'"{t}"*' if len(t) >= 4 else f'"{t}"')
    seen, out = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return " OR ".join(out[:24])


def search_pages(text: str, limit: int = 8, doc_id: int | None = None, page_type: str | None = None) -> list[dict]:
    q = fts_query(text)
    if not q:
        return []
    sql = (
        "SELECT p.id, p.doc_id, p.page_no, p.ata, p.page_label, p.subject, p.effectivity, p.page_type, "
        "p.ai_summary, d.title AS doc_title, bm25(pages_fts, 1.0, 4.0, 4.0, 3.0, 3.0, 1.0) AS score, "
        "snippet(pages_fts, 0, '[', ']', ' … ', 18) AS snippet "
        "FROM pages_fts JOIN pages p ON p.id = pages_fts.rowid JOIN documents d ON d.id = p.doc_id "
        "WHERE pages_fts MATCH ?"
    )
    params: list = [q]
    if doc_id is not None:
        sql += " AND p.doc_id = ?"
        params.append(doc_id)
    if page_type:
        sql += " AND p.page_type = ?"
        params.append(page_type)
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    try:
        return db.query(sql, params)
    except Exception:
        return []


def search_notes(text: str, limit: int = 6) -> list[dict]:
    q = fts_query(text)
    if not q:
        return []
    try:
        return db.query(
            "SELECT n.*, bm25(notes_fts) AS score FROM notes_fts JOIN notes n ON n.id = notes_fts.rowid "
            "WHERE notes_fts MATCH ? ORDER BY score LIMIT ?",
            (q, limit),
        )
    except Exception:
        return []
