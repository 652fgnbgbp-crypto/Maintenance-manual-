# MX Diagnostic Assistant

An AI-assisted maintenance library for one aircraft type. You upload your manuals, wiring
diagrams and troubleshooting notes. Then you describe a defect, and the AI diagnoses it **from your
data**. It shows how it reached the answer and opens the schematic pages it used, with the
relevant components and wires highlighted.

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
| **Learning loop** | Record the confirmed fix on a diagnosis. It is saved as a troubleshooting note, and future diagnoses check it. |
| **Notes** | Add snags, known issues and fixes for the fleet. The AI searches them on every diagnosis. |

Every page the AI cites is one it opened during that diagnosis. A cited page it did not open is
flagged in the answer.

## Setup

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
python -m app.cli import manuals/*.pdf --category manual
python -m app.cli import notes/*.md --category notes
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
  main.py       FastAPI server; the diagnosis streams its progress to the browser (server-sent events)
  static/       browser UI (no build step)
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
