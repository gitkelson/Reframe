"""Fixtures for the browser smoke tests: a seeded demo server on an ephemeral port plus an Edge page.

Never touches the real database or the user's Documents folder: --db and REFRAME_BACKUP_DIR point into tmp_path.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]


def _env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["REFRAME_BACKUP_DIR"] = str(tmp_path / "backups")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class Server:
    def __init__(self, tmp_path: Path, count: int = 300, flat: bool = True):
        self.db = tmp_path / "demo.sqlite"
        env = _env(tmp_path)
        subprocess.run([sys.executable, "-m", "reframe.seed", "--db", str(self.db), "--count", str(count),
                        *(["--flat"] if flat else [])],
                       cwd=ROOT, env=env, check=True, capture_output=True)
        self.proc = subprocess.Popen([sys.executable, "-m", "reframe", "--no-window", "--db", str(self.db)],
                                     cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.url = ""
        for line in self.proc.stdout:  # the server prints the URL (with the token) first
            if line.startswith("http://127.0.0.1"):
                self.url = line.strip()
                break
        # keep draining the pipe: a full pipe would block the server's request logging
        self._drain = threading.Thread(target=lambda: [None for _ in self.proc.stdout], daemon=True)
        self._drain.start()
        if not self.url:
            self.stop()
            raise RuntimeError("server did not print a URL")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


@pytest.fixture(scope="module")
def browser():
    # module scope: the Playwright sync context is released before another e2e module starts its own
    with playwright_sync.sync_playwright() as p:
        b = p.chromium.launch(channel="msedge")
        yield b
        b.close()


class Chrome:
    """A page with console-error and dialog tracking plus small helpers."""

    def __init__(self, browser, server: Server):
        self.server = server
        self.context = browser.new_context(viewport={"width": 1400, "height": 900})
        self.page = self.context.new_page()
        self.errors: list[str] = []
        self.dialogs: list[str] = []
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("dialog", self._dialog)
        self.open()

    def _dialog(self, dialog) -> None:
        self.dialogs.append(f"{dialog.type}: {dialog.message}")
        dialog.dismiss()

    def open(self) -> None:
        self.page.goto(self.server.url)
        try:
            self.page.wait_for_selector("#toolbar .tb-btn", timeout=8000)
            self.wait("() => document.querySelectorAll('#filterbar .filter-chip').length > 0")
        except Exception as e:
            raise RuntimeError(f"page did not finish loading; console errors: {self.errors}") from e

    def focus_timeline(self) -> None:
        self.page.evaluate("document.getElementById('timeline-host').focus()")

    def hover_tip(self, selector: str) -> str:
        self.page.hover(selector)
        self.page.wait_for_selector("#tooltip.is-visible", timeout=3000)
        text = self.page.inner_text("#tooltip")
        self.page.mouse.move(700, 450)
        return text

    def api(self, method: str, path: str, body=None):
        """Call the server API from the page (uses the page's token cookie)."""
        return self.page.evaluate(
            """async ([m, p, b]) => { const r = await fetch(p, {method: m, headers: {'Content-Type': 'application/json'},
                 body: b === null ? undefined : JSON.stringify(b)}); return r.json(); }""", [method, path, body])

    def wait(self, js_function: str, arg=None, timeout: float = 8.0) -> None:
        """Poll a JS function (string like '() => ...') until truthy. The page CSP forbids eval, which
        Playwright's wait_for_function uses for its polling, so this polls with page.evaluate instead."""
        import time
        end = time.time() + timeout
        while time.time() < end:
            if self.page.evaluate(js_function, arg):
                return
            self.page.wait_for_timeout(60)
        raise TimeoutError(f"condition never became true: {js_function}")

    def close(self) -> None:
        self.context.close()


@pytest.fixture
def chrome(browser, server):
    c = Chrome(browser, server)
    yield c
    c.close()


@pytest.fixture
def chrome_factory(browser, tmp_path):
    """Open an extra page on a separately seeded server: factory(count) -> Chrome."""
    made: list[tuple[Chrome, Server]] = []

    def make(count: int, flat: bool = True) -> Chrome:
        srv = Server(tmp_path / f"srv{len(made)}", count=count, flat=flat)
        made.append((Chrome(browser, srv), srv))
        return made[-1][0]

    yield make
    for c, srv in made:
        c.close()
        srv.stop()
