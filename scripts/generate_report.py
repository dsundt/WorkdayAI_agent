import os
import sys
import json
import smtplib
import ssl
import re
from urllib.parse import urlsplit, urlunsplit, unquote, quote
import html as html_lib
from email.mime.text import MIMEText
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:  # pragma: no cover - fallback for older runtimes
    ZoneInfo = None  # type: ignore

import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
EMAIL_FROM = os.environ.get("EMAIL_FROM", "").strip()
EMAIL_TO = os.environ.get("EMAIL_TO", "").strip()
GMAIL_USERNAME = os.environ.get("GMAIL_USERNAME", "").strip()
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").strip()


def _current_date_et() -> str:
    """Return YYYY-MM-DD in America/New_York; fallback to UTC if tz not available."""
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        except Exception:
            pass
    return datetime.utcnow().strftime("%Y-%m-%d")


RUN_DATE = _current_date_et()

SYSTEM_PROMPT_DAILY = (
    "You are Dan – Workday AI Research Agent. Produce a JSON object exactly matching the schema below. "
    "Do credible web research (Workday, Workday HCM [Human Capital Management], Workday AI, agentic AI for Workday, "
    "broader AI for HR technology, consultant upskilling, and SI/GSI competitive moves). Include working URLs for every claim. "
    "Keep daily to ~250 words. Explain why each item matters to Deloitte’s Workday practice. Today's date (ET) is now."
)

SYSTEM_PROMPT_WEEKLY = (
    "You are Dan – Workday AI Research Agent. Produce a JSON object exactly matching the schema below. "
    "Do credible web research (Workday, Workday HCM [Human Capital Management], Workday AI, agentic AI for Workday, "
    "broader AI for HR technology, consultant upskilling, and SI/GSI competitive moves). Include working URLs for every claim. "
    "For the weekly deep dive, write 600–900 words and include a short section titled 'What changed this week'. "
    "Explain why each item matters to Deloitte’s Workday practice. Today's date (ET) is now."
)

USER_PROMPT_SCHEMA = (
    "Return JSON ONLY in this shape:\n"
    "{\n"
    "  \"type\": \"daily or weekly\",\n"
    "  \"run_date\": \"YYYY-MM-DD\",\n"
    "  \"title\": \"Short headline\",\n"
    "  \"priority_focus\": \"1–2 sentences on what matters most now\",\n"
    "  \"highlights\": [ { \"headline\": \"…\", \"why_it_matters\": \"…\", \"source_url\": \"https://…\" } ],\n"
    "  \"competitive_watch\": [ { \"competitor\": \"Name\", \"move\": \"…\", \"implication\": \"…\" } ],\n"
    "  \"enablement\": [ { \"skill\": \"Topic\", \"resource_url\": \"https://…\", \"90_day_outcome\": \"…\" } ],\n"
    "  \"actions_next_week\": [\"…\"],\n"
    "  \"risks\": [ { \"risk\": \"…\", \"mitigation\": \"…\" } ],\n"
    "  \"sources\": [ { \"title\": \"…\", \"url\": \"https://…\" } ],\n"
    "  \"html_body\": \"<h2>…</h2> (well-formatted HTML with <a href> links)\",\n"
    "  \"plain_text_body\": \"Text-only with visible URLs\"\n"
    "}\n\n"
    "Parameters:\n"
    "- For DAILY: set \"type\":\"daily\"; target ~250 words.\n"
    "- For WEEKLY: set \"type\":\"weekly\"; target 600–900 words and include 'What changed this week'.\n"
    "- Always include URLs and explain why it matters to Deloitte’s Workday practice.\n"
    "- Use run_date in YYYY-MM-DD (ET).\n"
)

_ZERO_WIDTH_RE = re.compile(r"[\u200B\u200C\u200D\u2060\uFEFF]")


def _normalize_href_value(raw_url: str) -> str:
    """Return a sanitized, well-formed URL for use in href attributes.

    - Trims whitespace and removes zero-width characters
    - Fixes spaced or single-slash schemes like "https: //example.com" or "http:/example"
    - Adds https:// when a domain starts with www. or looks like a bare domain
    - Percent-encodes path/query/fragment as needed
    - Leaves non-web schemes (mailto:, tel:, etc.) untouched aside from trimming
    """
    if not raw_url:
        return raw_url

    # Unescape any HTML entities, strip whitespace and invisible characters
    url = html_lib.unescape(raw_url).strip()
    url = _ZERO_WIDTH_RE.sub("", url)

    # Early exit for common non-web schemes
    lowered = url.lower()
    if lowered.startswith(("mailto:", "tel:", "sms:", "slack:", "whatsapp:", "ftp:")):
        return url
    if url.startswith("#"):
        return url

    # Normalize spaced or partially malformed schemes like "https: //" or "http:/"
    # 1) Collapse spaces around '://'
    url = re.sub(r"^(https?)\s*:\s*//\s*", r"\1://", url, flags=re.IGNORECASE)
    # 2) Fix single-slash scheme like "http:/example.com" (but avoid matching 'http://')
    url = re.sub(r"^(https?)\s*:\s*/(?!/)", r"\1://", url, flags=re.IGNORECASE)

    # Add https:// for www.* or bare domains
    if url.lower().startswith("www."):
        url = f"https://{url}"
    elif re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/.*)?$", url):
        url = f"https://{url}"

    # Parse and re-encode components
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower() or "https"
        # Clean up netloc: remove spaces and lowercase
        netloc = parts.netloc.replace(" ", "").lower()

        # Percent-encode path, query, fragment (avoid double-encoding by unquoting first)
        path = quote(unquote(parts.path), safe="/@:+-._~!$&'()*+,;=")
        query = quote(unquote(parts.query), safe="=&:@/?+-._~!$'()*+,;,")
        fragment = quote(unquote(parts.fragment), safe="-._~!$&'()*+,;=:@/?")

        normalized = urlunsplit((scheme, netloc, path, query, fragment))
    except Exception:
        # If parsing fails, fall back to trimmed input
        normalized = url

    return normalized


def normalize_urls_in_html(html: str) -> str:
    """Normalize all href attribute values inside the provided HTML string.

    Also strips zero-width characters that can break URLs in some email clients.
    """
    if not html:
        return html

    cleaned_html = _ZERO_WIDTH_RE.sub("", html)

    href_pattern = re.compile(r"href\s*=\s*(['\"])(.*?)\1", flags=re.IGNORECASE | re.DOTALL)

    def _replace_href(match: re.Match) -> str:
        quote_char = match.group(1)
        raw_val = match.group(2)
        normalized_val = _normalize_href_value(raw_val)
        # Escape for HTML attribute context
        escaped_val = html_lib.escape(normalized_val, quote=True)
        return f"href={quote_char}{escaped_val}{quote_char}"

    return href_pattern.sub(_replace_href, cleaned_html)

def _build_stub_payload(run_type: str) -> Dict[str, Any]:
    """Produce a minimal valid payload when API is unavailable.

    This allows local runs and CI to succeed without secrets.
    """
    title = f"Workday HCM + AI ({run_type})"
    html = (
        f"<h2>{title}</h2>"
        f"<p>Placeholder content generated locally on {RUN_DATE}. "
        f"Set OPENAI_API_KEY to enable live research.</p>"
    )
    return {
        "type": run_type,
        "run_date": RUN_DATE,
        "title": title,
        "priority_focus": "Placeholder while API access is unavailable.",
        "highlights": [],
        "competitive_watch": [],
        "enablement": [],
        "actions_next_week": [],
        "risks": [],
        "sources": [],
        "html_body": html,
        "plain_text_body": f"{title}\nLocal placeholder on {RUN_DATE}"
    }


def _parse_json_from_model_text(content: str) -> Dict[str, Any]:
    """Parse JSON from a model response, tolerating fenced code blocks."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            # Drop the opening fence line (e.g., ``` or ```json)
            cleaned = "\n".join(lines[1:])
            # Drop trailing fence if present
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()
                cleaned = cleaned[: -3].strip()
        return json.loads(cleaned)


def call_openai(run_type: str) -> Dict[str, Any]:
    # If no key, return a stub so local runs don't fail
    if not OPENAI_API_KEY:
        return _build_stub_payload(run_type)

    system_prompt = SYSTEM_PROMPT_DAILY if run_type == "daily" else SYSTEM_PROMPT_WEEKLY

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    # 1) Try Responses API first (preferred for unified modalities)
    try:
        resp_payload = {
            "model": "gpt-5.1",
            "response_format": {"type": "json_object"},
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": USER_PROMPT_SCHEMA},
            ],
        }
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers=headers,
            json=resp_payload,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()

        content: Optional[str] = None
        if isinstance(data, dict):
            if isinstance(data.get("output"), str):
                content = data["output"]
            elif isinstance(data.get("output"), list):
                for item in data["output"]:
                    if isinstance(item, str):
                        content = item
                        break
                    if isinstance(item, dict) and "content" in item and isinstance(item["content"], str):
                        content = item["content"]
                        break
            # Some Responses API variants return nested fields
            if not content and isinstance(data.get("response"), dict):
                nested = data["response"]
                if isinstance(nested.get("output_text"), str):
                    content = nested["output_text"]
        if content:
            return _parse_json_from_model_text(content)
    except Exception:
        # Fall through to chat.completions
        pass

    # 2) Fallback to Chat Completions for wider compatibility
    try:
        chat_payload = {
            "model": "gpt-4o-mini",
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": USER_PROMPT_SCHEMA},
            ],
        }
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=chat_payload,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        return _parse_json_from_model_text(content)
    except Exception:
        # Final fallback: local stub.
        return _build_stub_payload(run_type)

def write_html_to_pages(run_type: str, payload: dict) -> str:
    target = "docs/index.html" if run_type == "daily" else "docs/weekly.html"
    html = payload.get("html_body", "<h2>No content</h2>")
    html = normalize_urls_in_html(html)
    wrapper = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>{}</title>"
        "<style>body{{font-family:Arial,Helvetica,sans-serif;max-width:760px;margin:32px auto;padding:0 16px;line-height:1.5}}</style>"
        "</head><body>{}</body></html>"
    )
    title = payload.get("title", f"Workday HCM + AI ({run_type})")
    html_page = wrapper.format(title, html)
    with open(target, "w", encoding="utf-8") as f:
        f.write(html_page)
    return target

def send_email(payload: dict):
    if not (EMAIL_FROM and EMAIL_TO and GMAIL_USERNAME and GMAIL_APP_PASSWORD):
        print("Email secrets missing; skipping email send.")
        return
    subject = f"{payload.get('type','daily')} Research – {payload.get('title','Workday HCM + AI')} – {payload.get('run_date','')}"
    body_html = payload.get("html_body", "<h2>No content</h2>")
    body_html = normalize_urls_in_html(body_html)
    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    recipients = [addr.strip() for addr in EMAIL_TO.split(",") if addr.strip()]
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(GMAIL_USERNAME, GMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_FROM, recipients, msg.as_string())


def main():
    # Determine run_type from argv
    if len(sys.argv) < 2 or sys.argv[1] not in ("daily", "weekly"):
        print("Usage: python scripts/generate_report.py [daily|weekly]")
        sys.exit(1)
    run_type = sys.argv[1]

    payload = call_openai(run_type)
    payload.setdefault("type", run_type)
    payload.setdefault("run_date", RUN_DATE)
    payload.setdefault("title", "Workday HCM + AI Brief")
    target_file = write_html_to_pages(run_type, payload)
    print(f"Wrote: {target_file}")
    send_email(payload)


if __name__ == "__main__":
    main()
