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

## How to tell whether a run used live OpenAI data

Every artifact the pipeline produces includes debug metadata so you can confirm
that the request reached OpenAI and returned a live payload:

- The generated HTML (for example `/docs/index.html`) prints an **OpenAI
  Debug** footer that shows the prompt that was sent, the selected endpoint,
  model, and whether the run is flagged as a “Live OpenAI response” or a
  “Stub preview.” When the call succeeds, the footer also embeds the full raw
  JSON returned by the API, exactly as received.
- Each invocation of `call_openai` stores `_debug_live=True` along with the raw
  HTTP JSON when a Responses or Chat Completions request succeeds. If the
  script must fall back to local preview data (for example because
  `OPENAI_API_KEY` is not set), `_debug_live` is `False` and the footer labels
  the run as a stub.
- Additional artifacts in `docs/debug/` capture the parsed payload and raw HTTP
  JSON per run, making it easy to audit what OpenAI returned versus what is
  published.

Together these signals let you trace each brief back to the exact API response
that generated it and quickly spot when the pipeline is operating in stub mode.

## Make results reliably real

To ensure the brief reflects verifiable, current items instead of unconstrained model output:

- **Enforce live API responses**
  - Set `OPENAI_REQUIRE_LIVE=1` so runs fail fast rather than publishing the local preview.
  - Example (daily workflow step):

  ```yaml
  - name: Generate daily report
    env:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      OPENAI_REQUIRE_LIVE: "1"
      EMAIL_FROM: ${{ secrets.EMAIL_FROM }}
      EMAIL_TO: ${{ secrets.EMAIL_TO }}
      GMAIL_USERNAME: ${{ secrets.GMAIL_USERNAME }}
      GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
    run: python scripts/generate_report.py daily
  ```

- **Add retrieval and verification before summarization**
  - Fetch candidate links via a trusted source (e.g., Bing Web Search API, NewsAPI, vendor newsroom RSS) constrained to target domains (Workday, Deloitte, major SIs, reputable tech press).
  - Validate links with HTTP HEAD/GET (short timeouts); keep only 2xx responses and acceptable `content-type` (e.g., `text/html`).
  - Extract publication timestamps from Open Graph / schema.org Article metadata or source APIs; filter to the last-48h (daily) or week window (weekly).
  - Pass the curated URLs and brief excerpts into the model as context; instruct the model to summarize only from the provided sources.
  - If no items pass validation, print “No material items in last 48 hours; including nearest recent updates.” and include only verified nearest recents.

- **Optional: commit debug artifacts for audit**
  - Also commit `docs/debug/*` so the raw HTTP and parsed payloads for each run are visible in Pages.
  - Example addition to the commit step:

  ```bash
  git add docs/index.html docs/debug/*.json
  git commit -m "Daily brief + debug: $(date -u +'%Y-%m-%dT%H:%M:%SZ')" || echo "No changes to commit"
  ```

These steps keep links launchable, dates accurate, and the brief grounded in verifiable sources while still leveraging the model for concise synthesis.
