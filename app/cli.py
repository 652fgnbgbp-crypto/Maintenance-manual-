"""Command-line import for large batches of files.

    python -m app.cli import manuals/*.pdf notes/*.md --category manual --enrich
    python -m app.cli enrich 3
"""
import argparse
from pathlib import Path

from . import db, enrich, ingest


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="Add files to the library")
    imp.add_argument("files", nargs="+", type=Path)
    imp.add_argument("--category", default="manual")
    imp.add_argument("--enrich", action="store_true", help="Have the AI read every page after import")
    en = sub.add_parser("enrich", help="Have the AI read every page of a document already in the library")
    en.add_argument("doc_id", type=int)
    args = ap.parse_args()

    if args.cmd == "import":
        for f in args.files:
            doc_id = ingest.ingest_file(f, f.name, None, args.category)
            doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
            print(f"doc {doc_id}: {doc['title']} — {doc['page_count']} pages")
            if args.enrich:
                enrich.enrich_document(doc_id)
                print(f"  AI read: {db.one('SELECT enrich_status FROM documents WHERE id=?', (doc_id,))['enrich_status']}")
    elif args.cmd == "enrich":
        enrich.enrich_document(args.doc_id)


if __name__ == "__main__":
    main()
