import os
import sys
import json
import smtplib
import ssl
import re
from urllib.parse import urlsplit, urlunsplit, unquote, quote
import html as html_lib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:  # pragma: no cover - fallback for older runtimes
    ZoneInfo = None  # type: ignore

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover - optional dependency for local runs
    requests = None  # type: ignore

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


def _weekly_window_for_anchor_date(anchor_date_str: str, lookback_days: int = 7) -> tuple[str, str]:
    """Return (start_date_inclusive, end_date_inclusive) YYYY-MM-DD strings for a weekly window.

    The window is anchored on the provided ET date string and looks back `lookback_days` inclusive.
    For a 7-day window anchored on D, this returns (D-6, D).
    """
    try:
        anchor_dt = datetime.fromisoformat(anchor_date_str)
    except Exception:
        # Fallback: if parsing fails for some reason, just use the anchor date for both
        return anchor_date_str, anchor_date_str

    start_dt = anchor_dt - timedelta(days=max(1, lookback_days) - 1)
    return start_dt.strftime("%Y-%m-%d"), anchor_dt.strftime("%Y-%m-%d")

SYSTEM_PROMPT_DAILY = (
    "You are Dan – Workday AI Research Agent. Produce a JSON object that matches the schema I'll give you exactly. "
    "Research: Workday, Workday HCM (Human Capital Management), Workday AI, agentic AI for Workday, broader AI for HR technology, "
    "consultant upskilling, and SI/GSI (Systems Integrators / Global Systems Integrators) competitive moves. "
    "Include working, publicly reachable URLs for every claim. Prioritize official sources and analyst posts. De-duplicate similar stories. "
    "For the DAILY brief, target ~250 words. "
    "\n\nVery important formatting rules for rendering:\n"
    "- Return JSON only (no prose, no Markdown, no code fences).\n"
    "- The \"html_body\" value must be valid, minimal HTML with semantic tags and inline links:\n"
    "  - Use <h2> for the headline, <h3> for section headers, <p>, <ul>, <li>, and <a href=\"...\">…</a>\n"
    "  - No <script>, no external CSS, no images, no iframes.\n"
    "  - Use short paragraphs and bullet lists for readability.\n"
    "  - All links must be absolute URLs (https://…).\n"
    "- The \"plain_text_body\" must be a text-only version with full URLs visible.\n\n"
    "Explain in each item why it matters to Deloitte's Workday practice (client conversations, offering strategy, enablement, competitive posture). "
    "Today's date in ET is {run_date}. Use this exact date as the run date."
)

SYSTEM_PROMPT_WEEKLY = (
    "You are Dan – Workday AI Research Agent. Produce a JSON object that matches the schema I'll give you exactly. "
    "Research: Workday, Workday HCM (Human Capital Management), Workday AI, agentic AI for Workday, broader AI for HR technology, "
    "consultant upskilling, and SI/GSI (Systems Integrators / Global Systems Integrators) competitive moves. "
    "Include working, publicly reachable URLs for every claim. Prioritize official sources and analyst posts. De-duplicate similar stories. "
    "For the WEEKLY deep dive, target 600–900 words and include a short section titled \"What changed this week\". "\
    "\n\nVery important formatting rules for rendering:\n"
    "- Return JSON only (no prose, no Markdown, no code fences).\n"
    "- The \"html_body\" value must be valid, minimal HTML with semantic tags and inline links:\n"
    "  - Use <h2> for the headline, <h3> for section headers, <p>, <ul>, <li>, and <a href=\"...\">…</a>\n"
    "  - No <script>, no external CSS, no images, no iframes.\n"
    "  - Use short paragraphs and bullet lists for readability.\n"
    "  - All links must be absolute URLs (https://…).\n"
    "- The \"plain_text_body\" must be a text-only version with full URLs visible.\n\n"
    "Explain in each item why it matters to Deloitte's Workday practice (client conversations, offering strategy, enablement, competitive posture). "
    "Today in ET is {run_date}. Anchor on this date and analyze the past 7 days inclusive: "
    "from {weekly_start_date} through {run_date}. Do not use a previous calendar week."
)

USER_PROMPT_SCHEMA = (
    "Return JSON ONLY in this exact shape (no code fences, no extra text):\n\n"
    "{\n"
    "  \"type\": \"daily or weekly\",\n"
    "  \"run_date\": \"YYYY-MM-DD\",\n"
    "  \"title\": \"Short headline\",\n"
    "  \"priority_focus\": \"1–2 sentences on what matters most now\",\n"
    "  \"highlights\": [\n"
    "    { \"headline\": \"…\", \"why_it_matters\": \"…\", \"source_url\": \"https://…\" }\n"
    "  ],\n"
    "  \"competitive_watch\": [\n"
    "    { \"competitor\": \"Name\", \"move\": \"…\", \"implication\": \"…\" }\n"
    "  ],\n"
    "  \"enablement\": [\n"
    "    { \"skill\": \"Topic\", \"resource_url\": \"https://…\", \"90_day_outcome\": \"…\" }\n"
    "  ],\n"
    "  \"actions_next_week\": [\"…\"],\n"
    "  \"risks\": [\n"
    "    { \"risk\": \"…\", \"mitigation\": \"…\" }\n"
    "  ],\n"
    "  \"sources\": [\n"
    "    { \"title\": \"…\", \"url\": \"https://…\" }\n"
    "  ],\n"
    "  \"html_body\": \"<h2>…</h2> (well-formatted HTML with <a href> links; no code fences, no scripts, no external CSS)\",\n"
    "  \"plain_text_body\": \"Text-only with visible URLs\"\n"
    "}\n\n"
    "Parameters:\n"
    "- For DAILY: set \"type\":\"daily\"; target ~250 words.\n"
    "- For WEEKLY: set \"type\":\"weekly\"; target 600–900 words AND include a <h3>What changed this week</h3> section.\n"
    "- Always include working URLs and explain why each item matters to Deloitte’s Workday practice.\n"
    "- Use run_date in YYYY-MM-DD (ET).\n"
    "- \"html_body\" rules for perfect Gmail + GitHub Pages rendering:\n"
    "  - Start with <h2>{{title}}</h2> then a <p><strong>What matters now:</strong> {{priority_focus}}</p>\n"
    "  - Section order: Highlights; Competitive Watch; Enablement; Actions for Next Week; Risks & Mitigations; All Sources\n"
    "  - Use <h3> for each section header\n"
    "  - Use <ul><li>…</li></ul> lists for items\n"
    "  - Every item must include at least one <a href=\"https://…\">link text</a>\n"
    "  - No images, iframes, scripts, or external CSS. Inline tags only (<h2>, <h3>, <p>, <ul>, <li>, <a>)\n"
    "  - Keep total HTML body under ~25KB\n"
    "- \"plain_text_body\" must be a readable summary with raw links visible.\n"
)

_ZERO_WIDTH_RE = re.compile(r"[\u200B\u200C\u200D\u2060\uFEFF]")

# Map common smart/curly quotes to ASCII quotes. Many sources introduce these,
# which can break HTML attribute parsing in browsers and email clients.
_SMART_TO_ASCII_MAP = str.maketrans({
    "\u201C": '"',  # left double quote
    "\u201D": '"',  # right double quote
    "\u201E": '"',  # low double quote
    "\u201F": '"',  # double high-reversed-9 quotation mark
    "\u275D": '"',  # heavy double turned comma quotation mark ornament
    "\u275E": '"',  # heavy double comma quotation mark ornament
    "\uFF02": '"',  # fullwidth quotation mark
    "\u2018": "'",  # left single quote
    "\u2019": "'",  # right single quote / apostrophe
    "\u201B": "'",  # single high-reversed-9 quotation mark
    "\u2032": "'",  # prime
    "\uFF07": "'",  # fullwidth apostrophe
})

def _normalize_smart_quotes(text: str) -> str:
    """Replace smart/curly quotes with ASCII quotes in text."""
    if not text:
        return text
    return html_lib.unescape(text).translate(_SMART_TO_ASCII_MAP)


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

    Behaviors:
    - Converts smart quotes to ASCII quotes
    - Strips zero-width characters that can break URLs
    - Fixes/canonicalizes values of href attributes, even if unquoted
    """
    if not html:
        return html

    cleaned_html = _normalize_smart_quotes(html)
    cleaned_html = _ZERO_WIDTH_RE.sub("", cleaned_html)

    # 1) Handle quoted href values first
    quoted_pattern = re.compile(r"href\s*=\s*(['\"])\s*(.*?)\1", flags=re.IGNORECASE | re.DOTALL)

    def _replace_quoted(match: re.Match) -> str:
        quote_char = match.group(1)
        raw_val = match.group(2)
        normalized_val = _normalize_href_value(raw_val)
        escaped_val = html_lib.escape(normalized_val, quote=True)
        return f"href={quote_char}{escaped_val}{quote_char}"

    cleaned_html = quoted_pattern.sub(_replace_quoted, cleaned_html)

    # 2) Handle unquoted href values: href=value (stop at whitespace or >)
    unquoted_pattern = re.compile(r"href\s*=\s*([^\s>'\"]+)", flags=re.IGNORECASE)

    def _replace_unquoted(match: re.Match) -> str:
        raw_val = match.group(1)
        normalized_val = _normalize_href_value(raw_val)
        escaped_val = html_lib.escape(normalized_val, quote=True)
        return f"href=\"{escaped_val}\""

    cleaned_html = unquoted_pattern.sub(_replace_unquoted, cleaned_html)

    return cleaned_html


def _convert_markdown_links_to_anchors(html: str) -> str:
    """Convert Markdown links [text](url) to <a href="url">text</a> in non-tag text."""
    if not html:
        return html
    parts = re.split(r"(<[^>]+>)", html)
    out_parts: List[str] = []
    md_link_re = re.compile(r"\[([^\]]+)\]\(([^\s)]+)\)")

    for part in parts:
        if part.startswith("<") and part.endswith(">"):
            out_parts.append(part)
            continue

        def _md_sub(m: re.Match) -> str:
            text = html_lib.escape(m.group(1))
            url = _normalize_href_value(m.group(2))
            url_escaped = html_lib.escape(url, quote=True)
            return f"<a href=\"{url_escaped}\">{text}</a>"

        out_parts.append(md_link_re.sub(_md_sub, part))

    return "".join(out_parts)


def _autolink_plain_urls(html: str) -> str:
    """Auto-link plain URLs in non-tag text segments."""
    if not html:
        return html
    parts = re.split(r"(<[^>]+>)", html)
    out_parts: List[str] = []
    url_re = re.compile(r"(?:(?:https?://)|(?:www\.))[^\s<>'\"]+")

    for part in parts:
        if part.startswith("<") and part.endswith(">"):
            out_parts.append(part)
            continue

        def _url_sub(m: re.Match) -> str:
            raw = m.group(0)
            url = _normalize_href_value(raw)
            url_escaped = html_lib.escape(url, quote=True)
            display = html_lib.escape(raw)
            return f"<a href=\"{url_escaped}\">{display}</a>"

        out_parts.append(url_re.sub(_url_sub, part))

    return "".join(out_parts)


def prepare_html(html: str) -> str:
    """Prepare HTML for pages and email: fix quotes, linkify, normalize."""
    if not html:
        return html
    step1 = _normalize_smart_quotes(html)
    step2 = _convert_markdown_links_to_anchors(step1)
    step3 = _autolink_plain_urls(step2)
    return normalize_urls_in_html(step3)


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
    # If no key or requests not available, return a stub so local runs don't fail
    if not OPENAI_API_KEY or requests is None:
        return _build_stub_payload(run_type)

    # Build a system prompt with explicit dates to avoid the model inferring past dates.
    if run_type == "daily":
        system_prompt = SYSTEM_PROMPT_DAILY.format(run_date=RUN_DATE)
    else:
        weekly_start_date, weekly_end_date = _weekly_window_for_anchor_date(RUN_DATE)
        system_prompt = SYSTEM_PROMPT_WEEKLY.format(
            run_date=weekly_end_date,
            weekly_start_date=weekly_start_date,
        )

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
    html = prepare_html(html)
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
    body_html = prepare_html(body_html)
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

    # Log the dates being passed for verification
    if run_type == "daily":
        print(f"[dates] type=daily run_date={RUN_DATE}")
    else:
        weekly_start_date, weekly_end_date = _weekly_window_for_anchor_date(RUN_DATE)
        print(f"[dates] type=weekly run_date={weekly_end_date} window={weekly_start_date}..{weekly_end_date}")

    payload = call_openai(run_type)
    payload.setdefault("type", run_type)
    payload.setdefault("run_date", RUN_DATE)
    payload.setdefault("title", "Workday HCM + AI Brief")
    target_file = write_html_to_pages(run_type, payload)
    print(f"Wrote: {target_file}")
    send_email(payload)


if __name__ == "__main__":
    main()
