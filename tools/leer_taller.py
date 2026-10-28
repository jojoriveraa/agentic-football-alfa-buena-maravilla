"""Read a workshop page with the AgentCore Browser.

The workshop catalog is a single-page app: plain HTTP fetches only get an
empty shell. This opens the page in the cloud browser created by
crear_componentes.py and prints the rendered text, so rule changes can be
checked from the terminal.

Usage (repo root, workshop credentials active):

    python3 tools/leer_taller.py                       # workshop home
    python3 tools/leer_taller.py 7-play-matches        # a page under /en-US/
    python3 tools/leer_taller.py https://...           # any URL
"""

import json
import os
import sys

from bedrock_agentcore.tools.browser_client import BrowserClient
from playwright.sync_api import sync_playwright

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RESOURCES = os.path.join(ROOT, "lib", "agentcore_resources.json")
BASE = "https://catalog.workshops.aws/agentic-football/en-US"


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    url = arg if arg.startswith("http") else f"{BASE}/{arg}".rstrip("/")
    browser_id = json.load(open(RESOURCES)).get("browser_id")
    if not browser_id:
        raise SystemExit("Corre primero tools/crear_componentes.py (falta browser_id).")

    client = BrowserClient(os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    client.start(identifier=browser_id)
    try:
        ws_url, headers = client.generate_ws_headers()
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(ws_url, headers=headers)
            page = browser.contexts[0].pages[0] if browser.contexts and browser.contexts[0].pages \
                else browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=60000)
            # The app first shows a loading line while it fetches the page.
            page.wait_for_function(
                "!document.body.innerText.includes('Getting content manifest')", timeout=30000)
            page.wait_for_load_state("networkidle")
            main_el = page.query_selector("main") or page.query_selector("body")
            print(f"# {page.title()}\n{url}\n")
            print(main_el.inner_text())
            browser.close()
    finally:
        client.stop()


if __name__ == "__main__":
    main()
