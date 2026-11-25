"""Browser smoke test for the HTML report: loads it in headless Chromium and
checks it renders with no console errors/warnings and no network requests.

Skipped unless Playwright is installed (CI's `report-smoke` job installs it):
    pip install playwright && python -m playwright install chromium
"""

from __future__ import annotations

from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from legacy2mcp.analysis.pipeline import run_analysis  # noqa: E402
from legacy2mcp.analysis.report import write_report  # noqa: E402

FIXTURES = Path(__file__).parent.parent / "fixtures" / "analyze"


@pytest.mark.parametrize("project", sorted(p.name for p in FIXTURES.iterdir() if p.is_dir()))
def test_report_loads_cleanly(project, tmp_path):
    path = write_report(run_analysis(FIXTURES / project), tmp_path / "report.html")
    messages, requests = [], []
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:  # no bundled Chromium downloaded: use an installed Chrome
            browser = p.chromium.launch(channel="chrome")
        page = browser.new_page()
        page.on("console", lambda m: messages.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: messages.append(f"pageerror: {e}"))
        page.on("request", lambda r: requests.append(r.url))
        page.goto(path.as_uri())
        page.wait_for_function("window.__legacy2mcpReady === true", timeout=10000)
        page.click("#theme-toggle")
        page.fill("#search", "order")
        page.wait_for_timeout(300)
        visible = page.locator("#flows .flow:not(.hidden)")
        if visible.count():
            visible.first.locator("summary").click()
            visible.first.locator("button.btn").click()
        page.wait_for_timeout(300)
        browser.close()
    assert [m for m in messages if m.startswith(("error", "warning", "pageerror"))] == []
    assert [u for u in requests if not u.startswith(("file:", "data:", "blob:"))] == []
