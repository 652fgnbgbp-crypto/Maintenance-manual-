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
    # codes one character apart are different wires/pins and must not match each other
    near = highlight.locate_terms(doc_id, 1, ["P86A12", "P80B20"])
    assert near == {"P86A12": [], "P80B20": []}
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
    fix_id = db.add_fix(1, "No.1 battery contactor drops out intermittently", "Cleaned and re-torqued ground stud G202",
                        checks="K45 coil ground resistance high", system="Battery Control 24-30-02", aircraft="0145",
                        created_by="J. Smith")

    diagnosis = {
        "summary": "No.1 battery contactor K45 is not closing.", "system": "Battery Control (24-30-02)",
        "confidence": "medium", "effectivity_note": "Page applies to 0001-0259.",
        "probable_causes": [{"rank": 1, "cause": "K45 ground G202 poor", "likelihood": "high", "reasoning": "...",
                             "previous_fix_ids": [fix_id, 999]},
                            {"rank": 2, "cause": "K45 coil open", "likelihood": "medium", "reasoning": "...",
                             "previous_fix_ids": []}],
        "previous_fixes": [{"note_id": fix_id, "match": "strong", "relevance": "Same contactor, same symptom."},
                           {"note_id": 999, "match": "partial", "relevance": "Invented entry: must be dropped."}],
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
    assert kinds == ["search", "notes", "view", "locate"]  # working notes are not shown, only research steps
    assert client.requests[0]["thinking"]["display"] == "omitted"

    # cited Fix Log entries come back as full "Previously resolved" cards; invented ids are dropped
    fixes = out["result"]["previous_fixes"]
    assert [f["note_id"] for f in fixes] == [fix_id]
    assert fixes[0]["fix"] == "Cleaned and re-torqued ground stud G202"
    assert fixes[0]["aircraft"] == "0145" and fixes[0]["engineer"] == "J. Smith" and fixes[0]["verified"] is True
    assert out["result"]["probable_causes"][0]["previous_fix_ids"] == [fix_id]
    other = db.add_fix(1, "Unrelated: cabin fan inop", "Replaced blower")
    unseen = agent.build_result({"previous_fixes": [{"note_id": other, "match": "partial", "relevance": ""}],
                                 "probable_causes": [], "references": []}, set(), 1, seen_notes={fix_id})
    assert unseen["previous_fixes"] == []  # cited but never retrieved by search_notes: dropped

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
        r = c.post("/api/fixes", json={"symptom": "Flaps inop both ways", "fix": "Reset CB4; replaced C3-43 pin",
                                       "system": "27-50-01", "aircraft": "0150"})
        assert r.status_code == 200 and r.json()["source"] == "manual"
        assert c.post("/api/fixes", json={"symptom": "x", "fix": " "}).status_code == 400
        assert c.get("/api/fixes", params={"q": "flaps"}).json()[0]["fix"].startswith("Reset CB4")
        assert c.get("/api/search", params={"q": "C3-43"}).json()["fixes"]
        assert c.post("/api/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
        assert c.delete(f"/api/documents/{doc_id}").json() == {"ok": True}
        assert c.get("/").status_code == 200


def test_sign_in(app_env, monkeypatch):
    from fastapi.testclient import TestClient
    from app import auth
    from app.main import app
    with TestClient(app) as c:
        # no users yet: open access (local use)
        assert c.get("/api/info").status_code == 200
        auth.add_user("tech@example.com", "correct horse", name="Tech")
        c.cookies.clear()
        assert c.get("/api/info").status_code == 401
        assert c.get("/", follow_redirects=False).headers["location"] == "/login"
        assert c.get("/login").status_code == 200
        assert c.get("/static/theme.css").status_code == 200
        assert c.post("/api/login", json={"email": "tech@example.com", "password": "wrong pass"}).status_code == 401
        r = c.post("/api/login", json={"email": "TECH@example.com", "password": "correct horse"})
        assert r.status_code == 200
        info = c.get("/api/info").json()
        assert info["user"]["name"] == "Tech" and info["login_required"] is True
        c.post("/api/logout")
        assert c.get("/api/info").status_code == 401


def test_organisations_are_isolated(app_env):
    """One subscriber must never see another subscriber's manuals, Fix Log or diagnoses."""
    from fastapi.testclient import TestClient
    from app import agent, auth, db, search
    from app.main import app
    src = app_env / "manual.pdf"
    make_manual(src)
    with TestClient(app) as anon:
        assert anon.get("/api/info").status_code == 200  # organisation 1 exists from start-up
    org_a = auth.create_org("Alpha Aviation", "Cessna 441")
    org_b = auth.create_org("Bravo Air", "King Air 200")
    auth.add_user("a@alpha.example", "password-a1", "Alice", is_admin=True, org_id=org_a)
    auth.add_user("b@bravo.example", "password-b1", "Bob", is_admin=True, org_id=org_b)
    auth.add_user("staff@torwood.example", "password-t1", "Torwood", is_admin=True, org_id=1, is_platform_admin=True)

    def client_for(email, pw):
        c = TestClient(app)
        assert c.post("/api/login", json={"email": email, "password": pw}).status_code == 200
        return c

    a, b = client_for("a@alpha.example", "password-a1"), client_for("b@bravo.example", "password-b1")
    doc = a.post("/api/documents", files={"file": ("m.pdf", src.read_bytes(), "application/pdf")}).json()
    a.post("/api/fixes", json={"symptom": "K45 drops out", "fix": "Cleaned G202"})
    diag_id = db.save_diagnosis("K45 drops out", "", {"summary": "s"}, [], org_id=org_a)

    assert a.get("/api/info").json()["organisation"] == "Alpha Aviation"
    assert b.get("/api/info").json()["aircraft_type"] == "King Air 200"
    assert [d["id"] for d in a.get("/api/documents").json()] == [doc["id"]]
    assert b.get("/api/documents").json() == []
    for path in (f"/api/pages/{doc['id']}/1", f"/api/pages/{doc['id']}/1/image", f"/api/documents/{doc['id']}/pages",
                 f"/api/diagnoses/{diag_id}"):
        assert a.get(path).status_code == 200, path
        assert b.get(path).status_code == 404, path
    assert b.delete(f"/api/documents/{doc['id']}").status_code == 404
    assert b.post(f"/api/pages/{doc['id']}/1/locate", json={"terms": ["K45"]}).status_code == 404
    assert b.get("/api/search", params={"q": "K45"}).json() == {"pages": [], "fixes": []}
    assert a.get("/api/search", params={"q": "K45"}).json()["pages"]
    assert b.get("/api/fixes").json() == [] and b.get("/api/diagnoses").json() == []
    fix_id = a.get("/api/fixes").json()[0]["id"]
    assert b.delete(f"/api/fixes/{fix_id}").status_code == 404

    # the AI's tools are scoped too
    sb = agent.Session("", org_id=org_b)
    assert "does not exist" in agent.tool_view_page({"doc_id": doc["id"], "page": 1}, set(), org_b)
    assert "does not exist" in agent.tool_locate({"doc_id": doc["id"], "page": 1, "terms": ["K45"]}, org_b)
    assert agent.tool_search_notes({"query": "K45"}, org_b, sb.seen_notes).startswith("No matching")
    assert search.search_pages("K45", org_id=org_b) == []
    res = agent.build_result({"references": [{"doc_id": doc["id"], "page": 1, "why": "", "highlights": [], "regions": []}],
                              "previous_fixes": [{"note_id": fix_id, "match": "strong", "relevance": ""}],
                              "probable_causes": []}, set(), org_b)
    assert res["references"] == [] and res["previous_fixes"] == []

    # team management: admins manage only their own organisation
    assert a.post("/api/org/members", json={"email": "e@alpha.example", "name": "Eng", "password": "password-e1"}).status_code == 200
    eng = client_for("e@alpha.example", "password-e1")
    assert eng.post("/api/org/members", json={"email": "x@alpha.example", "password": "password-x1"}).status_code == 403
    assert eng.get("/api/documents").json()[0]["id"] == doc["id"]
    bob_id = db.one("SELECT id FROM users WHERE email = 'b@bravo.example'")["id"]
    assert a.delete(f"/api/org/members/{bob_id}").status_code == 404
    assert a.get("/api/admin/orgs").status_code == 403

    # Torwood staff can read every subscriber's Fix Log; subscribers cannot
    staff = client_for("staff@torwood.example", "password-t1")
    b.post("/api/fixes", json={"symptom": "Prop sync hunting", "fix": "Replaced synchrophaser pickup"})
    all_fixes = staff.get("/api/admin/fixes").json()
    assert {f["org_name"] for f in all_fixes} >= {"Alpha Aviation", "Bravo Air"}
    assert [f["org_name"] for f in staff.get("/api/admin/fixes", params={"org_id": org_b}).json()] == ["Bravo Air"]
    assert staff.get("/api/admin/fixes", params={"q": "synchrophaser"}).json()[0]["org_name"] == "Bravo Air"
    assert a.get("/api/admin/fixes").status_code == 403 and b.get("/api/admin/fixes").status_code == 403
    assert [f["symptom"] for f in a.get("/api/fixes").json()] == ["K45 drops out"]
    assert staff.get("/api/fixes").json() == []  # Torwood's own organisation log stays separate

    # Diagnoses from a Torwood platform admin draw on every operator's Fix Log; nobody else's do
    sub_session = agent.get_or_create_session(None, "", org_a, "Cessna 441", "Alice", all_fixes=False)
    assert "synchrophaser" not in agent.tool_search_notes({"query": "synchrophaser"}, org_a, sub_session.seen_notes, sub_session.all_fixes)
    staff_session = agent.get_or_create_session(None, "", 1, "", "Torwood", all_fixes=True)
    found = json.loads(agent.tool_search_notes({"query": "synchrophaser K45"}, 1, staff_session.seen_notes, True))
    assert {e["operator"] for e in found} == {"Alpha Aviation", "Bravo Air"}
    b_fix = next(e["note_id"] for e in found if e["operator"] == "Bravo Air")
    res = agent.build_result({"previous_fixes": [{"note_id": b_fix, "match": "partial", "relevance": ""}],
                              "probable_causes": [], "references": []}, set(), 1, staff_session.seen_notes, all_orgs=True)
    assert res["previous_fixes"][0]["operator"] == "Bravo Air" and res["fix_scope"] == "all_operators"
    # the same citation is dropped for a subscriber, even if the id is guessed
    assert agent.build_result({"previous_fixes": [{"note_id": b_fix, "match": "partial", "relevance": ""}],
                               "probable_causes": [], "references": []}, set(), org_a, {b_fix})["previous_fixes"] == []
    # a session id can't be reused to switch scope
    assert agent.get_or_create_session(staff_session.id, "", 1, "", "x", all_fixes=False).id != staff_session.id
    # cross-operator diagnoses are hidden from other users in the admin's own organisation
    auth.add_user("colleague@torwood.example", "password-c1", "Colleague", org_id=1)
    colleague = client_for("colleague@torwood.example", "password-c1")
    cross_id = db.save_diagnosis("Prop sync hunting", "", res, [], org_id=1, cross_org=True)
    own_id = db.save_diagnosis("Battery flat", "", {"summary": "s"}, [], org_id=1)
    assert [d["id"] for d in colleague.get("/api/diagnoses").json()] == [own_id]
    assert colleague.get(f"/api/diagnoses/{cross_id}").status_code == 404
    assert colleague.post(f"/api/diagnoses/{cross_id}/resolution", json={"fix": "x"}).status_code == 404

    # Torwood staff can suspend a subscription
    assert {o["name"] for o in staff.get("/api/admin/orgs").json()} >= {"Alpha Aviation", "Bravo Air"}
    assert staff.put(f"/api/admin/orgs/{org_b}/status", json={"status": "suspended"}).status_code == 200
    assert b.get("/api/documents").status_code == 402
    assert b.get("/api/info").json()["org_status"] == "suspended"


def test_yes_no_questions_limit_and_extension(app_env):
    """The AI asks tick-box questions; at most 15 in total unless the technician allows 5 more."""
    from app import agent, config, db, ingest
    src = app_env / "manual.pdf"
    make_manual(src)
    ingest.ingest_file(src, "manual.pdf", "Test manual")

    def ask(id_, n, start=1):
        return [_tool_use(id_, "ask_questions", {
            "intro": "Cabin will not reach max differential.",
            "likely_causes": [{"cause": "Outflow valve not seating", "reason": "commonest cause"}],
            "unlikely_causes": [{"cause": "Door seal leak", "reason": "would hiss"}],
            "questions": [{"question": f"Question {start + i}?", "why": "w", "how_to_check": "",
                           "narrows": "unlikely" if i == 1 else ("bogus" if i == 2 else "likely")} for i in range(n)]})]

    diag = {"summary": "s", "system": "Battery", "confidence": "medium", "effectivity_note": "",
            "probable_causes": [{"rank": 1, "cause": "K45 coil", "likelihood": "high", "reasoning": "r",
                                 "previous_fix_ids": []}],
            "troubleshooting_steps": [], "reasoning": "r", "references": [], "previous_fixes": [],
            "eliminated": [{"cause": "Master switch", "because": "No.2 works"}],
            "more_questions_would_help": True, "safety_notes": [], "missing_info": []}
    client = FakeClient([
        ask("a1", 8),                         # asks 8 -> only 5 shown (batch size)
        ask("a2", 5, 6),                      # 5 more (10 used)
        ask("a3", 5, 11),                     # 5 more (15 used)
        ask("a4", 5, 16),                     # over the limit -> refused by the server
        [_tool_use("s1", "submit_diagnosis", diag)],
        ask("a5", 5, 16),                     # after the technician allows 5 more
        [_tool_use("s2", "submit_diagnosis", diag)],
    ])
    s = agent.get_or_create_session(None, "0123")
    out = agent.run(s, "No.1 battery contactor will not close", lambda e: None, client=client)
    assert out["type"] == "questions" and len(out["questions"]) == config.QUESTIONS_PER_BATCH
    assert out["likely_causes"][0]["cause"] == "Outflow valve not seating"
    assert out["unlikely_causes"][0]["cause"] == "Door seal leak"
    assert [q["narrows"] for q in out["questions"]] == ["likely", "unlikely", "likely", "likely", "likely"]
    assert "Never narrate" in client.requests[0]["system"]
    assert [q["id"] for q in out["questions"]] == ["q1", "q2", "q3", "q4", "q5"]
    assert "at most 15" in client.requests[0]["messages"][0]["content"][-1]["text"]

    out = agent.run(s, "", lambda e: None, client=client,
                    answers=[{"id": "q1", "answer": "yes"}, {"id": "q2", "answer": "no"}, {"id": "q3", "answer": "bogus"}])
    sent = client.requests[-1]["messages"][-1]["content"][0]
    assert sent["tool_use_id"] == "a1"
    assert "q1. Question 1? -> YES" in sent["content"] and "q2. Question 2? -> NO" in sent["content"]
    assert "q3. Question 3? -> UNSURE" in sent["content"] and "q5. Question 5? -> UNSURE" in sent["content"]
    assert out["type"] == "questions" and out["questions"][0]["id"] == "q6"

    out = agent.run(s, "", lambda e: None, client=client, answers=[])
    assert out["type"] == "questions" and out["questions"][-1]["id"] == "q15"

    out = agent.run(s, "", lambda e: None, client=client, answers=[])
    # the 4th ask was refused (limit), then the AI submitted
    refused = client.requests[4]["messages"][-1]["content"][0]
    assert refused["is_error"] and "limit reached (15)" in refused["content"]
    assert out["type"] == "diagnosis"
    r = out["result"]
    assert r["questions_used"] == 15 and r["question_limit"] == 15 and len(r["answers"]) == 15
    assert r["answers"][0] == {"question": "Question 1?", "answer": "yes", "why": "w"}
    assert r["eliminated"][0]["cause"] == "Master switch" and r["more_questions_would_help"] is True

    # technician allows 5 more
    out = agent.run(s, "", lambda e: None, client=client, extend=True)
    assert out["type"] == "questions" and [q["id"] for q in out["questions"]][0] == "q16"
    assert "allowed 5 more questions (limit now 20" in client.requests[-1]["messages"][-1]["content"][-1]["text"]
    out = agent.run(s, "", lambda e: None, client=client, answers=[{"id": "q16", "answer": "no"}])
    assert out["type"] == "diagnosis" and out["result"]["question_limit"] == 20
    assert out["result"]["questions_used"] == 20
    labels = [d["question"] for d in db.query("SELECT question FROM diagnoses ORDER BY id")]
    assert labels == ["No.1 battery contactor will not close", "(updated) No.1 battery contactor will not close"]


def test_diagnose_stream_reports_done(app_env, monkeypatch):
    """The finished event must keep type 'done' (the result's own type goes in 'kind')."""
    from fastapi.testclient import TestClient
    from app import agent
    from app.main import app
    monkeypatch.setattr(agent, "run", lambda s, q, emit, **kw: {"type": "questions", "session_id": s.id,
                                                                 "questions": [], "trace": []})
    with TestClient(app) as c:
        body = c.post("/api/diagnose", json={"question": "Flaps inop"}).text
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        assert events[-1]["type"] == "done" and events[-1]["kind"] == "questions"
        assert c.post("/api/diagnose", json={"question": ""}).status_code == 400
        assert c.post("/api/diagnose", json={"answers": [{"id": "q1", "answer": "yes"}]}).status_code == 200
