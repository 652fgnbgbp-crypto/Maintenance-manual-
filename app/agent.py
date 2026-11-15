"""The diagnosis agent.

Claude works like a technician with the manual open: it searches the library, opens schematic
pages (as images plus their OCR text), checks troubleshooting notes and past fixes, confirms that
the parts it wants to point at can be found on the page, and then submits a structured diagnosis.
Every page it cites is one it actually opened, and every highlight is located on the page's OCR
word boxes so the browser can draw it on the diagram.
"""
import base64
import json
import threading
import time
import uuid
from typing import Callable

import anthropic

from . import config, db, highlight, ingest, search

Emit = Callable[[dict], None]

SYSTEM_PROMPT = """You are an aircraft electrical and systems troubleshooting assistant for the {aircraft} fleet. \
You help a licensed maintenance technician find the most likely cause of a reported defect, using ONLY the \
maintenance data and troubleshooting notes in this library.

How to work:
1. Restate the symptom to yourself and decide which system(s) and circuits could produce it.
2. Use search_library to find the relevant circuit. Manuals in the library usually have index pages \
(circuit index, relay/valve/solenoid index, circuit-breaker index) that give the ATA chapter-section-subject \
and page numbers; use them the way a technician would. Search terms that work well: circuit names, component \
names, reference designators (K45, S102, CB12), wire numbers (P25A20) and ATA numbers (24-30-02).
3. Open the schematic and circuit-description pages with view_page and actually read them. Trace the circuit \
from the power source through circuit breakers, switches, relay contacts, the load and its ground, and note \
which components and wires could explain the symptom. Open the circuit-operation text pages as well when they exist.
4. Check effectivity: pages apply to aircraft serial ranges. If the technician gave a serial number, use only \
pages whose effectivity covers it, and say so. If not, note where the answer depends on serial range.
5. Use search_notes to look for troubleshooting notes and past fixes on this fleet; weigh them as field experience.
6. Before submitting, call locate_on_page for each page you will cite, with the exact labels you intend to \
highlight (reference designators, wire numbers, component names as printed). Only highlight terms that were found; \
if a key item cannot be found because the OCR is poor, describe it in a region instead.
7. Call submit_diagnosis once with your answer. Rank probable causes from most to least likely, and give \
practical isolation steps (what to measure, where, the expected reading) that tell the causes apart, cheapest and \
quickest checks first.

Rules:
- Cite only pages you opened with view_page in this conversation. Never invent page numbers, part numbers, \
wire numbers or values that you did not read in the library.
- If the library does not contain the circuit, say so plainly in the summary, give general guidance marked as \
such, and list what data would be needed.
- If the symptom is ambiguous, still give your best ranked diagnosis and put the questions that would narrow it \
down in missing_info.
- This is decision support for a qualified technician. Include relevant safety notes (e.g. pull circuit breakers, \
battery disconnect, propeller/engine hazards) and remind them to follow the approved maintenance data.
- Write for a technician: concise, specific, no filler."""

TOOLS = [
    {
        "name": "search_library",
        "description": "Full-text search over every page of the uploaded maintenance manuals and documents "
                       "(OCR text, circuit/subject names, ATA numbers, component and wire IDs). Returns the best "
                       "matching pages with document id, page number, ATA, subject, effectivity and a snippet. "
                       "page_type narrows results: schematic, text (circuit descriptions), index, location, or any.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords, component names, designators, wire numbers or ATA numbers."},
                "page_type": {"type": "string", "enum": ["any", "schematic", "text", "index", "location", "other"]},
            },
            "required": ["query", "page_type"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "view_page",
        "description": "Open one page of a document: returns the page image (schematic or text) plus its OCR text "
                       "and metadata. Use this to actually read schematics before relying on them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "integer"},
                "page": {"type": "integer", "description": "1-based page number in the file (the 'page' field from search results)."},
            },
            "required": ["doc_id", "page"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "search_notes",
        "description": "Search the fleet's troubleshooting notes, snag history and previously confirmed fixes.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "locate_on_page",
        "description": "Check which labels can be found on a page's OCR layer so they can be highlighted on the "
                       "diagram. Returns, for each term, how many places it was found and their approximate position.",
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "integer"},
                "page": {"type": "integer"},
                "terms": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["doc_id", "page", "terms"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "submit_diagnosis",
        "description": "Submit the final diagnosis. Call exactly once, after researching the library.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Two to four sentences: most likely cause and why."},
                "system": {"type": "string", "description": "Affected system/circuit with ATA number, e.g. 'Battery Control (24-30-02)'."},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "effectivity_note": {"type": "string", "description": "Which serial ranges the cited pages apply to, or empty."},
                "probable_causes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rank": {"type": "integer"},
                            "cause": {"type": "string", "description": "Short name, e.g. 'Battery No.1 contactor (K45) coil open'."},
                            "likelihood": {"type": "string", "enum": ["high", "medium", "low"]},
                            "reasoning": {"type": "string", "description": "How the symptom follows from this fault, referring to the schematic."},
                        },
                        "required": ["rank", "cause", "likelihood", "reasoning"],
                        "additionalProperties": False,
                    },
                },
                "troubleshooting_steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "integer"},
                            "action": {"type": "string", "description": "What to check or measure, and where (pin, wire, connector)."},
                            "expected": {"type": "string", "description": "Normal result."},
                            "if_abnormal": {"type": "string", "description": "What an abnormal result means / what to do next."},
                            "cause_ranks": {"type": "array", "items": {"type": "integer"}, "description": "Which probable causes this step tests."},
                        },
                        "required": ["step", "action", "expected", "if_abnormal", "cause_ranks"],
                        "additionalProperties": False,
                    },
                },
                "reasoning": {"type": "string", "description": "How you reached the diagnosis: which pages you used and how you traced the circuit. Markdown allowed."},
                "references": {
                    "type": "array",
                    "description": "Pages that support the diagnosis, most important first. Only pages opened with view_page.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "doc_id": {"type": "integer"},
                            "page": {"type": "integer"},
                            "why": {"type": "string", "description": "What this page shows and how it supports the diagnosis."},
                            "highlights": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "term": {"type": "string", "description": "Exact label as printed, e.g. 'K45', 'P86B12', 'BATTERY NO. 1 CONTACTOR'."},
                                        "note": {"type": "string", "description": "Why it matters, shown on hover."},
                                        "cause_rank": {"type": "integer", "description": "Probable cause this relates to, or 0 for context (power source, ground, etc.)."},
                                    },
                                    "required": ["term", "note", "cause_rank"],
                                    "additionalProperties": False,
                                },
                            },
                            "regions": {
                                "type": "array",
                                "description": "Areas to outline when no printed label marks them (e.g. a wire run). Fractions 0-1 of page width/height from the top-left.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "x": {"type": "number"}, "y": {"type": "number"},
                                        "w": {"type": "number"}, "h": {"type": "number"},
                                        "note": {"type": "string"},
                                        "cause_rank": {"type": "integer"},
                                    },
                                    "required": ["x", "y", "w", "h", "note", "cause_rank"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["doc_id", "page", "why", "highlights", "regions"],
                        "additionalProperties": False,
                    },
                },
                "safety_notes": {"type": "array", "items": {"type": "string"}},
                "missing_info": {"type": "array", "items": {"type": "string"}, "description": "Questions or data that would narrow the diagnosis."},
            },
            "required": ["summary", "system", "confidence", "effectivity_note", "probable_causes",
                         "troubleshooting_steps", "reasoning", "references", "safety_notes", "missing_info"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def library_overview() -> str:
    docs = db.query("SELECT id, title, kind, category, page_count FROM documents WHERE status='ready' ORDER BY id")
    notes = db.one("SELECT COUNT(*) AS n FROM notes")["n"]
    if not docs:
        lines = ["(no documents uploaded yet)"]
    else:
        lines = [f"- doc_id {d['id']}: {d['title']} ({d['category']}, {d['page_count']} pages)" for d in docs]
    return "Documents in the library:\n" + "\n".join(lines) + f"\nTroubleshooting notes on file: {notes}"


# ------------------------------------------------------------------ tool implementations


def _page_ref(p: dict) -> dict:
    return {
        "doc_id": p["doc_id"], "page": p["page_no"], "document": p.get("doc_title"),
        "ata": p.get("ata"), "printed_page": p.get("page_label"), "subject": p.get("subject"),
        "effectivity": p.get("effectivity"), "page_type": p.get("page_type"),
    }


def tool_search_library(inp: dict) -> str:
    pt = inp.get("page_type")
    hits = search.search_pages(inp["query"], limit=10, page_type=None if pt in (None, "any") else pt)
    if not hits:
        return "No matching pages. Try other terms (component names, designators, ATA numbers) or page_type 'any'."
    out = []
    for h in hits:
        r = _page_ref(h)
        r["snippet"] = (h.get("snippet") or "")[:300]
        if h.get("ai_summary"):
            r["summary"] = h["ai_summary"][:300]
        out.append(r)
    return json.dumps(out, indent=1)


def tool_view_page(inp: dict, viewed: set) -> list | str:
    page = ingest.get_page(inp["doc_id"], inp["page"])
    if page is None:
        return f"Page {inp['page']} of document {inp['doc_id']} does not exist."
    viewed.add((inp["doc_id"], inp["page"]))
    meta = _page_ref(page)
    if page.get("ai_summary"):
        meta["summary"] = page["ai_summary"]
    text = page["text"] or ""
    header = json.dumps(meta) + "\n\nOCR text (may contain recognition errors; trust the image):\n" + text[:5000]
    content: list = [{"type": "text", "text": header}]
    png = ingest.render_page(inp["doc_id"], inp["page"], config.MODEL_IMAGE_LONG_EDGE)
    if png:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(png).decode()},
        })
    return content


def tool_search_notes(inp: dict) -> str:
    notes = search.search_notes(inp["query"])
    if not notes:
        return "No matching troubleshooting notes."
    return json.dumps([
        {"note_id": n["id"], "title": n["title"], "aircraft": n["serial"], "date": n["created_at"],
         "source": n["source"], "tags": n["tags"], "body": n["body"][:2500]}
        for n in notes
    ], indent=1)


def tool_locate(inp: dict) -> str:
    found = highlight.locate_terms(inp["doc_id"], inp["page"], inp["terms"])
    out = {}
    for term, boxes in found.items():
        out[term] = {
            "found": len(boxes),
            "positions": [f"x={b['x']:.2f}, y={b['y']:.2f}" for b in boxes[:5]],
        }
    return json.dumps(out)


# ------------------------------------------------------------------ result post-processing


def build_result(diag: dict, viewed: set) -> dict:
    """Attach page metadata and on-page highlight boxes to the model's diagnosis."""
    refs = []
    for ref in diag.get("references", []):
        page = ingest.get_page(ref["doc_id"], ref["page"])
        if page is None:
            continue
        terms = [h["term"] for h in ref.get("highlights", [])]
        boxes = highlight.locate_terms(ref["doc_id"], ref["page"], terms) if terms else {}
        hl = []
        for h in ref.get("highlights", []):
            hl.append({**h, "boxes": boxes.get(h["term"], [])})
        regions = [
            {**r, "x": max(0.0, min(1.0, r["x"])), "y": max(0.0, min(1.0, r["y"])),
             "w": max(0.005, min(1.0, r["w"])), "h": max(0.005, min(1.0, r["h"]))}
            for r in ref.get("regions", [])
        ]
        refs.append({
            **_page_ref(page), "why": ref.get("why", ""), "highlights": hl, "regions": regions,
            "has_image": page["doc_kind"] != "text", "verified": (ref["doc_id"], ref["page"]) in viewed,
            "text": page["text"] if page["doc_kind"] == "text" else None,
        })
    return {**diag, "references": refs}


# ------------------------------------------------------------------ sessions


class Session:
    def __init__(self, serial: str):
        self.id = uuid.uuid4().hex
        self.serial = serial
        self.messages: list = []
        self.pending_tool_use: str | None = None  # submit_diagnosis id awaiting a tool_result
        self.viewed: set = set()
        self.trace: list = []
        self.diagnosis_id: int | None = None
        self.lock = threading.Lock()
        self.updated = time.time()


SESSIONS: dict[str, Session] = {}


def get_or_create_session(session_id: str | None, serial: str) -> Session:
    if session_id and session_id in SESSIONS:
        return SESSIONS[session_id]
    # drop sessions idle for more than 6 hours
    cutoff = time.time() - 6 * 3600
    for k in [k for k, s in SESSIONS.items() if s.updated < cutoff]:
        SESSIONS.pop(k, None)
    s = Session(serial)
    SESSIONS[s.id] = s
    return s


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=4)


def run(session: Session, user_text: str, emit: Emit, client=None) -> dict:
    """Run the agent for one technician message. Returns {'type': 'diagnosis'|'message', ...}."""
    client = client or _client()
    with session.lock:
        session.updated = time.time()
        first = not session.messages
        if first:
            serial_line = f"Aircraft serial number: {session.serial}" if session.serial else \
                "Aircraft serial number: not given"
            text = f"{library_overview()}\n\n{serial_line}\n\nReported problem:\n{user_text}"
        else:
            text = f"Technician follow-up:\n{user_text}"
        content: list = []
        if session.pending_tool_use:
            content.append({"type": "tool_result", "tool_use_id": session.pending_tool_use,
                            "content": "Diagnosis delivered to the technician."})
            session.pending_tool_use = None
        content.append({"type": "text", "text": text})
        session.messages.append({"role": "user", "content": content})

        system = SYSTEM_PROMPT.format(aircraft=config.AIRCRAFT_TYPE)
        for turn in range(config.MAX_AGENT_TURNS):
            emit({"type": "status", "text": "Thinking…" if turn == 0 else "Analysing…"})
            with client.beta.messages.stream(
                model=config.DIAGNOSIS_MODEL,
                max_tokens=64000,
                system=system,
                tools=TOOLS,
                messages=session.messages,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": config.DIAGNOSIS_EFFORT},
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                response = stream.get_final_message()

            if response.stop_reason == "refusal":
                raise RuntimeError("The model declined this request. Rephrase the problem description and try again.")

            # Append the content exactly as returned (thinking blocks must be passed back unchanged).
            session.messages.append({"role": "assistant", "content": response.content})

            for b in response.content:
                if b.type == "thinking" and getattr(b, "thinking", ""):
                    step = {"kind": "thinking", "text": b.thinking}
                    session.trace.append(step)
                    emit({"type": "step", **step})
                elif b.type == "text" and b.text.strip():
                    step = {"kind": "text", "text": b.text}
                    session.trace.append(step)
                    emit({"type": "step", **step})

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason == "pause_turn":
                continue
            if not tool_uses:
                if response.stop_reason == "max_tokens":
                    raise RuntimeError("The response was cut off (max_tokens). Try a narrower question.")
                answer = "\n".join(b.text for b in response.content if b.type == "text").strip()
                return {"type": "message", "text": answer, "session_id": session.id, "trace": session.trace}

            submit = next((b for b in tool_uses if b.name == "submit_diagnosis"), None)
            if submit is not None:
                others = [b for b in tool_uses if b is not submit]
                if not others:
                    result = build_result(submit.input, session.viewed)
                    session.pending_tool_use = submit.id
                    session.diagnosis_id = db.save_diagnosis(user_text if first else f"(follow-up) {user_text}",
                                                             session.serial, result, session.trace)
                    return {"type": "diagnosis", "session_id": session.id, "diagnosis_id": session.diagnosis_id,
                            "result": result, "trace": session.trace}

            results = []
            for b in tool_uses:
                results.append(_execute(b, session, emit))
            if turn >= config.MAX_AGENT_TURNS - 3:
                results.append({"type": "text", "text": "Research budget nearly used: call submit_diagnosis now "
                                                        "with what you have."})
            session.messages.append({"role": "user", "content": results})

        raise RuntimeError("The agent did not reach a diagnosis within the step limit.")


def _execute(block, session: Session, emit: Emit) -> dict:
    name, inp = block.name, block.input
    try:
        if name == "search_library":
            step = {"kind": "search", "text": f"Searched manuals: “{inp['query']}”"
                    + (f" ({inp['page_type']} pages)" if inp.get("page_type") not in (None, "any") else "")}
            out = tool_search_library(inp)
        elif name == "view_page":
            page = ingest.get_page(inp["doc_id"], inp["page"])
            label = f"page {inp['page']}"
            if page:
                bits = [page.get("ata"), f"p.{page['page_label']}" if page.get("page_label") else None, page.get("subject")]
                label += " — " + " ".join(b for b in bits if b)
            step = {"kind": "view", "text": f"Opened {label}", "doc_id": inp["doc_id"], "page": inp["page"]}
            out = tool_view_page(inp, session.viewed)
        elif name == "search_notes":
            step = {"kind": "notes", "text": f"Checked troubleshooting notes: “{inp['query']}”"}
            out = tool_search_notes(inp)
        elif name == "locate_on_page":
            step = {"kind": "locate", "text": f"Located {', '.join(inp['terms'][:6])} on page {inp['page']}",
                    "doc_id": inp["doc_id"], "page": inp["page"]}
            out = tool_locate(inp)
        elif name == "submit_diagnosis":
            step = None
            out = "Submit the diagnosis on its own, without other tool calls in the same turn."
            return {"type": "tool_result", "tool_use_id": block.id, "content": out, "is_error": True}
        else:
            return {"type": "tool_result", "tool_use_id": block.id, "content": f"Unknown tool {name}", "is_error": True}
        session.trace.append(step)
        emit({"type": "step", **step})
        return {"type": "tool_result", "tool_use_id": block.id, "content": out}
    except Exception as e:  # report tool failures back to the model so it can recover
        return {"type": "tool_result", "tool_use_id": block.id, "content": f"Error: {e}", "is_error": True}
