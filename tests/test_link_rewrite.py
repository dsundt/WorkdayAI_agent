import importlib
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_module_with_preserve_disabled():
    module_name = "scripts.generate_report"
    # Ensure a clean import so module-level constants pick up our env override.
    if module_name in sys.modules:
        del sys.modules[module_name]

    original_argv = sys.argv[:]
    original_env = os.environ.get("PRESERVE_MODEL_HTML")

    try:
        sys.argv = ["generate_report.py", "daily"]
        os.environ["PRESERVE_MODEL_HTML"] = "0"
        module = importlib.import_module(module_name)
    finally:
        sys.argv = original_argv
        if original_env is None:
            os.environ.pop("PRESERVE_MODEL_HTML", None)
        else:
            os.environ["PRESERVE_MODEL_HTML"] = original_env

    return module


def test_rewrite_links_handles_whitespace_around_equals():
    module = _load_module_with_preserve_disabled()

    html = '<p><a href = "workday.com/resources">Resource</a></p>'
    rewritten = module._rewrite_links_in_html(html)

    assert 'href="https://workday.com/resources"' in rewritten
    assert 'target="_blank"' in rewritten

    # Clean up the imported module so other tests can import with defaults.
    sys.modules.pop("scripts.generate_report", None)
