"""End-to-end tests with a synthetic manual and a scripted stand-in for the Claude API."""
import json
from types import SimpleNamespace

import pymupdf
import pytest


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    from app import config, db
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "library.db")
    db.reset_connection()
    yield tmp_path
    db.reset_connection()


def make_manual(path):
    pdf = pymupdf.open()
    p = pdf.new_page(width=610, height=792)
    p.insert_text((60, 60), "CESSNA AIRCRAFT CO. 441 ELECTRICAL TROUBLESHOOTING MANUAL", fontsize=9)
    p.insert_text((200, 120), "BATTERY", fontsize=8)
    p.insert_text((200, 130), "NO. 1", fontsize=8)
    p.insert_text((200, 140), "CONTACTOR", fontsize=8)
    p.insert_text((200, 150), "(K45)", fontsize=8)
    p.insert_text((330, 120), "BATTERY", fontsize=8)
    p.insert_text((330, 130), "NO. 2", fontsize=8)
    p.insert_text((330, 140), "CONTACTOR (K44)", fontsize=8)
    for i, w in enumerate(["P86B12", "P82B12", "P80A20", "P81A20", "P83A0N", "P85A0N"]):
        p.insert_text((150 + 60 * i, 300), w, fontsize=8)
    p.insert_text((120, 500), "MASTER BATTERY SWITCH (S92)", fontsize=8)
    p.insert_text((470, 730), "24-30-02", fontsize=10)
    p.insert_text((470, 745), "PAGE 100", fontsize=10)
    p.insert_text((40, 760), "BATTERY CONTROL", fontsize=10)
    p.insert_text((300, 760), "SERIAL 0001 TO 0259", fontsize=10)
    t = pdf.new_page(width=610, height=792)
    t.insert_textbox(pymupdf.Rect(40, 60, 570, 600),
                     "The battery contactors are closed when the battery switches are placed on. "
                     "Ground for the contactor coils is provided through the master battery switch. " * 6, fontsize=9)
    t.insert_text((470, 745), "24-30-02 PAGE 102", fontsize=10)
    t.insert_text((40, 760), "BATTERY CONTROL", fontsize=10)
    t.insert_text((300, 760), "SERIAL 0001 AND ON", fontsize=10)
    pdf.save(path)


def test_ingest_metadata_search_and_highlight(app_env):
    from app import highlight, ingest, search
    src = app_env / "manual.pdf"
    make_manual(src)
    doc_id = ingest.ingest_file(src, "manual.pdf", "Test manual")
    page = ingest.get_page(doc_id, 1)
    assert page["ata"] == "24-30-02"
    assert page["page_label"] == "100"
    assert page["subject"] == "BATTERY CONTROL"
    assert page["effectivity"] == "SERIAL 0001 TO 0259"
    assert page["page_type"] == "schematic"

    hits = search.search_pages("battery contactor K45")
    assert hits and hits[0]["doc_id"] == doc_id

    found = highlight.locate_terms(doc_id, 1, ["K45", "BATTERY NO. 1 CONTACTOR", "P86", "MASTER BATTERY SWITCH", "ZZ99"])
    assert len(found["K45"]) == 1
    assert len(found["BATTERY NO. 1 CONTACTOR"]) == 1
    assert len(found["P86"]) == 1          # wire-run prefix matches P86B12
    assert len(found["MASTER BATTERY SWITCH"]) == 1
    assert found["ZZ99"] == []
    box = found["K45"][0]
    assert 0 < box["x"] < 1 and 0 < box["y"] < 1

    png = ingest.render_page(doc_id, 1, 800)
    assert png[:4] == b"\x89PNG"


def test_ocr_cleanup():
    from app import highlight, ingest
    assert ingest.clean_text("SSEERRIIAALL 0001") == "SERIAL 0001"
    assert highlight.key("P8OA2O") == highlight.key("P80A20")


# ---------------------------------------------------------------- scripted agent run

def _tool_use(id_, name, inp):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot
        content = self.script.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        return FakeStream(SimpleNamespace(content=content, stop_reason=stop))


def test_agent_loop_produces_highlighted_diagnosis(app_env):
    from app import agent, db, ingest
    src = app_env / "manual.pdf"
    make_manual(src)
    doc_id = ingest.ingest_file(src, "manual.pdf", "Test manual")
    db.add_note("K45 intermittent", "Corroded ground at G202 caused K45 to drop out.", tags="battery")

    diagnosis = {
        "summary": "No.1 battery contactor K45 is not closing.", "system": "Battery Control (24-30-02)",
        "confidence": "medium", "effectivity_note": "Page applies to 0001-0259.",
        "probable_causes": [{"rank": 1, "cause": "K45 coil open", "likelihood": "high", "reasoning": "..."}],
        "troubleshooting_steps": [{"step": 1, "action": "Measure voltage at K45 coil", "expected": "24 V",
                                   "if_abnormal": "Check P86B12", "cause_ranks": [1]}],
        "reasoning": "Traced from the battery switch to K45.",
        "references": [{"doc_id": doc_id, "page": 1, "why": "Battery control schematic",
                        "highlights": [{"term": "K45", "note": "suspect", "cause_rank": 1},
                                       {"term": "BATTERY NO. 1 CONTACTOR", "note": "", "cause_rank": 1},
                                       {"term": "NOT-ON-PAGE", "note": "", "cause_rank": 0}],
                        "regions": [{"x": 0.2, "y": 0.3, "w": 0.1, "h": 0.05, "note": "wire run", "cause_rank": 1}]}],
        "safety_notes": ["Disconnect the battery."], "missing_info": [],
    }
    client = FakeClient([
        [SimpleNamespace(type="thinking", thinking="Battery contactor circuit."),
         _tool_use("t1", "search_library", {"query": "battery contactor", "page_type": "any"}),
         _tool_use("t2", "search_notes", {"query": "K45"})],
        [_tool_use("t3", "view_page", {"doc_id": doc_id, "page": 1})],
        [_tool_use("t4", "locate_on_page", {"doc_id": doc_id, "page": 1, "terms": ["K45"]})],
        [_tool_use("t5", "submit_diagnosis", diagnosis)],
        [SimpleNamespace(type="text", text="Then check ground G202.")],
    ])
    events = []
    session = agent.get_or_create_session(None, "0123")
    out = agent.run(session, "No.1 battery contactor will not close", events.append, client=client)

    assert out["type"] == "diagnosis"
    ref = out["result"]["references"][0]
    assert ref["verified"] is True and ref["ata"] == "24-30-02"
    found = {h["term"]: len(h["boxes"]) for h in ref["highlights"]}
    assert found == {"K45": 1, "BATTERY NO. 1 CONTACTOR": 1, "NOT-ON-PAGE": 0}
    kinds = [e.get("kind") for e in events if e["type"] == "step"]
    assert kinds == ["thinking", "search", "notes", "view", "locate"]

    # the page image went to the model, and the notes search found the note
    view_result = client.requests[2]["messages"][-1]["content"][0]["content"]
    assert any(c["type"] == "image" for c in view_result)
    notes_result = client.requests[1]["messages"][-1]["content"][1]["content"]
    assert "G202" in notes_result
    first_user = client.requests[0]["messages"][0]["content"][-1]["text"]
    assert "0123" in first_user and "Test manual" in first_user

    # follow-up continues the same conversation and closes the pending submit_diagnosis call
    out2 = agent.run(session, "Coil reads 24 V", events.append, client=client)
    assert out2 == {"type": "message", "text": "Then check ground G202.", "session_id": session.id, "trace": session.trace}
    follow = client.requests[-1]["messages"][-1]["content"]
    assert follow[0]["type"] == "tool_result" and follow[0]["tool_use_id"] == "t5"
    assert json.loads(db.one("SELECT result FROM diagnoses")["result"])["system"].startswith("Battery")


def test_http_api(app_env):
    from fastapi.testclient import TestClient
    from app.main import app
    src = app_env / "manual.pdf"
    make_manual(src)
    with TestClient(app) as c:
        r = c.post("/api/documents", files={"file": ("manual.pdf", src.read_bytes(), "application/pdf")},
                   data={"title": "Manual", "category": "manual"})
        assert r.status_code == 200
        doc_id = r.json()["id"]
        assert c.get("/api/documents").json()[0]["status"] == "ready"
        assert c.get(f"/api/documents/{doc_id}/pages").json()[0]["ata"] == "24-30-02"
        assert c.get(f"/api/pages/{doc_id}/1/image").headers["content-type"] == "image/png"
        loc = c.post(f"/api/pages/{doc_id}/1/locate", json={"terms": ["K44"]}).json()
        assert len(loc["K44"]) == 1
        assert c.get("/api/search", params={"q": "K45"}).json()["pages"]
        assert c.post("/api/notes", json={"title": "t", "body": "b"}).status_code == 200
        assert c.post("/api/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
        assert c.delete(f"/api/documents/{doc_id}").json() == {"ok": True}
        assert c.get("/").status_code == 200
