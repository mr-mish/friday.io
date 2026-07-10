"""End-to-end browser test of the chat panel against a LIVE agent.

Skipped by default: it needs the Claude CLI, network, and a Chromium build,
so it cannot run in CI. Run it by hand on a machine that can reach the API:

    FRIDAY_E2E=1 uv run friday --config your.toml --serve &   # in one shell
    FRIDAY_E2E=1 FRIDAY_E2E_URL=http://127.0.0.1:4527 \\
        uv run pytest tests/test_panel_e2e.py -s

It drives a real chat turn, triggers the permission dialog with a shell
command, denies it, and checks FRIDAY reports the decline.
"""

import os

import pytest

if not os.environ.get("FRIDAY_E2E"):
    pytest.skip("live browser E2E; set FRIDAY_E2E=1 to run", allow_module_level=True)

pytest.importorskip("playwright")

from playwright.sync_api import sync_playwright  # noqa: E402

URL = os.environ.get("FRIDAY_E2E_URL", "http://127.0.0.1:4527")
CHROMIUM = os.environ.get("FRIDAY_E2E_CHROMIUM", "/opt/pw-browsers/chromium")


def test_panel_chat_and_permission_dialog():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROMIUM)
        page = browser.new_page()
        try:
            page.goto(URL)
            page.wait_for_selector("#pulse:not(.offline)", timeout=15000)

            page.fill("#input", "Reply with exactly: hello from the test")
            page.press("#input", "Enter")
            page.wait_for_function("document.querySelectorAll('.cost').length >= 1", timeout=120000)
            assert "hello from the test" in page.locator(".msg.friday").last.text_content()

            page.fill("#input", "Run the shell command: echo hi")
            page.press("#input", "Enter")
            page.wait_for_selector("dialog[open]", timeout=120000)
            assert page.text_content("#confTool") == "Bash"
            page.click("#deny")
            page.wait_for_function("document.querySelectorAll('.cost').length >= 2", timeout=120000)
            assert "declined" in page.locator(".msg.friday").last.text_content().lower()
        finally:
            browser.close()
