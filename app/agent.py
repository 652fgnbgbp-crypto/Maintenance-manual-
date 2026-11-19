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
5. Always search the organisation's Fix Log with search_notes (try the symptom, the system and the main \
components). These are problems this organisation's engineers have already solved, and what fixed them. When an \
entry is relevant, list it in previous_fixes and link it to the causes it supports with previous_fix_ids. Treat it \
as strong field evidence when the symptom matches, but still check it against the manual: a past fix can be a \
different fault with a similar symptom.
6. Narrow it down by elimination. When more than one cause is still plausible, call ask_questions with \
yes/no questions for the technician, each chosen to rule causes in or out (for example "Does the landing gear \
operate normally?" splits a shared hydraulic fault from a flap-only one). Ask only what the technician can answer \
quickly by looking, listening, or a simple check, and say how to check it. Never ask what the manual or Fix Log \
already answers. Ask the most discriminating questions first, in batches of up to {batch}. The technician answers \
Yes, No or Unsure: treat Unsure as no information. There is a hard limit on the total number of questions (given \
in the first message); ask fewer when the answer is already clear. Do your library research before asking, so \
the questions name the real components and checks. After the answers, record in "eliminated" each cause they \
ruled out and why.
7. Before submitting, call locate_on_page for each page you will cite, with the exact labels you intend to \
highlight (reference designators, wire numbers, component names as printed). Only highlight terms that were found; \
if a key item cannot be found because the OCR is poor, describe it in a region instead.
8. Call submit_diagnosis once with your answer. Rank probable causes from most to least likely, and give \
practical isolation steps (what to measure, where, the expected reading) that tell the causes apart, cheapest and \
quickest checks first.

Rules:
- Cite only Fix Log entries that search_notes returned in this conversation, by their note_id.
- Cite only pages you opened with view_page in this conversation. Never invent page numbers, part numbers, \
wire numbers or values that you did not read in the library.
- If the library does not contain the circuit, say so plainly in the summary, give general guidance marked as \
such, and list what data would be needed.
- If the symptom is still ambiguous when the question limit is reached, give your best ranked diagnosis, set \
more_questions_would_help to true, and put what you would still ask in missing_info.
- ask_questions and submit_diagnosis must each be the only tool call in their turn.
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
        "description": "Search this organisation's Fix Log: problems its engineers have already solved on this fleet, "
                       "what they checked and what fixed it. Returns entries with a note_id.",
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
        "name": "ask_questions",
        "description": "Ask the technician yes/no questions to rule causes in or out. They answer each with "
                       "Yes, No or Unsure using tick boxes. Call it on its own; the answers come back as the result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "intro": {"type": "string", "description": "One short sentence: what these questions will tell apart."},
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "description": "A yes/no question, e.g. 'Does the landing gear cycle normally?'"},
                            "why": {"type": "string", "description": "What a Yes and a No would each rule out, in a few words."},
                            "how_to_check": {"type": "string", "description": "How to check it (where to look or measure), or empty if it is a plain observation."},
                        },
                        "required": ["question", "why", "how_to_check"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["intro", "questions"],
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
                            "previous_fix_ids": {"type": "array", "items": {"type": "integer"},
                                                 "description": "note_ids of Fix Log entries where this cause was the confirmed fix; empty if none."},
                        },
                        "required": ["rank", "cause", "likelihood", "reasoning", "previous_fix_ids"],
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
                "previous_fixes": {
                    "type": "array",
                    "description": "Relevant Fix Log entries (from search_notes), most relevant first. Empty if none apply.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "note_id": {"type": "integer"},
                            "match": {"type": "string", "enum": ["strong", "partial"],
                                      "description": "strong: same symptom on the same system; partial: related."},
                            "relevance": {"type": "string", "description": "One or two sentences: how this past fix relates to the current defect."},
                        },
                        "required": ["note_id", "match", "relevance"],
                        "additionalProperties": False,
                    },
                },
                "eliminated": {
                    "type": "array",
                    "description": "Causes ruled out by the technician's answers or checks, and why. Empty if none.",
                    "items": {
                        "type": "object",
                        "properties": {"cause": {"type": "string"}, "because": {"type": "string"}},
                        "required": ["cause", "because"],
                        "additionalProperties": False,
                    },
                },
                "more_questions_would_help": {"type": "boolean",
                                              "description": "True if more yes/no answers would materially narrow the diagnosis."},
                "safety_notes": {"type": "array", "items": {"type": "string"}},
                "missing_info": {"type": "array", "items": {"type": "string"}, "description": "Questions or data that would narrow the diagnosis."},
            },
            "required": ["summary", "system", "confidence", "effectivity_note", "probable_causes",
                         "troubleshooting_steps", "reasoning", "references", "previous_fixes", "eliminated",
                         "more_questions_would_help", "safety_notes", "missing_info"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def library_overview(org_id: int) -> str:
    docs = db.query("SELECT id, title, kind, category, page_count FROM documents WHERE status='ready' AND org_id = ? "
                    "ORDER BY id", (org_id,))
    notes = db.one("SELECT COUNT(*) AS n FROM notes WHERE org_id = ?", (org_id,))["n"]
    if not docs:
        lines = ["(no documents uploaded yet)"]
    else:
        lines = [f"- doc_id {d['id']}: {d['title']} ({d['category']}, {d['page_count']} pages)" for d in docs]
    return "Documents in the library:\n" + "\n".join(lines) + f"\nFix Log entries on file: {notes}"


# ------------------------------------------------------------------ tool implementations


def _page_ref(p: dict) -> dict:
    return {
        "doc_id": p["doc_id"], "page": p["page_no"], "document": p.get("doc_title"),
        "ata": p.get("ata"), "printed_page": p.get("page_label"), "subject": p.get("subject"),
        "effectivity": p.get("effectivity"), "page_type": p.get("page_type"),
    }


def tool_search_library(inp: dict, org_id: int) -> str:
    pt = inp.get("page_type")
    hits = search.search_pages(inp["query"], limit=10, page_type=None if pt in (None, "any") else pt, org_id=org_id)
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


def tool_view_page(inp: dict, viewed: set, org_id: int) -> list | str:
    page = ingest.get_page(inp["doc_id"], inp["page"]) if db.org_document(org_id, inp["doc_id"]) else None
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


def tool_search_notes(inp: dict, org_id: int, seen: set, all_orgs: bool = False) -> str:
    """The organisation's own Fix Log, or (Torwood platform admins only) every operator's."""
    notes = search.search_notes(inp["query"], limit=10 if all_orgs else 6, org_id=None if all_orgs else org_id)
    if not notes:
        return "No matching Fix Log entries."
    seen.update(n["id"] for n in notes)
    out = []
    for n in notes:
        e = {"note_id": n["id"], "aircraft": n["serial"], "system": n["system"], "date": n["created_at"],
             "engineer": n["created_by"], "tags": n["tags"], "entry": n["body"][:2500]}
        if all_orgs:
            e["operator"] = n["org_name"]
            e["operator_aircraft_type"] = n["org_aircraft"]
        out.append(e)
    return json.dumps(out, indent=1)


def tool_locate(inp: dict, org_id: int) -> str:
    if not db.org_document(org_id, inp["doc_id"]):
        return f"Document {inp['doc_id']} does not exist."
    found = highlight.locate_terms(inp["doc_id"], inp["page"], inp["terms"])
    out = {}
    for term, boxes in found.items():
        out[term] = {
            "found": len(boxes),
            "positions": [f"x={b['x']:.2f}, y={b['y']:.2f}" for b in boxes[:5]],
        }
    return json.dumps(out)


# ------------------------------------------------------------------ result post-processing


def _fix_card(n: dict) -> dict:
    return {"note_id": n["id"], "symptom": n["symptom"] or n["title"], "checks": n["checks"],
            "fix": n["fix"], "body": n["body"], "aircraft": n["serial"], "system": n["system"],
            "engineer": n["created_by"], "date": n["created_at"], "source": n["source"],
            "operator": n.get("org_name", ""), "operator_aircraft_type": n.get("org_aircraft", "")}


def build_result(diag: dict, viewed: set, org_id: int = 1, seen_notes: set | None = None,
                 all_orgs: bool = False) -> dict:
    """Attach page metadata, on-page highlight boxes and the cited Fix Log entries to the diagnosis.
    Anything that is not in the organisation's own library is dropped. With all_orgs (Torwood platform
    admins only), Fix Log entries may come from any operator."""
    fixes = []
    for pf in diag.get("previous_fixes", []):
        n = db.one("SELECT n.*, o.name AS org_name, o.aircraft_type AS org_aircraft FROM notes n "
                   "JOIN organisations o ON o.id = n.org_id WHERE n.id = ?"
                   + ("" if all_orgs else " AND n.org_id = ?"),
                   (pf["note_id"],) if all_orgs else (pf["note_id"], org_id))
        # only entries the AI actually retrieved in this conversation (seen_notes is None when rebuilding a saved result)
        if n is not None and (seen_notes is None or n["id"] in seen_notes):
            fixes.append({**_fix_card(n), "match": pf.get("match", "partial"), "relevance": pf.get("relevance", ""),
                          "verified": seen_notes is None or n["id"] in seen_notes})
    known = {f["note_id"] for f in fixes}
    causes = [{**c, "previous_fix_ids": [i for i in c.get("previous_fix_ids", []) if i in known]}
              for c in diag.get("probable_causes", [])]
    refs = []
    for ref in diag.get("references", []):
        page = ingest.get_page(ref["doc_id"], ref["page"]) if db.org_document(org_id, ref["doc_id"]) else None
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
    return {**diag, "probable_causes": causes, "previous_fixes": fixes, "references": refs,
            "fix_scope": "all_operators" if all_orgs else "own"}


# ------------------------------------------------------------------ sessions


class Session:
    def __init__(self, serial: str, org_id: int = 1, aircraft_type: str = "", user_name: str = "",
                 all_fixes: bool = False):
        self.id = uuid.uuid4().hex
        self.serial = serial
        self.org_id = org_id
        self.aircraft_type = aircraft_type or config.AIRCRAFT_TYPE
        self.user_name = user_name
        self.seen_notes: set = set()
        self.pending_kind = ""                 # "questions" or "diagnosis"
        self.pending_questions: list = []      # questions shown to the technician, awaiting answers
        self.qa: list = []                     # every question asked and its answer
        self.question_limit = config.QUESTION_LIMIT
        self.question_text = ""                # the original problem description
        self.all_fixes = all_fixes  # Torwood platform admin: draw on every operator's Fix Log
        self.user_id: int | None = None
        self.messages: list = []
        self.pending_tool_use: str | None = None  # submit_diagnosis id awaiting a tool_result
        self.viewed: set = set()
        self.trace: list = []
        self.diagnosis_id: int | None = None
        self.lock = threading.Lock()
        self.updated = time.time()


SESSIONS: dict[str, Session] = {}


def get_or_create_session(session_id: str | None, serial: str, org_id: int = 1, aircraft_type: str = "",
                          user_name: str = "", all_fixes: bool = False, user_id: int | None = None) -> Session:
    s = SESSIONS.get(session_id or "")
    if s and s.org_id == org_id and s.all_fixes == all_fixes and s.user_id == user_id:
        return s
    # drop sessions idle for more than 6 hours
    cutoff = time.time() - 6 * 3600
    for k in [k for k, s in SESSIONS.items() if s.updated < cutoff]:
        SESSIONS.pop(k, None)
    s = Session(serial, org_id, aircraft_type, user_name, all_fixes)
    s.user_id = user_id
    SESSIONS[s.id] = s
    return s


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=4)


ANSWER_LABEL = {"yes": "YES", "no": "NO", "unsure": "UNSURE (no information)"}


def _answers_result(session: Session, answers: list | None, note: str) -> str:
    """Record the technician's tick-box answers and format them for the model."""
    given = {a.get("id"): (a.get("answer") or "unsure").lower() for a in (answers or [])}
    lines = []
    for q in session.pending_questions:
        ans = given.get(q["id"], "unsure")
        if ans not in ANSWER_LABEL:
            ans = "unsure"
        session.qa.append({"question": q["question"], "answer": ans, "why": q["why"]})
        lines.append(f"{q['id']}. {q['question']} -> {ANSWER_LABEL[ans]}")
    step = {"kind": "answers", "text": "Answered: " + ", ".join(f"{q['id']} {given.get(q['id'], 'unsure')}"
                                                             for q in session.pending_questions),
            "items": session.qa[-len(session.pending_questions):]}
    session.trace.append(step)
    session.pending_questions = []
    used = len(session.qa)
    text = "Technician's answers:\n" + "\n".join(lines)
    if note:
        text += f"\n\nTechnician's note: {note}"
    text += f"\n\nQuestions used: {used} of {session.question_limit}."
    return text


def run(session: Session, user_text: str, emit: Emit, client=None, answers: list | None = None,
        extend: bool = False) -> dict:
    """Run the agent for one technician turn. Returns {'type': 'diagnosis'|'questions'|'message', ...}.

    answers: tick-box answers to the pending questions, [{"id": "q1", "answer": "yes"|"no"|"unsure"}].
    extend: the technician allows QUESTION_EXTENSION more questions."""
    client = client or _client()
    with session.lock:
        session.updated = time.time()
        first = not session.messages
        content: list = []
        if session.pending_tool_use:
            if session.pending_kind == "questions":
                result_text = _answers_result(session, answers, "" if answers is not None else user_text)
                if answers is None:
                    user_text = ""  # the free text was passed on as the answer note
            else:
                result_text = "Diagnosis delivered to the technician."
            content.append({"type": "tool_result", "tool_use_id": session.pending_tool_use, "content": result_text})
            session.pending_tool_use = None
            session.pending_kind = ""
        if extend:
            session.question_limit += config.QUESTION_EXTENSION
            session.trace.append({"kind": "text", "text": f"Technician allowed {config.QUESTION_EXTENSION} more questions."})
        if first:
            session.question_text = user_text
            serial_line = f"Aircraft serial number: {session.serial}" if session.serial else \
                "Aircraft serial number: not given"
            scope = ("\nFix Log scope: search_notes covers the Fix Logs of EVERY operator subscribed to Vantage. "
                     "Each entry names its operator and that operator's aircraft type; an entry from a different "
                     "aircraft type is weaker evidence, so say so when you cite it.") if session.all_fixes else ""
            budget = (f"\nQuestion limit: you may ask at most {session.question_limit} yes/no questions in total for "
                      f"this diagnosis, in batches of up to {config.QUESTIONS_PER_BATCH}.")
            content.append({"type": "text", "text": f"{library_overview(session.org_id)}{scope}{budget}\n\n"
                                                    f"{serial_line}\n\nReported problem:\n{user_text}"})
        else:
            parts = []
            if extend:
                parts.append(f"The technician has allowed {config.QUESTION_EXTENSION} more questions (limit now "
                             f"{session.question_limit}, {len(session.qa)} used). Ask the most useful remaining "
                             "questions, then submit an updated diagnosis.")
            if user_text:
                parts.append(f"Technician follow-up:\n{user_text}")
            if parts:
                content.append({"type": "text", "text": "\n\n".join(parts)})
        if not content:
            content.append({"type": "text", "text": "Continue."})
        session.messages.append({"role": "user", "content": content})

        system = SYSTEM_PROMPT.format(aircraft=session.aircraft_type, batch=config.QUESTIONS_PER_BATCH)
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

            if len(tool_uses) == 1 and tool_uses[0].name == "ask_questions":
                block = tool_uses[0]
                remaining = session.question_limit - len(session.qa)
                if remaining <= 0:
                    session.messages.append({"role": "user", "content": [{
                        "type": "tool_result", "tool_use_id": block.id, "is_error": True,
                        "content": f"Question limit reached ({session.question_limit}). Submit your diagnosis now with "
                                   "what you have; set more_questions_would_help if more answers would materially "
                                   "narrow it."}]})
                    continue
                qs = (block.input.get("questions") or [])[:min(remaining, config.QUESTIONS_PER_BATCH)]
                if not qs:
                    session.messages.append({"role": "user", "content": [{
                        "type": "tool_result", "tool_use_id": block.id, "is_error": True,
                        "content": "No questions were given."}]})
                    continue
                start_no = len(session.qa) + 1
                session.pending_questions = [{"id": f"q{start_no + i}", "question": q["question"], "why": q["why"],
                                              "how_to_check": q["how_to_check"]} for i, q in enumerate(qs)]
                session.pending_tool_use = block.id
                session.pending_kind = "questions"
                step = {"kind": "questions", "text": f"Asked {len(qs)} question{'s' if len(qs) != 1 else ''} "
                                                     f"({start_no}–{start_no + len(qs) - 1} of up to {session.question_limit})"}
                session.trace.append(step)
                emit({"type": "step", **step})
                return {"type": "questions", "session_id": session.id, "intro": block.input.get("intro", ""),
                        "questions": session.pending_questions, "used_before": len(session.qa),
                        "limit": session.question_limit, "trace": session.trace}

            submit = next((b for b in tool_uses if b.name == "submit_diagnosis"), None)
            if submit is not None and len(tool_uses) == 1:
                result = build_result(submit.input, session.viewed, session.org_id, session.seen_notes,
                                      all_orgs=session.all_fixes)
                result["answers"] = list(session.qa)
                result["questions_used"] = len(session.qa)
                result["question_limit"] = session.question_limit
                session.pending_tool_use = submit.id
                session.pending_kind = "diagnosis"
                label = session.question_text if session.diagnosis_id is None else f"(updated) {session.question_text}"
                session.diagnosis_id = db.save_diagnosis(label, session.serial, result, session.trace,
                                                         org_id=session.org_id, created_by=session.user_name,
                                                         cross_org=session.all_fixes)
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
            out = tool_search_library(inp, session.org_id)
        elif name == "view_page":
            page = ingest.get_page(inp["doc_id"], inp["page"]) if db.org_document(session.org_id, inp["doc_id"]) else None
            label = f"page {inp['page']}"
            if page:
                bits = [page.get("ata"), f"p.{page['page_label']}" if page.get("page_label") else None, page.get("subject")]
                label += " — " + " ".join(b for b in bits if b)
            step = {"kind": "view", "text": f"Opened {label}", "doc_id": inp["doc_id"], "page": inp["page"]}
            out = tool_view_page(inp, session.viewed, session.org_id)
        elif name == "search_notes":
            where = "every operator's Fix Log" if session.all_fixes else "the Fix Log"
            step = {"kind": "notes", "text": f"Checked {where}: “{inp['query']}”"}
            out = tool_search_notes(inp, session.org_id, session.seen_notes, session.all_fixes)
        elif name == "locate_on_page":
            step = {"kind": "locate", "text": f"Located {', '.join(inp['terms'][:6])} on page {inp['page']}",
                    "doc_id": inp["doc_id"], "page": inp["page"]}
            out = tool_locate(inp, session.org_id)
        elif name in ("submit_diagnosis", "ask_questions"):
            step = None
            out = f"Call {name} on its own, without other tool calls in the same turn."
            return {"type": "tool_result", "tool_use_id": block.id, "content": out, "is_error": True}
        else:
            return {"type": "tool_result", "tool_use_id": block.id, "content": f"Unknown tool {name}", "is_error": True}
        session.trace.append(step)
        emit({"type": "step", **step})
        return {"type": "tool_result", "tool_use_id": block.id, "content": out}
    except Exception as e:  # report tool failures back to the model so it can recover
        return {"type": "tool_result", "tool_use_id": block.id, "content": f"Error: {e}", "is_error": True}
