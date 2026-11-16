# Vantage

AI-assisted maintenance manual and troubleshooting web app, by Torwood Aerospace Group.
**Read HANDOFF.md first.** It holds the owner's decisions, the architecture and the open items.

- Test: `python -m pytest tests`. No API key needed; Claude is replaced by a scripted client.
- Run locally: `python launcher.py`, or `python -m uvicorn app.main:app --port 8000`.
- Multi-tenant: every query serving a signed-in user must filter by the user's `org_id`
  (`ctx(request)` in `app/main.py`). Extend `test_organisations_are_isolated` for any new
  endpoint or agent tool.
- Only platform admins (Torwood) may read across organisations: `/api/admin/*`, and diagnoses
  flagged `cross_org`. Manuals are never shared across organisations.
- Brand colours and fonts live only in `app/static/theme.css`. Logos are in `brand/`.
- The UI is iPad-first: touch targets ≥ 44px, inputs ≥ 16px, check landscape and portrait.
- Never commit manuals, `data/`, `settings.env` (it holds the API key) or screenshots.
