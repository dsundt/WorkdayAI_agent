import os
import sys
import json
import smtplib
import ssl
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo  # stdlib timezone support (Py 3.9+)

# requests is optional at runtime; tests run without network/API.
try:
    import requests  # type: ignore
except Exception:  # pragma: no cover - environment without requests
    requests = None  # type: ignore

# ====== Secrets / Env ======
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
EMAIL_FROM = os.environ.get("EMAIL_FROM", "").strip()
EMAIL_TO = os.environ.get("EMAIL_TO", "").strip()
GMAIL_USERNAME = os.environ.get("GMAIL_USERNAME", "").strip()
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").strip()

# ====== Args ======
if len(sys.argv) < 2 or sys.argv[1] not in ("daily", "weekly"):
    print("Usage: python scripts/generate_report.py [daily|weekly]")
    sys.exit(1)

RUN_TYPE = sys.argv[1]  # "daily" or "weekly"

# ====== Eastern Time Anchors ======
# Fallback to UTC if the IANA tz database is unavailable in the environment.
try:
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = timezone.utc
now_et = datetime.now(ET)
TODAY_ET = now_et.strftime("%Y-%m-%d")               # e.g., 2025-10-16
YESTERDAY_ET = (now_et - timedelta(days=1)).strftime("%Y-%m-%d")
WEEK_START_ET = (now_et - timedelta(days=7)).strftime("%Y-%m-%d")

# ====== System Prompts with Explicit ET Context ======
SYSTEM_PROMPT_DAILY = f"""
You are “Dan – Workday AI Research Agent.” Produce a JSON object that matches the schema I’ll give you exactly.

DATE CONTEXT (ET):
- TODAY_ET = {TODAY_ET}
- LAST_48H_WINDOW = from {YESTERDAY_ET} to {TODAY_ET} inclusive

Topic scope: Workday; Workday HCM (Human Capital Management); Workday AI; agentic AI for Workday; broader AI for HR technology; consultant upskilling; SI/GSI (Systems Integrators / Global Systems Integrators) competitive moves.

Rules for DAILY brief:
- Target ~250 words total.
- Prefer items published in the LAST_48H_WINDOW (from {YESTERDAY_ET} to {TODAY_ET}). If nothing relevant exists, choose the most recent credible items and explicitly say “No material items in last 48 hours; including nearest recent updates.”
- For every item, include a working public URL and state why it matters to Deloitte’s Workday practice (client conversations, offering strategy, enablement, competitive posture).
- Include the publication date for each “highlight” item in its text (e.g., “(Published: 2025-10-15)”).

Rendering constraints:
- Return JSON only (no prose, no Markdown, no code fences).
- The “html_body” must be valid minimal HTML with semantic tags and absolute links:
  - Use <h2> for the headline, <h3> for section headers, <p>, <ul>, <li>, and <a href="https://…">…</a>
  - No <script>, no external CSS, no images, no iframes.
  - Short paragraphs and bullet lists for readability.
- The “plain_text_body” must be a text-only version with full URLs visible.
""".strip()

SYSTEM_PROMPT_WEEKLY = f"""
You are “Dan – Workday AI Research Agent.” Produce a JSON object that matches the schema I’ll give you exactly.

DATE WINDOW (ET):
- WEEK_START_ET = {WEEK_START_ET}
- TODAY_ET = {TODAY_ET}
- Use items published from WEEK_START_ET through TODAY_ET inclusive.

Topic scope: Workday; Workday HCM (Human Capital Management); Workday AI; agentic AI for Workday; broader AI for HR technology; consultant upskilling; SI/GSI (Systems Integrators / Global Systems Integrators) competitive moves.

Rules for WEEKLY deep dive:
- Target 600–900 words.
- Synthesize trends across the window and include a <h3>What changed this week</h3> section.
- For every item, include a working public URL and state why it matters to Deloitte’s Workday practice.
- Include the publication date for each “highlight” item in its text (e.g., “(Published: 2025-10-12)”).

Rendering constraints:
- Return JSON only (no prose, no Markdown, no code fences).
- The “html_body” must be valid minimal HTML with semantic tags and absolute links:
  - Use <h2> for the headline, <h3> for section headers, <p>, <ul>, <li>, and <a href="https://…">…</a>
  - No <script>, no external CSS, no images, no iframes.
  - Short paragraphs and bullet lists for readability.
- The “plain_text_body” must be a text-only version with full URLs visible.
""".strip()

# ====== User Prompt / Schema ======
USER_PROMPT_SCHEMA = f"""
Return JSON ONLY in this exact shape (no code fences, no extra text):

{{
  "type": "daily or weekly",
  "run_date": "YYYY-MM-DD",
  "title": "Short headline",
  "priority_focus": "1–2 sentences on what matters most now",
  "highlights": [
    {{ "headline": "…", "why_it_matters": "…", "source_url": "https://…" }}
  ],
  "competitive_watch": [
    {{ "competitor": "Name", "move": "…", "implication": "…" }}
  ],
  "enablement": [
    {{ "skill": "Topic", "resource_url": "https://…", "90_day_outcome": "…" }}
  ],
  "actions_next_week": ["…"],
  "risks": [
    {{ "risk": "…", "mitigation": "…" }}
  ],
  "sources": [
    {{ "title": "…", "url": "https://…" }}
  ],
  "html_body": "<h2>…</h2> (well-formatted HTML with <a href=\"https://…\">links</a>; no scripts/external CSS)",
  "plain_text_body": "Text-only with visible URLs"
}}

Parameters:
- Set "run_date" to TODAY_ET = {TODAY_ET}.
- For DAILY: set "type":"daily"; target ~250 words; prefer items in the last 48h (from {YESTERDAY_ET} to {TODAY_ET}); include publication dates in highlight text.
- For WEEKLY: set "type":"weekly"; target 600–900 words; restrict to {WEEK_START_ET}…{TODAY_ET}; include <h3>What changed this week</h3> and publication dates in highlight text.
- Section order for html_body: Highlights; Competitive Watch; Enablement; Actions for Next Week; Risks & Mitigations; All Sources.
- Every item must include at least one absolute URL (https://…).
- Keep total HTML under ~25KB.
""".strip()

def _build_stub_payload(run_type: str) -> dict:
    title = "Workday HCM + AI (stub)"
    html_body = (
        "<h2>Workday HCM + AI – Stub</h2>"
        "<p><strong>What matters now:</strong> Local test run without external API.</p>"
        "<h3>Highlights</h3>"
        "<ul>"
        "<li><strong>Example Item:</strong> <a href=\"https://example.com/workday-ai\">Example link</a></li>"
        "</ul>"
        "<h3>All Sources</h3>"
        "<ul><li><a href=\"https://example.com/source\">Example Source</a></li></ul>"
    )
    return {
        "type": run_type,
        "run_date": TODAY_ET,
        "title": title,
        "priority_focus": "Local development stub; no network calls.",
        "highlights": [],
        "competitive_watch": [],
        "enablement": [],
        "actions_next_week": [],
        "risks": [],
        "sources": [],
        "html_body": html_body,
        "plain_text_body": (
            "Workday HCM + AI – Stub\n"
            "What matters now: Local test run without external API.\n"
            "Highlights:\n - Example Item: https://example.com/workday-ai\n"
            "All Sources:\n - https://example.com/source\n"
        ),
    }


# ====== OpenAI Call ======
def call_openai(run_type: str) -> dict:
    if not OPENAI_API_KEY:
        return _build_stub_payload(run_type)

    if requests is None:
        raise RuntimeError("The 'requests' package is required when OPENAI_API_KEY is set")

    system_prompt = SYSTEM_PROMPT_DAILY if run_type == "daily" else SYSTEM_PROMPT_WEEKLY

    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-5.1",
        "response_format": {"type": "json_object"},
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": USER_PROMPT_SCHEMA},
        ],
    }
    resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=120)
    resp.raise_for_status()
    data = resp.json()

    # Responses API normalization: support both "output" and "choices" shapes
    content = None
    if isinstance(data, dict) and "output" in data:
        output_val = data["output"]
        if isinstance(output_val, str):
            content = output_val
        elif isinstance(output_val, list) and output_val:
            for item in output_val:
                if isinstance(item, str):
                    content = item
                    break
                if isinstance(item, dict) and "content" in item:
                    content = item["content"]
                    break
    if not content and "choices" in data:
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            pass

    if not content:
        raise RuntimeError("OpenAI response did not include content in an expected format")

    # Ensure content is pure JSON (strip code fences if any)
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        content_stripped = content.strip()
        if content_stripped.startswith("```"):
            content_stripped = content_stripped.strip("`")
            content_stripped = content_stripped.split("\n", 1)[-1]
        result = json.loads(content_stripped)

    return result

# ====== Pages Writer ======
def write_html_to_pages(run_type: str, payload: dict) -> str:
    target = "docs/index.html" if run_type == "daily" else "docs/weekly.html"
    html = payload.get("html_body", "<h2>No content</h2>")
    wrapper = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>{}</title>"
        "<style>body{{font-family:Arial,Helvetica,sans-serif;max-width:760px;margin:32px auto;padding:0 16px;line-height:1.5}}</style>"
        "</head><body>{}</body></html>"
    )
    title = payload.get("title", f"Workday HCM + AI ({run_type})")
    html_page = wrapper.format(title, html)
    # Ensure target directory exists
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(html_page)
    return target

# ====== Email Sender ======
def send_email(payload: dict):
    if not (EMAIL_FROM and EMAIL_TO and GMAIL_USERNAME and GMAIL_APP_PASSWORD):
        print("Email secrets missing; skipping email send.")
        return
    subject = f"{payload.get('type','daily')} Research – {payload.get('title','Workday HCM + AI')} – {payload.get('run_date', TODAY_ET)}"
    body_html = payload.get("html_body", "<h2>No content</h2>")
    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(GMAIL_USERNAME, GMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO.split(","), msg.as_string())

# ====== Main ======
def main():
    payload = call_openai(RUN_TYPE)

    # Enforce ET date and type in the outgoing JSON to stabilize email/Pages labels
    payload.setdefault("type", RUN_TYPE)
    payload["run_date"] = TODAY_ET  # ensure ET date even if model returns UTC or missing

    # Write to Pages and email
    target_file = write_html_to_pages(RUN_TYPE, payload)
    print(f"Wrote: {target_file}")
    send_email(payload)

if __name__ == "__main__":
    main()
