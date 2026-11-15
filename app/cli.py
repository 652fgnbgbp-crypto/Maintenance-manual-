"""Vantage command line (for Torwood staff running the service).

    python -m app.cli create-org "Example Air Services" --aircraft "Cessna 441 Conquest"
    python -m app.cli orgs
    python -m app.cli add-user tech@example.com --org 2 --name "A. Technician" [--admin] [--platform-admin]
    python -m app.cli set-password tech@example.com
    python -m app.cli import manuals/*.pdf --org 2 --category manual [--enrich]
    python -m app.cli enrich 3
"""
import argparse
import getpass
from pathlib import Path

from . import auth, db, enrich, ingest


def _password() -> str:
    password = getpass.getpass("Password (8+ characters): ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return password


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    co = sub.add_parser("create-org", help="Create a subscribing organisation")
    co.add_argument("name")
    co.add_argument("--aircraft", default="", help="Aircraft type, e.g. 'Cessna 441 Conquest'")
    co.add_argument("--status", default="active", choices=["trial", "active", "suspended"])
    sub.add_parser("orgs", help="List organisations")
    st = sub.add_parser("set-status", help="Set an organisation's subscription status")
    st.add_argument("org_id", type=int)
    st.add_argument("status", choices=["trial", "active", "suspended"])
    au = sub.add_parser("add-user", help="Create a sign-in")
    au.add_argument("email")
    au.add_argument("--org", type=int, default=1)
    au.add_argument("--name", default="")
    au.add_argument("--admin", action="store_true", help="Organisation administrator (manages their team)")
    au.add_argument("--platform-admin", action="store_true", help="Torwood staff: can create organisations")
    pw = sub.add_parser("set-password", help="Reset a user's password")
    pw.add_argument("email")
    imp = sub.add_parser("import", help="Add files to an organisation's library")
    imp.add_argument("files", nargs="+", type=Path)
    imp.add_argument("--org", type=int, default=1)
    imp.add_argument("--category", default="manual")
    imp.add_argument("--enrich", action="store_true", help="Have the AI read every page after import")
    en = sub.add_parser("enrich", help="Have the AI read every page of a document already in the library")
    en.add_argument("doc_id", type=int)
    args = ap.parse_args()
    db.connect()
    auth.init()

    if args.cmd == "create-org":
        org_id = auth.create_org(args.name, args.aircraft, args.status)
        print(f"Organisation {org_id}: {args.name}. Add its first admin with: "
              f"python -m app.cli add-user EMAIL --org {org_id} --admin")
    elif args.cmd == "orgs":
        for o in db.query("SELECT o.*, (SELECT COUNT(*) FROM users u WHERE u.org_id = o.id) AS users "
                          "FROM organisations o ORDER BY id"):
            print(f"{o['id']:>4}  {o['status']:<9}  {o['users']:>3} users  {o['name']}  [{o['aircraft_type']}]")
    elif args.cmd == "set-status":
        with db.tx() as conn:
            conn.execute("UPDATE organisations SET status = ? WHERE id = ?", (args.status, args.org_id))
        print(f"Organisation {args.org_id} is now {args.status}.")
    elif args.cmd == "add-user":
        auth.add_user(args.email, _password(), args.name, args.admin, args.org, args.platform_admin)
        print(f"Created {args.email} in organisation {args.org}.")
    elif args.cmd == "set-password":
        if not auth.set_password(args.email, _password()):
            raise SystemExit(f"No user {args.email}.")
    elif args.cmd == "import":
        for f in args.files:
            doc_id = ingest.ingest_file(f, f.name, None, args.category, args.org)
            doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
            print(f"doc {doc_id}: {doc['title']}, {doc['page_count']} pages")
            if args.enrich:
                enrich.enrich_document(doc_id)
                print(f"  AI read: {db.one('SELECT enrich_status FROM documents WHERE id=?', (doc_id,))['enrich_status']}")
    elif args.cmd == "enrich":
        enrich.enrich_document(args.doc_id)


if __name__ == "__main__":
    main()
