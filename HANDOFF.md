# Vantage: project handoff

Read this before changing anything. It records what Vantage is, how it works, the decisions the
owner has made, and what is still open.

## 1. What Vantage is

**Vantage** is an AI-assisted maintenance manual and troubleshooting web app for aviation
maintenance organisations. It is **a Torwood Aerospace Group product**, a sister to Torwood's
AeroQuiz platform (Part 66 exam practice).

- A **subscribing organisation** uploads its own manuals, wiring diagrams and photos for its
  aircraft type, and its engineers record solved defects in the **Fix Log**.
- An engineer describes a defect. Claude researches **that organisation's own data** and returns:
  - ranked probable causes;
  - isolation steps (what to measure, expected value, what an abnormal result means);
  - the reasoning;
  - safety notes;
  - the cited pages, opened in a viewer with the relevant components and wires **highlighted on
    the schematic**.
- Matching Fix Log entries appear in a gold **"Previously resolved"** box. Each entry is labelled
  *Strong match* or *Related*, and the causes they confirm are tagged.
- Designed to be used **mainly on an iPad**, in Safari or added to the Home Screen.
- Business model: organisations **subscribe monthly**. The owner (Torwood) is the **platform
  administrator**.

## 2. Decisions made by the owner (keep these)

| Topic | Decision |
|---|---|
| Name | **Vantage**, "By Torwood Aerospace Group" |
| Look | Same as AeroQuiz: navy `#162943`, tile `#20395c`, gold `#dea652` with near-black text `#191406`, muted `#acb8cf`, **Inter** font. All in `app/static/theme.css`. |
| Logo | White "V" with a gold triangle on a navy rounded tile, echoing AeroQuiz's "T" tile. Files in `brand/`. Drawn by Claude, not a designer: get it refined and trademark-searched before launch. |
| Wording | **Fix Log** for the section where engineers record fixes. **Previously resolved** for the box in diagnoses. |
| Privacy | Each organisation's manuals, Fix Log and diagnoses are visible **only to that organisation**. |
| Owner access | The owner's login (platform admin) can **read every organisation's Fix Log**, read-only, and the owner's **diagnoses draw on every operator's Fix Log**, each fix tagged with operator and aircraft type. Those diagnoses are hidden from everyone else, including other users in the owner's organisation. Manuals are **never** shared across organisations, not even with the owner. |
| Manuals | Vantage is a tool. Customers upload their own manuals and keep them current. Torwood does not supply manuals. |
| Platform | iPad first. Hosted online for customers (Docker image). Also runs locally from a desktop icon (`launcher.py`). |

## 3. How it works

```
app/
  main.py       FastAPI server. Every endpoint is scoped to the user's organisation via ctx().
                Fix Log = /api/fixes, team = /api/org, owner tools = /api/admin/*.
                /api/diagnose streams progress (server-sent events).
  agent.py      The diagnosis agent: a manual tool-use loop with Claude.
                Tools: search_library, view_page (page image + OCR text), search_notes (Fix Log),
                locate_on_page, submit_diagnosis (strict JSON schema).
                build_result() attaches highlight boxes and Fix Log cards, and drops anything
                outside the organisation, any page not opened, and any Fix Log entry not retrieved.
  ingest.py     PDF/image/text -> pages. Reads ATA number, printed page, circuit name and
                serial-number effectivity from the page footer. Renders pages with PyMuPDF.
  highlight.py  Finds labels on a page from the OCR word boxes. Handles O/0, I/1, S/5, B/8, G/6
                scan errors, labels stacked over several lines, wire-run prefixes (P25 matches
                P25A20) and underlines read as a leading "L". Codes containing digits must match
                exactly (C4A20 is not C4B20).
  search.py     SQLite FTS5 (BM25) over pages and the Fix Log, filtered by organisation.
  db.py         Schema and migrations: organisations, documents, pages, notes (= Fix Log),
                diagnoses (cross_org flag).
  auth.py       Users (PBKDF2 passwords), sessions, organisations. Roles: organisation admin;
                platform admin (Torwood).
  enrich.py     Optional "AI-read every page": Claude reads each page once to clean up the
                metadata and improve search. One request per page.
  cli.py        create-org, orgs, set-status, add-user, set-password, import, enrich.
  static/       Vanilla HTML/CSS/JS, no build step. theme.css = brand tokens.
                Pinch-zoom diagram viewer, side rail in landscape, bottom tabs in portrait.
launcher.py     Local desktop start: creates .venv, installs requirements, asks for the API key
                (saved to settings.env), starts uvicorn, opens the browser.
brand/          Logo files (SVG and PNG).  desktop/ = app icons for the desktop shortcut.
tests/          pytest. Uses a synthetic manual page and a scripted stand-in for Claude; no key needed.
Dockerfile      For cloud hosting; mount a persistent volume at /data.
```

**Claude API usage** (in `agent.py`):
- Model `claude-opus-5-5`, set by `MX_DIAGNOSIS_MODEL`.
- Adaptive thinking with summaries streamed to the UI; effort `high`.
- Prompt caching (top-level `cache_control`).
- Strict tool schemas.
- Server-side refusal fallback: `betas=["server-side-fallback-2026-07-01"], fallbacks="default"`.
- Assistant content is appended back unchanged, so thinking blocks are preserved.

**Key rule:** every query that serves a signed-in user must be filtered by `org_id`.
`tests/test_app.py::test_organisations_are_isolated` checks this. Extend it whenever you add an
endpoint or a tool.

**Local mode:** with no users in the database, sign-in is skipped and everything is
organisation 1. This is how the desktop launcher runs.

## 4. Running and testing

```bash
python launcher.py                      # or double-click the desktop icon (see START HERE.txt)
pip install -r requirements.txt && python -m pytest tests     # 6 tests, no API key needed
python -m uvicorn app.main:app --port 8000                     # run directly
```

Hosting online (Render, Railway, Fly.io…): build the Dockerfile, mount a disk at `/data`, and set
`ANTHROPIC_API_KEY`, `VANTAGE_ADMIN_EMAIL` and `VANTAGE_ADMIN_PASSWORD`. The last two create the
owner's platform-admin login on first start. Details in README.md.

## 5. What has been verified, and what hasn't

**Verified:**
- Ingestion, search, metadata parsing and highlighting on a real 354-page scanned Cessna 441
  Electrical Troubleshooting Manual.
- Two proof-of-concept diagnoses, worked through by hand with the same tools the agent uses:
  - **"Flaps won't move either way"** found the shared CB4 feed, the hydraulic loading valve L15
    (via a cross-reference to chapter 32) and the common ground.
  - **"Cabin fan won't work"** correctly reported that the manual only covers the circuit to
    CB31/CB51 → H60A16 → C146-D.
- The whole UI on simulated iPad Pro 11" (landscape and portrait) in Chromium.
- Organisation isolation, sign-in, team management and the owner's cross-organisation access,
  in the tests.

**Not yet verified:**
- **Any call to the real Claude API.** No API key has been available. The agent loop, prompt and
  strict schemas are tested only against scripted responses. First job with a key: run real
  diagnoses, check quality, measure cost per diagnosis.
- `enrich.py` against the real API.
- Pinch, drag and double-tap on a real iPad in Safari.
- The desktop launchers on real Windows and Mac machines. They were written for those systems;
  the Linux path was tested.

## 6. Open items (roughly in priority order)

1. **API key, then real diagnoses.** Tune the prompt and measure cost per diagnosis. The estimate
   is A$0.75–2.50 per diagnosis, unmeasured.
2. **Legal before the first paying customer** (Australian IP/technology lawyer):
   - Terms: the customer warrants it is licensed to upload each manual, indemnifies Torwood,
     and is responsible for revision status; Vantage is not approved data.
   - A copyright takedown contact.
   - Disclosure that documents are processed by an AI provider overseas.
   - **Disclosure of Torwood's access to, and use of, every operator's Fix Log** (it contains
     engineer names and aircraft details; Australian Privacy Act).
   - Professional indemnity insurance.
3. **Offered and not yet built:**
   - Licence-confirmation tick box at upload, recorded with user and date.
   - Document revision and date fields, shown on diagnoses with a "check current revision"
     reminder.
4. **Billing:** Stripe (or similar) in Torwood's name. Subscription start, renewal and lapse
   should set `organisations.status` (trial / active / suspended; suspended already blocks
   access).
5. **Plan limits:** count and enforce users, aircraft types and monthly diagnoses, and show usage
   on the Organisation page.
6. **Several aircraft types per organisation** (asked, not yet answered). Currently one type per
   organisation.
7. A professional pass on the logo, and a trademark search on "Vantage".

## 7. Commercial notes discussed (for context)

- Suggested pricing (AUD per month, excluding GST):
  - Starter $149: 1 aircraft type, 3 users, ~40 diagnoses.
  - Workshop $349: 3 aircraft types, 10 users, ~150 diagnoses.
  - Operator $790: unlimited types and users, ~500 diagnoses.
  - Annual billing at 10 months' price; 30-day trial; founding-customer discount.
- Market: CASA lists about 180 Part 145 maintenance organisations, plus smaller maintenance
  shops not on that register (count unknown).
- Rough yearly model, after AI, hosting and fixed costs but before the owner's time and tax:
  - 10 customers: ≈ A$35k revenue, ≈ A$12k left over.
  - 30 customers: ≈ A$106k revenue, ≈ A$54k left over.
  - 75 customers: ≈ A$264k revenue, ≈ A$151k left over.
  - The biggest unknown is the AI cost per diagnosis.
