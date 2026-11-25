"""Ctrl+drag tag copy (R-48), minimap colours (R-21), group-by default (R-15) and the tray setting (R-104)."""
from __future__ import annotations

import datetime as dt

import pytest


@pytest.fixture(autouse=True)
def _no_console_errors(request):
    yield
    chrome = request.node.funcargs.get("chrome")
    if chrome is not None:
        assert chrome.errors == [], chrome.errors
        assert chrome.dialogs == [], chrome.dialogs


def day(offset):
    return (dt.date.today() + dt.timedelta(days=offset)).isoformat()


@pytest.fixture
def pair(chrome_factory):
    """An empty database with one tagged item and one new untagged item, both in view."""
    c = chrome_factory(0)
    src = c.api("POST", "/api/items", {"title": "Source", "start_date": day(1), "end_date": day(5),
                                       "tags": ["Spares", "PRJ-20", "Cell 4"]})["id"]
    tgt = c.api("POST", "/api/items", {"title": "Fresh item", "start_date": day(2), "end_date": day(6)})["id"]
    c.open()
    c.focus_timeline()
    return c, src, tgt


def centre(c, item_id):
    r = c.page.evaluate("id => window.__reframe.barRect(id)", item_id)
    return r["x"] + r["w"] / 2, r["y"] + r["h"] / 2


def tag_names(c, item_id):
    return [t["name"] for t in c.api("GET", f"/api/items/{item_id}")["tags"]]


def ctrl_drag(c, src, tgt, *, release_on=None, escape=False):
    page = c.page
    sx, sy = centre(c, src)
    tx, ty = release_on or centre(c, tgt)
    page.keyboard.down("Control")
    page.mouse.move(sx, sy)
    page.mouse.down()
    page.mouse.move((sx + tx) / 2, (sy + ty) / 2, steps=4)
    page.mouse.move(tx, ty, steps=6)
    if escape:
        page.keyboard.press("Escape")
    page.mouse.up()
    page.keyboard.up("Control")


def test_ctrl_drag_copies_all_tags_to_the_drop_target(pair):
    c, src, tgt = pair
    ctrl_drag(c, src, tgt)
    for _ in range(50):
        if tag_names(c, tgt) == ["Spares", "PRJ-20", "Cell 4"]:
            break
        c.page.wait_for_timeout(100)
    assert tag_names(c, tgt) == ["Spares", "PRJ-20", "Cell 4"]       # same tags, same primary (colour) tag
    assert tag_names(c, src) == ["Spares", "PRJ-20", "Cell 4"]       # the source is untouched
    assert "Copied the tags of 'Source'" in c.page.inner_text(".status__notice")


def test_tag_copy_is_one_undo(pair):
    c, src, tgt = pair
    ctrl_drag(c, src, tgt)
    for _ in range(50):
        if tag_names(c, tgt):
            break
        c.page.wait_for_timeout(100)
    c.focus_timeline()
    c.page.keyboard.press("Control+z")
    for _ in range(50):
        if not tag_names(c, tgt):
            break
        c.page.wait_for_timeout(100)
    assert tag_names(c, tgt) == []
    assert tag_names(c, src) == ["Spares", "PRJ-20", "Cell 4"]


def test_escape_cancels_the_tag_drag(pair):
    c, src, tgt = pair
    ctrl_drag(c, src, tgt, escape=True)
    c.page.wait_for_timeout(600)
    assert tag_names(c, tgt) == []


def test_dropping_on_empty_space_changes_nothing(pair):
    c, src, tgt = pair
    box = c.page.locator("#timeline-host canvas").bounding_box()
    ctrl_drag(c, src, tgt, release_on=(box["x"] + 40, box["y"] + box["height"] - 20))
    c.page.wait_for_timeout(600)
    assert tag_names(c, tgt) == []


def test_ctrl_click_without_drag_still_toggles_selection(pair):
    c, src, tgt = pair
    c.page.mouse.click(*centre(c, src))
    c.page.keyboard.down("Control")
    c.page.mouse.click(*centre(c, tgt))
    c.page.keyboard.up("Control")
    sel = c.page.evaluate("async () => { const s = await import('/static/js/store.js'); return [...s.state.selection]; }")
    assert sorted(sel) == sorted([src, tgt])
    assert tag_names(c, tgt) == []


def test_ctrl_drag_from_an_untagged_item_onto_a_bar_explains_instead_of_copying(pair):
    c, src, tgt = pair
    ctrl_drag(c, tgt, src)
    assert "no tags to copy" in c.page.inner_text(".status__notice")
    assert tag_names(c, src) == ["Spares", "PRJ-20", "Cell 4"]


def blank_point(c, avoid_ids):
    """A point on empty chart space: below every bar, in the plot area."""
    box = c.page.locator("#timeline-host canvas").bounding_box()
    ys = [c.page.evaluate("id => window.__reframe.barRect(id)", i) for i in avoid_ids]
    return box["x"] + box["width"] * 0.55, max(r["y"] + r["h"] for r in ys) + 70


def all_items(c):
    return c.api("GET", "/api/items")["items"]


def test_ctrl_drag_to_blank_space_duplicates_the_item_and_starts_renaming(pair):
    c, src, tgt = pair
    c.page.evaluate("async () => { const s = await import('/static/js/store.js'); s.setGroupByTag(false); }")
    c.page.wait_for_timeout(300)
    before = len(all_items(c))
    x, y = blank_point(c, [src, tgt])
    ctrl_drag(c, src, tgt, release_on=(x, y))
    for _ in range(50):
        if len(all_items(c)) == before + 1:
            break
        c.page.wait_for_timeout(100)
    copies = [i for i in all_items(c) if i["id"] not in (src, tgt)]
    assert len(copies) == 1
    cp = copies[0]
    assert cp["title"] == "New item" and cp["tags"] == next(i for i in all_items(c) if i["id"] == src)["tags"]
    length = lambda i: (dt.date.fromisoformat(i["end_date"]) - dt.date.fromisoformat(i["start_date"])).days
    assert length(cp) == 4                                           # same length as the source (day+1 .. day+5)
    assert cp["start_date"] != day(1)                                # it landed where it was dropped
    inp = c.page.locator(".rf-title-input")                          # straight into naming it
    inp.wait_for(state="visible", timeout=5000)
    inp.fill("Second install")
    inp.press("Enter")
    for _ in range(50):
        if c.api("GET", f"/api/items/{cp['id']}")["title"] == "Second install":
            break
        c.page.wait_for_timeout(100)
    assert c.api("GET", f"/api/items/{cp['id']}")["title"] == "Second install"
    assert tag_names(c, src) == ["Spares", "PRJ-20", "Cell 4"]       # the source is untouched


def test_duplicate_works_for_an_untagged_item_and_is_one_undo(pair):
    c, src, tgt = pair
    c.page.evaluate("async () => { const s = await import('/static/js/store.js'); s.setGroupByTag(false); }")
    c.page.wait_for_timeout(300)
    before = len(all_items(c))
    x, y = blank_point(c, [src, tgt])
    ctrl_drag(c, tgt, src, release_on=(x, y))
    for _ in range(50):
        if len(all_items(c)) == before + 1:
            break
        c.page.wait_for_timeout(100)
    assert len(all_items(c)) == before + 1
    c.page.locator(".rf-title-input").press("Escape")
    c.focus_timeline()
    c.page.keyboard.press("Control+z")
    for _ in range(50):
        if len(all_items(c)) == before:
            break
        c.page.wait_for_timeout(100)
    assert len(all_items(c)) == before


def test_escape_cancels_a_duplicate_drag(pair):
    c, src, tgt = pair
    c.page.evaluate("async () => { const s = await import('/static/js/store.js'); s.setGroupByTag(false); }")
    c.page.wait_for_timeout(300)
    before = len(all_items(c))
    x, y = blank_point(c, [src, tgt])
    ctrl_drag(c, src, tgt, release_on=(x, y), escape=True)
    c.page.wait_for_timeout(600)
    assert len(all_items(c)) == before


def test_filter_tag_chips_wrap_instead_of_scrolling(chrome):
    page = chrome.page
    page.set_viewport_size({"width": 900, "height": 800})
    page.wait_for_timeout(400)
    m = page.evaluate("""() => { const c = document.querySelector('.filter-bar__chips');
        const tops = new Set([...c.children].map((e) => Math.round(e.getBoundingClientRect().top)));
        return { overflowX: getComputedStyle(c).overflowX, scrollW: c.scrollWidth, clientW: c.clientWidth, lines: tops.size }; }""")
    assert m["overflowX"] != "auto" and m["overflowX"] != "scroll"
    assert m["scrollW"] <= m["clientW"] + 1                         # nothing hidden off to the side
    assert m["lines"] >= 2                                           # the chips really wrapped onto several lines
    box = page.locator("#timeline-host canvas").bounding_box()      # the chart still fits below the taller bar
    assert box["height"] > 200


def test_minimap_bars_are_the_plain_tag_colour_with_no_border(chrome_factory):
    c = chrome_factory(0)
    c.api("POST", "/api/items", {"title": "Wide", "start_date": day(200), "end_date": day(260), "tags": ["Alpha"]})
    c.open()
    c.page.wait_for_timeout(800)
    hue = {t["name"]: t["slot"] for t in c.api("GET", "/api/tags")["tags"]}["Alpha"] % 8
    r = c.page.evaluate("""(hue) => {
        const css = getComputedStyle(document.documentElement);
        const rgb = (n) => { const h = css.getPropertyValue(n).trim().replace('#', ''); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); };
        const cv = document.querySelector('#minimap-host canvas');
        const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
        const want = rgb('--tag-' + hue).join(','), halo = rgb('--minimap-bar').join(',');
        let tag = 0, edge = 0;
        for (let i = 0; i < d.length; i += 4) {
          const k = d[i] + ',' + d[i + 1] + ',' + d[i + 2];
          if (k === want) tag++;
          if (k === halo) edge++;
        }
        return { tag, edge };
    }""", hue)
    assert r["tag"] > 50, r                  # the bar is drawn in the tag colour
    assert r["edge"] == 0, r                 # and nothing is drawn around it in a generic colour


def test_grouped_minimap_does_not_draw_horizontal_dividers(chrome_factory):
    c = chrome_factory(0)
    for tag in ["Alpha", "Beta", "Gamma"]:
        c.api("POST", "/api/items", {"title": tag, "start_date": day(10), "end_date": day(20), "tags": [tag]})
    c.open()
    result = c.page.evaluate("""async () => {
        const s = await import('/static/js/store.js');
        const bus = await import('/static/js/layout_bus.js');
        const canvas = document.querySelector('#minimap-host canvas');
        const ctx = canvas.getContext('2d'), original = ctx.fillRect;
        const bars = [];
        ctx.fillRect = function(x, y, w, h) {
          if (Math.abs(w - (canvas.clientWidth - 16)) < 0.1 && h <= 1 / devicePixelRatio) bars.push([x, y, w, h]);
          return original.call(this, x, y, w, h);
        };
        s.setGroupByTag(false);
        await new Promise(requestAnimationFrame);
        s.setGroupByTag(true);
        await new Promise(requestAnimationFrame);
        await new Promise(requestAnimationFrame);
        ctx.fillRect = original;
        return { lanes: bus.currentLayout().lanes.length, bars };
    }""")
    assert result["lanes"] >= 3
    assert result["bars"] == []


def test_keyboard_copy_and_paste_tags(pair):
    c, src, tgt = pair
    page = c.page
    page.mouse.click(*centre(c, src))
    page.keyboard.press("Control+Alt+c")
    assert "Copied 3 tags" in page.inner_text(".status__notice")
    page.mouse.click(*centre(c, tgt))
    page.keyboard.press("Control+Alt+v")
    for _ in range(50):
        if tag_names(c, tgt):
            break
        page.wait_for_timeout(100)
    assert tag_names(c, tgt) == ["Spares", "PRJ-20", "Cell 4"]


def test_keyboard_and_context_menu_duplicate(pair):
    c, src, tgt = pair
    page = c.page
    before = len(all_items(c))
    page.mouse.click(*centre(c, src))
    page.keyboard.press("Control+Shift+d")
    page.locator(".rf-title-input").wait_for(state="visible", timeout=5000)
    page.keyboard.press("Escape")
    for _ in range(50):
        if len(all_items(c)) == before + 1:
            break
        page.wait_for_timeout(100)
    copy = next(i for i in all_items(c) if i["id"] not in (src, tgt))
    assert (copy["start_date"], copy["end_date"]) == (day(1), day(5))        # same days as the source
    assert len(copy["tags"]) == 3
    page.mouse.click(*centre(c, tgt))
    page.mouse.click(*centre(c, tgt), button="right")
    page.get_by_role("menuitem", name="Duplicate").click()
    for _ in range(50):
        if len(all_items(c)) == before + 2:
            break
        page.wait_for_timeout(100)
    assert len(all_items(c)) == before + 2


def test_context_menu_offers_copy_and_paste_tags(pair):
    c, src, tgt = pair
    page = c.page
    page.mouse.click(*centre(c, src))
    page.mouse.click(*centre(c, src), button="right")
    page.get_by_role("menuitem", name="Copy tags").click()
    page.mouse.click(*centre(c, tgt))
    page.mouse.click(*centre(c, tgt), button="right")
    page.get_by_role("menuitem", name="Paste tags from 'Source'").click()
    for _ in range(50):
        if tag_names(c, tgt):
            break
        page.wait_for_timeout(100)
    assert tag_names(c, tgt) == ["Spares", "PRJ-20", "Cell 4"]


def test_group_by_tag_is_the_default_and_the_choice_persists(chrome_factory):
    c = chrome_factory(40)
    assert c.page.get_attribute("#tb-groupByTag", "aria-pressed") == "true"
    c.page.click("#tb-groupByTag")                  # switching it off is remembered across a reload
    c.page.wait_for_timeout(1200)
    c.open()
    assert c.page.get_attribute("#tb-groupByTag", "aria-pressed") == "false"


def test_minimap_bars_use_the_same_colours_as_the_chart(chrome_factory):
    c = chrome_factory(0)
    for i, tag in enumerate(["Alpha", "Bravo", "Charlie"]):      # far from today, so outside the frame's tint
        c.api("POST", "/api/items", {"title": f"Item {tag}", "start_date": day(200 + i * 30), "end_date": day(215 + i * 30), "tags": [tag]})
    c.open()
    c.page.wait_for_timeout(800)
    slots = {t["name"]: t["slot"] for t in c.api("GET", "/api/tags")["tags"]}
    hues = sorted({slots[n] % 8 for n in ("Alpha", "Bravo", "Charlie")})
    assert len(hues) == 3
    result = c.page.evaluate("""(hues) => {
        const css = getComputedStyle(document.documentElement);
        const rgb = (name) => { const h = css.getPropertyValue(name).trim().replace('#', '');
            return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)).join(','); };
        const cv = document.querySelector('#minimap-host canvas');
        const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
        const seen = new Set();
        for (let i = 0; i < d.length; i += 4) seen.add(d[i] + ',' + d[i + 1] + ',' + d[i + 2]);
        return { tags: hues.map((n) => rgb('--tag-' + n)), seen: [...seen] };
    }""", hues)
    for colour in result["tags"]:
        assert colour in result["seen"], f"minimap is missing the tag colour {colour}"


def test_minimap_histogram_is_coloured_by_tag_too(chrome_factory):
    c = chrome_factory(900)          # above the bar limit, so the minimap draws the density histogram
    c.page.wait_for_timeout(800)
    result = c.page.evaluate("""() => {
        const css = getComputedStyle(document.documentElement);
        const rgb = (name) => { const h = css.getPropertyValue(name).trim().replace('#', '');
            return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)).join(','); };
        const cv = document.querySelector('#minimap-host canvas');
        const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
        const seen = new Set();
        for (let i = 0; i < d.length; i += 4) seen.add(d[i] + ',' + d[i + 1] + ',' + d[i + 2]);
        return { tags: [0, 1, 2, 3, 4, 5, 6, 7].map((n) => rgb('--tag-' + n)), seen: [...seen] };
    }""")
    assert sum(1 for t in result["tags"] if t in result["seen"]) >= 3


def test_settings_tab_has_the_tray_option_and_it_persists(chrome):
    page = chrome.page
    chrome.focus_timeline()
    page.keyboard.press("Control+Shift+h")
    page.click("#tab-settings")
    box = page.locator("#set-tray-on-minimize")
    box.wait_for()
    assert box.is_checked()                          # on by default
    box.uncheck()
    chrome.wait("() => fetch('/api/settings').then(r => r.json()).then(s => s.tray_on_minimize === false)")
    assert chrome.api("GET", "/api/settings")["tray_on_minimize"] is False
