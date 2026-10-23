"""Browser smoke test for the dashboard in demo mode (no backend needed).

Serves dashboard/dist/dashboard/browser with SPA fallback, visits every route in headless Chromium and fails on:
console errors, uncaught page errors, failed requests, or a missing expected heading. Optionally saves screenshots.

    cd dashboard && npm run build:demo && cd .. && uv run python scripts/ui_smoke.py [--shots DIR]

Needs Playwright with a Chromium (preinstalled in the dev container; otherwise `playwright install chromium`).
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dashboard" / "dist" / "dashboard" / "browser"


class SpaHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.translate_path(self.path.split("?", 1)[0])
        if not Path(path).exists() or Path(path).is_dir():
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, *args: object) -> None:  # keep test output quiet
        pass


def serve() -> tuple[socketserver.TCPServer, int]:
    handler = functools.partial(SpaHandler, directory=str(DIST))
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", help="directory to save screenshots into")
    args = ap.parse_args()
    if not (DIST / "index.html").exists():
        print("build first: cd dashboard && npm run build:demo", file=sys.stderr)
        return 2

    demo = DIST / "demo"
    runs = json.loads((demo / "runs.json").read_text())
    evals = json.loads((demo / "eval-runs.json").read_text())
    by_label = {e["label"]: e["id"] for e in evals}
    longest = max(
        (json.loads(f.read_text()) for f in (demo / "runs").glob("*.json")),
        key=lambda r: len(r["spans"]),
    )
    failed_eval_run = next(r for r in runs if r["status"] == "failed")
    answered = max(
        (
            r
            for r in map(lambda f: json.loads(f.read_text()), (demo / "runs").glob("*.json"))
            if r["final_answer"]
            and r["final_answer"].get("value") is not None
            and r["source"] == "interactive"
        ),
        key=lambda r: len(r["spans"]),
    )
    routes = [
        ("/", "Overview"),
        ("/runs", "Runs"),
        (f"/runs/{longest['id']}", longest["question"]),
        (f"/runs/{failed_eval_run['id']}", failed_eval_run["question"]),
        (f"/runs/{answered['id']}", answered["question"]),
        ("/ask", "Ask"),
        ("/evals", "Eval runs"),
        (f"/evals/{by_label['baseline-large']}", "baseline-large"),
        (
            f"/evals/compare?base={by_label['baseline-large']}&candidate={by_label['small-model']}",
            "Compare",
        ),
        ("/configs", "Configs"),
    ]

    from playwright.sync_api import sync_playwright

    srv, port = serve()
    problems: list[str] = []
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        # CHROMIUM_PATH lets you point at an existing Chromium instead of downloading Playwright's own.
        browser = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        current = {"route": ""}
        page.on(
            "console",
            lambda m: (
                m.type == "error"
                and problems.append(f"{current['route']}: console error: {m.text}")
            ),
        )
        page.on("pageerror", lambda e: problems.append(f"{current['route']}: page error: {e}"))
        page.on(
            "requestfailed",
            lambda r: problems.append(f"{current['route']}: request failed: {r.url}"),
        )
        page.on(
            "response",
            lambda r: (
                r.status >= 400 and problems.append(f"{current['route']}: HTTP {r.status} {r.url}")
            ),
        )
        for i, (route, heading) in enumerate(routes):
            current["route"] = route
            page.goto(f"http://127.0.0.1:{port}{route}", wait_until="networkidle")
            page.wait_for_selector("h1", timeout=5000)
            text = page.inner_text("h1")
            ok = (
                heading.lower() in text.lower()
                or heading.lower() in page.inner_text("body").lower()
            )
            print(f"{'ok ' if ok else 'BAD'} {route[:70]:<70} h1={text[:50]!r}")
            if not ok:
                problems.append(f"{route}: expected {heading!r} in page")
            if route == f"/runs/{answered['id']}":
                # Interaction check: selecting a tool span opens the inspector with its input/output.
                page.get_by_role("listitem").filter(
                    has_text="tool · get_financial_facts"
                ).first.click()
                page.wait_for_selector("text=Span: tool · get_financial_facts", timeout=3000)
                if "Output" not in page.inner_text("body") or "Citations" not in page.inner_text(
                    "body"
                ):
                    problems.append(f"{route}: inspector/answer card missing Output or Citations")
            if shots:
                page.screenshot(path=str(shots / f"{i:02d}.png"), full_page=True)
        browser.close()
    srv.shutdown()
    for p in problems:
        print("PROBLEM:", p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
