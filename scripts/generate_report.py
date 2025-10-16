import os
import sys
import json
import smtplib
import ssl
from email.mime.text import MIMEText
from datetime import datetime
import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
EMAIL_FROM = os.environ.get("EMAIL_FROM", "").strip()
EMAIL_TO = os.environ.get("EMAIL_TO", "").strip()
GMAIL_USERNAME = os.environ.get("GMAIL_USERNAME", "").strip()
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").strip()

if len(sys.argv) < 2 or sys.argv[1] not in ("daily", "weekly"):
    print("Usage: python scripts/generate_report.py [daily|weekly]")
    sys.exit(1)

RUN_TYPE = sys.argv[1]
RUN_DATE = datetime.utcnow().strftime("%Y-%m-%d")

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

def call_openai(run_type: str) -> dict:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing")

    system_prompt = SYSTEM_PROMPT_DAILY if run_type == "daily" else SYSTEM_PROMPT_WEEKLY

    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-5.1",
        "response_format": {"type": "json_object"},
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": USER_PROMPT_SCHEMA}
        ]
    }
    resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
    resp.raise_for_status()
    data = resp.json()

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

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        content_stripped = content.strip()
        if content_stripped.startswith("```"):
            content_stripped = content_stripped.strip("`")
            content_stripped = content_stripped.split("\n", 1)[-1]
        result = json.loads(content_stripped)

    return result

def write_html_to_pages(run_type: str, payload: dict) -> str:
    target = "docs/index.html" if run_type == "daily" else "docs/weekly.html"
    html = payload.get("html_body", "<h2>No content</h2>")
    wrapper = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>{}</title>"
        "<style>body{font-family:Arial,Helvetica,sans-serif;max-width:760px;margin:32px auto;padding:0 16px;line-height:1.5}</style>"
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
    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(GMAIL_USERNAME, GMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO.split(","), msg.as_string())


def main():
    payload = call_openai(RUN_TYPE)
    payload.setdefault("type", RUN_TYPE)
    payload.setdefault("run_date", RUN_DATE)
    payload.setdefault("title", "Workday HCM + AI Brief")
    target_file = write_html_to_pages(RUN_TYPE, payload)
    print(f"Wrote: {target_file}")
    send_email(payload)


if __name__ == "__main__":
    main()
