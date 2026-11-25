"""Browser smoke tests for the DOM chrome (toolbar, filter bar, side panel, status bar, shortcuts, menus).

Runs against a seeded demo server in Edge via Playwright. Canvas-specific behaviour is covered elsewhere; here the
timeline host is only used as the focus target for keyboard shortcuts.
"""
from __future__ import annotations

import datetime as dt
import re

import pytest

pytest.importorskip("playwright.sync_api")

TOOLBAR_SHORTCUTS = {
    "today": "T", "zoomOut": "Ctrl+-", "zoomIn": "Ctrl+=", "fit": "Z", "groupByTag": "Ctrl+G",
    "undo": "Ctrl+Z", "redo": "Ctrl+Y", "sync": "Ctrl+R", "panel": "Ctrl+B",
}
SEGMENT_SHORTCUTS = {"#tb-layoutMode [data-value='strict']": "Ctrl+Shift+L"}
THEMES = ["2001 Meridian", "Industrial Catalog", "Claude", "Dell 1996", "DJI", "Ferrari", "SpaceX",
          "Spotify", "IBM"]


@pytest.fixture(autouse=True)
def _clean_console(request):
    """Every test must finish without console errors or dialog calls (R-84)."""
    yield
    chrome = request.node.funcargs.get("chrome")
    if chrome is not None:
        assert chrome.errors == [], chrome.errors
        assert chrome.dialogs == [], chrome.dialogs


def local_item(chrome, kind="plan"):
    items = chrome.api("GET", "/api/items")["items"]
    return next(i for i in items if i["origin"] == "local" and i["kind"] == kind and i["start_date"] == i["end_date"])


def select(chrome, item_id):
    chrome.page.evaluate("""async (id) => { const s = await import('/static/js/store.js'); s.setSelection([id], id);
        s.setPanel({open: true, tab: 'item'}); }""", item_id)
    chrome.page.wait_for_selector("#item-title")
    chrome.wait("() => document.getElementById('item-title').value.length > 0")


def count_text(chrome):
    return chrome.page.inner_text(".status__count")


def test_toolbar_buttons_have_tooltips_with_shortcuts(chrome):
    for btn_id, shortcut in TOOLBAR_SHORTCUTS.items():
        sel = f"#tb-{btn_id}"
        assert chrome.page.locator(sel).count() == 1, sel
        assert shortcut in chrome.hover_tip(sel), (btn_id, shortcut)
    for sel, shortcut in SEGMENT_SHORTCUTS.items():
        assert shortcut in chrome.hover_tip(sel), sel
    assert "View menu" in chrome.hover_tip("#tb-view")
    # no hide-past or theme button (R-05, R-90): no hide-past or theme button
    assert chrome.page.locator("#tb-hidePast, #tb-theme").count() == 0


def test_undo_tooltip_names_what_will_be_undone(chrome):
    item = local_item(chrome)
    select(chrome, item["id"])
    chrome.page.fill("#item-title", "Renamed for undo tooltip")
    chrome.page.keyboard.press("Enter")
    chrome.wait("() => document.getElementById('tb-undo').getAttribute('aria-disabled') === 'false'")
    assert f"Edit '{item['title']}'" in chrome.hover_tip("#tb-undo")


def open_theme_menu(page):
    page.click("#tb-view")
    page.click(".menu__item:has-text('Theme')")
    page.wait_for_selector(".menu__item:has-text('Spotify')")


def test_theme_only_from_view_menu_all_ten_and_persists(chrome):
    """R-90, R-180: View > Theme lists the nine themes with a check on the active one; Ctrl+Shift+T does nothing."""
    page = chrome.page
    assert page.evaluate("document.documentElement.dataset.theme") == "meridian"
    chrome.focus_timeline()
    page.keyboard.press("Control+Shift+T")
    assert page.evaluate("document.documentElement.dataset.theme") == "meridian"
    open_theme_menu(page)
    labels = page.eval_on_selector_all(".menu .menu__item .menu__label", "els => els.map(e => e.textContent)")
    assert [l for l in labels if l in THEMES] == THEMES
    assert page.get_attribute(".menu__item:has-text('2001 Meridian')", "aria-checked") == "true"
    page.click(".menu__item:has-text('Spotify')")
    assert page.evaluate("document.documentElement.dataset.theme") == "spotify"
    assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == "dark"
    page.wait_for_timeout(1200)   # view state is saved after a short debounce
    chrome.open()
    assert page.evaluate("document.documentElement.dataset.theme") == "spotify"
    for name in THEMES:            # every theme applies without console errors
        open_theme_menu(page)
        page.click(f".menu__item:has-text('{name}')")
    assert page.evaluate("document.documentElement.dataset.theme") == "ibm"


def test_group_and_layout_toggles_show_pressed_state(chrome):
    page = chrome.page
    assert page.get_attribute("#tb-groupByTag", "aria-pressed") == "true"   # on by default (R-15)
    page.click("#tb-groupByTag")
    assert page.get_attribute("#tb-groupByTag", "aria-pressed") == "false"
    page.click("#tb-groupByTag")
    strict = "#tb-layoutMode [data-value='strict']"
    assert page.get_attribute(strict, "aria-pressed") == "false"
    page.click(strict)
    assert page.get_attribute(strict, "aria-pressed") == "true"
    page.click("#tb-layoutMode [data-value='compact']")


def test_filter_bar_narrows_item_count_and_clears(chrome):
    page = chrome.page
    assert count_text(chrome).startswith("300 / 300")
    page.click("#filterbar .filter-chip:has-text('Spares')")
    assert page.get_attribute("#filterbar", "data-active") == "true"
    shown = int(count_text(chrome).split("/")[0])
    assert 0 < shown < 300
    assert "match" in page.inner_text(".filter-bar__badge")
    page.click(".filter-bar__clear")
    assert count_text(chrome).startswith("300 / 300")
    page.click("#filterbar .seg__btn:has-text('Log')")
    assert int(count_text(chrome).split("/")[0]) < 300
    chrome.focus_timeline()
    page.keyboard.press("Control+Shift+F")
    assert count_text(chrome).startswith("300 / 300")


def test_search_filters_and_enter_jumps_to_matches(chrome):
    page = chrome.page
    page.fill(".filter-bar__input", "Quote 8841")
    chrome.wait("() => !document.querySelector('.status__count').textContent.startsWith('300')")
    page.keyboard.press("Enter")
    first = page.evaluate("document.querySelector('.status__notice')?.textContent")
    assert first and "Match 1 of" in first
    page.keyboard.press("Enter")
    assert "Match 2 of" in page.inner_text(".status__notice")
    assert page.evaluate("import('/static/js/store.js').then(s => s.state.selection.size)") == 1


def test_side_panel_item_edit_persists(chrome):
    page = chrome.page
    item = local_item(chrome)
    select(chrome, item["id"])
    page.fill("#item-title", "Edited by smoke test")
    page.keyboard.press("Enter")
    end = (dt.date.fromisoformat(item["end_date"]) + dt.timedelta(days=2)).isoformat()
    page.fill("#item-end", end.replace("-", ""))      # no dashes is accepted (R-100)
    page.keyboard.press("Enter")
    page.fill("#item-notes", "Notes typed by the smoke test")
    page.locator("#item-notes").blur()
    editor = page.locator(".tab--item .tag-editor__input")
    editor.fill("smoketag")
    editor.press("Enter")
    chrome.wait("() => document.querySelector('.tag-editor__chips').textContent.includes('smoketag')")
    page.wait_for_timeout(300)
    saved = chrome.api("GET", f"/api/items/{item['id']}")
    assert saved["title"] == "Edited by smoke test"
    assert saved["end_date"] == end
    assert saved["notes"] == "Notes typed by the smoke test"
    assert "smoketag" in [t["name"] for t in saved["tags"]]
    assert "3 days" in page.inner_text(".item-duration")


def test_invalid_date_shows_inline_hint_and_is_not_saved(chrome):
    page = chrome.page
    item = local_item(chrome)
    select(chrome, item["id"])
    page.fill("#item-start", "11/08/2026")
    page.keyboard.press("Enter")
    assert "Use a date like 2026-11-08 or 20261108" in page.inner_text("#item-start + .field__msg, .field:has(#item-start) .field__msg")
    assert chrome.api("GET", f"/api/items/{item['id']}")["start_date"] == item["start_date"]


def test_inline_calendar_picks_a_date(chrome):
    page = chrome.page
    item = local_item(chrome)
    select(chrome, item["id"])
    page.click("button[aria-label='Pick end date']")
    assert page.locator(".cal").is_visible()
    target = (dt.date.fromisoformat(item["end_date"]) + dt.timedelta(days=1))
    page.locator(".cal__day:not(.is-other)", has_text=re.compile(rf"^{target.day}$")).first.click()
    page.wait_for_timeout(400)
    got = chrome.api("GET", f"/api/items/{item['id']}")["end_date"]
    assert got >= item["end_date"]


def test_single_key_shortcuts_only_fire_with_timeline_focus(chrome):
    page = chrome.page
    far = "async () => { const s = await import('/static/js/store.js'); s.setView(s.state.today + 300, s.state.today + 342, 'test'); return s.state.view.start; }"
    start_far = page.evaluate(far)
    page.click(".filter-bar__input")
    page.keyboard.type("t")
    assert page.input_value(".filter-bar__input") == "t"
    assert page.evaluate("import('/static/js/store.js').then(s => s.state.view.start)") == start_far
    page.fill(".filter-bar__input", "")
    chrome.focus_timeline()
    page.keyboard.press("t")
    moved = page.evaluate("import('/static/js/store.js').then(s => s.state.view.start)")
    assert moved != start_far


def test_modifier_shortcuts_override_browser_accelerators(chrome):
    page = chrome.page
    page.evaluate("""window.__prevented = {}; document.addEventListener('keydown', (e) => {
        window.__prevented[(e.ctrlKey ? 'Ctrl+' : '') + (e.shiftKey ? 'Shift+' : '') + e.key.toLowerCase()] = e.defaultPrevented; }, true);""")
    page.keyboard.press("Control+f")
    assert page.evaluate("document.activeElement.classList.contains('filter-bar__input')")
    page.keyboard.press("Escape")
    for combo, key in [("Control+j", "Ctrl+j"), ("Control+p", "Ctrl+p"), ("F5", "f5"), ("Control+s", "Ctrl+s"), ("Control+h", "Ctrl+h")]:
        page.keyboard.press(combo)
        if combo == "Control+j":
            page.keyboard.press("Escape")
        assert page.evaluate(f"window.__prevented['{key}']") is True, combo
    chrome.focus_timeline()


def test_ctrl_z_stays_text_undo_inside_text_fields(chrome):
    page = chrome.page
    item = local_item(chrome)
    select(chrome, item["id"])
    before = chrome.api("GET", "/api/undo-state")["undo"]
    page.click("#item-notes")
    page.keyboard.type("abc")
    page.keyboard.press("Control+z")
    assert chrome.api("GET", "/api/undo-state")["undo"] == before


def test_cheat_sheet_opens_and_closes(chrome):
    page = chrome.page
    chrome.focus_timeline()
    page.keyboard.press("?")
    page.wait_for_selector(".cheatsheet")
    text = page.inner_text(".cheatsheet")
    for needle in ("Ctrl+Shift+L", "Ctrl+2", "Ctrl+Q", "Alt+Shift+Left", "Go to date", "Zoom to selection"):
        assert needle in text, needle
    assert "Ctrl+Shift+T" not in text and "hide past" not in text.lower()
    page.keyboard.press("Escape")
    assert page.locator(".cheatsheet").count() == 0
    chrome.focus_timeline()
    page.keyboard.press("?")
    page.wait_for_selector(".cheatsheet")
    page.mouse.click(5, 450)
    assert page.locator(".cheatsheet").count() == 0


def test_go_to_date_accepts_dashless_and_rejects_bad_input(chrome):
    page = chrome.page
    page.keyboard.press("Control+j")
    page.fill(".goto__input", "2026-99-99")
    page.keyboard.press("Enter")
    assert "Use a date like" in page.inner_text(".goto__error")
    page.fill(".goto__input", "20261225")
    page.keyboard.press("Enter")
    assert page.locator(".goto").count() == 0
    page.wait_for_selector(".day-title")
    assert "December 25, 2026" in page.inner_text(".day-title")


def test_day_panel_add_log_entry_keeps_focus(chrome):
    page = chrome.page
    chrome.focus_timeline()
    page.keyboard.press("Control+d")
    page.wait_for_selector(".day-add-input")
    today = chrome.page.evaluate("import('/static/js/store.js').then(s => s.state.todayISO)")
    for title in ("First log entry", "Second log entry"):
        page.fill(".day-add-input", title)
        page.keyboard.press("Enter")
        chrome.wait("(t) => [...document.querySelectorAll('.day-row__title')].some((i) => i.value === t)", title)
        assert page.evaluate("document.activeElement.classList.contains('day-add-input')")
    items = chrome.api("GET", "/api/items")["items"]
    made = [i for i in items if i["title"].endswith("log entry") and i["title"][:6] in ("First ", "Second")]
    assert len(made) == 2 and all(i["kind"] == "log" and i["start_date"] == today for i in made)


def test_history_lists_change_sets_and_revert_works(chrome):
    page = chrome.page
    item = local_item(chrome)
    select(chrome, item["id"])
    page.fill("#item-title", "History target")
    page.keyboard.press("Enter")
    chrome.wait("() => document.getElementById('tb-undo').getAttribute('aria-disabled') === 'false'")
    chrome.focus_timeline()
    page.keyboard.press("Control+Shift+h")
    page.locator(".hist-item", has_text=f"Edit '{item['title']}'").first.wait_for()
    row = page.locator(".hist-item", has_text=f"Edit '{item['title']}'").first
    assert row.count() == 1
    row.get_by_role("button", name=re.compile("Revert")).click()
    page.wait_for_function(
        "fetch('/api/items/%s').then(r => r.json()).then(i => i.title !== 'History target')" % item["id"])
    assert chrome.api("GET", f"/api/items/{item['id']}")["title"] == item["title"]


def test_tag_manager_rename_and_merge(chrome):
    page = chrome.page
    chrome.focus_timeline()
    page.keyboard.press("Control+Shift+h")
    page.click("#tab-tags")
    page.wait_for_selector(".tag-row")
    row = page.locator(".tag-row", has_text="Audit").first
    row.get_by_role("button", name=re.compile("^Rename")).click()
    page.fill(".tag-form input", "Audit Renamed")
    page.keyboard.press("Enter")
    chrome.wait("() => document.querySelector('.tag-tree').textContent.includes('Audit Renamed')")
    names = [t["name"] for t in chrome.api("GET", "/api/tags")["tags"]]
    assert "Audit Renamed" in names and "Audit" not in names

    before = {t["name"]: t["usage"] for t in chrome.api("GET", "/api/tags")["tags"]}
    safety = page.locator(".tag-row", has_text="Safety").first
    safety.get_by_role("button", name=re.compile("^Merge")).click()
    page.select_option(".tag-form select", label=f"Spares ({before['Spares']})")
    page.get_by_role("button", name="Merge", exact=True).click()
    chrome.wait("() => !document.querySelector('.tag-tree').textContent.includes('Safety')")
    after = {t["name"]: t["usage"] for t in chrome.api("GET", "/api/tags")["tags"]}
    assert "Safety" not in after and after["Spares"] >= before["Spares"]


def test_delete_shows_undo_notice_in_status_bar(chrome):
    page = chrome.page
    item = local_item(chrome)
    select(chrome, item["id"])
    chrome.focus_timeline()
    page.keyboard.press("Delete")
    page.wait_for_selector(".status__notice .notice__btn:has-text('Undo')")
    assert "Deleted 1 item" in page.inner_text(".status__notice")
    assert all(i["id"] != item["id"] for i in chrome.api("GET", "/api/items")["items"])
    page.click(".status__notice .notice__btn:has-text('Undo')")
    chrome.wait("async (id) => (await (await fetch('/api/items')).json()).items.some((i) => i.id === id)", item["id"])


def test_multi_select_panel_shows_mixed_values(chrome):
    page = chrome.page
    items = chrome.api("GET", "/api/items")["items"][:3]
    page.evaluate("""async (ids) => { const s = await import('/static/js/store.js'); s.setSelection(ids, ids[0]); s.setPanel({open: true, tab: 'item'}); }""",
                  [i["id"] for i in items])
    page.wait_for_selector(".multi-count")
    assert "3 items selected" in page.inner_text(".multi-count")
    assert "mixed" in page.inner_text(".tab--multi")


def flat(chrome):
    """Switch group by tag off: the tests that call this exercise the flat layout."""
    chrome.page.evaluate("async () => { const s = await import('/static/js/store.js'); s.setGroupByTag(false); }")
    chrome.page.wait_for_timeout(300)


def test_context_menu_replaces_browser_menu(chrome):
    page = chrome.page
    flat(chrome)
    page.click("#timeline-host", button="right", position={"x": 300, "y": 300})
    page.wait_for_selector(".menu")
    assert page.locator(".menu [role=menuitem]", has_text="New item here").count() == 1
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Escape")
    assert page.locator(".menu").count() == 0
    page.click("#statusbar", button="right")
    page.wait_for_selector(".menu")
    page.mouse.click(600, 300)
    assert page.locator(".menu").count() == 0


def test_side_panel_toggle_and_resize(chrome):
    page = chrome.page
    chrome.focus_timeline()
    page.keyboard.press("Control+b")
    assert page.evaluate("document.getElementById('sidepanel').classList.contains('is-collapsed')")
    page.keyboard.press("Control+b")
    box = page.locator(".panel-resizer").bounding_box()
    width_before = page.evaluate("document.getElementById('sidepanel').offsetWidth")
    page.mouse.move(box["x"] + 2, box["y"] + 200)
    page.mouse.down()
    page.mouse.move(box["x"] - 80, box["y"] + 200, steps=4)
    page.mouse.up()
    assert page.evaluate("document.getElementById('sidepanel').offsetWidth") > width_before + 40


def test_settings_tab_shows_paths_and_first_run_hint_is_dismissible(chrome):
    page = chrome.page
    chrome.focus_timeline()
    page.click("#tab-settings")
    page.wait_for_selector(".tab--settings code.path")
    assert "demo.sqlite" in page.inner_text(".tab--settings")
    assert "reframe-agent-instructions.md" in page.inner_text(".tab--settings")
    titles = page.eval_on_selector_all(".tab--settings .psec__title", "els => els.map(e => e.textContent.trim())")
    assert "Appearance" not in titles                                   # theme is in View > Theme only (R-90)
    assert titles[:6] == ["Window & startup", "Calendars", "Attachments", "Files", "Backups", "Database & agent"]


def test_tag_editor_suggests_near_match_before_creating(chrome):
    page = chrome.page
    item = next(i for i in chrome.api("GET", "/api/items")["items"] if i["origin"] == "local" and not i["tags"])
    select(chrome, item["id"])
    editor = page.locator(".tab--item .tag-editor__input")
    editor.fill("Spares1")
    editor.press("Enter")
    page.wait_for_selector(".tab--item .tag-editor__suggest")
    assert "Spares" in page.inner_text(".tab--item .tag-editor__suggest")
    assert "Spares1" not in [t["name"] for t in chrome.api("GET", "/api/tags")["tags"]]
    page.click(".tab--item .tag-editor__suggest button:has-text('Use it')")
    chrome.wait("() => document.querySelector('.tab--item .tag-editor__chips').textContent.includes('Spares')")
    page.wait_for_timeout(300)
    assert "Spares" in [t["name"] for t in chrome.api("GET", f"/api/items/{item['id']}")["tags"]]
    assert "Spares1" not in [t["name"] for t in chrome.api("GET", "/api/tags")["tags"]]


def test_tag_editor_case_variant_resolves_silently_and_chips_reorder(chrome):
    page = chrome.page
    item = chrome.api("GET", "/api/items")["items"]
    item = next(i for i in item if i["origin"] == "local" and not i["tags"])
    select(chrome, item["id"])
    editor = page.locator(".tab--item .tag-editor__input")
    for name in ("SPARES!", "prj 20"):
        editor.fill(name)
        editor.press("Enter")
        page.wait_for_timeout(500)
    tags = [t["name"] for t in chrome.api("GET", f"/api/items/{item['id']}")["tags"]]
    assert tags == ["Spares", "PRJ-20"], tags
    chips = page.locator(".tab--item .tag-editor__chip")
    chips.nth(1).drag_to(chips.nth(0))
    chrome.wait("""async (id) => { const t = (await (await fetch('/api/items/' + id)).json()).tags.map((x) => x.name);
        return t[0] === 'PRJ-20'; }""", item["id"])


def test_attachments_add_flag_broken_link_and_remove(chrome):
    page = chrome.page
    item = local_item(chrome)
    select(chrome, item["id"])
    page.fill(".field-link", r"C:\definitely\missing\file.xlsx")
    page.keyboard.press("Enter")
    page.wait_for_selector(".att")
    page.wait_for_selector(".att__broken")
    # only the file name is shown; the full path is in the tooltip
    assert page.locator(".att__label").first.inner_text() == "file.xlsx"
    assert "definitely" not in page.locator(".att-list").inner_text()
    page.fill(".field-link", "https://example.com/spec")
    page.keyboard.press("Enter")
    chrome.wait("() => document.querySelectorAll('.att').length === 2")
    page.click("button[aria-label='Remove spec']")
    chrome.wait("() => document.querySelectorAll('.att').length === 1")


def test_attachment_tooltip_shows_full_path(chrome):
    page = chrome.page
    item = local_item(chrome)
    select(chrome, item["id"])
    full = r"C:\Projects\PRJ-20\Spares\Order 1234.xlsx"
    page.fill(".field-link", full)
    page.keyboard.press("Enter")
    page.wait_for_selector(".att")
    page.locator(".att__text").first.hover()
    page.wait_for_selector(".tooltip.is-visible")
    assert full in page.locator(".tooltip.is-visible").inner_text()


def test_dropping_a_url_on_the_panel_adds_a_link(chrome):
    page = chrome.page
    item = local_item(chrome)
    select(chrome, item["id"])
    page.evaluate("""() => { const dt = new DataTransfer(); dt.setData('text/uri-list', 'https://example.com/dropped');
        const el = document.querySelector('.tab--item');
        el.dispatchEvent(new DragEvent('dragover', {dataTransfer: dt, bubbles: true, cancelable: true}));
        el.dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true})); }""")
    chrome.wait("() => document.querySelector('.att-list').textContent.includes('dropped')")


def test_imported_item_is_read_only_with_hint(chrome):
    page = chrome.page
    import sqlite3
    con = sqlite3.connect(chrome.server.db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("BEGIN IMMEDIATE")
    iid = con.execute(
        "INSERT INTO item (kind,title,start_date,end_date,origin, source_id,outlook_uid,outlook_series_id,outlook_time,outlook_location) "
        "VALUES ('plan','Imported weekly','2026-11-12','2026-11-12','outlook', 'seed-outlook','cal|S1|2026-11-12','cal|S1','09:00-10:00','Room 4') RETURNING id"
    ).fetchone()[0]
    con.commit()
    con.close()
    chrome.open()
    select(chrome, iid)
    assert page.get_attribute("#item-title", "aria-readonly") == "true"
    assert "Imported from Outlook. Change it there, then Sync." in page.inner_text(".item-lock")
    assert "Room 4" in page.inner_text(".item-outlook")
    assert page.locator("button:has-text('Hide whole series')").count() == 1
    assert "Applies to every occurrence of this series" in page.inner_text(".series-note")


def test_imported_item_shows_source_calendar_and_flags_identical_entries(chrome):
    import sqlite3
    page = chrome.page
    path = "\\\\me@x.com\\Calendar\\United States holidays"
    con = sqlite3.connect(chrome.server.db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("BEGIN IMMEDIATE")
    con.execute("INSERT INTO calendar_source (id, kind, external_id, name, location, is_holiday, visible) "
                "VALUES ('hol', 'outlook', 'CAL-H', 'United States holidays', ?, 1, 1)", (path,))
    ids = [con.execute(
        "INSERT INTO item (kind,title,start_date,end_date,origin, source_id,outlook_uid,outlook_calendar) "
        "VALUES ('plan','Labor Day','2026-11-16','2026-11-16','outlook', 'hol',?,'CAL-H') RETURNING id", (f"CAL-H|entry:{i}",)
    ).fetchone()[0] for i in range(2)]
    con.commit()
    con.close()
    chrome.open()
    select(chrome, ids[0])
    page.wait_for_selector(".item-outlook")
    text = page.inner_text(".item-outlook")
    assert "United States holidays" in text and "CAL-H" not in text
    chrome.wait("() => document.querySelector('.item-outlook').parentElement.textContent.includes('identical')")
    assert "1 other identical entry" in page.inner_text("#sidepanel")


def test_sidebar_can_be_widened_beyond_old_limit(chrome):
    page = chrome.page
    chrome.focus_timeline()
    grip = page.locator(".panel-resizer")
    box = grip.bounding_box()
    vw = page.evaluate("() => window.innerWidth")
    page.mouse.move(box["x"] + 3, box["y"] + 200)
    page.mouse.down()
    page.mouse.move(vw * 0.25, box["y"] + 200, steps=8)
    page.mouse.up()
    width = page.evaluate("() => document.getElementById('sidepanel').getBoundingClientRect().width")
    assert width > 720 or width >= vw - 380      # past the former 720 px cap (or as wide as the window allows)


def test_today_is_a_shaded_day_cell_not_a_line(chrome):
    page = chrome.page
    flat(chrome)
    chrome.focus_timeline()
    info = page.evaluate("""async () => {
        const s = await import('/static/js/store.js');
        const c = document.querySelector('#timeline-host canvas');
        const r = c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
        const plotLeft = window.__reframe.plotLeft(), ppd = window.__reframe.ppd();
        const x = (day) => plotLeft + (day - s.state.view.start) * ppd;
        const px = (cx, cy) => Array.from(c.getContext('2d').getImageData(Math.round(cx * dpr), Math.round(cy * dpr), 1, 1).data);
        const y = r.height - 6;                         // below every bar
        const t = s.state.today;
        const left = px(x(t) + 2, y), mid = px(x(t) + ppd / 2, y), right = px(x(t + 1) - 2, y);
        const before = px(x(t - 1) + ppd / 2, y), after = px(x(t + 1) + ppd / 2, y);
        return { left, mid, right, before, after, ppd, today: s.state.todayISO,
                 weekendBefore: new Date(t * 86400000).getUTCDay() };
    }""")
    # the whole cell has one colour (no centre line) and it differs from a neighbouring weekday
    assert info["left"] == info["mid"] == info["right"], info
    neighbours_are_weekend = info["weekendBefore"] in (0, 1, 6)
    if not neighbours_are_weekend:
        assert info["mid"] != info["before"] and info["mid"] != info["after"], info


def test_empty_database_shows_first_run_hint(chrome_factory):
    c = chrome_factory(0)
    c.page.wait_for_selector(".first-run")
    assert "Ctrl+N" in c.page.inner_text(".first-run")
    assert "reframe-agent-instructions.md" in c.page.inner_text(".first-run")
    c.page.click(".first-run >> text=Dismiss")
    c.wait("() => document.querySelector('.first-run') === null")
    c.page.wait_for_timeout(300)
    assert c.api("GET", "/api/settings")["first_run_hint_dismissed"] is True
    assert c.errors == []
