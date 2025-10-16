import os
import sys
import json
import smtplib
import ssl
import re
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo  # stdlib timezone support (Py 3.9+)
from urllib.parse import urlsplit, urlunsplit, quote, unquote
import html as html_lib

# requests is optional at runtime; tests run without network/API.
try:
    import requests  # type: ignore
except Exception:  # pragma: no cover - environment without requests
    requests = None  # type: ignore

# ====== Secrets / Env ======
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
# Allow overriding model from environment; choose a strong default for best results
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o").strip()
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


def _percent_encode_url(url: str) -> str:
    """Percent-encode unsafe characters in URL components without double-encoding.

    Leaves common safe/reserved characters intact.
    """
    try:
        parsed = urlsplit(url)
        path = quote(unquote(parsed.path), safe="/:@-._~!$&'()*+,;=")
        query = quote(unquote(parsed.query), safe="=&:@-._~!$'()*+,;")
        fragment = quote(unquote(parsed.fragment), safe=":@-._~!$&'()*+,;=")
        return urlunsplit((parsed.scheme, parsed.netloc, path, query, fragment))
    except Exception:
        return url


def _normalize_href(raw_val: str) -> str:
    """Normalize href values to reduce 404s and ensure absolute, launchable links.

    - Decode HTML entities
    - Strip smart quotes and trailing punctuation
    - Add schemes for // and www.* URLs
    - Fix common malformed scheme variants (https:/, http:/)
    - Percent-encode spaces and other unsafe characters
    - For domain-only or relative paths, best-effort to make absolute; otherwise fallback to '#'
    """
    if raw_val is None:
        return "#"

    s = html_lib.unescape(raw_val.strip())

    # Trim surrounding smart quotes/backticks
    s = s.strip('“”‘’"`')

    # Remove trailing punctuation commonly attached in prose
    while s and s[-1] in ",.);]»\"’”":
        s = s[:-1]

    # Normalize scheme variants and schemeless URLs
    if s.startswith("//"):
        s = "https:" + s
    elif s.startswith("www."):
        s = "https://" + s
    elif s.startswith("https:/") and not s.startswith("https://"):
        s = "https://" + s[len("https:/"):]
    elif s.startswith("http:/") and not s.startswith("http://"):
        s = "http://" + s[len("http:/"):]

    # If it's clearly a domain without scheme, prefix https
    if re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/.*)?$", s) and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", s):
        s = "https://" + s

    # Allow anchors and mail/tel as-is
    if s.startswith(("http://", "https://", "mailto:", "tel:", "#")):
        return _percent_encode_url(s)

    # Leading slash without domain: no reliable base; fall back to anchor
    if s.startswith("/"):
        return "#"

    # Anything else that lacks a scheme: convert to anchor to avoid broken external navigation
    return "#"


def _rewrite_links_in_html(html_markup: str) -> str:
    """Rewrite <a href> links to be absolute, safely quoted, and launchable.

    - Ensures href is double-quoted
    - Adds target="_blank" and rel="noopener noreferrer" for external links
    - Leaves mailto/tel/# intact, but still properly quoted
    """
    if not html_markup:
        return html_markup

    a_tag_pattern = re.compile(r"<a\s+([^>]*?)href=([\'\"])(.*?)(?:\2)([^>]*)>", re.IGNORECASE)

    def _replacer(match: re.Match) -> str:
        pre_attrs = match.group(1) or ""
        quote_ch = '"'  # standardize
        href_val = match.group(3) or ""
        post_attrs = match.group(4) or ""

        normalized = _normalize_href(href_val)

        attrs_combined = (pre_attrs + " " + post_attrs).strip()
        # Ensure target and rel are present for http(s) links
        is_external = normalized.startswith(("http://", "https://"))
        if is_external and "target=" not in attrs_combined:
            post_attrs = (post_attrs + " target=\"_blank\"").strip()
        if is_external and "rel=" not in attrs_combined:
            post_attrs = (post_attrs + " rel=\"noopener noreferrer\"").strip()

        # Normalize spacing around attributes
        pre_attrs_norm = (pre_attrs.strip() + " ") if pre_attrs.strip() else ""
        post_attrs_norm = (" " + post_attrs.strip()) if post_attrs.strip() else ""
        return f"<a {pre_attrs_norm}href=\"{normalized}\"{post_attrs_norm}>"

    return a_tag_pattern.sub(_replacer, html_markup)


# ====== OpenAI Call ======
def call_openai(run_type: str) -> dict:
    """Call OpenAI using the Responses API, with fallback to Chat Completions.

    If any API call fails or returns an unexpected payload, return a stub payload
    so CI can continue (and Pages/email still get generated).
    """
    # Compute prompt data up-front so we can expose it in HTML for debugging
    system_prompt = SYSTEM_PROMPT_DAILY if run_type == "daily" else SYSTEM_PROMPT_WEEKLY
    combined_prompt = f"{system_prompt}\n\n{USER_PROMPT_SCHEMA}"
    model_in_use = (OPENAI_MODEL or "gpt-4o").strip()

    if not OPENAI_API_KEY:
        payload = _build_stub_payload(run_type)
        payload["_debug_endpoint"] = "stub"
        payload["_debug_model"] = model_in_use
        payload["_debug_prompt"] = combined_prompt
        return payload

    if requests is None:
        raise RuntimeError("The 'requests' package is required when OPENAI_API_KEY is set")

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    def _extract_text_from_responses_api_payload(data: dict) -> str | None:
        # Prefer canonical aggregated field if present
        if isinstance(data, dict):
            output_text = data.get("output_text")
            if isinstance(output_text, str) and output_text.strip():
                return output_text

            if "output" in data:
                output_val = data["output"]
                if isinstance(output_val, str) and output_val.strip():
                    return output_val
                if isinstance(output_val, list) and output_val:
                    for item in output_val:
                        if isinstance(item, str) and item.strip():
                            return item
                        if isinstance(item, dict):
                            # Common shapes include {"type":"output_text","content":"..."}
                            text_candidate = item.get("content") or item.get("text")
                            if isinstance(text_candidate, str) and text_candidate.strip():
                                return text_candidate

            # Some deployments proxy Responses to Chat Completions-like shape
            try:
                return data["choices"][0]["message"]["content"]
            except Exception:
                pass
        return None

    def _coerce_json(content: str) -> dict:
        # First, try direct JSON
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Strip typical triple-backtick code fences, possibly with language hints
        text = content.strip()
        if text.startswith("```"):
            # Remove first line (``` or ```json) and trailing fence if present
            lines = text.split("\n")
            lines = lines[1:] if len(lines) > 1 else []
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass

        # Last resort: attempt to locate the first top-level JSON object
        # This is a conservative approach and avoids complex parsing.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            return json.loads(candidate)

        # If all else fails, propagate the error upward
        raise json.JSONDecodeError("Failed to parse JSON from model output", content, 0)

    # Attempt 1: Responses API with string input (portable shape)
    try:
        responses_url = "https://api.openai.com/v1/responses"
        responses_payload = {
            "model": model_in_use,
            "response_format": {"type": "json_object"},
            # The Responses API expects a string (or tool/content blocks). Use a single string.
            "input": combined_prompt,
        }
        resp = requests.post(responses_url, headers=headers, json=responses_payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        content = _extract_text_from_responses_api_payload(data)
        if not content:
            raise RuntimeError("Responses API did not include content in an expected format")
        payload = _coerce_json(content)
        payload["_debug_endpoint"] = "responses"
        payload["_debug_model"] = model_in_use
        payload["_debug_prompt"] = combined_prompt
        return payload
    except Exception as e_responses:
        # Continue to Chat Completions fallback
        print(f"Responses API failed, falling back to Chat Completions: {e_responses}", file=sys.stderr)

    # Attempt 2: Chat Completions (widely supported)
    try:
        chat_url = "https://api.openai.com/v1/chat/completions"
        chat_payload = {
            "model": model_in_use,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": USER_PROMPT_SCHEMA},
            ],
        }
        resp = requests.post(chat_url, headers=headers, json=chat_payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as e_extract:
            raise RuntimeError(f"Chat Completions content extraction failed: {e_extract}")
        payload = _coerce_json(content)
        payload["_debug_endpoint"] = "chat"
        payload["_debug_model"] = model_in_use
        payload["_debug_prompt"] = combined_prompt
        return payload
    except Exception as e_chat:
        print(f"OpenAI Chat Completions failed, using stub payload: {e_chat}", file=sys.stderr)
        return _build_stub_payload(run_type)

# ====== Pages Writer ======
def write_html_to_pages(run_type: str, payload: dict) -> str:
    target = "docs/index.html" if run_type == "daily" else "docs/weekly.html"
    html = payload.get("html_body", "<h2>No content</h2>")
    # Rewrite and normalize links to reduce broken URLs
    html = _rewrite_links_in_html(html)

    # Append debug block showing the exact prompt sent to OpenAI
    debug_endpoint = payload.get("_debug_endpoint", "n/a")
    debug_model = payload.get("_debug_model", "n/a")
    debug_prompt = payload.get("_debug_prompt", "")
    if debug_prompt:
        escaped_prompt = html_lib.escape(debug_prompt)
        debug_html = (
            "<hr>"
            "<h3>Prompt sent to OpenAI</h3>"
            f"<p><strong>Endpoint:</strong> {html_lib.escape(str(debug_endpoint))} &nbsp; "
            f"<strong>Model:</strong> {html_lib.escape(str(debug_model))}</p>"
            f"<pre style=\"white-space:pre-wrap;overflow-x:auto;border:1px solid #ddd;padding:12px;border-radius:6px;background:#fafafa\">{escaped_prompt}</pre>"
        )
        html = html + debug_html
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
