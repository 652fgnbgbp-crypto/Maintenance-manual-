"""Turn uploaded files into indexed pages.

PDFs (including scanned manuals with an OCR text layer) keep their word positions, which is what
lets us draw highlights on a schematic later. Photos/scans are converted to one-page PDFs so they go
through the same path. Plain-text notes files are split into page-sized chunks.
"""
import re
import shutil
import threading
import uuid
from pathlib import Path

import pymupdf

from . import config, db

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
TEXT_EXT = {".txt", ".md", ".csv", ".log"}

_render_lock = threading.Lock()

# ------------------------------------------------------------------ text clean-up


def _undouble(token: str) -> str:
    """OCR of bold type sometimes doubles every letter: 'SSEERRIIAALL' -> 'SERIAL'."""
    if len(token) >= 6 and len(token) % 2 == 0 and all(token[i] == token[i + 1] for i in range(0, len(token), 2)):
        return token[::2]
    return token


def clean_text(text: str) -> str:
    return " ".join(_undouble(t) for t in text.split())


# ------------------------------------------------------------------ footer metadata

ATA_RE = re.compile(r"(\d{2})\s?-\s?(\d)\s?(\d)\s?-\s?(\d)\s?(\d)")
PAGE_RE = re.compile(r"\b(?:PAGE|FAGE|Page|PAG E|FAG E)\s*([0-9](?:\s?[0-9]){0,2})\b")
SERIAL_RE = re.compile(r"SERIAL\s*([0-9OoIl]{3,4}(?:\s*(?:AND ON|AND|TO:?\s*[0-9OoIl]{2,4}))?)", re.I)
WIRE_RE = re.compile(r"^[A-Z]{1,2}\d{1,4}[A-Z]{1,2}\d{1,2}N?$")
COMPONENT_RE = re.compile(r"^\(?[A-Z]{1,3}\d{1,4}\)?$")


def _digits(s: str) -> str:
    """Fix OCR letter/digit confusion inside serial numbers only ('O1O0' -> '0100'), not in words like 'ON'."""
    def fix(m):
        t = m.group(0)
        if any(c.isdigit() for c in t):
            return t.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
        return t
    return re.sub(r"[0-9OoIl]{2,4}", fix, s)


def parse_page_metadata(words: list, width: float, height: float) -> dict:
    """Best-effort read of the ATA number, page number, subject and effectivity block at the
    bottom of a manual page. OCR is noisy, so every field may come back None."""
    foot = sorted((w for w in words if w[1] > height * 0.86), key=lambda w: (round(w[1] / 6), w[0]))
    foot_text = " ".join(w[4] for w in foot)
    meta = {"ata": None, "page_label": None, "subject": None, "effectivity": None}

    atas = ATA_RE.findall(foot_text)
    if atas:
        a = atas[-1]
        meta["ata"] = f"{a[0]}-{a[1]}{a[2]}-{a[3]}{a[4]}"

    pages = PAGE_RE.findall(foot_text)
    if pages:
        label = pages[-1].replace(" ", "")
        if label:
            meta["page_label"] = label

    serials = SERIAL_RE.findall(foot_text)
    if serials:
        meta["effectivity"] = "SERIAL " + _digits(re.sub(r"\s+", " ", serials[-1]).upper()).replace(":", "")

    # Subject: words left of "SERIAL" on the same line of the footer.
    serial_words = [w for w in foot if w[4].upper().startswith("SERIAL")]
    if serial_words:
        s = serial_words[-1]
        line = [w for w in foot if abs(w[1] - s[1]) < 8 and w[2] <= s[0] + 1]
        subject = " ".join(w[4] for w in sorted(line, key=lambda w: w[0]))
        subject = re.sub(r"[|~_]+", " ", subject).strip(" .-")
        if 2 < len(subject) < 80:
            meta["subject"] = clean_text(subject)
    return meta


def classify_page(words: list, meta: dict) -> str:
    subject = (meta.get("subject") or "").upper()
    if "LOCATION" in subject:
        return "location"
    if "INDEX" in subject:
        return "index"
    tokens = [w[4].strip("()[],.") for w in words]
    wires = sum(1 for t in tokens if WIRE_RE.match(t))
    if wires >= 4:
        return "schematic"
    long_words = sum(1 for t in tokens if t.isalpha() and len(t) > 3 and t.islower())
    if long_words > 80:
        return "text"
    return "other"


# ------------------------------------------------------------------ ingestion


def file_path(doc: dict) -> Path:
    return config.FILES_DIR / doc["stored_name"]


def create_document(src: Path, original_name: str, title: str | None, category: str) -> int:
    """Copy the upload into the library and register it. Processing happens in process_document."""
    config.ensure_dirs()
    ext = Path(original_name).suffix.lower()
    if ext == ".pdf":
        kind = "pdf"
    elif ext in IMAGE_EXT:
        kind = "image"
    elif ext in TEXT_EXT:
        kind = "text"
    else:
        raise ValueError(f"Unsupported file type '{ext}'. Use PDF, an image, or a .txt/.md file.")

    stored = f"{uuid.uuid4().hex}{'.pdf' if kind in ('pdf', 'image') else ext}"
    dest = config.FILES_DIR / stored
    if kind == "image":
        img = pymupdf.open(src)
        pdf_bytes = img.convert_to_pdf()
        img.close()
        dest.write_bytes(pdf_bytes)
    else:
        shutil.copyfile(src, dest)

    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO documents(title, filename, stored_name, kind, category) VALUES(?, ?, ?, ?, ?)",
            (title or Path(original_name).stem, original_name, stored, kind, category),
        )
        return cur.lastrowid


def _chunk_text(text: str, size: int = 3500) -> list[str]:
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if len(cur) + len(para) > size and cur:
            parts.append(cur.strip())
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        parts.append(cur.strip())
    return parts or [""]


def process_document(doc_id: int) -> None:
    doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    try:
        path = file_path(doc)
        rows = []
        if doc["kind"] == "text":
            text = path.read_text(errors="replace")
            for i, chunk in enumerate(_chunk_text(text), start=1):
                rows.append(dict(page_no=i, text=chunk, page_type="text", width=None, height=None,
                                 ata=None, page_label=str(i), subject=None, effectivity=None))
        else:
            pdf = pymupdf.open(path)
            for i, page in enumerate(pdf, start=1):
                words = page.get_text("words")
                meta = parse_page_metadata(words, page.rect.width, page.rect.height)
                text = clean_text(page.get_text("text"))
                rows.append(dict(page_no=i, text=text, page_type=classify_page(words, meta),
                                 width=page.rect.width, height=page.rect.height, **meta))
            pdf.close()

        with db.tx() as conn:
            for r in rows:
                cur = conn.execute(
                    "INSERT INTO pages(doc_id, page_no, text, ata, page_label, subject, effectivity, page_type, width, height) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (doc_id, r["page_no"], r["text"], r["ata"], r["page_label"], r["subject"], r["effectivity"],
                     r["page_type"], r["width"], r["height"]),
                )
                db.index_page(conn, cur.lastrowid)
            conn.execute("UPDATE documents SET status='ready', page_count=? WHERE id=?", (len(rows), doc_id))
    except Exception as e:  # surfaced in the library UI
        with db.tx() as conn:
            conn.execute("UPDATE documents SET status='error', error=? WHERE id=?", (str(e), doc_id))
        raise


def ingest_file(src: Path, original_name: str, title: str | None = None, category: str = "manual") -> int:
    doc_id = create_document(src, original_name, title, category)
    process_document(doc_id)
    return doc_id


# ------------------------------------------------------------------ page access


def get_page(doc_id: int, page_no: int) -> dict | None:
    return db.one(
        "SELECT p.*, d.title AS doc_title, d.kind AS doc_kind, d.stored_name FROM pages p "
        "JOIN documents d ON d.id = p.doc_id WHERE p.doc_id = ? AND p.page_no = ?",
        (doc_id, page_no),
    )


def page_words(doc_id: int, page_no: int) -> tuple[list, float, float]:
    """OCR words with boxes in PDF points: (x0, y0, x1, y1, text, ...)."""
    doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if doc is None or doc["kind"] == "text":
        return [], 0, 0
    pdf = pymupdf.open(file_path(doc))
    try:
        page = pdf[page_no - 1]
        return page.get_text("words"), page.rect.width, page.rect.height
    finally:
        pdf.close()


def render_page(doc_id: int, page_no: int, long_edge: int) -> bytes | None:
    """PNG of a page scaled so its longer side is `long_edge` px. Cached on disk."""
    doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if doc is None or doc["kind"] == "text":
        return None
    cache = config.CACHE_DIR / f"{doc['stored_name']}.{page_no}.{long_edge}.png"
    if cache.exists():
        return cache.read_bytes()
    with _render_lock:
        pdf = pymupdf.open(file_path(doc))
        try:
            page = pdf[page_no - 1]
            zoom = long_edge / max(page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY)
            data = pix.tobytes("png")
        finally:
            pdf.close()
    cache.write_bytes(data)
    return data
