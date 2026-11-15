"""Full-text search over an organisation's manual pages and Fix Log (SQLite FTS5, BM25 ranking)."""
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


def search_pages(text: str, limit: int = 8, doc_id: int | None = None, page_type: str | None = None,
                 org_id: int = 1) -> list[dict]:
    q = fts_query(text)
    if not q:
        return []
    sql = (
        "SELECT p.id, p.doc_id, p.page_no, p.ata, p.page_label, p.subject, p.effectivity, p.page_type, "
        "p.ai_summary, d.title AS doc_title, bm25(pages_fts, 1.0, 4.0, 4.0, 3.0, 3.0, 1.0) AS score, "
        "snippet(pages_fts, 0, '[', ']', ' … ', 18) AS snippet "
        "FROM pages_fts JOIN pages p ON p.id = pages_fts.rowid JOIN documents d ON d.id = p.doc_id "
        "WHERE pages_fts MATCH ? AND d.org_id = ?"
    )
    params: list = [q, org_id]
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


def search_notes(text: str, limit: int = 6, org_id: int | None = 1) -> list[dict]:
    """Search one organisation's Fix Log. org_id=None searches every organisation: only for the
    Torwood platform-admin view, never for a subscriber's request."""
    q = fts_query(text)
    if not q:
        return []
    sql = ("SELECT n.*, o.name AS org_name, bm25(notes_fts) AS score FROM notes_fts "
           "JOIN notes n ON n.id = notes_fts.rowid JOIN organisations o ON o.id = n.org_id WHERE notes_fts MATCH ?")
    params: list = [q]
    if org_id is not None:
        sql += " AND n.org_id = ?"
        params.append(org_id)
    try:
        return db.query(sql + " ORDER BY score LIMIT ?", (*params, limit))
    except Exception:
        return []
