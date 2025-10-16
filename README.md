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

## Outputs

- Daily page: `/docs/index.html`  
- Weekly page: `/docs/weekly.html`

## Notes

- To accommodate DST shifts, adjust cron or run hourly and gate inside Python.
- Mail uses Gmail SMTP via app password.
- Only `requests` is installed; everything else is stdlib.
