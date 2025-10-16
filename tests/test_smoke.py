import os
import subprocess
from pathlib import Path

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


def test_weekly_generates_weekly_html():
    code, out, err = run_cmd(["python3", str(SCRIPT), "weekly"])  # uses stub if no OPENAI_API_KEY
    assert code == 0, f"Non-zero exit code: {code}\nstdout: {out}\nstderr: {err}"
    target = DOCS / "weekly.html"
    assert target.exists(), "docs/weekly.html was not created"
    content = target.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content and "<body>" in content
