# Workday AI Research Agent (GitHub Pages + Actions)

This repo publishes daily and weekly Workday/AI briefs to `/docs` (served by GitHub Pages) and emails the HTML via Gmail.

## Setup

1. **GitHub Pages**  
   Settings → Pages → Source: Deploy from a branch → Branch: `main` → Folder: `/docs`.

2. **Secrets (Settings → Secrets and variables → Actions):**  
   - `OPENAI_API_KEY`  
   - `EMAIL_FROM` (Gmail address)  
   - `EMAIL_TO` (one or more emails, comma-separated)  
   - `GMAIL_USERNAME` (same as EMAIL_FROM)  
   - `GMAIL_APP_PASSWORD` (Gmail app password)

3. **Schedules (UTC):**  
   - Daily: `30 11 * * 1-5` (7:30 AM ET during DST)  
   - Weekly: `30 12 * * 6` (8:30 AM ET during DST)

4. **Manual run:**  
   Actions → select workflow → Run workflow → choose branch.

## Model

- Default model is `gpt-4.1` (override with env `OPENAI_MODEL`).
- Automatic fallbacks: `OPENAI_MODEL` → `gpt-4.1` → `gpt-4.1-mini` → `gpt-4o-mini` → `gpt-4o` → `o4-mini`.
- Responses API now uses `text.format` for structured output instead of top-level `response_format`.
- Chat Completions avoids `temperature` (some models only support default=1) and prefers `response_format={"type":"json_object"}` when available.
- Set `PRESERVE_MODEL_HTML=1` to render the model's `html_body` exactly. Default is `0`, which rewrites/normalizes links so every anchor resolves.
- Set `OPENAI_REQUIRE_LIVE=1` to fail fast if the script would otherwise fall back to the local preview stub. Useful for CI or manual runs where a live OpenAI response is mandatory.

## Outputs

- Daily page: `/docs/index.html`  
- Weekly page: `/docs/weekly.html`

## Notes

- To accommodate DST shifts, adjust cron or run hourly and gate inside Python.
- Mail uses Gmail SMTP via app password.
- Only `requests` is installed; everything else is stdlib.

## Verify that app output matches ChatGPT JSON

You can run a local verification that captures the raw JSON returned by OpenAI and compares it to what the site and email would show.
Every published HTML page also includes a debug footer showing the exact prompt, live/stub status, and—when available—the raw JSON returned by OpenAI.

```bash
# Verify daily (uses OPENAI_API_KEY if set; otherwise runs stub):
python3 scripts/generate_report.py verify daily

# Outputs go to docs/debug/ with filenames like:
#  - daily-YYYY-MM-DD-raw-http.json      (Responses API raw HTTP JSON)
#  - daily-YYYY-MM-DD-payload.json       (parsed payload as used by the app)
#  - daily-YYYY-MM-DD-verify.json        (verification report)
#  - daily-YYYY-MM-DD-chat-raw-http.json (Chat Completions raw JSON)
#  - daily-YYYY-MM-DD-chat-payload.json  (Chat Completions parsed)
```

The verification report ensures:
- `html_body` is preserved in Pages and email only when `PRESERVE_MODEL_HTML=1`.
- `run_date` and `type` from the model are not overridden (they are only set if missing).
