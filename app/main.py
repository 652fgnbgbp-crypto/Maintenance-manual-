"""Web server: library management, search, notes, and streaming AI diagnosis."""
import json
import os
import queue
import shutil
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

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
    if path.startswith(PUBLIC) or request.state.user or not auth.login_required():
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


@app.get("/api/info")
def info(request: Request):
    user = request.state.user
    return {
        "user": {"name": user["name"] or user["email"], "email": user["email"]} if user else None,
        "login_required": auth.login_required(),
        "aircraft_type": config.AIRCRAFT_TYPE,
        "model": config.DIAGNOSIS_MODEL,
        "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
        "documents": db.one("SELECT COUNT(*) AS n FROM documents")["n"],
        "pages": db.one("SELECT COUNT(*) AS n FROM pages")["n"],
        "notes": db.one("SELECT COUNT(*) AS n FROM notes")["n"],
    }


# ------------------------------------------------------------------ documents


@app.get("/api/documents")
def list_documents():
    return db.query("SELECT * FROM documents ORDER BY id DESC")


def _process_upload(doc_id: int, run_enrich: bool) -> None:
    try:
        ingest.process_document(doc_id)
    except Exception:
        return
    if run_enrich:
        enrich.enrich_document(doc_id)


@app.post("/api/documents")
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    category: str = Form("manual"),
    enrich_with_ai: bool = Form(False),
):
    suffix = Path(file.filename or "upload").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)
    try:
        doc_id = ingest.create_document(tmp_path, file.filename or "upload", title.strip() or None, category)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        tmp_path.unlink(missing_ok=True)
    background.add_task(_process_upload, doc_id, enrich_with_ai)
    return db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int):
    doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if doc is None:
        raise HTTPException(404)
    db.delete_document(doc_id)
    ingest.file_path(doc).unlink(missing_ok=True)
    for f in config.CACHE_DIR.glob(f"{doc['stored_name']}.*"):
        f.unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/documents/{doc_id}/enrich")
def start_enrich(doc_id: int):
    doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if doc is None:
        raise HTTPException(404)
    if doc["enrich_status"] == "running":
        return doc
    enrich.start_background(doc_id)
    return {**doc, "enrich_status": "running"}


@app.get("/api/documents/{doc_id}/pages")
def list_pages(doc_id: int):
    return db.query(
        "SELECT id, doc_id, page_no, ata, page_label, subject, effectivity, page_type, ai_summary IS NOT NULL AS enriched "
        "FROM pages WHERE doc_id = ? ORDER BY page_no", (doc_id,))


@app.get("/api/pages/{doc_id}/{page_no}")
def page_detail(doc_id: int, page_no: int):
    page = ingest.get_page(doc_id, page_no)
    if page is None:
        raise HTTPException(404)
    doc = db.one("SELECT page_count FROM documents WHERE id = ?", (doc_id,))
    return {**page, "page_count": doc["page_count"], "has_image": page["doc_kind"] != "text"}


@app.get("/api/pages/{doc_id}/{page_no}/image")
def page_image(doc_id: int, page_no: int):
    png = ingest.render_page(doc_id, page_no, config.VIEW_IMAGE_LONG_EDGE)
    if png is None:
        raise HTTPException(404)
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


class LocateBody(BaseModel):
    terms: list[str]


@app.post("/api/pages/{doc_id}/{page_no}/locate")
def locate(doc_id: int, page_no: int, body: LocateBody):
    return highlight.locate_terms(doc_id, page_no, [t for t in body.terms if t.strip()][:20])


@app.get("/api/search")
def search_all(q: str, limit: int = 20):
    return {"pages": search.search_pages(q, limit=limit), "notes": search.search_notes(q, limit=10)}


# ------------------------------------------------------------------ notes


class NoteBody(BaseModel):
    title: str
    body: str
    tags: str = ""
    serial: str = ""


@app.get("/api/notes")
def list_notes():
    return db.query("SELECT * FROM notes ORDER BY id DESC")


@app.post("/api/notes")
def create_note(note: NoteBody):
    if not note.title.strip() or not note.body.strip():
        raise HTTPException(400, "Title and details are required.")
    nid = db.add_note(note.title.strip(), note.body.strip(), note.tags.strip(), note.serial.strip())
    return db.one("SELECT * FROM notes WHERE id = ?", (nid,))


@app.delete("/api/notes/{note_id}")
def remove_note(note_id: int):
    db.delete_note(note_id)
    return {"ok": True}


# ------------------------------------------------------------------ diagnosis


class DiagnoseBody(BaseModel):
    question: str
    serial: str = ""
    session_id: str | None = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.post("/api/diagnose")
def diagnose(body: DiagnoseBody):
    if not body.question.strip():
        raise HTTPException(400, "Describe the problem first.")
    session = agent.get_or_create_session(body.session_id, body.serial.strip())
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
def list_diagnoses():
    rows = db.query("SELECT id, question, serial, resolution, created_at, result FROM diagnoses ORDER BY id DESC LIMIT 100")
    for r in rows:
        res = json.loads(r.pop("result") or "{}")
        r["summary"] = res.get("summary", "")
        r["system"] = res.get("system", "")
    return rows


@app.get("/api/diagnoses/{diag_id}")
def get_diagnosis(diag_id: int):
    row = db.one("SELECT * FROM diagnoses WHERE id = ?", (diag_id,))
    if row is None:
        raise HTTPException(404)
    row["result"] = json.loads(row["result"] or "{}")
    row["trace"] = json.loads(row["trace"] or "[]")
    return row


class ResolutionBody(BaseModel):
    resolution: str


@app.post("/api/diagnoses/{diag_id}/resolution")
def record_resolution(diag_id: int, body: ResolutionBody):
    """Record what actually fixed the aircraft; it becomes a troubleshooting note for future diagnoses."""
    row = db.one("SELECT * FROM diagnoses WHERE id = ?", (diag_id,))
    if row is None:
        raise HTTPException(404)
    res = json.loads(row["result"] or "{}")
    with db.tx() as conn:
        conn.execute("UPDATE diagnoses SET resolution = ? WHERE id = ?", (body.resolution, diag_id))
    body_text = (f"Reported problem: {row['question']}\n\nConfirmed fix: {body.resolution}\n\n"
                 f"AI diagnosis at the time: {res.get('summary', '')}")
    db.add_note(f"Confirmed fix: {res.get('system') or row['question'][:60]}", body_text,
                tags="confirmed-fix", serial=row["serial"], source="diagnosis")
    return {"ok": True}
