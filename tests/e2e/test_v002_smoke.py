"""Browser smoke tests for the tag tree, calendars, themes, tray menu, path bar and timeline file browser."""
from __future__ import annotations

import sqlite3
import time

import pytest
from PIL import Image

pytest.importorskip("playwright.sync_api")


@pytest.fixture(autouse=True)
def _clean_console(request):
    yield
    chrome = request.node.funcargs.get("chrome")
    if chrome is not None:
        assert chrome.errors == [], chrome.errors
        assert chrome.dialogs == [], chrome.dialogs


def store(page, expr):
    return page.evaluate(f"async () => {{ const s = await import('/static/js/store.js'); return ({expr}); }}")


def count_text(chrome):
    return chrome.page.inner_text(".status__count")


def add_imported(chrome, title="Board review", day="2026-11-18"):
    con = sqlite3.connect(chrome.server.db)
    con.execute("BEGIN IMMEDIATE")
    iid = con.execute(
        "INSERT INTO item (kind,title,start_date,end_date,origin,source_id,outlook_uid,outlook_calendar) "
        "VALUES ('plan',?,?,?,'outlook','seed-outlook',?, 'seed') RETURNING id", (title, day, day, f"seed|{title}")).fetchone()[0]
    con.commit()
    con.close()
    chrome.open()
    return iid


def test_keyboard_file_browser_keeps_gantt_visible_and_restores_focus(chrome):
    page = chrome.page
    page.evaluate("""() => {
        const fetchNow = window.fetch;
        window.fetch = (...args) => String(args[0]).includes('/api/files/entries?')
            ? new Promise((resolve, reject) => setTimeout(() => fetchNow(...args).then(resolve, reject), 500))
            : fetchNow(...args);
    }""")
    store(page, "s.setView(s.state.today - 3, s.state.today + 11, 'test')")
    before = store(page, "[s.state.view.start, s.state.view.end]")
    chrome.focus_timeline()
    page.keyboard.press("Control+2")
    chrome.wait("() => !document.querySelector('.rf-files-overlay').hidden")
    assert page.is_visible("#timeline-host") and page.is_visible("#filterbar")
    assert page.locator("#tb-viewSwitch, #files-host").count() == 0
    assert page.locator("#tb-layoutMode").is_visible()
    page.wait_for_timeout(800)
    assert page.locator(".ft-browser__list").evaluate("(e) => document.activeElement === e")
    page.keyboard.press("Escape")
    assert page.locator(".rf-files-overlay").is_hidden()
    assert page.locator("#timeline-host").evaluate("(e) => document.activeElement === e")
    assert store(page, "[s.state.view.start, s.state.view.end]") == before


def test_all_none_and_untagged_chips(chrome):
    page = chrome.page
    page.click(".filter-bar__all")
    chips = page.locator("#filterbar .filter-chip[aria-pressed=true]").count()
    assert chips == page.locator("#filterbar .filter-chip").count()      # every chip including Untagged
    assert count_text(chrome).startswith("300 / 300")
    page.click("#filterbar .filter-chip:has-text('Spares')")            # leave one out
    shown = int(count_text(chrome).split("/")[0])
    assert shown < 300
    page.click(".filter-bar__none-btn")
    assert page.locator("#filterbar .filter-chip[aria-pressed=true]").count() == 0
    page.click("#filterbar .seg__btn:has-text('AND')")
    assert page.get_attribute(".filter-chip--untagged", "aria-disabled") == "true"
    page.click("#filterbar .seg__btn:has-text('OR')")
    page.click(".filter-chip--untagged")
    untagged = [i for i in chrome.api("GET", "/api/items")["items"] if not i["tags"]]
    assert count_text(chrome).startswith(f"{len(untagged)} / 300")


def test_dragging_a_filter_chip_onto_a_bar_adds_the_tag_once_and_undoes(chrome):
    page = chrome.page
    items = chrome.api("GET", "/api/items")["items"]
    tags = chrome.api("GET", "/api/tags")["tags"]
    audit = next(t for t in tags if t["name"] == "Audit")
    target = next(i for i in items if audit["id"] not in i["tags"] and i["start_date"] == i["end_date"] and i["tags"])
    page.evaluate("""async (iso) => { const s = await import('/static/js/store.js'); s.setGroupByTag(false);
        const n = s.toDayNumber(iso); s.setView(n - 7, n + 7, 't'); }""", target["start_date"])
    page.evaluate("async (id) => { const c = await import('/static/js/commands.js'); c.timelineHook('reveal')?.(id); }", target["id"])
    page.wait_for_timeout(300)
    rect = page.evaluate("(id) => window.__reframe.barRect(id)", target["id"])
    assert rect is not None
    chip = page.locator("#filterbar .filter-chip", has_text="Audit").first.bounding_box()
    page.mouse.move(chip["x"] + 10, chip["y"] + chip["height"] / 2)
    page.mouse.down()
    page.mouse.move(chip["x"] + 40, chip["y"] + 40, steps=5)
    page.mouse.move(rect["x"] + min(10, rect["w"] / 2), rect["y"] + rect["h"] / 2, steps=10)
    page.mouse.up()
    chrome.wait("() => document.querySelector('.status__notice')?.textContent.includes(\"Added tag 'Audit'\")")
    after = next(i for i in chrome.api("GET", "/api/items")["items"] if i["id"] == target["id"])
    assert after["tags"][0] == target["tags"][0] and audit["id"] in after["tags"]   # primary tag unchanged
    assert page.get_attribute("#filterbar .filter-chip:has-text('Audit')", "aria-pressed") == "false"   # a drag is not a click
    chrome.api("POST", "/api/undo", {})
    undone = next(i for i in chrome.api("GET", "/api/items")["items"] if i["id"] == target["id"])
    assert audit["id"] not in undone["tags"]


def test_tag_tree_groups_retired_section_and_move(chrome_factory):
    chrome = chrome_factory(120, flat=False)
    page = chrome.page
    page.click("#tab-tags")
    page.wait_for_selector(".tag-tree .tag-row")
    projects = page.locator(".tag-row[aria-level='1']", has_text="Projects").first
    assert projects.get_attribute("aria-expanded") == "true"
    assert page.locator(".tag-row[aria-level='2']").count() == 4
    assert "Audit" not in page.inner_text(".tag-tree:not(.tag-tree--retired)")   # retired tags only in their section
    page.click("button:has-text('Show 1 retired tag')")
    assert "Audit" in page.inner_text(".tag-tree--retired")
    assert page.locator("#filterbar .filter-chip", has_text="Audit").count() == 0     # no chip for a retired tag
    # keyboard path: Move to... the top level
    row = page.locator(".tag-row[aria-level='2']").first
    name = row.locator(".tag-row__name").inner_text()
    row.get_by_role("button", name=f"Move {name} to…").click()
    page.click(".tag-move-opt:has-text('(top level: no group)')")
    chrome.wait(f"() => [...document.querySelectorAll(\".tag-row[aria-level='1'] .tag-row__name\")].some(e => e.textContent === {name!r})")
    tags = {t["name"]: t for t in chrome.api("GET", "/api/tags")["tags"]}
    assert tags[name]["parent_id"] is None
    assert chrome.api("POST", "/api/undo", {})["undone_label"].startswith(f"Move tag '{name}'")


def test_path_bar_path_mode_and_search_mode_with_fake_backend(chrome):
    page = chrome.page
    page.route("**/api/search/files*", lambda route: route.fulfill(json={
        "request_id": route.request.url.split("rid=")[-1].split("&")[0], "state": "ok", "total": 1, "offset": 0, "limit": 100,
        "results": [{"name": "spec <b>.pdf", "folder": "C:\\Docs", "path": "C:\\Docs\\spec <b>.pdf", "size": 2048,
                     "modified": "2026-11-20T10:00:00"}]}))
    item = next(i for i in chrome.api("GET", "/api/items")["items"] if i["origin"] == "local")
    page.evaluate("""async (id) => { const s = await import('/static/js/store.js'); s.setSelection([id], id);
        s.setPanel({open: true, tab: 'item'}); }""", item["id"])
    bar = page.locator(".field-link")
    bar.fill("spec")
    page.wait_for_selector(".path-bar__list [role=option]")
    assert "spec <b>.pdf" in page.inner_text(".path-bar__list")            # rendered as text, never HTML
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    assert bar.input_value() == "C:\\Docs\\spec <b>.pdf"                  # path mode now; not yet added
    links = chrome.api("GET", f"/api/items/{item['id']}")["attachments"]
    bar.fill("https://example.com/report")
    page.keyboard.press("Enter")
    chrome.wait(f"async () => (await (await fetch('/api/items/{item['id']}')).json()).attachments.length > {len(links)}")


def test_calendars_control_lists_sources_and_toggles_visibility(chrome):
    page = chrome.page
    page.click(".filter-bar__calendars")
    page.wait_for_selector(".menu .menu__item:has-text('Calendar')")
    item = page.locator(".menu__item[role=menuitemcheckbox]", has_text="Calendar").first
    checked = item.get_attribute("aria-checked")
    item.click()                                                          # the checklist stays open
    assert page.locator(".menu").count() == 1
    chrome.wait(f"async () => {{ const s = await import('/static/js/store.js'); return s.state.calendars['seed-outlook'].visible === {'false' if checked == 'true' else 'true'}; }}")
    page.keyboard.press("Escape")


def test_file_browser_shows_files_and_filters(chrome, tmp_path):
    page = chrome.page
    root = tmp_path / "Projects"
    (root / "Job A").mkdir(parents=True)
    for i in range(5):
        (root / "Job A" / f"note{i}.txt").write_text("x", encoding="utf-8")
    (root / "Job A" / "photo.jpg").write_bytes(b"not an image")
    for name in ("report.pdf", "video.mp4", "sound.mp3", "archive.zip"):
        (root / "Job A" / name).write_bytes(b"test")
    r = chrome.api("PUT", "/api/files/options", {"roots": [str(root)], "excluded_ext": [], "include_hidden": False})
    assert "problems" not in r or not r["problems"], r
    chrome.wait("async () => (await (await fetch('/api/files/status')).json()).state !== 'scanning'", timeout=15)
    entries = chrome.api("GET", "/api/files/entries?mode=created")
    assert len(entries.get("rows") or entries.get("entries") or []) == 10
    day = entries["rows"][0][entries["columns"].index("day")]
    page.evaluate("""async (day) => { const s = await import('/static/js/store.js');
        s.setView(day - 3, day + 4, 'test'); }""", day)
    chrome.focus_timeline()
    page.keyboard.press("Control+2")
    chrome.wait("() => !document.querySelector('.rf-files-overlay').hidden")
    chrome.wait("() => document.querySelector('.ft-panel__body .field__hint')?.textContent.includes('10 files')")
    assert page.locator(".ft-filter__tags").count() == 0
    types = page.get_by_role("group", name="File type")
    types.get_by_role("button", name="Media").click()
    assert types.get_by_role("button", name="Archive").count() == 0
    chrome.wait("() => document.querySelector('.ft-filter__badge')?.textContent === '3 match in this period'")
    types.get_by_role("button", name="Documents").click()
    chrome.wait("() => document.querySelector('.ft-filter__badge')?.textContent === '9 match in this period'")
    types.get_by_role("button", name="Media").click()
    types.get_by_role("button", name="PDF").click()
    chrome.wait("() => document.querySelector('.ft-filter__badge')?.textContent === '6 match in this period'")
    assert page.locator(".ft-browser__row", has_text="report.pdf").count() == 1
    page.get_by_role("button", name="Clear", exact=True).last.click()
    assert page.locator(".ft-filter__badge").is_hidden()
    page.keyboard.press("Escape")


@pytest.mark.parametrize("target", ["bar", "panel", "blank"])
def test_drag_indexed_file_to_gantt_or_blank_space(chrome, tmp_path, target):
    page = chrome.page
    root = tmp_path / "drag-files"
    root.mkdir()
    path = root / "drag.txt"
    path.write_text("dragged", encoding="utf-8")
    chrome.api("PUT", "/api/files/options", {"roots": [str(root)], "excluded_ext": [], "include_hidden": False})
    chrome.wait("async () => (await (await fetch('/api/files/status')).json()).state !== 'scanning'", timeout=15)
    entries = chrome.api("GET", "/api/files/entries?mode=created")
    day = entries["rows"][0][entries["columns"].index("day")]
    iso = page.evaluate("(day) => new Date(day * 86400000).toISOString().slice(0, 10)", day)
    item_id = chrome.api("POST", "/api/items", {"title": "Drag attachment target", "start_date": iso})["id"]
    chrome.open()
    page.evaluate("""async ({day, id}) => {
        const s = await import('/static/js/store.js');
        s.setGroupByTag(false); s.setView(day - 7, day + 7, 'test');
        s.setSelection([id], id); s.setPanel({open: true, tab: 'item'});
        const c = await import('/static/js/commands.js'); c.timelineHook('reveal')?.(id);
    }""", {"day": day, "id": item_id})
    page.wait_for_timeout(300)
    rect = page.evaluate("(id) => window.__reframe.barRect(id)", item_id)
    assert rect is not None
    chrome.focus_timeline()
    page.keyboard.press("Control+2")
    row = page.locator(".ft-browser__row", has_text="drag.txt")
    row.wait_for()
    box = row.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 + 15, box["y"] + box["height"] / 2, steps=5)
    page.mouse.move(5, 5, steps=10)
    assert page.locator(".rf-files-overlay").is_hidden()
    if target == "bar":
        page.mouse.move(rect["x"] + min(10, rect["w"] / 2), rect["y"] + rect["h"] / 2, steps=10)
    elif target == "panel":
        panel = page.locator(".tab--item .item-body").bounding_box()
        page.mouse.move(panel["x"] + panel["width"] / 2, panel["y"] + 30, steps=10)
    page.mouse.up()
    if target != "blank":
        chrome.wait(f"async () => (await (await fetch('/api/items/{item_id}')).json()).attachments.length === 1")
        attachments = chrome.api("GET", f"/api/items/{item_id}")["attachments"]
        assert attachments[0]["target"] == str(path)
        chrome.api("POST", "/api/undo", {})
        assert chrome.api("GET", f"/api/items/{item_id}")["attachments"] == []
    else:
        assert chrome.api("GET", f"/api/items/{item_id}")["attachments"] == []
    _move_ruler(page, 100, "minor")
    chrome.wait("() => !document.querySelector('.rf-files-overlay').hidden")


def test_file_browser_draws_windows_icons_and_keeps_photo_thumbnails(chrome, tmp_path):
    page = chrome.page
    root = tmp_path / "icons"
    root.mkdir()
    for name in ("first.txt", "second.txt", "report.pdf", "README"):
        (root / name).write_text("test", encoding="utf-8")
    Image.new("RGB", (64, 48), "green").save(root / "photo.jpg")
    icon_urls = []
    page.on("response", lambda response: icon_urls.append(response.url)
            if "/api/files/icon?" in response.url and response.status == 200 else None)
    page.evaluate("""() => {
        window.fileBrowserDraws = [];
        const draw = CanvasRenderingContext2D.prototype.drawImage;
        CanvasRenderingContext2D.prototype.drawImage = function(image, ...args) {
            if (this.canvas.classList.contains('ft-browser__thumb')) {
                window.fileBrowserDraws.push([image.width, image.height]);
            }
            return draw.call(this, image, ...args);
        };
    }""")
    chrome.api("PUT", "/api/files/options", {"roots": [str(root)], "excluded_ext": [], "include_hidden": False})
    chrome.wait("async () => (await (await fetch('/api/files/status')).json()).state !== 'scanning'", timeout=15)
    entries = chrome.api("GET", "/api/files/entries?mode=created")
    day = entries["rows"][0][entries["columns"].index("day")]
    page.evaluate("""async (day) => { const s = await import('/static/js/store.js');
        s.setView(day - 3, day + 4, 'test'); }""", day)
    chrome.focus_timeline()
    page.keyboard.press("Control+2")
    chrome.wait("() => window.fileBrowserDraws.some(([w, h]) => w === 64 && h === 48)")
    chrome.wait("() => performance.getEntriesByType('resource').some(e => e.name.includes('/api/files/icon?ext=.pdf'))")
    chrome.wait("() => performance.getEntriesByType('resource').some(e => e.name.endsWith('/api/files/icon?ext='))")
    assert sum("ext=.txt" in url for url in icon_urls) == 1
    assert any("ext=.pdf" in url for url in icon_urls)
    size = page.evaluate("""async () => {
        const bitmap = await createImageBitmap(await (await fetch('/api/files/icon?ext=.txt')).blob());
        const size = [bitmap.width, bitmap.height];
        bitmap.close();
        return size;
    }""")
    chrome.wait("([w, h]) => window.fileBrowserDraws.some(([x, y]) => x === w && y === h)", size)


def _mock_ruler_files(chrome):
    page = chrome.page
    today = store(page, "s.state.today")
    columns = ["id", "name", "ext", "kind", "day", "src", "size", "online", "root", "pending", "dir"]
    rows = [[str(day), f"day{day}.txt", ".txt", 0, day, 1, 1, 0, 0, 0, ""]
            for day in range(today - 950, today + 951)]
    undated = [["undated", "undated.pdf", ".pdf", 0, None, 1, 1, 0, 0, 0, ""]]
    page.route("**/api/files/entries?*", lambda route: route.fulfill(json={
        "columns": columns, "rows": rows, "undated": undated, "version": 1, "sources": {"1": "Created"},
    }))
    page.route("**/api/files/entry/*", lambda route: route.fulfill(json={
        "id": route.request.url.rsplit("/", 1)[-1], "name": "file.txt", "path": "C:\\file.txt",
        "ext": ".txt", "kind": "other", "size": 1, "created": "2026-01-01", "modified": "2026-01-01",
        "taken": {"value": None}, "modes": {"created": {"source_label": "Created", "date": "2026-01-01"}},
        "root": {"path": "C:\\"}, "online_only": False, "exists": True,
    }))
    return today


def _set_ruler(chrome, center, ppd):
    return chrome.page.evaluate("""async ({center, ppd}) => {
        const s = await import('/static/js/store.js');
        const width = document.querySelector('#timeline-host canvas').clientWidth;
        const span = width / ppd;
        s.setView(center - span / 2, center + span / 2, 'test');
        return width;
    }""", {"center": center, "ppd": ppd})


def _tick(chrome, x, row):
    return chrome.page.evaluate("""async ({x, row}) => {
        const s = await import('/static/js/store.js');
        const z = await import('/static/js/zoom.js');
        const width = document.querySelector('#timeline-host canvas').clientWidth;
        const ppd = width / (s.state.view.end - s.state.view.start);
        const day = z.xToDay(x, s.state.view, 0, ppd);
        return z.rulerRows(s.state.view.start, s.state.view.end, ppd)[row]
            .find(t => t.start <= day && day < t.end);
    }""", {"x": x, "row": row})


def _move_ruler(page, x, row):
    box = page.locator("#timeline-host > canvas").bounding_box()
    page.mouse.move(box["x"] + x, box["y"] + (11 if row == "major" else 33))


@pytest.mark.parametrize("ppd,units", [(0.8, ("year", "quarter")), (2, ("year", "month")),
                                       (8, ("month", "week")), (20, ("month", "day"))])
def test_ruler_popup_matches_all_zoom_segments(chrome, ppd, units):
    page = chrome.page
    today = _mock_ruler_files(chrome)
    width = _set_ruler(chrome, today + 30, ppd)
    x = width * 0.48
    for row, unit in zip(("major", "minor"), units):
        page.mouse.move(1, 1)
        tick = _tick(chrome, x, row)
        assert tick["unit"] == unit
        _move_ruler(page, x, row)
        chrome.wait("() => !document.querySelector('.rf-files-overlay').hidden")
        count = max(0, min(tick["end"], today + 951) - max(tick["start"], today - 950))
        count += int(tick["start"] > today)
        chrome.wait(f"() => document.querySelector('.ft-panel__body .field__hint')?.textContent.startsWith('{count} file')")
        assert page.locator(".rf-files-overlay__footer .ft-filter").is_visible()
        assert page.locator(".rf-files-overlay__footer .ft-status").is_visible()


def test_ruler_wait_transitions_and_dismissal(chrome):
    page = chrome.page
    today = _mock_ruler_files(chrome)
    width = _set_ruler(chrome, today, 25)
    x = width * 0.4
    _move_ruler(page, x, "minor")
    page.wait_for_timeout(500)
    _move_ruler(page, x + 30, "minor")
    page.wait_for_timeout(400)
    assert page.locator(".rf-files-overlay").is_hidden()
    chrome.wait("() => !document.querySelector('.rf-files-overlay').hidden")
    title = page.locator(".ft-panel__title").inner_text()
    _move_ruler(page, x + 60, "minor")
    assert page.locator(".ft-panel__title").inner_text() != title
    _move_ruler(page, x + 60, "major")
    assert page.locator(".rf-files-overlay").is_hidden()
    page.wait_for_timeout(400)
    assert page.locator(".rf-files-overlay").is_hidden()
    chrome.wait("() => !document.querySelector('.rf-files-overlay').hidden")
    title = page.locator(".ft-panel__title").inner_text()
    _move_ruler(page, x + 60, "minor")
    assert page.locator(".rf-files-overlay").is_visible()
    assert page.locator(".ft-panel__title").inner_text() == title
    overlay = page.locator(".rf-files-overlay").bounding_box()
    page.mouse.move(overlay["x"] + overlay["width"] / 2, overlay["y"] + 30)
    assert page.locator(".rf-files-overlay").is_visible()
    page.mouse.move(1, 1)
    assert page.locator(".rf-files-overlay").is_hidden()


def test_future_undated_and_keyboard_open(chrome):
    page = chrome.page
    today = _mock_ruler_files(chrome)
    opened = []
    revealed = []
    page.route("**/api/files/open/*", lambda route: (opened.append(route.request.url), route.fulfill(json={"ok": True})))
    page.route("**/api/files/reveal/*", lambda route: (revealed.append(route.request.url), route.fulfill(json={"ok": True})))
    width = _set_ruler(chrome, today + 15.5, 25)
    x = width / 2
    _move_ruler(page, x, "minor")
    chrome.wait("() => document.querySelector('.ft-panel__body .field__hint')?.textContent.startsWith('2 files')")
    assert page.locator(".ft-browser__row", has_text="undated.pdf").count() == 1
    page.locator(".ft-browser__list").focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    chrome.wait("() => document.querySelector('.ft-browser__row.is-selected') !== null")
    assert len(opened) == 1
    page.get_by_role("button", name="Open containing folder").click()
    assert len(revealed) == 1
    assert page.get_by_role("button", name="Modified", exact=True).count() == 0
    page.locator(".ft-filter__input").fill("undated")
    chrome.wait("() => document.querySelector('.ft-filter__badge')?.textContent === '1 match in this period'")
    assert page.locator(".ft-browser__row", has_text="undated.pdf").count() == 1
    page.keyboard.press("Escape")
    _set_ruler(chrome, today + 0.5, 25)
    chrome.focus_timeline()
    page.keyboard.press("Control+2")
    chrome.wait("() => !document.querySelector('.rf-files-overlay').hidden")
    assert page.locator(".ft-browser__row", has_text="undated.pdf").count() == 0


def test_ruler_pan_dismisses_popup_and_still_moves_gantt(chrome):
    page = chrome.page
    today = _mock_ruler_files(chrome)
    width = _set_ruler(chrome, today, 10)
    x = width / 2
    _move_ruler(page, x, "minor")
    chrome.wait("() => !document.querySelector('.rf-files-overlay').hidden")
    before = store(page, "s.state.view.start")
    page.mouse.down()
    _move_ruler(page, x + 120, "minor")
    page.mouse.up()
    assert page.locator(".rf-files-overlay").is_hidden()
    assert store(page, "s.state.view.start") < before


def test_stale_file_details_do_not_reappear_after_changing_period(chrome):
    page = chrome.page
    today = _mock_ruler_files(chrome)
    page.evaluate("""() => {
        const realFetch = window.fetch;
        window.fetch = (...args) => String(args[0]).includes('/api/files/entry/')
          ? new Promise(resolve => { window.finishFileDetails = () => resolve(new Response(JSON.stringify({
              id: 'old', name: 'stale.txt', path: 'C:\\\\stale.txt', ext: '.txt', kind: 'other', size: 1,
              taken: {value: null}, modes: {created: {source_label: 'Created', date: '2026-01-01'}},
              root: {path: 'C:\\\\'}, exists: true
          }), {headers: {'Content-Type': 'application/json'}})); })
          : realFetch(...args);
    }""")
    width = _set_ruler(chrome, today, 25)
    x = width / 2
    _move_ruler(page, x, "minor")
    chrome.wait("() => document.querySelectorAll('.ft-browser__row').length > 0")
    page.locator(".ft-browser__list").focus()
    page.keyboard.press("ArrowDown")
    chrome.wait("() => typeof window.finishFileDetails === 'function'")
    _move_ruler(page, x + 30, "minor")
    page.evaluate("window.finishFileDetails()")
    page.wait_for_timeout(100)
    assert "stale.txt" not in page.locator(".rf-files-overlay").inner_text()


def test_legacy_files_view_is_ignored_and_first_open_starts_scan(chrome):
    page = chrome.page
    chrome.api("PUT", "/api/settings", {"active_view": "files"})
    chrome.open()
    assert page.locator("#timeline-host").is_visible()
    assert page.locator(".rf-files-overlay").is_hidden()
    _mock_ruler_files(chrome)
    requests = []
    page.route("**/api/files/status", lambda route: route.fulfill(json={"state": "never", "files": 0, "roots": []}))
    page.route("**/api/files/rescan", lambda route: (requests.append(route.request.method),
                                                     route.fulfill(json={"state": "scanning", "files": 0, "roots": []})))
    chrome.focus_timeline()
    page.keyboard.press("Control+2")
    chrome.wait("() => document.querySelector('.ft-status__line')?.textContent.includes('Scanning')")
    assert requests == ["POST"]


def test_mp4_details_show_media_created_date(chrome):
    page = chrome.page
    page.route("**/api/files/entry/test-video", lambda route: route.fulfill(json={
        "id": "test-video", "name": "video.mp4", "path": "C:\\Videos\\video.mp4", "ext": ".mp4",
        "kind": "other", "size": 42, "created": "2021-01-01 12:00:00", "modified": "2021-01-01 12:00:00",
        "taken": {"value": "2015-07-04 01:00:00", "offset": "+00:00"},
        "modes": {"created": {"source_label": "Media created", "date": "2015-07-03", "undated": False}},
        "root": {"path": "C:\\Videos"}, "online_only": False, "exists": True,
    }))
    page.evaluate("""async () => {
        const { createPanel } = await import('/static/js/file_timeline/panel.js');
        const { createFileStore } = await import('/static/js/file_timeline/store.js');
        const store = createFileStore(() => 20000);
        store.setData({columns: ['id','name','ext','kind','day','src','size','online','root','pending','dir'],
          rows: [['test-video','video.mp4','.mp4',0,16619,3,42,0,0,0,'']]});
        store.select('test-video');
        store.setPanel({mode: 'details'});
        const host = document.createElement('div');
        host.id = 'test-file-details';
        document.body.append(host);
        createPanel(host, {store, loader: {want: () => {}}, getTheme: () => ({})}).render();
    }""")
    chrome.wait("() => document.querySelector('#test-file-details .ft-details__name')?.textContent === 'video.mp4'")
    text = page.locator("#test-file-details").inner_text()
    assert "Media created" in text
    assert "Date taken" in text and "2015-07-04 01:00:00 (UTC+00:00)" in text


def test_hal_answers_the_second_locked_edit_in_2001_only(chrome):
    page = chrome.page
    iid = add_imported(chrome)
    page.evaluate("async (id) => { const s = await import('/static/js/store.js'); s.setSelection([id], id); }", iid)
    chrome.focus_timeline()
    page.keyboard.press("F2")
    assert page.locator(".hal").count() == 0 and "Imported from Outlook" in page.inner_text(".status__notice")
    page.keyboard.press("F2")
    page.wait_for_selector(".hal")
    assert "I'm sorry, Dave" in page.inner_text(".hal")
    time.sleep(4.3)
    assert page.locator(".hal").count() == 0
    store(page, "s.setTheme('catalog')")
    page.keyboard.press("F2")
    page.keyboard.press("F2")
    assert page.locator(".hal").count() == 0


def test_monolith_in_2001_only_and_search_still_works(chrome):
    page = chrome.page
    page.fill(".filter-bar__input", "monolith")
    chrome.wait("() => window.__reframe.egg() === 'monolith'")
    assert store(page, "s.state.filter.text") == "monolith"
    chrome.wait("() => window.__reframe.egg() === null", timeout=6)
    page.fill(".filter-bar__input", "")
    store(page, "s.setTheme('ibm')")
    page.fill(".filter-bar__input", "Monolith")
    page.wait_for_timeout(500)
    assert page.evaluate("window.__reframe.egg()") is None


def test_notes_are_spell_checked_titles_and_paths_are_not(chrome):
    page = chrome.page
    item = next(i for i in chrome.api("GET", "/api/items")["items"] if i["origin"] == "local")
    page.evaluate("""async (id) => { const s = await import('/static/js/store.js'); s.setSelection([id], id);
        s.setPanel({open: true, tab: 'item'}); }""", item["id"])
    page.wait_for_selector(".field-notes")
    assert page.get_attribute(".field-notes", "spellcheck") == "true"
    assert page.get_attribute(".field-title", "spellcheck") == "false"
    assert page.get_attribute(".field-link", "spellcheck") == "false"
    assert page.get_attribute(".tag-editor__input", "spellcheck") == "false"

def test_migration_banner_summarises_the_import_once(chrome):
    page = chrome.page
    chrome.api("PUT", "/api/settings", {"migrated_from": {"imported_at": "2026-11-24T10:00:00Z",
                                                          "row_counts": {"item": 195, "tag": 21}}})
    chrome.open()
    page.evaluate("async () => { const s = await import('/static/js/store.js'); s.setSelection([], null); "
                  "s.setPanel({open: true, tab: 'item'}); }")
    page.wait_for_selector(".migration-banner")
    text = page.inner_text(".migration-banner")
    assert "195 items and 21 tags imported" in text and "calendar" in text and "protected" in text
    page.click(".migration-banner button:has-text('Dismiss')")
    chrome.wait("() => !document.querySelector('.migration-banner')")
    assert chrome.api("GET", "/api/settings")["migration_banner_dismissed"] is True
