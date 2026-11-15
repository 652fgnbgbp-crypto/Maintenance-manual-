"""Optional AI enrichment pass: Claude reads each page image once and records a clean title, ATA
number, effectivity, a one-paragraph summary and the components / wire numbers / breakers on it.

Scanned manuals have noisy OCR, so this makes search much better. It is also the only way
photos of diagrams or handwritten notes become searchable.
"""
import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import anthropic

from . import config, db, ingest

SCHEMA = {
    "type": "object",
    "properties": {
        "page_type": {"type": "string", "enum": ["schematic", "text", "index", "location", "other"]},
        "subject": {"type": "string", "description": "Circuit or subject name, e.g. 'Battery Control'. Empty if none."},
        "ata": {"type": "string", "description": "ATA chapter-section-subject like 24-30-02, empty if not printed."},
        "printed_page": {"type": "string"},
        "effectivity": {"type": "string", "description": "Serial range as printed, e.g. 'SERIAL 0001 TO 0259'."},
        "summary": {"type": "string", "description": "2-4 sentences: what the page shows and how the circuit works."},
        "components": {"type": "array", "items": {"type": "string"},
                       "description": "Components with reference designators, e.g. 'BATTERY NO. 1 CONTACTOR (K45)'."},
        "wire_numbers": {"type": "array", "items": {"type": "string"}},
        "circuit_breakers": {"type": "array", "items": {"type": "string"}},
        "cross_references": {"type": "array", "items": {"type": "string"},
                             "description": "Other circuits/ATA pages this page points to."},
    },
    "required": ["page_type", "subject", "ata", "printed_page", "effectivity", "summary", "components",
                 "wire_numbers", "circuit_breakers", "cross_references"],
    "additionalProperties": False,
}

PROMPT = """This is page {page_no} of "{title}", maintenance data for the {aircraft}. \
Read the page image (the OCR text below is noisy; trust the image) and fill in the fields. \
Copy designators, wire numbers and labels exactly as printed. Leave a field empty rather than guess.

OCR text:
{ocr}"""


def enrich_page(client: anthropic.Anthropic, doc: dict, page: dict) -> dict:
    content: list = []
    png = ingest.render_page(doc["id"], page["page_no"], config.MODEL_IMAGE_LONG_EDGE)
    if png:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": base64.standard_b64encode(png).decode()}})
    content.append({"type": "text", "text": PROMPT.format(page_no=page["page_no"], title=doc["title"],
                                                           aircraft=config.AIRCRAFT_TYPE,
                                                           ocr=(page["text"] or "")[:6000])})
    response = client.messages.create(
        model=config.ENRICH_MODEL,
        max_tokens=16000,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("refused")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def _apply(page: dict, data: dict) -> None:
    terms = data["components"] + data["wire_numbers"] + data["circuit_breakers"] + data["cross_references"]
    with db.tx() as conn:
        conn.execute(
            "UPDATE pages SET page_type=?, subject=COALESCE(NULLIF(?, ''), subject), ata=COALESCE(NULLIF(?, ''), ata), "
            "page_label=COALESCE(NULLIF(?, ''), page_label), effectivity=COALESCE(NULLIF(?, ''), effectivity), "
            "ai_summary=?, ai_terms=? WHERE id=?",
            (data["page_type"], data["subject"].upper(), data["ata"], data["printed_page"], data["effectivity"].upper(),
             data["summary"], "; ".join(terms), page["id"]),
        )
        db.index_page(conn, page["id"])
        conn.execute("UPDATE documents SET enrich_done = enrich_done + 1 WHERE id = ?", (page["doc_id"],))


def enrich_document(doc_id: int, workers: int = 4, client: anthropic.Anthropic | None = None) -> None:
    client = client or anthropic.Anthropic(max_retries=6)
    doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    pages = db.query("SELECT * FROM pages WHERE doc_id = ? AND ai_summary IS NULL ORDER BY page_no", (doc_id,))
    with db.tx() as conn:
        conn.execute("UPDATE documents SET enrich_status='running', enrich_done=page_count-? WHERE id=?",
                     (len(pages), doc_id))
    errors = []

    def work(page):
        try:
            _apply(page, enrich_page(client, doc, page))
        except Exception as e:  # keep going; the page stays searchable by its OCR text
            errors.append(f"page {page['page_no']}: {e}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, pages))
    with db.tx() as conn:
        conn.execute("UPDATE documents SET enrich_status=?, error=? WHERE id=?",
                     ("error" if errors else "done", "; ".join(errors[:5]) or None, doc_id))


def start_background(doc_id: int) -> None:
    threading.Thread(target=enrich_document, args=(doc_id,), daemon=True).start()
