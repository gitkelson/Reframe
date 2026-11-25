import sqlite3

import pytest
from reframe.db.changelog import writer
from reframe.domain import items, tags


def make(conn, w, title="A", start="2026-12-01", end=None, **kw):
    return w(lambda: items.create_item(conn, title=title, start=start, end=end, **kw))


# ------------------------------------------------------------------ tags

def test_variants_resolve_to_one_tag(conn, w):
    ids = set()
    for name in ("Spares", "spares", "SPARES!", " spares "):
        ids.add(w(lambda n=name: tags.get_or_create_tag(conn, n)[0]))
    assert len(ids) == 1
    assert conn.execute("SELECT count(*) FROM tag").fetchone()[0] == 1


def test_plurals_word_order_typos_not_merged_silently(conn, w):
    for name in ("Spares", "Spare", "Order Spares", "Spares Order", "Sparse"):
        w(lambda n=name: tags.get_or_create_tag(conn, n))
    assert conn.execute("SELECT count(*) FROM tag").fetchone()[0] == 5


def test_near_match_suggested_and_helper_refuses(conn, w):
    w(lambda: tags.get_or_create_tag(conn, "Spares Order"))
    s = tags.suggestions(conn, "Spares Ordr")
    assert [t["name"] for t in s["near"]] == ["Spares Order"]
    s = tags.suggestions(conn, "Order Spares")
    assert [t["name"] for t in s["near"]] == ["Spares Order"]
    with pytest.raises(tags.NearTagMatch):
        w(lambda: tags.get_or_create_tag(conn, "Spares Ordr", allow_near=False))
    assert conn.execute("SELECT count(*) FROM tag").fetchone()[0] == 1


def test_autocomplete_ranking_prefix_before_fuzzy(conn, w):
    for n in ("Spares", "Sparrow", "Shipping", "Super Spares"):
        w(lambda n=n: tags.get_or_create_tag(conn, n))
    names = [t["name"] for t in tags.suggestions(conn, "spa")["matches"]]
    assert names[0] == "Spares" and "Shipping" not in names
    assert tags.suggestions(conn, "SPARES!")["exact"]["name"] == "Spares"


def test_auto_slots_are_distinct_first(conn, w):
    for n in "ABCDEFGH":
        w(lambda n=n: tags.get_or_create_tag(conn, "tag" + n))
    assert sorted(t["slot"] for t in tags.list_tags(conn)) == list(range(8))


def test_rename_conflict_and_merge_delete(conn, w):
    a = make(conn, w, "A", tags=["Alpha"])
    b = make(conn, w, "B", tags=["Beta", "Alpha"])
    ids = {t["name"]: t["id"] for t in tags.list_tags(conn)}
    with pytest.raises(ValueError):
        w(lambda: tags.rename_tag(conn, ids["Beta"], "ALPHA"))
    w(lambda: tags.merge_tags(conn, ids["Beta"], ids["Alpha"]), "merge")
    assert [t["name"] for t in items.get_item(conn, b)["tags"]] == ["Alpha"]
    with pytest.raises(ValueError):
        w(lambda: tags.delete_tag(conn, ids["Alpha"]))
    w(lambda: tags.delete_tag(conn, ids["Alpha"], force=True))
    assert items.get_item(conn, a)["tags"] == []


def test_hygiene_lists_pairs_created_by_agent_sql(conn):
    conn.execute("INSERT INTO tag (name) VALUES ('Spares Order')")
    conn.execute("INSERT INTO tag (name) VALUES ('Spares Orders')")
    conn.execute("INSERT INTO tag (name) VALUES ('Totally Different')")
    pairs = tags.hygiene(conn)
    assert len(pairs) == 1 and {pairs[0]["a"]["name"], pairs[0]["b"]["name"]} == {"Spares Order", "Spares Orders"}


# ----------------------------------------------------------------- dates

@pytest.mark.parametrize("text,iso", [("2026-11-08", "2026-11-08"), ("20261108", "2026-11-08"), (" 2026-11-08 ", "2026-11-08")])
def test_parse_date_ok(text, iso):
    assert items.parse_date(text) == iso


@pytest.mark.parametrize("text", ["11/08/2026", "2026-13-01", "2026-02-30", "tomorrow", "2026-1-8", ""])
def test_parse_date_rejects(text):
    with pytest.raises(items.DateError):
        items.parse_date(text)


# ----------------------------------------------------------------- items

def test_create_defaults_one_day_and_inclusive_end(conn, w):
    i = make(conn, w, start="20261201")
    it = items.get_item(conn, i)
    assert it["start_date"] == it["end_date"] == "2026-12-01" and it["kind"] == "plan" and it["origin"] == "local"


def test_update_only_changed_columns(conn, w):
    i = make(conn, w, notes="n")
    assert w(lambda: items.update_item(conn, i, title="A", notes="n")) == []
    assert w(lambda: items.update_item(conn, i, title="New")) == ["title"]
    ch = conn.execute("SELECT before_json, after_json FROM change WHERE op='update'").fetchone()
    assert '"title":"A"' in ch[0] and '"title":"New"' in ch[1]


def test_update_rejects_end_before_start(conn, w):
    i = make(conn, w, start="2026-12-05", end="2026-12-06")
    with pytest.raises(items.DateError):
        w(lambda: items.update_item(conn, i, end_date="2026-12-01"))


def test_soft_delete_hides_from_fetch_and_restores(conn, w):
    i = make(conn, w)
    w(lambda: items.delete_items(conn, [i]))
    assert items.fetch_items(conn) == []
    w(lambda: items.restore_item(conn, i))
    assert len(items.fetch_items(conn)) == 1


def test_fetch_items_range_and_tag_order(conn, w):
    make(conn, w, "In", "2026-12-01", "2026-12-10", tags=["Zed", "Alpha"])
    make(conn, w, "Out", "2027-03-01")
    r = items.fetch_items(conn, "2026-12-05", "2026-12-20")
    assert [x["title"] for x in r] == ["In"]
    names = [t["name"] for t in items.get_item(conn, r[0]["id"])["tags"]]
    assert names == ["Zed", "Alpha"]  # first = primary color tag


def test_fetch_excludes_rows_with_bad_dates_and_needs_attention_lists_them(conn):
    conn.execute("PRAGMA ignore_check_constraints=ON")
    conn.execute("INSERT INTO item (id,title,start_date,end_date) VALUES ('bad','Bad','11/08/2026','11/09/2026')")
    conn.execute("PRAGMA ignore_check_constraints=OFF")
    assert items.fetch_items(conn) == []
    assert items.needs_attention(conn)["bad_dates"][0]["id"] == "bad"


def test_move_items_bulk_one_change_set_and_relative_spacing(conn, w):
    a = make(conn, w, "A", "2026-12-01", "2026-12-03")
    b = make(conn, w, "B", "2026-12-10")
    n_before = conn.execute("SELECT count(*) FROM change_set").fetchone()[0]
    res = w(lambda: items.move_items(conn, [a, b], 3), "move")
    assert res == {"moved": 2, "skipped": 0}
    assert conn.execute("SELECT count(*) FROM change_set").fetchone()[0] == n_before + 1
    ga, gb = items.get_item(conn, a), items.get_item(conn, b)
    assert (ga["start_date"], ga["end_date"], gb["start_date"]) == ("2026-12-04", "2026-12-06", "2026-12-13")


def _imported(conn, uid="u1", series=None, title="Mtg", day="2026-12-01"):
    with writer(conn, "sync", "import"):
        iid = conn.execute(
            "INSERT INTO item (title,start_date,end_date,origin, source_id,outlook_uid,outlook_series_id) "
            "VALUES (?,?,?,'outlook', 'testcal',?,?) RETURNING id", (title, day, day, uid, series)).fetchone()[0]
        items.add_system_tag(conn, iid, "meeting")
    return iid


def test_imported_items_locked_move_delete_skipped_tags_editable(conn, w):
    o = _imported(conn)
    with pytest.raises(items.LockedItem):
        w(lambda: items.update_item(conn, o, title="x"))
    with pytest.raises(items.LockedItem):
        w(lambda: items.update_item(conn, o, start_date="2026-12-02", end_date="2026-12-02"))
    assert w(lambda: items.move_items(conn, [o], 2)) == {"moved": 0, "skipped": 1}
    assert w(lambda: items.delete_items(conn, [o])) == {"deleted": 0, "skipped": 1}
    w(lambda: items.update_item(conn, o, notes="hello"))
    w(lambda: items.set_item_tags(conn, o, ["Project X", "meeting"]))
    w(lambda: items.add_attachment(conn, o, "https://x.example"))
    d = items.get_item(conn, o)
    assert d["notes"] == "hello" and [t["name"] for t in d["tags"]] == ["Project X", "meeting"]


def test_system_tags_sort_after_user_tags(conn, w):
    o = _imported(conn)
    w(lambda: items.set_item_tags(conn, o, ["meeting", "Alpha"]))
    pos = {t["name"]: t["position"] for t in items.get_item(conn, o)["tags"]}
    assert pos["Alpha"] < 1000 <= pos["meeting"]
    assert items.get_item(conn, o)["tags"][0]["name"] == "Alpha"


def test_series_tags_apply_to_whole_series_and_new_occurrences(conn, w):
    o1 = _imported(conn, "u1", "cal|S1", day="2026-12-01")
    o2 = _imported(conn, "u2", "cal|S1", day="2026-12-08")
    lone = _imported(conn, "u3", None, day="2026-12-03")
    w(lambda: items.set_item_tags(conn, o1, ["Weekly Sync", "meeting"]))
    assert [t["name"] for t in items.get_item(conn, o2)["tags"]] == ["Weekly Sync", "meeting"]
    assert [t["name"] for t in items.get_item(conn, lone)["tags"]] == ["meeting"]
    assert conn.execute("SELECT count(*) FROM outlook_series_tag").fetchone()[0] == 2   # owner's full order (R-114)
    with writer(conn, "sync", "new occurrence"):
        o3 = conn.execute("INSERT INTO item (title,start_date,end_date,origin, source_id,outlook_uid,outlook_series_id) "
                          "VALUES ('Mtg','2026-12-15','2026-12-15','outlook', 'testcal','u4','cal|S1') RETURNING id").fetchone()[0]
        items.apply_series_tags(conn, o3, "cal|S1")
    assert [t["name"] for t in items.get_item(conn, o3)["tags"]] == ["Weekly Sync", "meeting"]
    w(lambda: items.set_item_tags(conn, o2, ["meeting"]))   # remove from one => removed from all
    assert [t["name"] for t in items.get_item(conn, o1)["tags"]] == ["meeting"]
    assert [r[0] for r in conn.execute("SELECT t.name FROM outlook_series_tag s JOIN tag t ON t.id=s.tag_id")] == ["meeting"]


# ------------------------------------------------------------ R-114: any tag can become the primary tag

def test_reorder_of_system_implied_and_series_tags_persists(conn, w):
    """Regression: tags stored at position >= 1000 (system tags) snapped back when dragged first."""
    o = _imported(conn)
    w(lambda: items.set_item_tags(conn, o, ["Project X", "meeting"]))
    w(lambda: items.set_item_tags(conn, o, ["meeting", "Project X"]))          # drag the system tag first
    assert [t["name"] for t in items.get_item(conn, o)["tags"]] == ["meeting", "Project X"]
    # implied group tag dragged first stays implied and first
    def group():
        g, _ = tags.get_or_create_tag(conn, "Customer")
        c, _ = tags.get_or_create_tag(conn, "Job 12")
        tags.move_tag(conn, c, g)
    w(group)
    a = make(conn, w, "A", tags=["Job 12"])
    assert [(t["name"], t["implied"]) for t in items.get_item(conn, a)["tags"]] == [("Job 12", 0), ("Customer", 1)]
    w(lambda: items.set_item_tags(conn, a, ["Customer", "Job 12"]))
    assert [(t["name"], t["implied"]) for t in items.get_item(conn, a)["tags"]] == [("Customer", 1), ("Job 12", 0)]
    # a reorder on one series occurrence applies to every occurrence, and to new ones
    s1 = _imported(conn, "s1", "cal|S9")
    s2 = _imported(conn, "s2", "cal|S9", day="2026-12-08")
    w(lambda: items.set_item_tags(conn, s1, ["Project X", "meeting"]))
    w(lambda: items.set_item_tags(conn, s2, ["meeting", "Project X"]))
    assert [t["name"] for t in items.get_item(conn, s1)["tags"]] == ["meeting", "Project X"]
    with writer(conn, "sync", "new occurrence"):
        s3 = conn.execute("INSERT INTO item (title,start_date,end_date,origin,source_id,outlook_uid,outlook_series_id) "
                          "VALUES ('Mtg','2026-12-15','2026-12-15','outlook','testcal','s3','cal|S9') RETURNING id").fetchone()[0]
        items.add_system_tag(conn, s3, "meeting")
        items.apply_series_tags(conn, s3, "cal|S9")
    assert [t["name"] for t in items.get_item(conn, s3)["tags"]] == ["meeting", "Project X"]


# ------------------------------------------------------------ tag tree (R-110 .. R-113)

def _tree(conn, w):
    def run():
        p, _ = tags.get_or_create_tag(conn, "Projects")
        b, _ = tags.get_or_create_tag(conn, "ClientA")
        m, _ = tags.get_or_create_tag(conn, "Line 2")
        tags.move_tag(conn, b, p)
        tags.move_tag(conn, m, b)
        return p, b, m
    return w(run)


def _names(conn, iid):
    return [(t["name"], t["implied"]) for t in items.get_item(conn, iid)["tags"]]


def test_applying_a_tag_adds_its_groups_as_implied_and_removal_drops_them(conn, w):
    _tree(conn, w)
    a = make(conn, w, "A", tags=["Spares", "Line 2"])
    assert _names(conn, a) == [("Spares", 0), ("Line 2", 0), ("ClientA", 1), ("Projects", 1)]
    w(lambda: items.set_item_tags(conn, a, ["Spares", "ClientA", "Projects"]))
    assert _names(conn, a) == [("Spares", 0)]


def test_explicit_ancestor_is_kept_and_required_implied_tag_cannot_be_removed(conn, w):
    _tree(conn, w)
    a = make(conn, w, "A", tags=["ClientA", "Line 2"])
    assert ("ClientA", 0) in _names(conn, a)
    w(lambda: items.set_item_tags(conn, a, ["ClientA", "Projects"]))       # child removed, explicit group stays
    assert _names(conn, a) == [("ClientA", 0), ("Projects", 1)]
    b = make(conn, w, "B", tags=["Line 2"])
    with pytest.raises(items.TagRequired, match="Remove Line 2 first"):
        w(lambda: items.set_item_tags(conn, b, ["Line 2", "Projects"]))


def test_raw_sql_insert_gets_ancestors_and_raw_delete_of_required_group_is_refused(conn, w):
    _, _, m = _tree(conn, w)
    a = make(conn, w, "A")
    conn.execute("INSERT INTO item_tag (item_id, tag_id) VALUES (?, ?)", (a, m))   # agent, no protocol
    assert {n for n, _ in _names(conn, a)} == {"Line 2", "ClientA", "Projects"}
    clienta = tags.find_tag(conn, "ClientA")["id"]
    with pytest.raises(sqlite3.IntegrityError, match="Remove the descendant tag first"):
        conn.execute("DELETE FROM item_tag WHERE item_id=? AND tag_id=?", (a, clienta))


def test_cycles_are_rejected(conn, w):
    p, b, m = _tree(conn, w)
    with pytest.raises(ValueError):
        w(lambda: tags.move_tag(conn, p, m))
    with pytest.raises(sqlite3.IntegrityError, match="cannot contain itself"):
        conn.execute("UPDATE tag SET parent_id=? WHERE id=?", (m, p))


def test_move_tag_updates_items_as_one_change_set_and_one_undo(conn, w):
    from reframe.db import undo as undomod
    p, b, m = _tree(conn, w)
    a = make(conn, w, "A", tags=["Line 2"])
    c = make(conn, w, "C", tags=["ClientA", "Line 2"])
    def other():
        return tags.get_or_create_tag(conn, "Customers")[0]
    cust = w(other)
    n_sets = conn.execute("SELECT count(*) FROM change_set").fetchone()[0]
    assert w(lambda: tags.move_tag(conn, b, cust), "Move tag 'ClientA' to 'Customers'") == 2
    assert conn.execute("SELECT count(*) FROM change_set").fetchone()[0] == n_sets + 1
    assert {n for n, _ in _names(conn, a)} == {"Line 2", "ClientA", "Customers"}
    assert ("ClientA", 0) in _names(conn, c)                                  # explicit tags never removed
    logged = conn.execute("SELECT count(*) FROM change WHERE change_set_id=(SELECT max(id) FROM change_set) "
                          "AND table_name='item_tag'").fetchone()[0]
    assert logged >= 4                                                       # trigger-generated rows are logged
    undomod.undo(conn)
    assert {n for n, _ in _names(conn, a)} == {"Line 2", "ClientA", "Projects"}
    assert tags.implied_drift(conn) == {"missing": [], "extra": []}
    undomod.redo(conn)
    assert {n for n, _ in _names(conn, a)} == {"Line 2", "ClientA", "Customers"}


def test_retired_tags_hidden_from_autocomplete_refused_but_kept_on_items(conn, w):
    a = make(conn, w, "A", tags=["Old project"])
    tid = tags.find_tag(conn, "Old project")["id"]
    w(lambda: tags.set_retired(conn, tid, True))
    assert not tags.suggestions(conn, "old proj")["matches"]
    s = tags.suggestions(conn, "Old project")
    assert not s["matches"] and s["exact"] is None and s["retired"]["name"] == "Old project"
    assert [t["name"] for t in items.get_item(conn, a)["tags"]] == ["Old project"]
    b = make(conn, w, "B")
    with pytest.raises(tags.RetiredTag):
        w(lambda: items.set_item_tags(conn, b, ["Old project"]))
    w(lambda: items.set_item_tags(conn, a, ["Old project", "New"]))          # already there: stays
    w(lambda: tags.set_retired(conn, tid, False))
    w(lambda: items.set_item_tags(conn, b, ["Old project"]))


def test_chip_drop_appends_without_changing_primary_and_reports_already(conn, w):
    a = make(conn, w, "A", tags=["First"])
    assert w(lambda: items.add_tag(conn, [a], "Second")) == {"added": 1, "already": 0}
    assert w(lambda: items.add_tag(conn, [a], "second")) == {"added": 0, "already": 1}
    assert [t["name"] for t in items.get_item(conn, a)["tags"]] == ["First", "Second"]
    o = _imported(conn, "q1", "cal|Q")
    o2 = _imported(conn, "q2", "cal|Q", day="2026-12-08")
    w(lambda: items.add_tag(conn, [o], "Proj"))
    assert [t["name"] for t in items.get_item(conn, o2)["tags"]] == ["meeting", "Proj"]


def test_merge_and_delete_group_tag_reparent_children(conn, w):
    p, b, m = _tree(conn, w)
    a = make(conn, w, "A", tags=["Line 2"])
    w(lambda: tags.delete_tag(conn, b, force=True))
    assert conn.execute("SELECT parent_id FROM tag WHERE id=?", (m,)).fetchone()[0] == p
    assert {n for n, _ in _names(conn, a)} == {"Line 2", "Projects"}
    q = w(lambda: tags.get_or_create_tag(conn, "Programs")[0])
    w(lambda: tags.merge_tags(conn, p, q))
    assert {n for n, _ in _names(conn, a)} == {"Line 2", "Programs"}
    assert tags.implied_drift(conn) == {"missing": [], "extra": []}


def test_series_tag_change_is_one_undoable_set(conn, w):
    from reframe.db import undo as undomod
    o1 = _imported(conn, "u1", "cal|S1")
    o2 = _imported(conn, "u2", "cal|S1", day="2026-12-08")
    w(lambda: items.set_item_tags(conn, o1, ["X", "meeting"]), "tag series")
    undomod.undo(conn)
    assert [t["name"] for t in items.get_item(conn, o2)["tags"]] == ["meeting"]


def test_bulk_tag_edit_includes_locked_items(conn, w):
    a = make(conn, w, "A", tags=["Keep"])
    o = _imported(conn)
    n = w(lambda: items.edit_tags_bulk(conn, [a, o], add=["Bulk"]))
    assert n == 2
    assert "Bulk" in [t["name"] for t in items.get_item(conn, o)["tags"]]
    n = w(lambda: items.edit_tags_bulk(conn, [a, o], remove=["bulk"]))
    assert n == 2 and [t["name"] for t in items.get_item(conn, a)["tags"]] == ["Keep"]


def test_hide_item_and_series(conn, w):
    o1 = _imported(conn, "u1", "cal|S1")
    o2 = _imported(conn, "u2", "cal|S1", day="2026-12-08")
    assert w(lambda: items.hide_item(conn, o1)) == 1
    assert [i["id"] for i in items.fetch_items(conn)] == [o2]
    assert w(lambda: items.hide_item(conn, o2, series=True)) == 1
    assert items.fetch_items(conn) == []
    assert conn.execute("SELECT count(*) FROM outlook_hidden_series").fetchone()[0] == 1
    w(lambda: items.unhide_item(conn, o1, series=True))
    assert len(items.fetch_items(conn)) == 2
    local = make(conn, w)
    with pytest.raises(ValueError):
        w(lambda: items.hide_item(conn, local))


def test_attachments_kind_detection(conn, w):
    i = make(conn, w)
    for t, k in (("https://a.com/x", "url"), ("C:\\Docs\\a.pdf", "file"), ("\\\\srv\\share\\a.xlsx", "file"),
                 ("mailto:a@b.com", "url")):
        aid = w(lambda t=t: items.add_attachment(conn, i, t))
        assert conn.execute("SELECT kind FROM attachment WHERE id=?", (aid,)).fetchone()[0] == k


def test_find_duplicates(conn, w):
    make(conn, w, "Spares order due", "2026-12-01", "2026-12-03")
    make(conn, w, "Unrelated", "2026-12-02")
    d = items.find_duplicates(conn, "Spares order due!", "2026-12-02")
    assert [x["title"] for x in d] == ["Spares order due"]
    assert items.find_duplicates(conn, "Spares order due", "2027-06-01") == []


def test_copy_tags_to_untagged_item_gives_identical_tags_and_primary(conn, w):
    src = make(conn, w, "Source", tags=["PRJ-20", "Spares", "Cell 4"])
    new = make(conn, w, "Brand new")
    res = w(lambda: items.copy_tags(conn, src, [new]), "copy")
    assert res == {"changed": 1, "skipped": 0, "tag_count": 3}
    assert [t["name"] for t in items.get_item(conn, new)["tags"]] == ["PRJ-20", "Spares", "Cell 4"]


def test_copy_tags_keeps_target_tags_first_and_skips_duplicates(conn, w):
    src = make(conn, w, "Source", tags=["A", "B", "C"])
    tgt = make(conn, w, "Target", tags=["Mine", "B"])
    w(lambda: items.copy_tags(conn, src, [tgt]))
    assert [t["name"] for t in items.get_item(conn, tgt)["tags"]] == ["Mine", "B", "A", "C"]
    # nothing new to add: no change, counted as skipped
    assert w(lambda: items.copy_tags(conn, src, [tgt])) == {"changed": 0, "skipped": 1, "tag_count": 3}


def test_copy_tags_is_one_change_set_one_undo_for_several_targets(conn, w):
    from reframe.db import undo as undomod
    src = make(conn, w, "Source", tags=["A", "B"])
    t1, t2 = make(conn, w, "T1"), make(conn, w, "T2")
    before = conn.execute("SELECT count(*) FROM change_set").fetchone()[0]
    w(lambda: items.copy_tags(conn, src, [t1, t2, src]), "copy")        # copying onto itself is ignored
    assert conn.execute("SELECT count(*) FROM change_set").fetchone()[0] == before + 1
    undomod.undo(conn)
    assert items.get_item(conn, t1)["tags"] == [] and items.get_item(conn, t2)["tags"] == []


def test_copy_tags_source_without_tags_is_an_error(conn, w):
    src, tgt = make(conn, w, "Source"), make(conn, w, "Target")
    with pytest.raises(ValueError, match="no tags"):
        w(lambda: items.copy_tags(conn, src, [tgt]))


def test_copy_tags_onto_imported_series_applies_to_whole_series(conn, w):
    src = make(conn, w, "Source", tags=["Weekly Project"])
    o1 = _imported(conn, "u1", "cal|S1")
    o2 = _imported(conn, "u2", "cal|S1", day="2026-12-08")
    w(lambda: items.copy_tags(conn, src, [o1]))
    assert [t["name"] for t in items.get_item(conn, o2)["tags"]] == ["Weekly Project", "meeting"]
    assert items.get_item(conn, o1)["title"] == "Mtg"            # core fields untouched


def test_copy_tags_includes_the_sources_system_tags_as_ordinary_tags(conn, w):
    o = _imported(conn, "u1")                                      # carries the system tag 'meeting'
    tgt = make(conn, w, "Local")
    w(lambda: items.copy_tags(conn, o, [tgt]))
    assert [t["name"] for t in items.get_item(conn, tgt)["tags"]] == ["meeting"]


def test_duplicate_item_copies_everything_but_the_title(conn, w):
    src = make(conn, w, "Install window", "2026-12-01", "2026-12-04", kind="log", notes="context here",
               tags=["PRJ-20", "Spares"], attachments=[{"target": "C:\\docs\\plan.xlsx", "label": "Plan"}])
    new = w(lambda: items.duplicate_item(conn, src, "20261215"), "dup")
    d = items.get_item(conn, new)
    assert (d["start_date"], d["end_date"]) == ("2026-12-15", "2026-12-18")        # same 4-day length
    assert (d["kind"], d["notes"], d["origin"]) == ("log", "context here", "local")
    assert d["title"] == "New item"                                               # the owner names it next
    assert [t["name"] for t in d["tags"]] == ["PRJ-20", "Spares"]
    assert [(a["target"], a["label"]) for a in d["attachments"]] == [("C:\\docs\\plan.xlsx", "Plan")]
    assert items.get_item(conn, src)["title"] == "Install window"                  # source untouched


def test_duplicate_is_one_change_set_and_one_undo(conn, w):
    from reframe.db import undo as undomod
    src = make(conn, w, "A", tags=["X"], attachments=[{"target": "https://e.com"}])
    before = conn.execute("SELECT count(*) FROM change_set").fetchone()[0]
    w(lambda: items.duplicate_item(conn, src, "2026-12-10"), "dup")
    assert conn.execute("SELECT count(*) FROM change_set").fetchone()[0] == before + 1
    undomod.undo(conn)
    assert [i["id"] for i in items.fetch_items(conn)] == [src]
    assert conn.execute("SELECT count(*) FROM attachment").fetchone()[0] == 1


def test_duplicate_of_an_imported_item_is_a_local_editable_item(conn, w):
    o = _imported(conn, "u1", "cal|S1")
    new = w(lambda: items.duplicate_item(conn, o, "2026-12-20"))
    d = items.get_item(conn, new)
    assert d["origin"] == "local" and d["outlook_uid"] is None and d["outlook_series_id"] is None
    assert [t["name"] for t in d["tags"]] == ["meeting"]
    w(lambda: items.update_item(conn, new, title="Renamed"))                       # not locked


def test_duplicate_rejects_a_bad_start_date(conn, w):
    src = make(conn, w, "A")
    with pytest.raises(items.DateError):
        w(lambda: items.duplicate_item(conn, src, "11/20/2026"))


def test_html_title_stored_as_text(conn, w):
    i = make(conn, w, "<script>alert(1)</script>")
    assert items.get_item(conn, i)["title"] == "<script>alert(1)</script>"
