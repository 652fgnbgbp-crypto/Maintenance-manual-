"""Vantage command line: bulk import, AI page reading, and user accounts.

    python -m app.cli import manuals/*.pdf notes/*.md --category manual --enrich
    python -m app.cli enrich 3
    python -m app.cli add-user tech@example.com --name "A. Technician"
"""
import argparse
from pathlib import Path

import getpass

from . import auth, db, enrich, ingest


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="Add files to the library")
    imp.add_argument("files", nargs="+", type=Path)
    imp.add_argument("--category", default="manual")
    imp.add_argument("--enrich", action="store_true", help="Have the AI read every page after import")
    en = sub.add_parser("enrich", help="Have the AI read every page of a document already in the library")
    en.add_argument("doc_id", type=int)
    au = sub.add_parser("add-user", help="Create a sign-in for Vantage")
    au.add_argument("email")
    au.add_argument("--name", default="")
    au.add_argument("--admin", action="store_true")
    pw = sub.add_parser("set-password", help="Reset a user's password")
    pw.add_argument("email")
    args = ap.parse_args()
    db.connect()
    auth.init()

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
    elif args.cmd in ("add-user", "set-password"):
        password = getpass.getpass("Password (8+ characters): ")
        if password != getpass.getpass("Repeat password: "):
            raise SystemExit("Passwords do not match.")
        if args.cmd == "add-user":
            auth.add_user(args.email, password, args.name, args.admin)
            print(f"Created {args.email}. Sign-in is now required.")
        elif not auth.set_password(args.email, password):
            raise SystemExit(f"No user {args.email}.")


if __name__ == "__main__":
    main()
