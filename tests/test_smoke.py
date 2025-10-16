import os
import subprocess
from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "generate_report.py"
DOCS = REPO_ROOT / "docs"


def run_cmd(args):
    completed = subprocess.run(args, capture_output=True, text=True, check=False)
    return completed.returncode, completed.stdout, completed.stderr


def test_daily_generates_index_html():
    code, out, err = run_cmd(["python3", str(SCRIPT), "daily"])  # uses stub if no OPENAI_API_KEY
    assert code == 0, f"Non-zero exit code: {code}\nstdout: {out}\nstderr: {err}"
    target = DOCS / "index.html"
    assert target.exists(), "docs/index.html was not created"
    content = target.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content and "<body>" in content

    # Ensure href attributes are quoted with straight quotes and contain valid schemes or anchors
    hrefs = re.findall(r"href=([\'\"])(.*?)(?:\1)", content)
    for _q, val in hrefs:
        assert not any(ch in val for ch in ["\u201C", "\u201D", "\u2018", "\u2019"])  # no smart quotes
        assert val.startswith(("http://", "https://", "mailto:", "tel:", "#"))


def test_weekly_generates_weekly_html():
    code, out, err = run_cmd(["python3", str(SCRIPT), "weekly"])  # uses stub if no OPENAI_API_KEY
    assert code == 0, f"Non-zero exit code: {code}\nstdout: {out}\nstderr: {err}"
    target = DOCS / "weekly.html"
    assert target.exists(), "docs/weekly.html was not created"
    content = target.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content and "<body>" in content

    hrefs = re.findall(r"href=([\'\"])(.*?)(?:\1)", content)
    for _q, val in hrefs:
        assert not any(ch in val for ch in ["\u201C", "\u201D", "\u2018", "\u2019"])  # no smart quotes
        assert val.startswith(("http://", "https://", "mailto:", "tel:", "#"))
