# Vantage

**AI-assisted maintenance manuals, by Torwood Aerospace Group.**

Vantage is a maintenance library for one aircraft type. You upload your manuals, wiring diagrams
and troubleshooting notes. Then you describe a defect, and the AI diagnoses it **from your data**.
It shows how it reached the answer and opens the schematic pages it used, with the relevant
components and wires highlighted. Vantage is designed to be used mainly on an iPad.

Built and tested against the *Cessna Model 441 Conquest / Conquest II Electrical Troubleshooting
Manual*, a 354-page scanned PDF with an OCR text layer.

## What it does

| | |
|---|---|
| **Library** | Upload PDFs (manuals, wiring diagrams, service bulletins), photos or scans of diagrams, and `.txt` / `.md` notes. Every page is indexed for search. For manual pages it also tries to read the ATA number (e.g. `24-30-02`), the printed page number, the circuit name and the serial-number effectivity from the page footer. |
| **Diagnose** | Describe the symptom and, optionally, the aircraft serial number. The AI works the way a technician would: it searches the indexes, finds the circuit, opens the schematic and circuit-description pages, traces power from source to ground, checks effectivity, and reads your notes and past fixes. You can watch each step as it happens. |
| **Answer** | A ranked list of probable causes, isolation steps (what to measure, the expected reading, and what an abnormal reading means), the reasoning, safety notes, and the questions that would narrow the fault down. |
| **Highlighted diagrams** | Each referenced page opens in the viewer with the cited components, wire numbers and labels outlined, colour-coded by probable cause. You can zoom in, and clicking an item in the legend jumps to it. Highlights come from the page's OCR word positions, so they sit on the actual printed labels. |
| **Follow-ups** | Report a test result ("24 V at the K45 coil, still doesn't close") and the AI continues from where it left off. |
| **Questions** | Before diagnosing, Vantage asks yes/no questions with **Yes / No / Unsure** tick boxes, up to 5 at a time, to rule causes in or out. There are at most 15 per diagnosis; when the limit is reached, **Ask 5 more questions** continues in steps of 5. The diagnosis lists your answers and what they **ruled out**. |
| **Fix Log** | Engineers record problems they have solved: the symptom, what they found, what fixed it, the aircraft, and the system. The AI searches the Fix Log on every diagnosis. |
| **Previously resolved** | Matching Fix Log entries appear in their own gold box at the top of the diagnosis, labelled *Strong match* or *Related*. Each one shows who fixed it, on which aircraft and when, and why it is relevant. Probable causes that a past fix confirms carry a *Previously resolved* tag. |
| **Learning loop** | When an aircraft is fixed, record the fix on the diagnosis. It goes straight into the Fix Log. |

Every page the AI cites is one it opened during that diagnosis. A cited page it did not open is
flagged in the answer.

## Subscription model

Vantage is a website that organisations subscribe to. Each subscribing organisation has:

- **Private data.** Its own library, Fix Log, diagnoses and team. No organisation can see another
  organisation's data. Every request and every AI tool call is filtered by the user's
  organisation, and the tests check this.
- **Its own aircraft type,** which is given to the AI with every diagnosis.
- **Administrators,** who add and remove their engineers on the **Organisation** page.
- **A status:** `trial`, `active` or `suspended`. A suspended organisation's users can sign in,
  but they see a notice and can't use the data until it is reactivated.

**Fix Log access.** A subscriber's Fix Log is visible only to that subscriber's own users, and its
diagnoses draw only on its own entries. Torwood platform administrators can also read every
subscriber's Fix Log, read-only. On the Fix Log page, a *Showing* selector switches between your own
organisation, all subscribers, or a single subscriber. Diagnoses run from a platform administrator's login also
search **every operator's** Fix Log. Each past fix in the *Previously resolved* box is tagged with
the operator and its aircraft type. Those diagnoses are kept out of everyone else's History, even
other users in the administrator's own organisation. Subscribers can't see this selector, and the
server refuses those requests from them. **Tell subscribers about this access in the subscription
terms and privacy policy.** Entries contain their engineers' names and their aircraft details.

**Torwood staff** are platform administrators. On the Organisation page they also see a
**Subscribers** section, where they can create an organisation with its first administrator and
suspend or reactivate an organisation. The same tasks are available from the command line:

```bash
python -m app.cli create-org "Example Air Services" --aircraft "Cessna 441 Conquest"
python -m app.cli orgs
python -m app.cli add-user admin@example.com --org 2 --name "Chief Engineer" --admin
python -m app.cli set-status 2 suspended
```

Billing isn't connected yet. Monthly payments need a payment provider (for example Stripe) set up
in Torwood's name. Once that exists, a subscription that starts, renews or lapses will set the
organisation's status automatically.

## Using it on an iPad

- **Layout:** in landscape, navigation sits on the left, with the diagnosis and the diagram side by
  side. In portrait, tabs run along the bottom, and tapping a reference opens the diagram full screen.
- **Diagrams:** pinch to zoom, drag to move, and double-tap to zoom in or back out. Tap a highlight
  to see why it matters, or tap an item in the legend to jump to it.
- **Typing:** tap the microphone on the iPad keyboard to dictate a defect.
- **Adding pages:** in the Library, **Take photo** adds a page straight from the camera.
- **Install it like an app:** open Vantage in Safari, tap **Share → Add to Home Screen**. It then
  opens full screen from its own icon.

## Hosting online

Vantage runs on a server, and iPads open it in Safari. The repository includes a `Dockerfile`, so it
runs on any container host (Render, Railway, Fly.io, or your own server):

1. Create a web service from this repository using the Dockerfile.
2. Attach a **persistent disk** mounted at `/data`. This holds the library database, the uploaded
   manuals and the page cache. Without it, uploads are lost on every restart.
3. Set these environment variables:
   - `ANTHROPIC_API_KEY`
   - `MX_AIRCRAFT_TYPE`
   - `VANTAGE_ADMIN_EMAIL` and `VANTAGE_ADMIN_PASSWORD` (creates the first Torwood platform administrator)
4. Open the service's https address on the iPad and sign in.

Sign-in is required as soon as one user exists. Add more users from the server's shell:

```bash
python -m app.cli add-user tech@example.com --org 1 --name "A. Technician"
python -m app.cli set-password tech@example.com
```

Running locally with no users configured skips sign-in, which is convenient on your own computer.

## Theme

Vantage uses the AeroQuiz / Torwood Aerospace Group look: navy `#162943`, gold `#dea652`, and the
Inter typeface. All colours and fonts are in `app/static/theme.css`. The Vantage and Torwood
emblems are drawn as SVG in `app/static/index.html` and `login.html`. Swap in official logo artwork
there if you have it.

## Run it on your own computer (desktop icon)

See **START HERE.txt**. In short: install Python 3.10 or newer, run the "Install desktop icon"
file for your system once, then double-click the Vantage icon. `launcher.py` sets everything up
on first start, asks for the API key (saved in `settings.env`) and opens the browser.

**On an iPad over Wi-Fi:** add a sign-in on the Organisation page, set `SHARE_ON_NETWORK=yes` in
`settings.env` and restart. The launcher then prints the address to open in Safari. It refuses to
share until a sign-in exists, so the library is never open to the whole network.

## Setup (local, manual)

Requires Python 3.10+.

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...                     # needed for diagnosis and AI page reading
export MX_AIRCRAFT_TYPE="Cessna 441 Conquest / Conquest II"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open <http://localhost:8000>, go to **Library** and upload your files.

For a large batch, you can import from the command line instead:

```bash
python -m app.cli import manuals/*.pdf --org 1 --category manual
python -m app.cli import notes/*.md --org 1 --category notes
```

### Optional: have the AI read every page

Scanned manuals have noisy OCR, for example `AIRC~AFT` and `FAG E 1 00`. Choosing **AI-read** on a
document (or `--enrich` on import) has Claude read each page image once. It records a clean circuit
name, ATA number, effectivity, a summary, and the components, wire numbers and circuit breakers on
the page. This makes search noticeably better, and it is the only way photos of diagrams become
searchable. It makes one API request per page. Diagnosis works without it.

## Configuration

| Variable | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Anthropic API key |
| `MX_AIRCRAFT_TYPE` | Cessna 441 Conquest / Conquest II | Shown in the UI and given to the AI |
| `MX_DATA_DIR` | `./data` | Database, uploaded files, rendered page cache |
| `MX_DIAGNOSIS_MODEL` | `claude-opus-5-5` | Model used for diagnosis |
| `MX_DIAGNOSIS_EFFORT` | `high` | `low` / `medium` / `high` / `xhigh` / `max` |
| `MX_ENRICH_MODEL` | `claude-opus-5-5` | Model used for AI page reading |
| `MX_MAX_AGENT_TURNS` | `24` | Upper limit on research steps per diagnosis |

Everything is stored locally in `data/`: a SQLite database with full-text search, the original
files, and cached page images. Back up that folder to keep your library.

## How it works

```
app/
  ingest.py     PDF/image/text -> pages; OCR clean-up; ATA / page / effectivity / circuit from the footer
  search.py     SQLite FTS5 (BM25) search over pages and notes
  highlight.py  finds labels on a page from OCR word boxes (handles O/0, I/1, S/5, B/8 errors,
                labels stacked over several lines, wire-run prefixes such as P25 -> P25A20, P25B20)
  agent.py      the diagnosis agent: Claude with tools search_library, view_page (page image + OCR text),
                search_notes, locate_on_page, submit_diagnosis (structured result)
  enrich.py     optional per-page AI reading
  auth.py       users, hashed passwords and sign-in sessions
  main.py       FastAPI server; the diagnosis streams its progress to the browser (server-sent events)
  static/       browser UI (no build step), iPad-first; theme.css holds the brand colours and fonts
```

Notes on the Claude API usage:

- Adaptive thinking is on, and its summaries are streamed to the progress view.
- Prompt caching is on.
- Strict tool schemas guarantee the diagnosis is well-formed.
- Server-side refusal fallback is enabled (`fallbacks: "default"`). If the main model declines a
  request, the API re-runs it on a fallback model instead of failing.

Run the tests with `python -m pytest tests`. They use a synthetic manual page and a scripted
stand-in for the API, so no key is needed.

## Important

This is decision support for qualified maintenance personnel. Its answers come from the data you
upload and may be wrong or incomplete. Always verify against the current approved maintenance data,
and follow your organisation's procedures before any work on the aircraft.
