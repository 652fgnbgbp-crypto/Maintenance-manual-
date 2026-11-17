"""Vantage web server: library management, search, the Fix Log, teams, and streaming AI diagnosis.

Vantage is a subscription service: each organisation sees only its own documents, Fix Log and
diagnoses. Every request is scoped to the signed-in user's organisation.
"""
import json
import os
import queue
import shutil
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import mimetypes

# Windows reads file types from the registry, where .js is sometimes registered as text/plain;
# browsers may then refuse to run the app's script. Set the types explicitly.
for _ext, _type in ((".js", "text/javascript"), (".css", "text/css"), (".svg", "image/svg+xml"),
                    (".webmanifest", "application/manifest+json"), (".png", "image/png")):
    mimetypes.add_type(_type, _ext)

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, auth, config, db, enrich, highlight, ingest, search

@asynccontextmanager
async def lifespan(_app):
    config.ensure_dirs()
    db.connect()
    auth.init()
    # AI reading interrupted by a restart can be started again from the library
    with db.tx() as conn:
        conn.execute("UPDATE documents SET enrich_status='none' WHERE enrich_status='running'")
    yield


app = FastAPI(title="Vantage", lifespan=lifespan)
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# Paths reachable without signing in: the sign-in page and what it (and the home-screen icon) needs.
PUBLIC = ("/login", "/api/login", "/static/", "/manifest.webmanifest", "/favicon.ico")


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    request.state.user = auth.user_for(request.cookies.get(auth.COOKIE))
    user = request.state.user
    if user and user["org_status"] == "suspended" and path.startswith("/api/") and path not in ("/api/info", "/api/logout"):
        return JSONResponse({"detail": "This organisation's Vantage subscription is not active. "
                                       "Contact Torwood Aerospace Group."}, status_code=402)
    if path.startswith(PUBLIC) or user or not auth.login_required():
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Please sign in."}, status_code=401)
    return RedirectResponse("/login")


@app.get("/login")
def login_page():
    return FileResponse(STATIC / "login.html")


class LoginBody(BaseModel):
    email: str
    password: str


@app.post("/api/login")
def do_login(body: LoginBody, request: Request):
    token = auth.login(body.email, body.password)
    if token is None:
        raise HTTPException(401, "Email or password is incorrect.")
    res = JSONResponse({"ok": True})
    res.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                   secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
    return res


@app.post("/api/logout")
def do_logout(request: Request):
    auth.logout(request.cookies.get(auth.COOKIE))
    res = JSONResponse({"ok": True})
    res.delete_cookie(auth.COOKIE)
    return res


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC / "icons" / "icon-192.png", media_type="image/png")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ------------------------------------------------------------------ request context


def ctx(request: Request) -> dict:
    """Who is asking, and for which organisation. With no users set up (local use) everything is organisation 1."""
    user = request.state.user
    if user:
        return {"org_id": user["org_id"], "name": user["name"] or user["email"], "is_admin": bool(user["is_admin"]),
                "is_platform_admin": bool(user["is_platform_admin"]), "user_id": user["id"]}
    return {"org_id": 1, "name": "", "is_admin": True, "is_platform_admin": not auth.login_required(), "user_id": None}


def org_row(org_id: int) -> dict:
    return db.one("SELECT * FROM organisations WHERE id = ?", (org_id,))


def doc_or_404(c: dict, doc_id: int) -> dict:
    doc = db.org_document(c["org_id"], doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found.")
    return doc


def require_admin(c: dict) -> None:
    if not c["is_admin"]:
        raise HTTPException(403, "Only an organisation administrator can do that.")


@app.get("/api/info")
def info(request: Request):
    c = ctx(request)
    org = org_row(c["org_id"])
    count = lambda sql: db.one(sql, (c["org_id"],))["n"]  # noqa: E731
    return {
        "user": {"name": c["name"], "email": request.state.user["email"]} if request.state.user else None,
        "is_admin": c["is_admin"], "is_platform_admin": c["is_platform_admin"],
        "login_required": auth.login_required(),
        "organisation": org["name"], "org_status": org["status"],
        "aircraft_type": org["aircraft_type"] or config.AIRCRAFT_TYPE,
        "model": config.DIAGNOSIS_MODEL,
        "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
        "documents": count("SELECT COUNT(*) AS n FROM documents WHERE org_id = ?"),
        "pages": count("SELECT COUNT(*) AS n FROM pages p JOIN documents d ON d.id = p.doc_id WHERE d.org_id = ?"),
        "fixes": count("SELECT COUNT(*) AS n FROM notes WHERE org_id = ?"),
    }


# ------------------------------------------------------------------ documents


@app.get("/api/documents")
def list_documents(request: Request):
    return db.query("SELECT * FROM documents WHERE org_id = ? ORDER BY id DESC", (ctx(request)["org_id"],))


def _process_upload(doc_id: int, run_enrich: bool) -> None:
    try:
        ingest.process_document(doc_id)
    except Exception:
        return
    if run_enrich:
        enrich.enrich_document(doc_id)


@app.post("/api/documents")
async def upload_document(
    request: Request,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    category: str = Form("manual"),
    enrich_with_ai: bool = Form(False),
):
    c = ctx(request)
    suffix = Path(file.filename or "upload").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)
    try:
        doc_id = ingest.create_document(tmp_path, file.filename or "upload", title.strip() or None, category, c["org_id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        tmp_path.unlink(missing_ok=True)
    background.add_task(_process_upload, doc_id, enrich_with_ai)
    return db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int, request: Request):
    doc = doc_or_404(ctx(request), doc_id)
    db.delete_document(doc_id)
    ingest.file_path(doc).unlink(missing_ok=True)
    for f in config.CACHE_DIR.glob(f"{doc['stored_name']}.*"):
        f.unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/documents/{doc_id}/enrich")
def start_enrich(doc_id: int, request: Request):
    doc = doc_or_404(ctx(request), doc_id)
    if doc["enrich_status"] == "running":
        return doc
    enrich.start_background(doc_id)
    return {**doc, "enrich_status": "running"}


@app.get("/api/documents/{doc_id}/pages")
def list_pages(doc_id: int, request: Request):
    doc_or_404(ctx(request), doc_id)
    return db.query(
        "SELECT id, doc_id, page_no, ata, page_label, subject, effectivity, page_type, ai_summary IS NOT NULL AS enriched "
        "FROM pages WHERE doc_id = ? ORDER BY page_no", (doc_id,))


@app.get("/api/pages/{doc_id}/{page_no}")
def page_detail(doc_id: int, page_no: int, request: Request):
    doc = doc_or_404(ctx(request), doc_id)
    page = ingest.get_page(doc_id, page_no)
    if page is None:
        raise HTTPException(404)
    return {**page, "page_count": doc["page_count"], "has_image": page["doc_kind"] != "text"}


@app.get("/api/pages/{doc_id}/{page_no}/image")
def page_image(doc_id: int, page_no: int, request: Request):
    doc_or_404(ctx(request), doc_id)
    png = ingest.render_page(doc_id, page_no, config.VIEW_IMAGE_LONG_EDGE)
    if png is None:
        raise HTTPException(404)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


class LocateBody(BaseModel):
    terms: list[str]


@app.post("/api/pages/{doc_id}/{page_no}/locate")
def locate(doc_id: int, page_no: int, body: LocateBody, request: Request):
    doc_or_404(ctx(request), doc_id)
    return highlight.locate_terms(doc_id, page_no, [t for t in body.terms if t.strip()][:20])


@app.get("/api/search")
def search_all(q: str, request: Request, limit: int = 20):
    org_id = ctx(request)["org_id"]
    return {"pages": search.search_pages(q, limit=limit, org_id=org_id),
            "fixes": search.search_notes(q, limit=10, org_id=org_id)}


# ------------------------------------------------------------------ Fix Log


class FixBody(BaseModel):
    symptom: str
    fix: str
    checks: str = ""
    system: str = ""
    aircraft: str = ""
    tags: str = ""


@app.get("/api/fixes")
def list_fixes(request: Request, q: str = ""):
    org_id = ctx(request)["org_id"]
    if q.strip():
        return search.search_notes(q, limit=50, org_id=org_id)
    return db.query("SELECT * FROM notes WHERE org_id = ? ORDER BY id DESC", (org_id,))


@app.post("/api/fixes")
def create_fix(body: FixBody, request: Request):
    c = ctx(request)
    if not body.symptom.strip() or not body.fix.strip():
        raise HTTPException(400, "Enter the symptom and what fixed it.")
    nid = db.add_fix(c["org_id"], body.symptom.strip(), body.fix.strip(), body.checks.strip(), body.system.strip(),
                     body.aircraft.strip(), body.tags.strip(), created_by=c["name"])
    return db.one("SELECT * FROM notes WHERE id = ?", (nid,))


@app.delete("/api/fixes/{note_id}")
def remove_fix(note_id: int, request: Request):
    if not db.delete_note(note_id, ctx(request)["org_id"]):
        raise HTTPException(404)
    return {"ok": True}


# ------------------------------------------------------------------ diagnosis


class DiagnoseBody(BaseModel):
    question: str
    serial: str = ""
    session_id: str | None = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.post("/api/diagnose")
def diagnose(body: DiagnoseBody, request: Request):
    c = ctx(request)
    if not body.question.strip():
        raise HTTPException(400, "Describe the problem first.")
    org = org_row(c["org_id"])
    # Only a signed-in Torwood platform admin draws on every operator's Fix Log.
    all_fixes = bool(request.state.user) and c["is_platform_admin"]
    session = agent.get_or_create_session(body.session_id, body.serial.strip(), c["org_id"],
                                          org["aircraft_type"], c["name"], all_fixes, c["user_id"])
    events: queue.Queue = queue.Queue()

    def worker():
        try:
            result = agent.run(session, body.question.strip(), events.put)
            events.put({"type": "done", **result})
        except Exception as e:
            events.put({"type": "error", "text": f"{type(e).__name__}: {e}"})
        finally:
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        yield _sse({"type": "session", "session_id": session.id})
        while True:
            try:
                ev = events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if ev is None:
                break
            yield _sse(ev)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/diagnoses")
def list_diagnoses(request: Request):
    c = ctx(request)
    rows = db.query("SELECT id, question, serial, resolution, created_at, created_by, cross_org, result FROM diagnoses "
                    "WHERE org_id = ? AND (cross_org = 0 OR ?) ORDER BY id DESC LIMIT 200",
                    (c["org_id"], int(c["is_platform_admin"])))
    for r in rows:
        res = json.loads(r.pop("result") or "{}")
        r["summary"] = res.get("summary", "")
        r["system"] = res.get("system", "")
        r["previous_fixes"] = len(res.get("previous_fixes", []))
    return rows


@app.get("/api/diagnoses/{diag_id}")
def get_diagnosis(diag_id: int, request: Request):
    c = ctx(request)
    row = db.one("SELECT * FROM diagnoses WHERE id = ? AND org_id = ? AND (cross_org = 0 OR ?)",
                 (diag_id, c["org_id"], int(c["is_platform_admin"])))
    if row is None:
        raise HTTPException(404)
    row["result"] = json.loads(row["result"] or "{}")
    row["trace"] = json.loads(row["trace"] or "[]")
    return row


class ResolutionBody(BaseModel):
    fix: str
    checks: str = ""


@app.post("/api/diagnoses/{diag_id}/resolution")
def record_resolution(diag_id: int, body: ResolutionBody, request: Request):
    """Record what actually fixed the aircraft. It goes into the Fix Log for future diagnoses."""
    c = ctx(request)
    row = db.one("SELECT * FROM diagnoses WHERE id = ? AND org_id = ? AND (cross_org = 0 OR ?)",
                 (diag_id, c["org_id"], int(c["is_platform_admin"])))
    if row is None:
        raise HTTPException(404)
    if not body.fix.strip():
        raise HTTPException(400, "Enter what fixed it.")
    res = json.loads(row["result"] or "{}")
    with db.tx() as conn:
        conn.execute("UPDATE diagnoses SET resolution = ? WHERE id = ?", (body.fix.strip(), diag_id))
    db.add_fix(c["org_id"], row["question"].replace("(follow-up) ", ""), body.fix.strip(), body.checks.strip(),
               res.get("system", ""), row["serial"], created_by=c["name"], source="diagnosis")
    return {"ok": True}


# ------------------------------------------------------------------ organisation & team


class OrgBody(BaseModel):
    name: str
    aircraft_type: str


class MemberBody(BaseModel):
    email: str
    name: str = ""
    password: str
    is_admin: bool = False


@app.get("/api/org")
def get_org(request: Request):
    c = ctx(request)
    org = org_row(c["org_id"])
    return {**org, "members": auth.members(c["org_id"]) if c["is_admin"] else [], "is_admin": c["is_admin"],
            "you": c["user_id"]}


@app.put("/api/org")
def update_org(body: OrgBody, request: Request):
    c = ctx(request)
    require_admin(c)
    if not body.name.strip():
        raise HTTPException(400, "Enter the organisation name.")
    with db.tx() as conn:
        conn.execute("UPDATE organisations SET name = ?, aircraft_type = ? WHERE id = ?",
                     (body.name.strip(), body.aircraft_type.strip(), c["org_id"]))
    return org_row(c["org_id"])


@app.post("/api/org/members")
def add_member(body: MemberBody, request: Request):
    c = ctx(request)
    require_admin(c)
    try:
        auth.add_user(body.email, body.password, body.name, body.is_admin, org_id=c["org_id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/org/members/{user_id}")
def delete_member(user_id: int, request: Request):
    c = ctx(request)
    require_admin(c)
    if user_id == c["user_id"]:
        raise HTTPException(400, "You can't remove yourself.")
    if not auth.remove_user(c["org_id"], user_id):
        raise HTTPException(404)
    return {"ok": True}


# ------------------------------------------------------------------ platform administration (Torwood staff)


class NewOrgBody(BaseModel):
    name: str
    aircraft_type: str = ""
    admin_email: str
    admin_name: str = ""
    admin_password: str


class OrgStatusBody(BaseModel):
    status: str


def require_platform_admin(request: Request) -> None:
    if not ctx(request)["is_platform_admin"]:
        raise HTTPException(403, "Torwood platform administrators only.")


@app.get("/api/admin/orgs")
def admin_orgs(request: Request):
    require_platform_admin(request)
    return db.query(
        "SELECT o.*, (SELECT COUNT(*) FROM users u WHERE u.org_id = o.id) AS users, "
        "(SELECT COUNT(*) FROM documents d WHERE d.org_id = o.id) AS documents, "
        "(SELECT COUNT(*) FROM diagnoses x WHERE x.org_id = o.id) AS diagnoses FROM organisations o ORDER BY o.name")


@app.post("/api/admin/orgs")
def admin_create_org(body: NewOrgBody, request: Request):
    require_platform_admin(request)
    if not body.name.strip():
        raise HTTPException(400, "Enter the organisation name.")
    if db.one("SELECT id FROM users WHERE email = ?", (body.admin_email.strip(),)):
        raise HTTPException(400, "A user with that email already exists.")
    org_id = auth.create_org(body.name, body.aircraft_type)
    try:
        auth.add_user(body.admin_email, body.admin_password, body.admin_name, is_admin=True, org_id=org_id)
    except ValueError as e:
        with db.tx() as conn:
            conn.execute("DELETE FROM organisations WHERE id = ?", (org_id,))
        raise HTTPException(400, str(e))
    return org_row(org_id)


@app.get("/api/admin/fixes")
def admin_fixes(request: Request, q: str = "", org_id: int | None = None):
    """Every subscriber's Fix Log, read-only. Torwood platform administrators only."""
    require_platform_admin(request)
    if q.strip():
        return search.search_notes(q, limit=200, org_id=org_id)
    sql = "SELECT n.*, o.name AS org_name FROM notes n JOIN organisations o ON o.id = n.org_id"
    params: tuple = ()
    if org_id is not None:
        sql += " WHERE n.org_id = ?"
        params = (org_id,)
    return db.query(sql + " ORDER BY n.id DESC LIMIT 500", params)


@app.put("/api/admin/orgs/{org_id}/status")
def admin_org_status(org_id: int, body: OrgStatusBody, request: Request):
    require_platform_admin(request)
    if body.status not in ("trial", "active", "suspended"):
        raise HTTPException(400, "Status must be trial, active or suspended.")
    with db.tx() as conn:
        conn.execute("UPDATE organisations SET status = ? WHERE id = ?", (body.status, org_id))
    return org_row(org_id)
