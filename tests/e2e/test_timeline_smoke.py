"""Playwright smoke test for the timeline canvas, minimap and accessibility mirror (spec 11, UI smoke).

Starts the real server on a seeded temporary database (never the user's database or backup folder) and drives
it in Microsoft Edge. Run from the project root:  python -m pytest tests/e2e/test_timeline_smoke.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync.sync_playwright

ROOT = Path(__file__).resolve().parents[2]
RULER_H = 44
ROW_H = 26


def _env(tmp: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["REFRAME_BACKUP_DIR"] = str(tmp / "backups")
    return env


def _wait(fn, timeout=8.0, what="condition"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what} (last={last!r})")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("reframe_e2e")
    db = tmp / "demo.sqlite"
    env = _env(tmp)
    subprocess.run([sys.executable, "-m", "reframe.seed", "--db", str(db), "--count", "300", "--recurring", "1"],
                   cwd=ROOT, env=env, check=True, capture_output=True)
    proc = subprocess.Popen([sys.executable, "-m", "reframe", "--no-window", "--db", str(db)],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    lines: list[str] = []

    def drain():                      # keep reading so the server can never block on a full pipe
        for line in proc.stdout:
            lines.append(line)

    threading.Thread(target=drain, daemon=True).start()
    url = _wait(lambda: next((ln.strip() for ln in lines if ln.startswith("http")), None), 20, "server URL")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def page(server):
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        pg = ctx.new_page()
        pg.on("console", lambda m: problems.append(f"console {m.type}: {m.text} {m.location.get('url', '')}")
              if m.type == "error" and not m.location.get("url", "").endswith("/favicon.ico") else None)
        pg.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        pg.add_init_script(
            "window.__dialogCalls = [];"
            "for (const n of ['alert','confirm','prompt']) { const o = window[n]; window[n] = (...a) => { window.__dialogCalls.push(n); return null; }; }"
        )
        pg.goto(server)
        pg.wait_for_selector("#timeline-host canvas")
        _wait(lambda: pg.evaluate("window.__reframe && window.__reframe.rowCount() > 0"), 15, "bars laid out")
        # these tests exercise the flat packing; group by tag (the default) is covered by its own tests
        pg.evaluate("async () => { const s = await import('/static/js/store.js'); s.setGroupByTag(false); }")
        pg.wait_for_timeout(300)
        pg.problems = problems
        yield pg
        browser.close()


def store(page, expr: str):
    """Evaluate `expr` with the app's store module bound to `s`."""
    return page.evaluate(f"async () => {{ const s = await import('/static/js/store.js'); return ({expr}); }}")


def api_items(page):
    return page.evaluate("async () => (await (await fetch('/api/items')).json()).items")


def canvas_box(page, selector):
    return page.locator(selector).bounding_box()


def view(page):
    return store(page, "({start: s.state.view.start, end: s.state.view.end})")


def test_bars_render(page):
    stats = page.evaluate("""() => {
        const c = document.querySelector('#timeline-host canvas');
        const dpr = window.devicePixelRatio || 1;
        const px = c.getContext('2d').getImageData(0, Math.round(44 * dpr), c.width, c.height - Math.round(44 * dpr)).data;
        const seen = new Map();
        for (let i = 0; i < px.length; i += 4) {
            const k = (px[i] << 16) | (px[i + 1] << 8) | px[i + 2];
            seen.set(k, (seen.get(k) || 0) + 1);
        }
        return { distinct: seen.size, rows: window.__reframe.rowCount() };
    }""")
    assert stats["rows"] > 1
    assert stats["distinct"] > 4, "expected background, weekend shade, bars and text"
    ruler = page.evaluate("""() => {
        const c = document.querySelector('#timeline-host canvas');
        const d = c.getContext('2d').getImageData(0, 0, c.width, Math.round(20 * (window.devicePixelRatio || 1))).data;
        return d[0] + d[1] + d[2];
    }""")
    assert ruler < 300, "ruler should be the dark (navy) field"


def test_minimap_frame_is_bound_to_the_timeline(page):
    box = canvas_box(page, "#minimap-host canvas")
    before = view(page)
    shot_before = page.locator("#timeline-host canvas").screenshot()
    y = box["y"] + box["height"] / 2
    page.mouse.click(box["x"] + box["width"] * 0.8, y)        # click outside the frame re-centres it
    after_click = view(page)
    assert after_click["start"] > before["start"]
    assert abs((after_click["end"] - after_click["start"]) - (before["end"] - before["start"])) < 1e-6
    cx = box["x"] + box["width"] * 0.8
    page.mouse.move(cx, y)
    page.mouse.down()
    page.mouse.move(cx - 120, y, steps=6)
    page.mouse.up()
    dragged = view(page)
    assert dragged["start"] < after_click["start"]
    assert page.locator("#timeline-host canvas").screenshot() != shot_before
    span0 = dragged["end"] - dragged["start"]
    assert store(page, "s.state.view.end - s.state.view.start") == pytest.approx(span0)   # pan keeps the span


def test_minimap_wheel_zooms_frame(page):
    box = canvas_box(page, "#minimap-host canvas")
    v0 = view(page)
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, -300)
    v1 = _wait(lambda: (lambda v: v if v["end"] - v["start"] < v0["end"] - v0["start"] else None)(view(page)), 3, "zoom in")
    assert v1["end"] - v1["start"] >= 7


def go_to_today(page):
    store(page, "s.setView(s.state.today - 10, s.state.today + 32, 'test')")
    time.sleep(0.2)


def test_wheel_zoom_pan_and_scroll(page):
    go_to_today(page)
    tbox = canvas_box(page, "#timeline-host canvas")
    cx, cy = tbox["x"] + tbox["width"] / 2, tbox["y"] + 200
    v0 = view(page)
    page.mouse.move(cx, cy)
    page.keyboard.down("Control")
    page.mouse.wheel(0, -200)
    page.keyboard.up("Control")
    v1 = _wait(lambda: (lambda v: v if (v["end"] - v["start"]) < (v0["end"] - v0["start"]) - 1 else None)(view(page)), 3, "ctrl+wheel zoom")
    assert 7 <= v1["end"] - v1["start"]
    day_under = v0["start"] + (cx - tbox["x"]) / tbox["width"] * (v0["end"] - v0["start"])
    day_after = v1["start"] + (cx - tbox["x"]) / tbox["width"] * (v1["end"] - v1["start"])
    assert abs(day_under - day_after) < 0.01, "zoom is anchored on the cursor"
    page.mouse.move(cx, tbox["y"] + 20)                         # wheel over the ruler zooms
    page.mouse.wheel(0, 200)
    v2 = _wait(lambda: (lambda v: v if (v["end"] - v["start"]) > (v1["end"] - v1["start"]) + 0.5 else None)(view(page)), 3, "ruler wheel zoom out")
    page.mouse.move(cx, cy)
    page.keyboard.down("Shift")
    page.mouse.wheel(0, 200)
    page.keyboard.up("Shift")
    v3 = _wait(lambda: (lambda v: v if v["start"] > v2["start"] + 0.1 else None)(view(page)), 3, "shift+wheel pan")
    assert abs((v3["end"] - v3["start"]) - (v2["end"] - v2["start"])) < 1e-6
    page.mouse.wheel(0, 100)                                   # plain wheel scrolls rows, window unchanged
    time.sleep(0.2)
    v4 = view(page)
    assert v4["start"] == pytest.approx(v3["start"]) and v4["end"] == pytest.approx(v3["end"])


def test_middle_drag_pans(page):
    go_to_today(page)
    tbox = canvas_box(page, "#timeline-host canvas")
    v0 = view(page)
    x, y = tbox["x"] + 400, tbox["y"] + 300
    page.mouse.move(x, y)
    page.mouse.down(button="middle")
    page.mouse.move(x + 150, y, steps=5)
    page.mouse.up(button="middle")
    v1 = view(page)
    assert v1["start"] < v0["start"]


def _bar_for(page, title):
    items = [i for i in api_items(page) if i["title"] == title]
    return items[0] if items else None


def test_create_rename_drag_resize_persist(page):
    go_to_today(page)
    tbox = canvas_box(page, "#timeline-host canvas")
    n0 = len(api_items(page))
    ppd = page.evaluate("window.__reframe.ppd()")
    rows = page.evaluate("window.__reframe.rowCount()")
    y = tbox["y"] + RULER_H + (rows + 1) * ROW_H - page.evaluate("window.__reframe.scrollY()") + ROW_H / 2
    assert y < tbox["y"] + tbox["height"] - 10, "need empty space below the last row"
    x0 = tbox["x"] + 300
    page.mouse.move(x0, y)
    page.mouse.down()
    page.mouse.move(x0 + 2.5 * ppd, y, steps=6)
    page.mouse.up()
    _wait(lambda: len(api_items(page)) == n0 + 1, 8, "new item persisted")
    inp = page.locator(".rf-title-input")
    _wait(lambda: inp.is_visible(), 5, "inline rename input")
    inp.fill("Gantry hoist install")
    inp.press("Enter")
    item = _wait(lambda: _bar_for(page, "Gantry hoist install"), 8, "renamed item persisted")
    assert item["start_date"] <= item["end_date"]
    span_days = (_days(item["end_date"]) - _days(item["start_date"])) + 1
    assert span_days >= 2

    r = page.evaluate(f"window.__reframe.barRect('{item['id']}')")
    page.mouse.move(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
    page.mouse.down()
    page.mouse.move(r["x"] + r["w"] / 2 + 2 * ppd, r["y"] + r["h"] / 2, steps=6)
    page.mouse.up()
    moved = _wait(lambda: (lambda i: i if i and i["start_date"] != item["start_date"] else None)(_bar_for(page, "Gantry hoist install")), 8, "move persisted")
    assert _days(moved["start_date"]) - _days(item["start_date"]) == 2
    assert _days(moved["end_date"]) - _days(item["end_date"]) == 2

    r = page.evaluate(f"window.__reframe.barRect('{item['id']}')")
    ex, ey = r["x"] + r["w"] - 2, r["y"] + r["h"] / 2
    page.mouse.move(ex, ey)
    assert page.locator("#timeline-host canvas").evaluate("c => c.style.cursor") == "ew-resize"
    page.mouse.down()
    page.mouse.move(ex + 3 * ppd, ey, steps=6)
    page.mouse.up()
    resized = _wait(lambda: (lambda i: i if i and i["end_date"] != moved["end_date"] else None)(_bar_for(page, "Gantry hoist install")), 8, "resize persisted")
    assert _days(resized["end_date"]) - _days(moved["end_date"]) == 3
    assert resized["start_date"] == moved["start_date"]


def _days(iso):
    import datetime as dt
    return dt.date.fromisoformat(iso).toordinal()


def test_escape_cancels_drag(page):
    item = _bar_for(page, "Gantry hoist install")
    r = page.evaluate(f"window.__reframe.barRect('{item['id']}')")
    ppd = page.evaluate("window.__reframe.ppd()")
    page.mouse.move(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
    page.mouse.down()
    page.mouse.move(r["x"] + r["w"] / 2 + 4 * ppd, r["y"] + r["h"] / 2, steps=5)
    page.keyboard.press("Escape")
    page.mouse.up()
    time.sleep(0.5)
    assert _bar_for(page, "Gantry hoist install")["start_date"] == item["start_date"]


def test_double_click_renames_and_escape_cancels(page):
    item = _bar_for(page, "Gantry hoist install")
    r = page.evaluate(f"window.__reframe.barRect('{item['id']}')")
    page.mouse.dblclick(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
    inp = page.locator(".rf-title-input")
    _wait(lambda: inp.is_visible(), 3, "rename input")
    inp.fill("Should not stick")
    inp.press("Escape")
    time.sleep(0.4)
    assert not inp.is_visible()
    assert _bar_for(page, "Gantry hoist install") is not None


def test_click_selects_and_ctrl_click_toggles(page):
    item = _bar_for(page, "Gantry hoist install")
    r = page.evaluate(f"window.__reframe.barRect('{item['id']}')")
    page.mouse.click(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
    assert store(page, "[...s.state.selection]") == [item["id"]]
    page.keyboard.down("Control")
    page.mouse.click(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
    page.keyboard.up("Control")
    assert store(page, "s.state.selection.size") == 0


def test_shift_drag_marquee_selects_several(page):
    go_to_today(page)
    store(page, "s.setSelection([])")
    tbox = canvas_box(page, "#timeline-host canvas")
    rows = page.evaluate("window.__reframe.rowCount()")
    y0 = tbox["y"] + RULER_H + (rows + 1) * ROW_H - page.evaluate("window.__reframe.scrollY()") + ROW_H / 2
    page.mouse.move(tbox["x"] + 20, y0)
    page.keyboard.down("Shift")
    page.mouse.down()
    page.mouse.move(tbox["x"] + 700, tbox["y"] + RULER_H + 6, steps=8)
    page.mouse.up()
    page.keyboard.up("Shift")
    assert store(page, "s.state.selection.size") > 1
    store(page, "s.setSelection([])")


def test_hide_past_is_removed_and_ctrl_h_does_nothing(page):
    """R-05 (removed): no gutter, no button, no setting; Ctrl+H is captured but does nothing."""
    assert page.evaluate("window.__reframe.plotLeft()") == 0
    assert page.locator("#tb-hidePast").count() == 0
    assert store(page, "'hidePast' in s.state") is False and store(page, "typeof s.setHidePast") == "undefined"
    before = view(page)
    page.locator("#timeline-host canvas").focus()
    page.keyboard.press("Control+h")
    time.sleep(0.2)
    assert view(page) == before and page.url.endswith("/")
    settings = page.evaluate("async () => (await (await fetch('/api/settings')).json())")
    assert "hide_past" not in settings


def test_strict_layout_gives_one_row_per_item_and_minimap_mirrors_it(page):
    """R-17, R-21: strict mode, later start lower; the minimap draws the same rows (shared packing result)."""
    store(page, "s.setLayoutMode('strict')")
    time.sleep(0.3)
    lay = page.evaluate("window.__reframe.layout()")
    assert lay["mode"] == "strict"
    rows = sorted(r for _, r in lay["rows"])
    assert rows == list(range(len(rows)))
    items = {i["id"]: i for i in api_items(page)}
    by_row = sorted(lay["rows"], key=lambda p: p[1])
    starts = [items[i]["start_date"] for i, _ in by_row]
    assert starts == sorted(starts)
    mm = page.evaluate("async () => { const b = await import('/static/js/layout_bus.js'); const l = b.currentLayout();"
                       " return [...l.entries.values()].map((e) => [e.id, e.row]); }")
    assert sorted(map(tuple, mm)) == sorted(map(tuple, lay["rows"]))
    page.keyboard.press("Control+Shift+L")
    time.sleep(0.3)
    assert page.evaluate("window.__reframe.layout()")["mode"] == "compact"


def test_ruler_click_opens_day_panel_and_group_by_tag(page):
    go_to_today(page)
    tbox = canvas_box(page, "#timeline-host canvas")
    page.mouse.click(tbox["x"] + 500, tbox["y"] + 30)
    assert store(page, "s.state.panel.tab") == "day"
    assert store(page, "!!s.state.panel.dayISO")
    store(page, "s.setGroupByTag(true)")
    time.sleep(0.3)
    assert page.evaluate("window.__reframe.rowCount()") > 1
    store(page, "s.setGroupByTag(false)")


def _frame_bounds(page):
    """Left/right x (client px) of the minimap frame, found by scanning for the frame colour on its top edge."""
    return page.evaluate("""() => {
        const c = document.querySelector('#minimap-host canvas');
        const dpr = window.devicePixelRatio || 1;
        const want = getComputedStyle(document.documentElement).getPropertyValue('--minimap-frame').trim().replace('#', '');
        const rgb = [0, 2, 4].map((i) => parseInt(want.slice(i, i + 2), 16));
        const row = c.getContext('2d').getImageData(0, Math.round(2 * dpr), c.width, 1).data;
        let lo = -1, hi = -1;
        for (let x = 0; x < c.width; x++) {
            if (row[x * 4] === rgb[0] && row[x * 4 + 1] === rgb[1] && row[x * 4 + 2] === rgb[2]) { if (lo < 0) lo = x; hi = x; }
        }
        const r = c.getBoundingClientRect();
        return { left: r.left + lo / dpr, right: r.left + (hi + 1) / dpr, top: r.top, height: r.height };
    }""")


def test_minimap_edge_drag_zooms(page):
    go_to_today(page)
    f = _frame_bounds(page)
    assert f["right"] - f["left"] >= 12, "frame keeps a usable minimum width"
    v0 = view(page)
    y = f["top"] + f["height"] / 2
    page.mouse.move(f["right"] - 2, y)
    page.mouse.down()
    page.mouse.move(f["right"] + 60, y, steps=6)
    page.mouse.up()
    v1 = view(page)
    assert v1["start"] == pytest.approx(v0["start"])
    assert v1["end"] > v0["end"] + 1, "dragging the right edge zooms the window out"


def test_bar_tooltip_after_delay(page):
    go_to_today(page)
    item = _bar_for(page, "Gantry hoist install")
    store(page, "s.setView(s.state.today - 10, s.state.today + 32, 'test')")
    r = page.evaluate(f"window.__reframe.barRect('{item['id']}')")
    assert r, "created item should be in the packed layout"
    page.mouse.move(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
    tip = page.locator("#timeline-host .rf-tooltip")
    time.sleep(0.2)
    assert not tip.is_visible(), "tooltip must wait about 400 ms"
    _wait(lambda: tip.is_visible(), 2, "tooltip")
    assert "Gantry hoist install" in tip.inner_text()
    assert "day" in tip.inner_text()


def test_imported_items_are_locked(page):
    items = [i for i in api_items(page) if i["origin"] == "outlook"]
    assert items
    it = items[3]
    store(page, f"s.setView({_days(it['start_date']) - _EPOCH} - 10, {_days(it['start_date']) - _EPOCH} + 32, 'test')")
    time.sleep(0.3)
    r = page.evaluate(f"window.__reframe.barRect('{it['id']}')")
    assert r
    page.mouse.click(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
    store(page, "s.dismissNotice()")
    page.mouse.move(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
    page.mouse.down()
    page.mouse.move(r["x"] + r["w"] / 2 + 60, r["y"] + r["h"] / 2, steps=5)
    page.mouse.up()
    notice = _wait(lambda: store(page, "s.state.notice && s.state.notice.text"), 3, "locked notice")
    assert notice == "Imported from Outlook. Change it there, then Sync."
    time.sleep(0.4)
    after = next(i for i in api_items(page) if i["id"] == it["id"])
    assert after["start_date"] == it["start_date"]
    store(page, "s.dismissNotice()")
    page.keyboard.press("F2")
    _wait(lambda: store(page, "!!s.state.notice"), 3, "locked rename notice")
    assert not page.locator(".rf-title-input").is_visible()


_EPOCH = 719163     # date.toordinal() of 1970-01-01


def test_accessibility_mirror(page):
    _wait(lambda: page.locator("#a11y [role=option]").count() > 0, 5, "option list")
    _wait(lambda: "items in view" in page.locator("#a11y [role=status]").inner_text(), 5, "live summary")
    page.locator("#a11y [role=option]").first.dispatch_event("click")
    assert store(page, "s.state.selection.size") == 1
    _wait(lambda: "selected" in page.locator("#a11y [role=status]").inner_text(), 5, "selected item announced")


def test_no_console_errors_and_no_dialogs(page):
    assert page.evaluate("window.__dialogCalls") == []
    assert page.problems == []
