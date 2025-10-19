import os
import sys
import json
import smtplib
import ssl
import re
import copy
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo  # stdlib timezone support (Py 3.9+)
from urllib.parse import urlsplit, urlunsplit, quote, unquote, urljoin
import html as html_lib

# requests is optional at runtime; tests run without network/API.
try:
    import requests  # type: ignore
except Exception:  # pragma: no cover - environment without requests
    requests = None  # type: ignore

# Lightweight stdlib HTTP fallback so Tavily works even without 'requests'
from urllib.request import Request as _UrlRequest, urlopen as _urlopen  # type: ignore
from urllib.error import HTTPError as _HTTPError, URLError as _URLError  # type: ignore

# ====== Secrets / Env ======
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
# Allow overriding model from environment; choose a strong default for best results
# Prefer widely available, JSON-mode compatible default
# Users can override via OPENAI_MODEL
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1").strip()
# Preserve model-provided HTML only when explicitly requested; default to rewriting
# links so published pages/emails always use launchable URLs.
PRESERVE_MODEL_HTML = os.environ.get("PRESERVE_MODEL_HTML", "0").strip() == "1"
OPENAI_REQUIRE_LIVE = os.environ.get("OPENAI_REQUIRE_LIVE", "0").strip() == "1"
EMAIL_FROM = os.environ.get("EMAIL_FROM", "").strip()
EMAIL_TO = os.environ.get("EMAIL_TO", "").strip()
GMAIL_USERNAME = os.environ.get("GMAIL_USERNAME", "").strip()
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()

# Tavily tuning (can be adjusted later)
_TAVILY_SEARCH_DEPTH_DEFAULT = "advanced"   # "basic"=1 credit; "advanced"=2 credits
TAVILY_SEARCH_DEPTH = (
    os.environ.get("TAVILY_SEARCH_DEPTH", _TAVILY_SEARCH_DEPTH_DEFAULT).strip().lower()
)
if TAVILY_SEARCH_DEPTH not in {"basic", "advanced"}:
    TAVILY_SEARCH_DEPTH = _TAVILY_SEARCH_DEPTH_DEFAULT

# Prefer official/credible sources
_DEFAULT_PREFERRED_DOMAINS = [
    # Workday official
    "workday.com", "blog.workday.com", "newsroom.workday.com", "community.workday.com",
    "investor.workday.com", "developers.workday.com",

    # Deloitte + other GSIs / SIs
    "deloitte.com", "newsroom.accenture.com", "accenture.com",
    "ey.com", "kpmg.com", "pwc.com",
    "ibm.com", "capgemini.com", "infosys.com", "tcs.com", "wipro.com", "cognizant.com",

    # Leading Workday partners / boutiques
    "kainos.com", "invisors.com", "topbloc.com", "onesourcevirtual.com", "alight.com", "mercer.com", "avaap.com",

    # Competitors & adjacent platforms
    "oracle.com", "sap.com", "successfactors.com", "ukg.com", "adp.com", "ceridian.com", "dayforce.com",
    "servicenow.com", "salesforce.com", "microsoft.com", "googlecloud.google", "cloud.google.com", "aws.amazon.com",

    # Integration / data / security ecosystem
    "mulesoft.com", "boomi.com", "workato.com",
    "okta.com", "auth0.com",
    "snowflake.com", "databricks.com",
    "collibra.com", "alation.com",

    # Analysts & credible media
    "gartner.com", "forrester.com", "idc.com",
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com",
    "venturebeat.com", "techcrunch.com", "infoq.com", "theregister.com",

    # Standards / policy (risk, compliance)
    "nist.gov", "eeoc.gov", "europa.eu",
]
_preferred_domains_env = os.environ.get("TAVILY_PREFERRED_DOMAINS", "").strip()
if _preferred_domains_env:
    PREFERRED_DOMAINS = [d.strip() for d in _preferred_domains_env.split(",") if d.strip()]
else:
    PREFERRED_DOMAINS = _DEFAULT_PREFERRED_DOMAINS

RESPONSES_JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "workday_ai_report",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "type",
                "run_date",
                "title",
                "priority_focus",
                "highlights",
                "competitive_watch",
                "enablement",
                "actions_next_week",
                "risks",
                "sources",
                "html_body",
                "plain_text_body",
            ],
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["daily", "weekly"],
                },
                "run_date": {"type": "string"},
                "title": {"type": "string"},
                "priority_focus": {"type": "string"},
                "highlights": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["headline", "why_it_matters", "source_url"],
                        "properties": {
                            "headline": {"type": "string"},
                            "why_it_matters": {"type": "string"},
                            "source_url": {"type": "string"},
                        },
                    },
                },
                "competitive_watch": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["competitor", "move", "implication"],
                        "properties": {
                            "competitor": {"type": "string"},
                            "move": {"type": "string"},
                            "implication": {"type": "string"},
                        },
                    },
                },
                "enablement": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["skill", "resource_url", "90_day_outcome"],
                        "properties": {
                            "skill": {"type": "string"},
                            "resource_url": {"type": "string"},
                            "90_day_outcome": {"type": "string"},
                        },
                    },
                },
                "actions_next_week": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "risks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["risk", "mitigation"],
                        "properties": {
                            "risk": {"type": "string"},
                            "mitigation": {"type": "string"},
                        },
                    },
                },
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["title", "url"],
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string"},
                        },
                    },
                },
                "html_body": {"type": "string"},
                "plain_text_body": {"type": "string"},
            },
        },
    },
}


def _mask_secret(secret: str, visible: int = 4) -> str:
    """Return a masked representation of a secret for debugging output."""

    secret = (secret or "").strip()
    if not secret:
        return ""
    if len(secret) <= visible:
        return "*" * len(secret)
    return f"{secret[:visible]}…{'*' * max(len(secret) - visible, 0)}"


def _unique_payload_variants(payloads: list[dict]) -> list[dict]:
    """Return payload variants with duplicates removed while preserving order."""

    seen: set[str] = set()
    unique: list[dict] = []
    for payload in payloads:
        try:
            key = json.dumps(payload, sort_keys=True)
        except TypeError:
            # Fallback to repr for objects that are not JSON serializable (should not occur)
            key = repr(payload)
        if key not in seen:
            seen.add(key)
            unique.append(payload)
    return unique


def _responses_payload_variants(model: str, system_prompt: str, user_prompt: str) -> list[dict]:
    """Build payload variants for the Responses API to maximize compatibility.

    - Use text.format (object) for structured output (json_schema/json_object)
    - Omit temperature (some models only support default=1)
    - Prefer simple string input for portability
    """

    combined_input = f"{system_prompt}\n\n{user_prompt}"

    # Translate legacy RESPONSES_JSON_SCHEMA into the latest text.format shape.
    # Newer API variants require text.format.name at the top level, alongside schema.
    schema_meta = RESPONSES_JSON_SCHEMA.get("json_schema", {}) or {}
    schema_name = schema_meta.get("name") or "workday_ai_report"
    schema_def = schema_meta.get("schema") or {}

    # Preferred (current) shape: name and schema are top-level under text.format
    text_format_preferred = {
        "format": {
            "type": "json_schema",
            "name": schema_name,
            "schema": schema_def,
        }
    }

    # Back-compat shape: nest {name, schema} under json_schema for older proxies
    text_format_legacy_nested = {
        "format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema_def},
        }
    }

    base = {
        "model": model,
        "input": combined_input,
        "text": text_format_preferred,
    }

    variants: list[dict] = [base]

    # Compatibility variant using the legacy-nested json_schema shape
    legacy_nested_variant = copy.deepcopy(base)
    legacy_nested_variant["text"] = text_format_legacy_nested
    variants.append(legacy_nested_variant)

    # Some deployments reject json_schema; fall back to json_object, then to no schema.
    json_object_variant = copy.deepcopy(base)
    json_object_variant["text"] = {"format": {"type": "json_object"}}
    variants.append(json_object_variant)

    no_schema_variant = copy.deepcopy(json_object_variant)
    no_schema_variant.pop("text", None)
    variants.append(no_schema_variant)

    # Alternate shape: message-style input with explicit blocks (for older proxies)
    messages_shape = {
        "model": model,
        "text": text_format_preferred,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_prompt}],
            },
        ],
    }
    variants.append(messages_shape)

    # Message shape without text.format for strict proxies
    messages_no_schema = copy.deepcopy(messages_shape)
    messages_no_schema.pop("text", None)
    variants.append(messages_no_schema)

    return _unique_payload_variants(variants)


def _chat_payload_variants(model: str, system_prompt: str, user_prompt: str) -> list[dict]:
    """Build payload variants for the Chat Completions API.

    - Avoid json_schema (many models require strict properties)
    - Prefer json_object, then no response_format
    - Omit temperature for models that only support default=1
    """

    base_no_schema = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    variants = []

    # Try json_object first as a soft structured output
    json_object_variant = copy.deepcopy(base_no_schema)
    json_object_variant["response_format"] = {"type": "json_object"}
    variants.append(json_object_variant)

    # Then try without response_format (prompt-only JSON coercion)
    variants.append(base_no_schema)

    return _unique_payload_variants(variants)


def _summarize_http_error(err: Exception) -> str:
    """Return a short string summarizing an HTTP error response body."""

    response = getattr(err, "response", None)
    if response is None:
        return ""
    try:
        text = response.text or ""
    except Exception:
        return ""
    text = text.strip().replace("\n", " ")
    if not text:
        return ""
    if len(text) > 240:
        text = text[:240] + "…"
    return text

# ====== Args ======
if len(sys.argv) < 2 or sys.argv[1] not in ("daily", "weekly", "verify"):
    print("Usage: python scripts/generate_report.py [daily|weekly|verify] [optional: daily|weekly for verify]")
    sys.exit(1)

RUN_TYPE = sys.argv[1]  # "daily" or "weekly" or "verify"

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
You are “Dan – Workday AI Research Agent.” 
Produce a comprehensive JSON object that matches the schema I will give you exactly.

DATE CONTEXT (ET)
- TODAY_ET = {TODAY_ET}
- LAST_48H_WINDOW = {YESTERDAY_ET} → {TODAY_ET} inclusive

RESEARCH SCOPE
Focus on Workday and its ecosystem — Workday HCM, Workday Extend, Workday Skills Cloud, Workday AI Marketplace, and all agentic or autonomous AI capabilities in SaaS solutions. 
Include:
- Workday product developments, patents, partnerships, and leadership commentary.
- Competitive analysis (SAP, Oracle, ServiceNow, UKG, ADP, Dayforce, etc.).
- GSI & boutique consultancy perspectives (Deloitte, Accenture, EY, KPMG, PwC, IBM, Capgemini, Infosys, TCS, Wipro, Cognizant, Kainos, Invisors, TopBloc, OSV, Alight, Mercer, etc.).
- Ecosystem vendors and hyperscalers (AWS, Azure, GCP, Salesforce).
- Analyst & media insights (Gartner, Forrester, IDC, Reuters, Bloomberg, VentureBeat, etc.).
- Emerging risks, governance, and responsible AI developments affecting SaaS/HR technology.

WRITING EXPECTATIONS
- For each cited source, provide **multi-paragraph summaries (150–250 words)** that explain:
  - What was announced, found, or claimed.
  - Why it matters strategically, technically, and competitively.
  - Implications for Workday clients and Deloitte’s Workday practice.
- Incorporate synthesis across related articles (e.g., “Across multiple GSIs…” or “Several sources indicate…”).
- Prioritize insights, nuance, and context over mere headlines.
- Every key claim must cite a valid source URL from the provided SOURCE LIST.
- If no credible items are found in the last 48h, produce an honest “no significant updates” section.

FORMATTING RULES
- Return JSON only (no Markdown or prose outside the object).
- `html_body` must be valid, minimal HTML (<h2>, <h3>, <p>, <ul>, <li>, <a href="...">).
- Section order: Highlights; Competitive Watch; Enablement; Actions for Next Week; Risks & Mitigations; All Sources.
- Keep total HTML under 35 KB.
- `plain_text_body` must be readable text with visible URLs.
""".strip()

SYSTEM_PROMPT_WEEKLY = f"""
You are “Dan – Workday AI Research Agent.” 
Produce a comprehensive JSON object that matches the schema I will give you exactly.

DATE WINDOW (ET)
- WEEK_START_ET = {WEEK_START_ET}
- TODAY_ET = {TODAY_ET}
- Include items published or updated between these dates.

RESEARCH SCOPE
Analyze developments related to Agentic AI in SaaS ecosystems — with Workday as the anchor — over the past week. 
Include:
- Workday AI advancements, partnerships, and feature releases.
- Competitive moves across major HCM/ERP vendors.
- GSI and boutique consultancy thought leadership, whitepapers, or partnership news.
- Analyst and press coverage (Forrester, Gartner, IDC, Bloomberg, Reuters, etc.).
- Regulatory, governance, or ethical AI considerations impacting enterprise HR systems.
- Patterns across vendor ecosystems and how they reshape client conversations.

WRITING EXPECTATIONS
- Create an **executive-grade synthesis (1200–1500 words)** with deep analysis and contextual insights.
- Each cited source should be summarized in **2–3 paragraphs**, explaining:
  - The essence of the update.
  - The implications for Workday and broader SaaS players.
  - The impact on Deloitte’s Workday practice, offerings, or positioning.
- Include a <h3>“What changed this week”</h3> section synthesizing shifts and sentiment.
- Highlight interconnections among Workday, competitors, GSIs, and boutiques.
- Include quantified insights or examples where possible.
- Do not fabricate; rely strictly on the SOURCE LIST URLs.

FORMATTING RULES
- Return JSON only (no Markdown or prose outside the object).
- `html_body` must use semantic HTML (<h2>, <h3>, <p>, <ul>, <li>, <a href="...">) with absolute URLs.
- Section order: Highlights; Competitive Watch; Enablement; Actions for Next Week; Risks & Mitigations; All Sources.
- Limit HTML body to ~45 KB.
- `plain_text_body` = clean text summary with URLs visible.
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
- For DAILY: set "type":"daily"; target ~1000 words; prefer items in the last 48h (from {YESTERDAY_ET} to {TODAY_ET}); include publication dates in highlight text.
- For WEEKLY: set "type":"weekly"; target 2000–2500 words; restrict to {WEEK_START_ET}…{TODAY_ET}; include <h3>What changed this week</h3> and publication dates in highlight text.
- Section order for html_body: Highlights; Competitive Watch; Enablement; Actions for Next Week; Risks & Mitigations; All Sources.
- Every item must include at least one absolute URL (https://…).
- Keep total HTML under ~25KB.

ADDITIONAL REQUIREMENTS
- For every highlight and competitive_watch item, include a 2–3 paragraph detailed summary within the "why_it_matters" text.
- Where relevant, compare perspectives across multiple sources.
- Include a clear "so what" statement for Deloitte’s Workday practice.
- Ensure all cited URLs appear in the `sources` array and inside the `html_body` link text.
- Keep factual accuracy and avoid speculation.
""".strip()

DEBUG_DIR = os.path.join("docs", "debug")


def tavily_search(
    query: str,
    time_range: str,
    include_domains: list[str] | None = None,
    max_results: int = 10,
    debug_log: list[dict] | None = None,
):
    """
    Call Tavily /search and return [{title,url,snippet,source,date}, ...].
    time_range: "day","week","month","year"
    search_depth: "basic" (1 credit) or "advanced" (2 credits)
    """
    entry: dict[str, object] = {
        "query": query,
        "time_range": time_range,
        "search_depth": TAVILY_SEARCH_DEPTH,
        "max_results": max_results,
    }
    if include_domains:
        entry["include_domains"] = list(include_domains)
    if debug_log is not None:
        debug_log.append(entry)

    entry["api_key_present"] = bool(TAVILY_API_KEY)
    if not TAVILY_API_KEY:
        # Without an API key we cannot call Tavily; record and skip gracefully
        entry["status"] = "skipped"
        entry["reason"] = "missing_api_key"
        return []

    url = "https://api.tavily.com/search"
    # Use official Tavily header; keep Authorization as a secondary for broader compatibility
    headers = {
        "Content-Type": "application/json",
        "x-api-key": TAVILY_API_KEY,
        "Authorization": f"Bearer {TAVILY_API_KEY}",
    }
    payload = {
        "query": query,
        "search_depth": TAVILY_SEARCH_DEPTH,
        "time_range": time_range,
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False
    }
    if include_domains:
        payload["include_domains"] = include_domains
    entry["request_payload"] = copy.deepcopy(payload)
    entry["request_headers"] = {
        "Content-Type": "application/json",
        "x-api-key": _mask_secret(TAVILY_API_KEY),
        "Authorization": f"Bearer {_mask_secret(TAVILY_API_KEY)}",
    }

    # Perform POST using requests if available, otherwise stdlib urllib
    try:
        if requests is not None:
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        else:
            req = _UrlRequest(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with _urlopen(req, timeout=20) as resp2:
                raw = resp2.read().decode("utf-8", errors="ignore")
            try:
                data = json.loads(raw)
            except Exception:
                raise RuntimeError(f"Non-JSON response from Tavily: {raw[:240]}")
    except (_HTTPError, _URLError, Exception) as exc:  # pragma: no cover - network dependent
        entry["status"] = "error"
        # Try to summarize requests HTTP errors; otherwise fall back to string
        summary = _summarize_http_error(exc)
        if not summary and isinstance(exc, _HTTPError):
            try:
                body = exc.read().decode("utf-8", errors="ignore").strip()
            except Exception:
                body = ""
            if body:
                summary = f"HTTP {getattr(exc, 'code', 'error')}: {body[:240]}"
        entry["error"] = summary or str(exc)
        return []
    out = []
    for item in data.get("results", []) or []:
        link = (item.get("url") or "").strip()
        if not link:
            continue
        out.append({
            "title": (item.get("title") or "").strip(),
            "url": link,
            "snippet": (item.get("content") or "").strip(),
            "source": (item.get("source") or "").strip(),
            "date": item.get("published_date") or None
        })
    entry["status"] = "ok"
    entry["result_count"] = len(out)
    entry["response_payload"] = copy.deepcopy(data)
    entry["response_results"] = copy.deepcopy(out)
    return out


def build_context(run_type: str):
    """
    Build a vetted source list using Tavily.
    - DAILY: time_range = "day"
    - WEEKLY: time_range = "week"
    Bias to PREFERRED_DOMAINS.
    """
    time_range = "day" if run_type == "daily" else "week"

    def _week_index(dt: datetime) -> int:
        # ISO week of year, stable rotation key
        return int(dt.strftime("%G%V"))

    def _theme_boosters(now: datetime) -> list[str]:
        boosters = [
            "skills graph",
            "recruiting AI agents",
            "payroll agents",
            "employee experience copilots",
        ]
        # Rotate deterministically by ISO week
        idx = _week_index(now) % len(boosters)
        # Pick two adjacent boosters for variety
        chosen = [boosters[idx], boosters[(idx + 1) % len(boosters)]]
        # Scope to Workday/HR tech context
        themed = []
        for b in chosen:
            themed.append(f"Workday {b} HCM")
            themed.append(f"HR technology {b} Workday")
        return themed

    def _daily_queries() -> list[str]:
        return [
            # Core Workday + Agentic AI
            "Workday agentic AI HCM",
            "site:blog.workday.com (agentic OR autonomous OR copilots OR AI) Workday HCM",
            "site:newsroom.workday.com (AI OR agent) announcement",
            "Workday Extend agentic AI use cases",
            "Workday Skills Cloud AI orchestration",
            "Workday Prism Analytics AI governance",
            "Workday AI Marketplace partners",

            # Competitors' AI in HCM/ERP
            "SAP SuccessFactors AI agentic HCM",
            "Oracle Fusion HCM AI agent OR copilot",
            "UKG AI HCM copilot",
            "ADP AI HCM innovations",
            "Dayforce (Ceridian) AI HCM agent",
            "ServiceNow HRSD AI agent HR service delivery",

            # Integrations / platform enablers
            "Workday + Microsoft Copilot integration",
            "Workday + AWS Bedrock agent",
            "Workday + Google Cloud gen AI partnership",
            "Workday + Salesforce integration AI HCM",
            "Workday + MuleSoft OR Boomi OR Workato integration AI",

            # Risk, trust, compliance
            "Workday AI governance bias mitigation HCM",
            "Responsible AI HR technology Workday",

            # GSIs / SIs moves (fresh press/POVs)
            "site:newsroom.accenture.com Workday AI",
            "site:deloitte.com Workday AI POV",
            "site:ey.com Workday AI",
            "site:kpmg.com Workday AI",
            "site:pwc.com Workday AI",
            "site:ibm.com Workday AI",
            "site:capgemini.com Workday AI",
            "site:infosys.com Workday AI Workday",
            "site:tcs.com Workday AI",
            "site:wipro.com Workday AI",

            # Boutiques / leading partners
            "site:kainos.com Workday AI",
            "site:invisors.com Workday AI",
            "site:topbloc.com Workday AI",
            "site:onesourcevirtual.com Workday AI",
            "site:alight.com Workday Workday AI",
            "site:mercer.com Workday AI",
        ]

    def _weekly_queries(now: datetime) -> list[str]:
        base = [
            # Strategy & architecture
            "Agentic AI in SaaS enterprise patterns orchestration",
            "Autonomous agents HR tech governance ROI case studies",
            "RAG orchestration HR data Workday integrations",
            "Workday Extend patterns agentic automations reference architectures",

            # Workday product + platform deep dives
            "Workday Skills Cloud AI roadmap",
            "Workday Prism Analytics AI data quality lineage",
            "Workday AI Marketplace catalog partners",
            "Workday Rising announcements AI",
            "Workday DevCon agentic demos Extend",
            "VNDLY Workday AI procurement workforce apps",

            # Competitive landscape (HCM/ERP)
            "Oracle Fusion HCM gen AI roadmap agent",
            "SAP SuccessFactors Joule agent capabilities",
            "ServiceNow Now Assist HR agentic",
            "UKG Pro AI copilot HCM",
            "Dayforce AI roadmap agent",
            "Microsoft Copilot HR integrations Workday",
            "Google Vertex AI HR solutions Workday partner",

            # Integration / ecosystem enablers
            "MuleSoft Workday AI integration blueprint",
            "Boomi Workday HR integration AI",
            "Workato Workday HR automations AI",
            "Okta Workday lifecycle automation + AI",
            "Snowflake Workday data + gen AI",
            "Databricks Workday HR analytics gen AI",

            # Risk, trust, compliance
            "Responsible AI in HR technology guidelines",
            "EEOC algorithmic bias HR AI guidance",
            "NIST AI RMF HR use cases",
            "EU AI Act HR impact Workday",

            # GSIs / SIs POVs & offers
            "site:deloitte.com Workday AI point of view",
            "site:newsroom.accenture.com Workday AI platform",
            "site:ey.com Workday gen AI",
            "site:kpmg.com Workday AI accelerators",
            "site:pwc.com Workday AI transformation",
            "site:ibm.com Workday AI consulting",
            "site:capgemini.com Workday generative AI",
            "site:infosys.com Live Enterprise Workday AI",
            "site:tcs.com Workday gen AI",
            "site:wipro.com ai360 Workday",

            # Boutiques / leading partners
            "site:kainos.com Workday AI case study",
            "site:invisors.com Workday AI",
            "site:topbloc.com AI Workday",
            "site:onesourcevirtual.com AI Workday",
            "site:alight.com Workday AI",
            "site:mercer.com gen AI Workday",

            # Analysts & credible press
            "Gartner HCM suite AI agent research",
            "Forrester generative AI HR platforms Wave",
            "IDC Workday AI assessment",
            "Reuters Workday AI news",
            "Bloomberg Workday AI strategy",
            "VentureBeat HR AI agents Workday",
            "TechCrunch Workday AI",
        ]
        return base + _theme_boosters(now)

    # Build query set
    now = now_et
    if run_type == "daily":
        queries = _daily_queries()
    else:
        queries = _weekly_queries(now)

    results: list[dict] = []
    tavily_debug: list[dict] = []

    # First pass: bias to preferred domains
    for q in queries:
        results.extend(
            tavily_search(
                q,
                time_range,
                include_domains=PREFERRED_DOMAINS,
                max_results=10,
                debug_log=tavily_debug,
            )
        )

    # Optional broaden pass if not enough results
    broaden_threshold = 8
    if len(results) < broaden_threshold:
        for q in queries:
            results.extend(
                tavily_search(
                    q,
                    time_range,
                    include_domains=None,
                    max_results=5,
                    debug_log=tavily_debug,
                )
            )

    # ---- De-duplication helpers ----
    def _normalize_url(url: str) -> str:
        try:
            parts = urlsplit(url)
            # Drop fragments; remove common tracking params
            query_pairs = []
            if parts.query:
                drop = {
                    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                    "gclid", "fbclid", "msclkid", "ocid", "sc_cid",
                }
                for kv in parts.query.split("&"):
                    if not kv:
                        continue
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                    else:
                        k, v = kv, ""
                    if k.lower() in drop:
                        continue
                    query_pairs.append((k, v))
            query_str = "&".join([f"{k}={v}" if v else k for k, v in query_pairs])
            # Normalize // and trailing slash on path
            path = parts.path or "/"
            norm = urlunsplit((parts.scheme or "https", parts.netloc.lower(), path, query_str, ""))
            return norm.rstrip("/")
        except Exception:
            return url

    def _hostname(url: str) -> str:
        try:
            return (urlsplit(url).netloc or "").lower().lstrip("www.")
        except Exception:
            return ""

    def _score(item: dict) -> int:
        title = (item.get("title") or "").strip()
        snippet = (item.get("snippet") or "").strip()
        # Favor longer, descriptive titles/snippets; cap contribution to avoid bias
        return min(len(title), 180) + min(len(snippet), 400)

    # 1) Deduplicate by normalized URL, keep highest score
    by_url: dict[str, dict] = {}
    for it in results:
        u_norm = _normalize_url(it.get("url", ""))
        if not u_norm:
            continue
        prev = by_url.get(u_norm)
        if prev is None or _score(it) > _score(prev):
            by_url[u_norm] = dict(it, url=u_norm)

    # 2) Within each hostname, dedupe by (hostname, normalized title)
    def _norm_title(s: str) -> str:
        s2 = (s or "").strip().lower()
        s2 = re.sub(r"\s+", " ", s2)
        return s2

    host_title_best: dict[tuple[str, str], dict] = {}
    for it in by_url.values():
        host = _hostname(it.get("url", ""))
        title_key = _norm_title(it.get("title", ""))
        key = (host, title_key)
        prev = host_title_best.get(key)
        if prev is None or _score(it) > _score(prev):
            host_title_best[key] = it

    deduped = list(host_title_best.values())

    # 3) Optionally validate URLs (2xx). Keep fast timeouts and fail-open when no network.
    def _http_ok(url: str, timeout: float = 6.0) -> bool:
        try:
            if requests is not None:
                try:
                    r = requests.head(url, timeout=timeout, allow_redirects=True)
                except Exception:
                    r = requests.get(url, timeout=timeout, allow_redirects=True)
                return 200 <= int(getattr(r, "status_code", 0)) < 300
            # stdlib fallback
            try:
                req = _UrlRequest(url, method="HEAD")
                with _urlopen(req, timeout=timeout) as resp:
                    code = getattr(resp, "status", 200)
                return 200 <= int(code) < 300
            except Exception:
                req = _UrlRequest(url, method="GET")
                with _urlopen(req, timeout=timeout) as resp:
                    code = getattr(resp, "status", 200)
                return 200 <= int(code) < 300
        except Exception:
            return False

    validated: list[dict] = []
    for it in deduped:
        u = it.get("url", "")
        if not u:
            continue
        if not _http_ok(u):
            continue
        validated.append(it)

    # If validation removed everything, fall back to deduped set
    final_items = validated or deduped

    if not final_items:
        return [], "NO_SEARCH_RESULTS", tavily_debug

    # Compact context string given to the model
    lines = [f"{i+1}. {it['title']} — {it['url']}" for i, it in enumerate(final_items)]
    return final_items, "\n".join(lines), tavily_debug


def _build_stub_payload(run_type: str) -> dict:
    """Return a production-style preview payload when the API is unavailable.

    The goal is to keep local/dev runs and CI green without publishing obvious
    "stub" placeholders. Content is neutral, evergreen, and uses public URLs.
    """
    title = "Workday HCM + AI"

    # Curated, always-on public links (no auth) that are safe to render
    url_workday_ai = "https://www.workday.com/en-us/products/ai-ml/ai.html"
    url_sap_sfx = "https://www.sap.com/products/hcm/employee-experience-management/successfactors.html"
    url_enablement = "https://github.com/openai/openai-cookbook"

    html_body = (
        "<h2>Workday HCM + AI – Brief</h2>"
        "<p><strong>What matters now:</strong> Preview build generated locally. "
        "Connect OpenAI to populate today's headlines.</p>"
        "<h3>Highlights</h3>"
        "<ul>"
        f"<li><strong>Workday AI overview:</strong> <a href=\"{url_workday_ai}\">workday.com</a></li>"
        "</ul>"
        "<h3>Competitive Watch</h3>"
        "<ul>"
        f"<li><strong>SAP SuccessFactors:</strong> <a href=\"{url_sap_sfx}\">product page</a></li>"
        "</ul>"
        "<h3>Enablement</h3>"
        "<ul>"
        f"<li><strong>Prompt engineering for briefs:</strong> <a href=\"{url_enablement}\">OpenAI Cookbook</a></li>"
        "</ul>"
        "<h3>Actions for Next Week</h3>"
        "<ul>"
        "<li>Connect OpenAI API and set OPENAI_API_KEY</li>"
        "<li>Review and refine prompts and section ordering</li>"
        "</ul>"
        "<h3>Risks & Mitigations</h3>"
        "<ul>"
        "<li><strong>API unavailable:</strong> falls back to local preview; ensure GitHub/CI secrets are configured.</li>"
        "</ul>"
        "<h3>All Sources</h3>"
        f"<ul><li><a href=\"{url_workday_ai}\">{url_workday_ai}</a></li>"
        f"<li><a href=\"{url_sap_sfx}\">{url_sap_sfx}</a></li>"
        f"<li><a href=\"{url_enablement}\">{url_enablement}</a></li></ul>"
    )

    payload = {
        "type": run_type,
        "run_date": TODAY_ET,
        "title": title,
        "priority_focus": (
            "Local preview is active. Add OPENAI_API_KEY to generate live, dated highlights."
        ),
        "highlights": [
            {
                "headline": "Workday AI – product overview (Published: PREVIEW)",
                "why_it_matters": "Evergreen overview to validate rendering while API is disconnected.",
                "source_url": url_workday_ai,
            }
        ],
        "competitive_watch": [
            {
                "competitor": "SAP SuccessFactors",
                "move": "AI overview and positioning",
                "implication": "Track competitive messaging; refine Deloitte POV when live data is enabled.",
            }
        ],
        "enablement": [
            {
                "skill": "Prompt engineering for research briefs",
                "resource_url": url_enablement,
                "90_day_outcome": "Publish high-quality daily/weekly briefs reliably and safely.",
            }
        ],
        "actions_next_week": [
            "Connect OpenAI API and secrets in CI",
            "Tune prompts and validate link hygiene",
        ],
        "risks": [
            {"risk": "External API unavailable", "mitigation": "Fallback to local preview without 'stub' labels"}
        ],
        "sources": [
            {"title": "Workday AI overview", "url": url_workday_ai},
            {"title": "SAP SuccessFactors HCM", "url": url_sap_sfx},
            {"title": "OpenAI Cookbook", "url": url_enablement},
        ],
        "html_body": html_body,
        "plain_text_body": (
            "Workday HCM + AI – Brief\n"
            "What matters now: Preview build generated locally. Connect OpenAI to populate today's headlines.\n"
            "Highlights:\n"
            f" - Workday AI overview: {url_workday_ai}\n"
            "Competitive Watch:\n"
            f" - SAP SuccessFactors: {url_sap_sfx}\n"
            "Enablement:\n"
            f" - Prompt engineering for briefs: {url_enablement}\n"
            "Actions for Next Week:\n"
            " - Connect OpenAI API and set OPENAI_API_KEY\n"
            " - Review and refine prompts and section ordering\n"
            "Risks & Mitigations:\n"
            " - API unavailable: falls back to local preview; ensure secrets configured.\n"
            "All Sources:\n"
            f" - {url_workday_ai}\n"
            f" - {url_sap_sfx}\n"
            f" - {url_enablement}\n"
        ),
    }
    return payload


def _build_no_results_payload(run_type: str) -> dict:
    """Return a payload noting that no credible sources were available."""

    window_desc = "last day" if run_type == "daily" else "last week"
    title = "No credible Workday AI updates"
    message = f"No credible items found in the {window_desc} window."
    html_body = (
        f"<h2>Workday HCM + AI – {run_type.title()} Brief</h2>"
        f"<p>{html_lib.escape(message)}</p>"
        "<p>Sources will resume once new vetted updates are available.</p>"
    )
    plain_text_body = (
        f"Workday HCM + AI – {run_type.title()} Brief\n"
        f"{message}\n"
        "Sources will resume once new vetted updates are available."
    )
    return {
        "type": run_type,
        "run_date": TODAY_ET,
        "title": title,
        "priority_focus": message,
        "highlights": [],
        "competitive_watch": [],
        "enablement": [],
        "actions_next_week": [],
        "risks": [],
        "sources": [],
        "html_body": html_body,
        "plain_text_body": plain_text_body,
    }


def _make_user_prompt(context_text: str) -> str:
    header = (
        "Use the following vetted sources to craft the brief.\n"
        "SOURCE LIST (you must ONLY cite these; do not fabricate new sources or URLs):\n"
        f"{context_text}\n\n"
    )
    return header + USER_PROMPT_SCHEMA


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


def _get_site_base_url() -> str | None:
    """Return absolute base URL for making relative links absolute.

    Preference order:
    1) Explicit env SITE_BASE_URL (e.g., https://user.github.io/repo/)
    2) Derive from GITHUB_REPOSITORY for GitHub Pages project sites
       -> https://{owner}.github.io/{repo}/

    Ensures a trailing slash.
    """
    base = (os.environ.get("SITE_BASE_URL", "").strip() or None)
    if not base:
        repo = os.environ.get("GITHUB_REPOSITORY", "").strip()  # owner/repo
        if repo and "/" in repo:
            owner, repo_name = repo.split("/", 1)
            if owner and repo_name:
                base = f"https://{owner}.github.io/{repo_name}/"
    if not base:
        return None
    # Normalize to include trailing slash
    if not base.endswith("/"):
        base = base + "/"
    return base


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

    # Allow anchors and mail/tel and already-absolute http(s) as-is
    if s.startswith(("http://", "https://", "mailto:", "tel:", "#")):
        return _percent_encode_url(s)

    # Leading slash without domain: leave relative paths untouched so humans can
    # see the model output, but percent-encode unsafe characters.
    if s.startswith("/"):
        return _percent_encode_url(s)

    # Anything else that lacks a scheme: keep the original text rather than
    # forcing a "#" anchor so operators can see and diagnose the value.
    return _percent_encode_url(s)


def _rewrite_links_in_html(html_markup: str) -> str:
    """Rewrite <a href> links to be absolute, safely quoted, and launchable.

    - Ensures href is double-quoted
    - Adds target="_blank" and rel="noopener noreferrer" for external links
    - Leaves mailto/tel/# intact, but still properly quoted
    """
    if not html_markup:
        return html_markup

    # Respect preservation flag; when enabled, do not alter model HTML
    if PRESERVE_MODEL_HTML:
        return html_markup

    # Opportunistically convert plain URLs/markdown to anchors when the model
    # returned text without proper <a> tags. This reduces broken links when the
    # model violates the prompt and emits non-HTML links.
    html_markup = _autolink_plain_urls_and_markdown(html_markup)

    # Permit optional whitespace around the equals sign so tags like
    # <a href = "..."> are matched (models occasionally emit this style).
    # Support both quoted and unquoted href values. Standardize to double quotes.
    a_tag_pattern = re.compile(
        r"<a\s+([^>]*?)href\s*=\s*(?:([\'\"])(.*?)(?:\2)|([^\s>]+))([^>]*)>",
        re.IGNORECASE,
    )

    def _replacer(match: re.Match) -> str:
        pre_attrs = match.group(1) or ""
        quote_ch = '"'  # standardize
        # Group 3 is URL when quoted; group 4 when unquoted
        href_val = (match.group(3) if match.group(3) is not None else match.group(4)) or ""
        post_attrs = match.group(5) or ""

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


def _autolink_plain_urls_and_markdown(html_markup: str) -> str:
    """Convert common non-HTML link syntaxes to anchors when <a> tags are absent.

    - Converts markdown [text](https://...) to <a href="...">text</a>
    - Converts bare https://... URLs to anchors
    - Converts <https://...> or &lt;https://...&gt; to anchors

    This function is intentionally conservative: it only activates when there
    are no <a ...> tags present to avoid double-wrapping or interfering with
    already well-formed HTML.
    """
    try:
        if not html_markup:
            return html_markup
        lower = html_markup.lower()
        if "<a " in lower:
            return html_markup

        text = html_markup

        # 1) Markdown links: [label](https://example.com/path)
        md_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
        def _md_repl(m: re.Match) -> str:
            label = html_lib.escape(m.group(1))
            url = _normalize_href(m.group(2))
            return f'<a href="{url}">{label}</a>'
        text = md_pattern.sub(_md_repl, text)

        # 2) Angle-bracket links: <https://example.com> or &lt;https://...&gt;
        angle_pattern = re.compile(r"(?:<|&lt;)(https?://[^\s<>]+)(?:>|&gt;)", re.IGNORECASE)
        def _angle_repl(m: re.Match) -> str:
            url = m.group(1)
            norm = _normalize_href(url)
            return f'<a href="{norm}">{url}</a>'
        text = angle_pattern.sub(_angle_repl, text)

        # 3) Bare URLs in text nodes: wrap with anchors; keep preceding delimiter
        bare_url_pattern = re.compile(r'(^|[\s\(\[])(https?://[^\s<>()\"]+)', re.IGNORECASE)
        def _bare_repl(m: re.Match) -> str:
            prefix = m.group(1) or ''
            url = m.group(2)
            norm = _normalize_href(url)
            return prefix + f'<a href="{norm}">{url}</a>'
        text = bare_url_pattern.sub(_bare_repl, text)

        return text
    except Exception:
        # Fail open; if anything goes wrong, return original markup
        return html_markup


def _render_html_from_structured(payload: dict) -> str:
    """Render a minimal HTML body from structured fields as a fallback.

    Used when the model's html_body is missing/invalid. Keeps ordering stable:
    Highlights; Competitive Watch; Enablement; Actions; Risks; All Sources.
    """
    title = html_lib.escape(payload.get("title", "Workday HCM + AI – Brief") or "Workday HCM + AI – Brief")
    priority_focus = html_lib.escape(payload.get("priority_focus", "") or "")

    def _li(content: str) -> str:
        return f"<li>{content}</li>"

    # Highlights
    highlights_html = ""
    highlights = payload.get("highlights") or []
    if isinstance(highlights, list) and highlights:
        items: list[str] = []
        for item in highlights:
            if not isinstance(item, dict):
                continue
            headline = html_lib.escape(item.get("headline", "") or "")
            why = html_lib.escape(item.get("why_it_matters", "") or "")
            url = _normalize_href(str(item.get("source_url", "") or ""))
            link = f'<a href="{url}">{headline or url}</a>' if url else headline
            text = f"<strong>{link}</strong>"
            if why:
                text += f": {why}"
            items.append(_li(text))
        if items:
            highlights_html = "<h3>Highlights</h3><ul>" + "".join(items) + "</ul>"

    # Competitive Watch
    comp_html = ""
    comp = payload.get("competitive_watch") or []
    if isinstance(comp, list) and comp:
        items = []
        for c in comp:
            if not isinstance(c, dict):
                continue
            competitor = html_lib.escape(c.get("competitor", "") or "")
            move = html_lib.escape(c.get("move", "") or "")
            implication = html_lib.escape(c.get("implication", "") or "")
            txt = f"<strong>{competitor}</strong>: {move}"
            if implication:
                txt += f" – {implication}"
            items.append(_li(txt))
        if items:
            comp_html = "<h3>Competitive Watch</h3><ul>" + "".join(items) + "</ul>"

    # Enablement
    enable_html = ""
    enable = payload.get("enablement") or []
    if isinstance(enable, list) and enable:
        items = []
        for e in enable:
            if not isinstance(e, dict):
                continue
            skill = html_lib.escape(e.get("skill", "") or "")
            outcome = html_lib.escape(e.get("90_day_outcome", "") or "")
            res_url = _normalize_href(str(e.get("resource_url", "") or ""))
            link = f'<a href="{res_url}">{html_lib.escape("Resource")}</a>' if res_url else "Resource"
            txt = f"<strong>{skill}:</strong> {link}"
            if outcome:
                txt += f" – {outcome}"
            items.append(_li(txt))
        if items:
            enable_html = "<h3>Enablement</h3><ul>" + "".join(items) + "</ul>"

    # Actions
    actions_html = ""
    actions = payload.get("actions_next_week") or []
    if isinstance(actions, list) and actions:
        items = []
        for a in actions:
            if not isinstance(a, str):
                continue
            items.append(_li(html_lib.escape(a)))
        if items:
            actions_html = "<h3>Actions for Next Week</h3><ul>" + "".join(items) + "</ul>"

    # Risks
    risks_html = ""
    risks = payload.get("risks") or []
    if isinstance(risks, list) and risks:
        items = []
        for r in risks:
            if not isinstance(r, dict):
                continue
            risk = html_lib.escape(r.get("risk", "") or "")
            mit = html_lib.escape(r.get("mitigation", "") or "")
            txt = f"<strong>{risk}</strong>: {mit}" if mit else risk
            items.append(_li(txt))
        if items:
            risks_html = "<h3>Risks & Mitigations</h3><ul>" + "".join(items) + "</ul>"

    # Sources
    sources_html = ""
    sources = payload.get("sources") or []
    if isinstance(sources, list) and sources:
        items = []
        for s in sources:
            if not isinstance(s, dict):
                continue
            title = html_lib.escape(s.get("title", "") or "Source")
            url = _normalize_href(str(s.get("url", "") or ""))
            if url:
                items.append(_li(f'<a href="{url}">{title}</a>'))
        if items:
            sources_html = "<h3>All Sources</h3><ul>" + "".join(items) + "</ul>"

    body_parts = [
        f"<h2>{title}</h2>",
    ]
    if priority_focus:
        body_parts.append(f"<p><strong>What matters now:</strong> {priority_focus}</p>")
    body_parts.extend([
        highlights_html,
        comp_html,
        enable_html,
        actions_html,
        risks_html,
        sources_html,
    ])
    # Filter out empty strings
    body = "".join([part for part in body_parts if part])
    return body or "<h2>Workday HCM + AI – Brief</h2><p>No content available.</p>"


# ====== OpenAI Call ======
def call_openai(run_type: str, mode: str = "auto") -> dict:
    """Call OpenAI using the Responses API, with fallback to Chat Completions.

    If any API call fails or returns an unexpected payload, return a stub payload
    so CI can continue (and Pages/email still get generated).
    """
    # Gather Tavily context and build prompts up-front for debugging
    context_results, context_text_or_flag, tavily_debug = build_context(run_type)
    if context_text_or_flag == "NO_SEARCH_RESULTS":
        payload = _build_no_results_payload(run_type)
        payload["_debug_endpoint"] = "no-search-results"
        payload["_debug_model"] = None
        payload["_debug_prompt"] = "OpenAI skipped: no credible Tavily sources available."
        payload["_debug_live"] = False
        payload["_debug_context_sources"] = context_results
        payload["_debug_context_text"] = context_text_or_flag
        payload["_debug_tavily"] = tavily_debug
        return payload

    context_text = context_text_or_flag

    # Compute prompt data up-front so we can expose it in HTML for debugging
    system_prompt = SYSTEM_PROMPT_DAILY if run_type == "daily" else SYSTEM_PROMPT_WEEKLY
    user_prompt = _make_user_prompt(context_text)
    combined_prompt = f"{system_prompt}\n\n{user_prompt}"
    # Build model candidate list with sensible fallbacks
    configured_model = (OPENAI_MODEL or "").strip()
    candidate_models: list[str] = [
        m
        for m in [
            configured_model,
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4o-mini",
            "gpt-4o",
            "o4-mini",
        ]
        if m
    ]
    # De-duplicate while preserving order
    seen_models: set[str] = set()
    candidate_models = [m for m in candidate_models if not (m in seen_models or seen_models.add(m))]

    if not OPENAI_API_KEY:
        if OPENAI_REQUIRE_LIVE:
            raise RuntimeError("OPENAI_API_KEY is required when OPENAI_REQUIRE_LIVE=1")
        payload = _build_stub_payload(run_type)
        payload["_debug_endpoint"] = "stub"
        # Use configured model if present, otherwise the first candidate (gpt-4o-mini)
        debug_model = configured_model or (candidate_models[0] if candidate_models else "gpt-4o-mini")
        payload["_debug_model"] = debug_model
        payload["_debug_prompt"] = combined_prompt
        payload["_debug_live"] = False
        payload["_debug_context_sources"] = context_results
        payload["_debug_context_text"] = context_text
        payload["_debug_tavily"] = tavily_debug
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

            # Newer Responses API payloads wrap text inside nested content blocks.
            response_obj = data.get("response")
            if isinstance(response_obj, dict):
                nested = response_obj.get("output")
                if nested:
                    data = {"output": nested}

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
                            content_list = item.get("content")
                            if isinstance(content_list, list):
                                for block in content_list:
                                    if isinstance(block, str) and block.strip():
                                        return block
                                    if isinstance(block, dict):
                                        block_text = block.get("text") or block.get("content")
                                        if isinstance(block_text, str) and block_text.strip():
                                            return block_text

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

    # Iterate through candidate models; for each model try Responses first, then Chat
    last_error: Exception | None = None
    for model_in_use in candidate_models:
        # Attempt 1: Responses API with string input (portable shape)
        try:
            responses_url = "https://api.openai.com/v1/responses"
            if mode in ("auto", "responses"):
                responses_variants = _responses_payload_variants(
                    model_in_use, system_prompt, user_prompt
                )
                responses_error: Exception | None = None
                for idx, payload_variant in enumerate(responses_variants):
                    try:
                        resp = requests.post(
                            responses_url,
                            headers=headers,
                            json=payload_variant,
                            timeout=120,
                        )
                        resp.raise_for_status()
                    except Exception as request_error:
                        responses_error = request_error
                        if requests is not None and isinstance(request_error, requests.HTTPError):
                            response = getattr(request_error, "response", None)
                            status = getattr(response, "status_code", None)
                            if status == 400 and idx + 1 < len(responses_variants):
                                summary = _summarize_http_error(request_error)
                                if summary:
                                    print(
                                        f"Responses API 400 with variant {idx + 1}/{len(responses_variants)}: {summary}",
                                        file=sys.stderr,
                                    )
                                continue
                        raise

                    data = resp.json()
                    content = _extract_text_from_responses_api_payload(data)
                    if not content:
                        raise RuntimeError("Responses API did not include content in an expected format")
                    payload = _coerce_json(content)
                    payload["_debug_endpoint"] = "responses"
                    payload["_debug_model"] = model_in_use
                    payload["_debug_prompt"] = combined_prompt
                    payload["_debug_raw_http_json"] = data
                    payload["_debug_content"] = content
                    payload["_debug_live"] = True
                    payload["_debug_context_sources"] = context_results
                    payload["_debug_context_text"] = context_text
                    # Retain a deep-ish copy of the parsed payload prior to any post-processing
                    try:
                        payload["_debug_parsed_from_content"] = json.loads(
                            json.dumps(payload, ensure_ascii=False)
                        )
                    except Exception:
                        pass
                    return payload

                if responses_error:
                    raise responses_error
        except Exception as e_responses:
            last_error = e_responses
            if mode in ("auto", "responses"):
                print(
                    f"Responses API failed, falling back to Chat Completions: {e_responses}",
                    file=sys.stderr,
                )
                # If the caller explicitly requested only Responses, keep trying other models; if none succeed, return stub later
                if mode == "responses":
                    continue

        # Attempt 2: Chat Completions (widely supported)
        try:
            chat_url = "https://api.openai.com/v1/chat/completions"
            if mode in ("auto", "chat"):
                chat_variants = _chat_payload_variants(model_in_use, system_prompt, user_prompt)
                chat_error: Exception | None = None
                for idx, payload_variant in enumerate(chat_variants):
                    try:
                        resp = requests.post(
                            chat_url,
                            headers=headers,
                            json=payload_variant,
                            timeout=120,
                        )
                        resp.raise_for_status()
                    except Exception as request_error:
                        chat_error = request_error
                        if requests is not None and isinstance(request_error, requests.HTTPError):
                            response = getattr(request_error, "response", None)
                            status = getattr(response, "status_code", None)
                            if status == 400 and idx + 1 < len(chat_variants):
                                summary = _summarize_http_error(request_error)
                                if summary:
                                    print(
                                        f"Chat Completions 400 with variant {idx + 1}/{len(chat_variants)}: {summary}",
                                        file=sys.stderr,
                                    )
                                continue
                        raise

                    data = resp.json()
                    try:
                        content = data["choices"][0]["message"]["content"]
                    except Exception as e_extract:
                        raise RuntimeError(f"Chat Completions content extraction failed: {e_extract}")
                    payload = _coerce_json(content)
                    payload["_debug_endpoint"] = "chat"
                    payload["_debug_model"] = model_in_use
                    payload["_debug_prompt"] = combined_prompt
                    payload["_debug_raw_http_json"] = data
                    payload["_debug_content"] = content
                    payload["_debug_live"] = True
                    payload["_debug_context_sources"] = context_results
                    payload["_debug_context_text"] = context_text
                    payload["_debug_tavily"] = tavily_debug
                    try:
                        payload["_debug_parsed_from_content"] = json.loads(
                            json.dumps(payload, ensure_ascii=False)
                        )
                    except Exception:
                        pass
                    return payload

                if chat_error:
                    raise chat_error
        except Exception as e_chat:
            last_error = e_chat
            if mode in ("auto", "chat"):
                print(
                    f"OpenAI Chat Completions failed, using stub payload: {e_chat}",
                    file=sys.stderr,
                )
                # In chat-only mode, return stub immediately; in auto, try next model
                if mode == "chat":
                    stub_payload = _build_stub_payload(run_type)
                    stub_payload["_debug_endpoint"] = "stub"
                    stub_payload["_debug_model"] = model_in_use
                    stub_payload["_debug_prompt"] = combined_prompt
                    stub_payload["_debug_live"] = False
                    stub_payload["_debug_context_sources"] = context_results
                    stub_payload["_debug_context_text"] = context_text
                    stub_payload["_debug_tavily"] = tavily_debug
                    return stub_payload
                continue

    # If we reach here, all attempts failed; return a stub to keep runs green
    if mode in ("auto", "chat", "responses"):
        if OPENAI_REQUIRE_LIVE:
            if last_error:
                raise last_error
            raise RuntimeError("OpenAI call failed and live mode is required")
        payload = _build_stub_payload(run_type)
        payload["_debug_endpoint"] = "stub"
        payload["_debug_model"] = candidate_models[0] if candidate_models else (configured_model or "gpt-4o-mini")
        payload["_debug_prompt"] = combined_prompt
        payload["_debug_live"] = False
        payload["_debug_context_sources"] = context_results
        payload["_debug_context_text"] = context_text
        payload["_debug_tavily"] = tavily_debug
        return payload
    # Fallback (should not be reached)
    if last_error:
        raise last_error
    raise RuntimeError("OpenAI call failed with no additional error context")

# ====== Pages Writer ======
def write_html_to_pages(run_type: str, payload: dict) -> str:
    target = "docs/index.html" if run_type == "daily" else "docs/weekly.html"
    html = payload.get("html_body", "<h2>No content</h2>")
    # Fallback: if the model did not provide usable HTML, render from structured fields
    try:
        lacks_anchors = ("<a " not in (html or "").lower())
    except Exception:
        lacks_anchors = False
    if (not html or lacks_anchors) and any(payload.get(k) for k in ("highlights", "competitive_watch", "enablement", "actions_next_week", "risks", "sources")):
        html = _render_html_from_structured(payload)
    # Optionally rewrite and normalize links; default is to preserve model HTML
    html = _rewrite_links_in_html(html)

    # Append debug block showing the exact prompt and raw response from OpenAI
    debug_endpoint = payload.get("_debug_endpoint", "n/a")
    debug_model = payload.get("_debug_model", "n/a")
    debug_prompt = payload.get("_debug_prompt", "")
    debug_raw = payload.get("_debug_raw_http_json")
    debug_live = payload.get("_debug_live")
    debug_content = payload.get("_debug_content", "")
    debug_sections: list[str] = []

    if debug_prompt:
        escaped_prompt = html_lib.escape(debug_prompt)
        debug_sections.append(
            (
                "<details open><summary><strong>Prompt sent to OpenAI</strong></summary>"
                "<pre style=\"white-space:pre-wrap;overflow-x:auto;border:1px solid #ddd;padding:12px;"
                "border-radius:6px;background:#fafafa;margin-top:12px\">"
                f"{escaped_prompt}" "</pre></details>"
            )
        )

    if debug_raw is not None:
        try:
            raw_dump = json.dumps(debug_raw, ensure_ascii=False, indent=2)
        except Exception:
            raw_dump = repr(debug_raw)
        escaped_raw = html_lib.escape(raw_dump)
        debug_sections.append(
            (
                "<details><summary><strong>Raw OpenAI response JSON</strong></summary>"
                "<pre style=\"white-space:pre-wrap;overflow-x:auto;border:1px solid #ddd;padding:12px;"
                "border-radius:6px;background:#fff9e6;margin-top:12px\">"
                f"{escaped_raw}" "</pre></details>"
            )
        )

    if debug_content and not debug_sections:
        escaped_content = html_lib.escape(str(debug_content))
        debug_sections.append(
            (
                "<details open><summary><strong>Model content</strong></summary>"
                "<pre style=\"white-space:pre-wrap;overflow-x:auto;border:1px solid #ddd;padding:12px;"
                "border-radius:6px;background:#fafafa;margin-top:12px\">"
                f"{escaped_content}" "</pre></details>"
            )
        )

    debug_block = ""
    if debug_sections:
        live_status = "Live OpenAI response" if debug_live else "Stub preview (no live response)"
        meta = (
            "<hr>"
            "<h3>OpenAI Debug</h3>"
            f"<p><strong>Status:</strong> {html_lib.escape(live_status)} &nbsp; "
            f"<strong>Endpoint:</strong> {html_lib.escape(str(debug_endpoint))} &nbsp; "
            f"<strong>Model:</strong> {html_lib.escape(str(debug_model))}</p>"
        )
        debug_block = meta + "".join(debug_sections)

    tavily_debug_html = ""
    tavily_debug = payload.get("_debug_tavily")
    if isinstance(tavily_debug, list) and tavily_debug:
        sections: list[str] = []
        for idx, entry in enumerate(tavily_debug, start=1):
            if not isinstance(entry, dict):
                continue
            query = html_lib.escape(str(entry.get("query", "") or f"Search {idx}"))
            status_raw = str(entry.get("status", "unknown"))
            status_label = html_lib.escape(status_raw.replace("_", " ").title())
            summary = f"<details{' open' if idx == 1 else ''}><summary><strong>{query}</strong> — {status_label}</summary>"
            body_parts = ["<div style=\"margin:12px 0 24px\">"]

            time_range = entry.get("time_range")
            if time_range:
                body_parts.append(
                    f"<p><strong>Time range:</strong> {html_lib.escape(str(time_range))}</p>"
                )
            include_domains = entry.get("include_domains")
            if include_domains:
                try:
                    domains_joined = ", ".join(str(d) for d in include_domains)
                except Exception:
                    domains_joined = str(include_domains)
                body_parts.append(
                    f"<p><strong>Preferred domains:</strong> {html_lib.escape(domains_joined)}</p>"
                )
            result_count = entry.get("result_count")
            if isinstance(result_count, int):
                plural = "s" if result_count != 1 else ""
                body_parts.append(
                    f"<p><strong>Result count:</strong> {result_count} item{plural}</p>"
                )
            api_key_present = entry.get("api_key_present")
            if api_key_present is not None:
                status = "Yes" if api_key_present else "No"
                body_parts.append(
                    f"<p><strong>Tavily API key detected:</strong> {status}</p>"
                )
            reason = entry.get("reason")
            if reason:
                body_parts.append(
                    f"<p><strong>Reason:</strong> {html_lib.escape(str(reason))}</p>"
                )
            error = entry.get("error")
            if error:
                body_parts.append(
                    f"<p><strong>Error:</strong> {html_lib.escape(str(error))}</p>"
                )

            request_payload = entry.get("request_payload")
            if request_payload is not None:
                try:
                    request_dump = json.dumps(request_payload, ensure_ascii=False, indent=2)
                except Exception:
                    request_dump = repr(request_payload)
                body_parts.append(
                    "<details open><summary><strong>Request payload</strong></summary>"
                    "<pre style=\"white-space:pre-wrap;overflow-x:auto;border:1px solid #ddd;padding:12px;"
                    "border-radius:6px;background:#fafafa;margin-top:12px\">"
                    f"{html_lib.escape(request_dump)}" "</pre></details>"
                )

            request_headers = entry.get("request_headers")
            if request_headers is not None:
                try:
                    headers_dump = json.dumps(request_headers, ensure_ascii=False, indent=2)
                except Exception:
                    headers_dump = repr(request_headers)
                body_parts.append(
                    "<details><summary><strong>Request headers</strong></summary>"
                    "<pre style=\"white-space:pre-wrap;overflow-x:auto;border:1px solid #ddd;padding:12px;"
                    "border-radius:6px;background:#eef5ff;margin-top:12px\">"
                    f"{html_lib.escape(headers_dump)}" "</pre></details>"
                )

            response_payload = entry.get("response_payload")
            if response_payload is not None:
                try:
                    response_dump = json.dumps(response_payload, ensure_ascii=False, indent=2)
                except Exception:
                    response_dump = repr(response_payload)
                body_parts.append(
                    "<details><summary><strong>Raw Tavily response JSON</strong></summary>"
                    "<pre style=\"white-space:pre-wrap;overflow-x:auto;border:1px solid #ddd;padding:12px;"
                    "border-radius:6px;background:#fff9e6;margin-top:12px\">"
                    f"{html_lib.escape(response_dump)}" "</pre></details>"
                )

            body_parts.append("</div>")
            sections.append(summary + "".join(body_parts) + "</details>")

        if sections:
            tavily_debug_html = "<hr><h3>Tavily Debug</h3>" + "".join(sections)

    if debug_block or tavily_debug_html:
        html = html + debug_block + tavily_debug_html
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


def write_debug_artifacts(run_type: str, payload: dict) -> list[str]:
    """Persist model payload and raw HTTP (if present) under docs/debug.

    Returns list of file paths written.
    """
    paths: list[str] = []
    try:
        _ensure_debug_dir()
        ts = str(payload.get("run_date") or TODAY_ET)
        # Save parsed payload as used by the app
        payload_to_save = dict(payload)
        raw_http = payload_to_save.pop("_debug_raw_http_json", None)
        out_payload = os.path.join(DEBUG_DIR, f"{run_type}-{ts}-payload.json")
        _write_json(out_payload, payload_to_save)
        paths.append(out_payload)
        # Save raw HTTP if available
        if raw_http is not None:
            out_raw = os.path.join(DEBUG_DIR, f"{run_type}-{ts}-raw-http.json")
            _write_json(out_raw, raw_http)
            paths.append(out_raw)
    except Exception:
        pass
    return paths

# ====== Email Sender ======
def send_email(payload: dict):
    if not (EMAIL_FROM and EMAIL_TO and GMAIL_USERNAME and GMAIL_APP_PASSWORD):
        print("Email secrets missing; skipping email send.")
        return
    subject = f"{payload.get('type','daily')} Research – {payload.get('title','Workday HCM + AI')} – {payload.get('run_date', TODAY_ET)}"
    body_html = payload.get("html_body", "<h2>No content</h2>")
    # Normalize links in email as well to avoid broken URLs in clients
    body_html = _rewrite_links_in_html(body_html)
    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(GMAIL_USERNAME, GMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO.split(","), msg.as_string())

# ====== Post-processing and Verify ======
def _postprocess_payload(run_type: str, payload: dict) -> dict:
    """Apply minimal, non-destructive post-processing.

    - Ensure type/run_date exist if omitted by the model (do not override if present)
    """
    processed = dict(payload)  # shallow copy is fine; values are primitives/strings
    processed.setdefault("type", run_type)
    processed.setdefault("run_date", TODAY_ET)
    html_body = processed.get("html_body")
    if isinstance(html_body, str):
        processed["html_body"] = _rewrite_links_in_html(html_body)
    return processed


def _ensure_debug_dir() -> None:
    os.makedirs(DEBUG_DIR, exist_ok=True)


def _write_json(path: str, obj: dict | list | str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def run_verify(default_run: str = "daily") -> int:
    """Run a verification that compares model output vs rendered artifacts.

    Writes artifacts into docs/debug and prints file paths.
    Returns process exit code (0 success, 1 mismatch that matters or error).
    """
    verify_run = default_run if default_run in ("daily", "weekly") else "daily"
    # Collect both endpoints to compare their raw content and parsed payloads
    payload = call_openai(verify_run, mode="responses")
    payload_chat = None
    try:
        payload_chat = call_openai(verify_run, mode="chat")
    except Exception:
        payload_chat = None

    # Capture originals and post-processed versions
    original = dict(payload)
    processed = _postprocess_payload(verify_run, original)

    # Compute HTML transformations that the app would apply
    model_html = original.get("html_body", "") or ""
    rewritten_html = _rewrite_links_in_html(model_html)
    email_html = processed.get("html_body", "") or ""

    html_preserved = (model_html == rewritten_html)
    email_matches_model = (email_html == model_html)
    email_matches_rewritten = (email_html == rewritten_html)

    # If the model provided run_date/type, ensure we did not override
    model_run_date = None
    model_type = None
    try:
        # Best-effort to parse from original content string to avoid any mutation
        content_str = original.get("_debug_content", "") or ""
        if content_str:
            # Minimal coerce: strip fences if present
            text = content_str.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                lines = lines[1:] if len(lines) > 1 else []
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                text = "\n".join(lines).strip()
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                model_run_date = parsed.get("run_date")
                model_type = parsed.get("type")
    except Exception:
        pass

    run_date_preserved = True
    type_preserved = True
    if model_run_date:
        run_date_preserved = (processed.get("run_date") == model_run_date)
    if model_type:
        type_preserved = (processed.get("type") == model_type)

    # Build report
    report = {
        "endpoint": original.get("_debug_endpoint"),
        "model": original.get("_debug_model"),
        "run_type": verify_run,
        "html_preserved": html_preserved,
        "email_matches_model": email_matches_model,
        "email_matches_rewritten": email_matches_rewritten,
        "run_date_preserved": run_date_preserved,
        "type_preserved": type_preserved,
        "prompt_preview": (original.get("_debug_prompt") or "")[:3000],
        "chat_endpoint_available": bool(payload_chat),
    }

    _ensure_debug_dir()
    ts = TODAY_ET
    # Save raw HTTP shape if present
    raw_http = original.get("_debug_raw_http_json")
    if raw_http is not None:
        _write_json(os.path.join(DEBUG_DIR, f"{verify_run}-{ts}-raw-http.json"), raw_http)
    # Save parsed payload exactly as used before post-processing
    payload_to_save = dict(original)
    # Avoid duplicating massive fields in the saved payload
    payload_to_save.pop("_debug_raw_http_json", None)
    _write_json(os.path.join(DEBUG_DIR, f"{verify_run}-{ts}-payload.json"), payload_to_save)
    # Save chat variant if available
    if payload_chat is not None:
        chat_raw = payload_chat.get("_debug_raw_http_json")
        if chat_raw is not None:
            _write_json(os.path.join(DEBUG_DIR, f"{verify_run}-{ts}-chat-raw-http.json"), chat_raw)
        payload_chat_to_save = dict(payload_chat)
        payload_chat_to_save.pop("_debug_raw_http_json", None)
        _write_json(os.path.join(DEBUG_DIR, f"{verify_run}-{ts}-chat-payload.json"), payload_chat_to_save)
    # Save verification report
    _write_json(os.path.join(DEBUG_DIR, f"{verify_run}-{ts}-verify.json"), report)

    # Print a concise summary to stdout
    print("Verify artifacts written to:")
    print(os.path.join(DEBUG_DIR, f"{verify_run}-{ts}-raw-http.json"))
    print(os.path.join(DEBUG_DIR, f"{verify_run}-{ts}-payload.json"))
    print(os.path.join(DEBUG_DIR, f"{verify_run}-{ts}-verify.json"))
    if payload_chat is not None:
        print(os.path.join(DEBUG_DIR, f"{verify_run}-{ts}-chat-raw-http.json"))
        print(os.path.join(DEBUG_DIR, f"{verify_run}-{ts}-chat-payload.json"))

    # Determine exit code: mismatch that matters?
    ok = email_matches_rewritten and run_date_preserved and type_preserved
    return 0 if ok else 1

# ====== Main ======
def main():
    if RUN_TYPE == "verify":
        verify_target = sys.argv[2] if len(sys.argv) >= 3 else "daily"
        exit_code = run_verify(verify_target)
        sys.exit(exit_code)

    payload = call_openai(RUN_TYPE)

    # Minimal, non-destructive post-processing
    payload = _postprocess_payload(RUN_TYPE, payload)

    # Persist debug artifacts for this run (pages will link to these)
    write_debug_artifacts(RUN_TYPE, payload)

    # Write to Pages and email
    target_file = write_html_to_pages(RUN_TYPE, payload)
    print(f"Wrote: {target_file}")
    send_email(payload)

if __name__ == "__main__":
    main()
