"""Tag normalization, resolution, autocomplete and maintenance (R-40..R-47)."""
from __future__ import annotations

import difflib
import re
import sqlite3
from typing import Any

SYSTEM_TAG_POSITION = 1000
NEAR_MATCH_THRESHOLD = 0.85  # fixed constant (spec 8.3)

_SEP = re.compile(r"[!-/:-@\[-`{-~\t\r\n ]+")
_UPPER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}


def norm_key(name: str) -> str:
    """Normalization key (R-41). Identical to ``build_schema.norm_sql``.

    ASCII-only lower-casing and ASCII punctuation, because SQLite's built-in
    lower() is ASCII-only and the same key must be computable in plain SQL.
    """
    return _SEP.sub(" ", name.translate(_UPPER)).strip(" ")


def _similarity(a: str, b: str) -> float:
    r1 = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    r2 = difflib.SequenceMatcher(None, ta, tb).ratio()
    return max(r1, r2)


def clean_name(name: str) -> str:
    return " ".join(name.split())


def find_tag(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    key = norm_key(name)
    if not key:
        return None
    return conn.execute("SELECT * FROM tag WHERE norm_key=?", (key,)).fetchone()


def next_auto_slot(conn: sqlite3.Connection) -> int:
    """Least-used base hue (0-7) so new tags get distinct colors first."""
    counts = [0] * 8
    for r in conn.execute("SELECT color_slot FROM tag WHERE color_slot IS NOT NULL"):
        counts[r[0] % 8] += 1
    return counts.index(min(counts))


def suggestions(conn: sqlite3.Connection, text: str, limit: int = 8) -> dict[str, Any]:
    """Autocomplete for the tag editor (R-42).

    ``exact`` is the existing tag the text resolves to (if any); ``matches`` are
    prefix and fuzzy matches; ``near`` are matches over the 0.85 threshold that
    are not the exact tag, to be shown as "Use X?" before creating a new tag.
    """
    key = norm_key(text)
    tags = conn.execute("SELECT t.id, t.name, t.norm_key, t.color_slot, t.retired_at FROM tag t").fetchall()
    paths = {t["id"]: t["path"] for t in list_tags(conn)}
    exact = None
    retired_exact = None
    scored: list[tuple[float, sqlite3.Row]] = []
    near = []
    for t in tags:
        if t["norm_key"] == key and key:
            if t["retired_at"]:
                retired_exact = {**dict(t), "path": paths.get(t["id"], t["name"])}
            else:
                exact = {**dict(t), "path": paths.get(t["id"], t["name"])}
            continue
        if t["retired_at"]:
            continue   # retired tags are never offered (R-42, R-113)
        if not key:
            scored.append((0.0, t))
            continue
        tk = t["norm_key"]
        score = 0.0
        if tk.startswith(key):
            score = 2.0 - min(len(tk) - len(key), 50) / 100
        elif any(w.startswith(key) for w in tk.split()):
            score = 1.5
        else:
            sim = _similarity(key, tk)
            if sim >= 0.6:
                score = sim
            if sim >= NEAR_MATCH_THRESHOLD:
                near.append({**dict(t), "path": paths.get(t["id"], t["name"])})
        if score > 0:
            scored.append((score, t))
    scored.sort(key=lambda p: (-p[0], p[1]["name"].lower()))
    return {"exact": exact, "retired": retired_exact, "near": near,
            "matches": [{**dict(t), "path": paths.get(t["id"], t["name"])} for _, t in scored[:limit]]}


class NearTagMatch(Exception):
    def __init__(self, name: str, near: list[dict[str, Any]]):
        self.name, self.near = name, near
        super().__init__(f"'{name}' is similar to existing tag(s): " + ", ".join(n["name"] for n in near))


class RetiredTag(ValueError):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"'{name}' is retired. Un-retire it first (or pass --allow-retired).")


def get_or_create_tag(conn: sqlite3.Connection, name: str, *, allow_near: bool = True,
                      parent_id: str | None = None) -> tuple[str, bool]:
    """Resolve ``name`` to a tag id, creating it if needed. Returns (id, created).

    Case/punctuation variants silently resolve to the existing tag. With
    ``allow_near=False`` a new tag that nearly matches an existing one raises
    ``NearTagMatch`` instead of being created (agent helper behaviour). New tags are
    groupless unless ``parent_id`` is given (R-110). Must run inside an open writer session.
    """
    name = clean_name(name)
    if not norm_key(name):
        raise ValueError("Tag name must contain at least one letter or digit")
    existing = find_tag(conn, name)
    if existing is not None:
        return existing["id"], False
    if not allow_near:
        near = suggestions(conn, name)["near"]
        if near:
            raise NearTagMatch(name, near)
    cur = conn.execute("INSERT INTO tag (name, color_slot, parent_id) VALUES (?, ?, ?) RETURNING id",
                       (name, next_auto_slot(conn), parent_id))
    return cur.fetchone()[0], True


def list_tags(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every tag with usage, color slot, tree position (parent, path, depth, tree order) and retirement."""
    rows = conn.execute(
        """SELECT t.id, t.name, t.norm_key, t.color_slot, t.created_at, t.parent_id, t.sort_order, t.retired_at,
                  (SELECT count(*) FROM item_tag it JOIN item i ON i.id = it.item_id
                    WHERE it.tag_id = t.id AND i.deleted_at IS NULL) AS usage
             FROM tag t ORDER BY lower(t.name)""").fetchall()
    out = [dict(r) for r in rows]
    order = sorted(range(len(out)), key=lambda i: (out[i]["created_at"], out[i]["id"]))
    for rank, i in enumerate(order):
        # NULL color_slot means "auto": derive a stable base hue from creation order
        out[i]["slot"] = out[i]["color_slot"] if out[i]["color_slot"] is not None else rank % 8
    by_id = {t["id"]: t for t in out}
    children: dict[str | None, list[dict[str, Any]]] = {}
    for t in out:
        parent = t["parent_id"] if t["parent_id"] in by_id else None
        children.setdefault(parent, []).append(t)
    for kids in children.values():
        kids.sort(key=lambda t: (t["sort_order"] if t["sort_order"] is not None else 1 << 30, t["name"].lower()))
    n = 0

    def walk(parent: str | None, path: list[str], depth: int) -> None:
        nonlocal n
        for t in children.get(parent, []):
            t["tree_order"], t["depth"] = n, depth
            t["path"] = " > ".join(path + [t["name"]])
            t["has_children"] = bool(children.get(t["id"]))
            t["retired"] = t["retired_at"] is not None
            n += 1
            if depth < 64:
                walk(t["id"], path + [t["name"]], depth + 1)

    walk(None, [], 0)
    return out


def ancestors(conn: sqlite3.Connection, tag_id: str) -> list[str]:
    """Ancestor ids of a tag, nearest first."""
    out: list[str] = []
    cur = conn.execute("SELECT parent_id FROM tag WHERE id=?", (tag_id,)).fetchone()
    while cur and cur[0] and cur[0] not in out and len(out) < 64:
        out.append(cur[0])
        cur = conn.execute("SELECT parent_id FROM tag WHERE id=?", (cur[0],)).fetchone()
    return out


def descendants(conn: sqlite3.Connection, tag_id: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "WITH RECURSIVE des(id, d) AS (SELECT id, 1 FROM tag WHERE parent_id = ? "
        "UNION ALL SELECT t.id, des.d + 1 FROM tag t JOIN des ON t.parent_id = des.id WHERE des.d < 64) "
        "SELECT id FROM des", (tag_id,))]


def items_affected_by_move(conn: sqlite3.Connection, tag_id: str) -> int:
    """Items carrying the tag or any descendant explicitly: the count shown while dragging (R-112)."""
    ids = [tag_id, *descendants(conn, tag_id)]
    marks = ",".join("?" for _ in ids)
    return conn.execute(f"SELECT count(DISTINCT item_id) FROM item_tag WHERE implied = 0 AND tag_id IN ({marks})",
                        ids).fetchone()[0]


def move_tag(conn: sqlite3.Connection, tag_id: str, parent_id: str | None) -> int:
    """Put a tag into a group (or make it groupless). Database triggers update every affected item (R-112).

    Must run inside one writer session so the move and all item changes are one change set and one undo.
    Returns the number of affected items.
    """
    row = conn.execute("SELECT id, parent_id FROM tag WHERE id=?", (tag_id,)).fetchone()
    if row is None:
        raise LookupError(f"No tag {tag_id}")
    if parent_id is not None:
        if parent_id == tag_id or parent_id in descendants(conn, tag_id):
            raise ValueError("A tag group cannot contain itself")
        if conn.execute("SELECT 1 FROM tag WHERE id=?", (parent_id,)).fetchone() is None:
            raise LookupError(f"No tag {parent_id}")
    if row["parent_id"] == parent_id:
        return 0
    n = items_affected_by_move(conn, tag_id)
    conn.execute("UPDATE tag SET parent_id=? WHERE id=?", (parent_id, tag_id))
    return n


def set_retired(conn: sqlite3.Connection, tag_id: str, retired: bool) -> None:
    """Retire or un-retire one tag (R-113). Children are not affected."""
    if retired:
        conn.execute("UPDATE tag SET retired_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                     "WHERE id=? AND retired_at IS NULL", (tag_id,))
    else:
        conn.execute("UPDATE tag SET retired_at = NULL WHERE id=? AND retired_at IS NOT NULL", (tag_id,))


def implied_drift(conn: sqlite3.Connection) -> dict[str, list[tuple[str, str]]]:
    """Implied group tags that disagree with the tag tree (R-46), e.g. after an agent bypassed the triggers."""
    parents = {r[0]: r[1] for r in conn.execute("SELECT id, parent_id FROM tag")}

    def anc(t: str) -> list[str]:
        out = []
        while parents.get(t) and parents[t] not in out and len(out) < 64:
            t = parents[t]
            out.append(t)
        return out

    rows: dict[str, dict[str, int]] = {}
    for r in conn.execute("SELECT item_id, tag_id, implied FROM item_tag"):
        rows.setdefault(r[0], {})[r[1]] = r[2]
    missing, extra = [], []
    for item_id, tags_ in rows.items():
        required = {a for t, imp in tags_.items() if not imp for a in anc(t)}
        missing += [(item_id, a) for a in required if a not in tags_]
        extra += [(item_id, t) for t, imp in tags_.items() if imp and t not in required]
    return {"missing": missing, "extra": extra}


def repair_implied(conn: sqlite3.Connection) -> int:
    """Insert missing implied ancestors and drop unjustified implied rows. Run in a writer session."""
    drift = implied_drift(conn)
    for item_id, tag_id in drift["extra"]:
        conn.execute("DELETE FROM item_tag WHERE item_id=? AND tag_id=? AND implied=1", (item_id, tag_id))
    for item_id, tag_id in drift["missing"]:
        base = conn.execute("SELECT coalesce(max(position), -1) FROM item_tag WHERE item_id=? AND position < ?",
                            (item_id, SYSTEM_TAG_POSITION)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO item_tag (item_id, tag_id, position, implied) VALUES (?,?,?,1)",
                     (item_id, tag_id, base + 1))
    return len(drift["extra"]) + len(drift["missing"])


def rename_tag(conn: sqlite3.Connection, tag_id: str, new_name: str) -> None:
    new_name = clean_name(new_name)
    key = norm_key(new_name)
    if not key:
        raise ValueError("Tag name must contain at least one letter or digit")
    clash = conn.execute("SELECT id, name FROM tag WHERE norm_key=? AND id<>?", (key, tag_id)).fetchone()
    if clash:
        raise ValueError(f"Another tag already uses this name: '{clash['name']}'. Merge instead.")
    conn.execute("UPDATE tag SET name=? WHERE id=?", (new_name, tag_id))


def set_color(conn: sqlite3.Connection, tag_id: str, slot: int | None) -> None:
    if slot is not None and not 0 <= int(slot) <= 15:
        raise ValueError("Color slot must be 0-15")
    conn.execute("UPDATE tag SET color_slot=? WHERE id=?", (slot, tag_id))


def merge_tags(conn: sqlite3.Connection, src: str, dst: str) -> int:
    """Move every use of ``src`` to ``dst`` and delete ``src``. Returns rows moved.

    ``src``'s children join ``dst``'s group first, so implied group tags stay consistent (R-111).
    """
    if src == dst:
        raise ValueError("Cannot merge a tag into itself")
    if dst in descendants(conn, src):
        raise ValueError("Cannot merge a group into one of its own members; move that tag out first")
    for r in conn.execute("SELECT id FROM tag WHERE parent_id=?", (src,)).fetchall():
        conn.execute("UPDATE tag SET parent_id=? WHERE id=?", (dst, r["id"]))
    moved = 0
    for r in conn.execute("SELECT item_id, position, implied FROM item_tag WHERE tag_id=?", (src,)).fetchall():
        has = conn.execute("SELECT implied FROM item_tag WHERE item_id=? AND tag_id=?", (r["item_id"], dst)).fetchone()
        if has is None:
            conn.execute("INSERT INTO item_tag (item_id, tag_id, position, implied) VALUES (?,?,?,?)",
                         (r["item_id"], dst, r["position"], r["implied"]))
        elif has["implied"] and not r["implied"]:
            conn.execute("UPDATE item_tag SET implied=0 WHERE item_id=? AND tag_id=?", (r["item_id"], dst))
        conn.execute("DELETE FROM item_tag WHERE item_id=? AND tag_id=?", (r["item_id"], src))
        moved += 1
    for r in conn.execute("SELECT series_id, position FROM outlook_series_tag WHERE tag_id=?", (src,)).fetchall():
        has = conn.execute("SELECT 1 FROM outlook_series_tag WHERE series_id=? AND tag_id=?",
                           (r["series_id"], dst)).fetchone()
        if not has:
            conn.execute("INSERT INTO outlook_series_tag (series_id, tag_id, position) VALUES (?,?,?)",
                         (r["series_id"], dst, r["position"]))
        conn.execute("DELETE FROM outlook_series_tag WHERE series_id=? AND tag_id=?", (r["series_id"], src))
    conn.execute("DELETE FROM tag WHERE id=?", (src,))
    return moved


def delete_tag(conn: sqlite3.Connection, tag_id: str, *, force: bool = False) -> int:
    """Delete a tag. Without ``force`` it must be unused. Its children move to its parent (R-110).

    Returns removed assignments.
    """
    n = conn.execute("SELECT count(*) FROM item_tag WHERE tag_id=? AND implied=0", (tag_id,)).fetchone()[0]
    n += conn.execute("SELECT count(*) FROM outlook_series_tag WHERE tag_id=?", (tag_id,)).fetchone()[0]
    if n and not force:
        raise ValueError(f"Tag is used by {n} assignment(s); remove it from all items or merge it")
    row = conn.execute("SELECT parent_id FROM tag WHERE id=?", (tag_id,)).fetchone()
    parent = row["parent_id"] if row else None
    for r in conn.execute("SELECT id FROM tag WHERE parent_id=?", (tag_id,)).fetchall():
        conn.execute("UPDATE tag SET parent_id=? WHERE id=?", (parent, r["id"]))
    conn.execute("DELETE FROM item_tag WHERE tag_id=?", (tag_id,))
    conn.execute("DELETE FROM outlook_series_tag WHERE tag_id=?", (tag_id,))
    conn.execute("DELETE FROM tag WHERE id=?", (tag_id,))
    return n


def hygiene(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Near-duplicate tag pairs (R-46) for the tag manager to offer merging."""
    tags = conn.execute("SELECT id, name, norm_key FROM tag ORDER BY name").fetchall()
    pairs = []
    for i, a in enumerate(tags):
        for b in tags[i + 1:]:
            s = _similarity(a["norm_key"], b["norm_key"])
            if s >= NEAR_MATCH_THRESHOLD:
                pairs.append({"a": {"id": a["id"], "name": a["name"]},
                              "b": {"id": b["id"], "name": b["name"]}, "score": round(s, 3)})
    return pairs
