"""Generates schema.sql and migrations/002_v002.sql (spec sections 5.2, 5.3).

The change-log triggers are mechanical, so they are generated from the table definitions below. ``schema.sql``
is the artifact applied to a fresh database and read by agents; ``migrations/002_v002.sql`` upgrades a
version-1 database to exactly the same schema. ``tests/py/test_schema.py`` fails if either file is out of date
and asserts that a fresh and a migrated database have identical normalized ``sqlite_master`` contents.

Run: python -m reframe.db.build_schema
"""
from __future__ import annotations

import string
from pathlib import Path

SCHEMA_VERSION = 2

# Characters that normalize to a single space in a tag key (R-41): all ASCII
# punctuation plus tab, CR and LF. Space itself is handled by collapsing.
_PUNCT = string.punctuation
_WS_CODES = (9, 10, 13)

SYSTEM_TAG_POSITION = 1000


def norm_sql(expr: str) -> str:
    """SQL expression computing the tag normalization key of ``expr`` (R-41).

    Must stay identical to ``reframe.domain.tags.norm_key``.
    """
    s = f"lower({expr})"
    for ch in _PUNCT:
        lit = "''''" if ch == "'" else f"'{ch}'"
        s = f"replace({s}, {lit}, ' ')"
    for code in _WS_CODES:
        s = f"replace({s}, char({code}), ' ')"
    s = f"replace(replace(replace({s}, '  ', ' <>'), '<> ', ''), '<>', '')"
    return f"trim({s})"


# table -> (columns in JSON, primary-key columns). Only these tables are change-logged (spec 5.3).
TABLES: dict[str, tuple[list[str], list[str]]] = {
    "item": (
        ["id", "kind", "title", "start_date", "end_date", "notes", "origin", "source_id", "outlook_uid", "ics_uid",
         "outlook_series_id", "outlook_calendar", "outlook_time", "outlook_location",
         "deleted_at", "hidden_at", "created_at", "updated_at"],
        ["id"],
    ),
    "tag": (["id", "name", "norm_key", "color_slot", "parent_id", "sort_order", "retired_at", "created_at"], ["id"]),
    "item_tag": (["item_id", "tag_id", "position", "implied"], ["item_id", "tag_id"]),
    "outlook_series_tag": (["series_id", "tag_id", "position"], ["series_id", "tag_id"]),
    "outlook_hidden_series": (["series_id", "hidden_at"], ["series_id"]),
    "attachment": (["id", "item_id", "kind", "target", "label", "pinned_version_id", "pin_basis", "created_at"], ["id"]),
}

# Generated columns are present in the JSON but never written back by undo.
GENERATED = {"tag": ["norm_key"]}

# Columns each logged table had in schema version 1; the migration copies exactly these.
V1_COLUMNS: dict[str, list[str]] = {
    "item": ["id", "kind", "title", "start_date", "end_date", "notes", "origin", "outlook_uid", "outlook_series_id",
             "outlook_calendar", "outlook_time", "outlook_location", "deleted_at", "hidden_at", "created_at",
             "updated_at"],
    "tag": ["id", "name", "color_slot", "created_at"],
    "item_tag": ["item_id", "tag_id", "position"],
    "attachment": ["id", "item_id", "kind", "target", "label", "created_at"],
    "sync_run": ["id", "started_at", "finished_at", "calendars", "added", "updated", "removed", "status", "error"],
}

_CTX = "(SELECT change_set_id FROM app_context WHERE id = 1)"
_ACTOR = "(SELECT actor FROM app_context WHERE id = 1)"
_NOW = "(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"

# ------------------------------------------------------------------ tables (normative DDL, spec 5.2)

DDL: dict[str, str] = {
    "calendar_source": f"""CREATE TABLE calendar_source (
  id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  kind        TEXT NOT NULL CHECK (kind IN ('outlook','ics')),
  external_id TEXT NOT NULL,
  name        TEXT NOT NULL,
  location    TEXT NOT NULL,
  is_holiday  INTEGER NOT NULL DEFAULT 0 CHECK (is_holiday IN (0,1)),
  connected   INTEGER NOT NULL DEFAULT 1 CHECK (connected IN (0,1)),
  visible     INTEGER NOT NULL DEFAULT 0 CHECK (visible IN (0,1)),
  refresh_min INTEGER,
  etag        TEXT,
  last_modified TEXT,
  last_read_at TEXT,
  last_status TEXT,
  last_error  TEXT,
  created_at  TEXT NOT NULL DEFAULT {_NOW},
  UNIQUE (kind, external_id)
);""",
    "item": f"""CREATE TABLE item (
  id            TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  kind          TEXT NOT NULL DEFAULT 'plan' CHECK (kind IN ('plan','log')),
  title         TEXT NOT NULL CHECK (length(trim(title)) > 0),
  start_date    TEXT NOT NULL CHECK (date(start_date) IS start_date),
  end_date      TEXT NOT NULL CHECK (date(end_date)   IS end_date),
  notes         TEXT NOT NULL DEFAULT '',
  origin        TEXT NOT NULL DEFAULT 'local' CHECK (origin IN ('local','outlook','ics')),
  source_id     TEXT REFERENCES calendar_source(id),
  outlook_uid   TEXT UNIQUE,
  ics_uid       TEXT UNIQUE,
  outlook_series_id TEXT,
  outlook_calendar TEXT,
  outlook_time  TEXT,
  outlook_location TEXT,
  deleted_at    TEXT,
  hidden_at     TEXT,
  created_at    TEXT NOT NULL DEFAULT {_NOW},
  updated_at    TEXT NOT NULL DEFAULT {_NOW},
  CHECK (end_date >= start_date),
  CHECK ((origin = 'local') = (source_id IS NULL))
);""",
    "outlook_hidden_series": """CREATE TABLE outlook_hidden_series (
  series_id TEXT PRIMARY KEY,
  hidden_at TEXT NOT NULL
);""",
    # norm_key is a generated column (R-41): agents only supply name, and two tags whose names normalize to
    # the same key cannot coexist anywhere in the tag tree.
    "tag": f"""CREATE TABLE tag (
  id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  name       TEXT NOT NULL CHECK (length(trim(name)) > 0),
  norm_key   TEXT GENERATED ALWAYS AS ({norm_sql('name')}) STORED NOT NULL UNIQUE CHECK (norm_key <> ''),
  color_slot INTEGER CHECK (color_slot IS NULL OR color_slot BETWEEN 0 AND 15),
  parent_id  TEXT REFERENCES tag(id) ON DELETE SET NULL,
  sort_order INTEGER,
  retired_at TEXT,
  created_at TEXT NOT NULL DEFAULT {_NOW},
  CHECK (parent_id IS NULL OR parent_id <> id)
);""",
    "item_tag": """CREATE TABLE item_tag (
  item_id  TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  tag_id   TEXT NOT NULL REFERENCES tag(id)  ON DELETE CASCADE,
  position INTEGER NOT NULL DEFAULT 0,
  implied  INTEGER NOT NULL DEFAULT 0 CHECK (implied IN (0,1)),
  PRIMARY KEY (item_id, tag_id)
);""",
    "outlook_series_tag": """CREATE TABLE outlook_series_tag (
  series_id TEXT NOT NULL,
  tag_id    TEXT NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
  position  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (series_id, tag_id)
);""",
    "file_source": """CREATE TABLE file_source (
  id            TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  path_key      TEXT NOT NULL UNIQUE,
  display_path  TEXT NOT NULL,
  last_size     INTEGER,
  last_mtime    TEXT,
  last_hash     TEXT,
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','protected','changed','missing','locked','denied','unreachable','failed')),
  last_check_at TEXT,
  last_capture_at TEXT,
  last_error    TEXT
);""",
    "file_version": f"""CREATE TABLE file_version (
  id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  source_id   TEXT NOT NULL REFERENCES file_source(id),
  sha256      TEXT NOT NULL,
  size        INTEGER NOT NULL,
  file_mtime  TEXT,
  captured_at TEXT NOT NULL DEFAULT {_NOW},
  backend_ref TEXT NOT NULL,
  UNIQUE (source_id, sha256)
);""",
    "attachment": f"""CREATE TABLE attachment (
  id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  item_id    TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL CHECK (kind IN ('file','folder','url')),
  target     TEXT NOT NULL,
  label      TEXT,
  pinned_version_id TEXT REFERENCES file_version(id),
  pin_basis  TEXT CHECK (pin_basis IN ('attach','migration','duplicate')),
  created_at TEXT NOT NULL DEFAULT {_NOW}
);""",
    "change_set": f"""CREATE TABLE change_set (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     TEXT NOT NULL DEFAULT {_NOW},
  actor  TEXT NOT NULL CHECK (actor IN ('user','agent','sync','undo','external')),
  label  TEXT NOT NULL,
  reverts_change_set_id INTEGER REFERENCES change_set(id)
);""",
    "change": f"""CREATE TABLE change (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  change_set_id INTEGER REFERENCES change_set(id),
  ts            TEXT NOT NULL DEFAULT {_NOW},
  table_name    TEXT NOT NULL,
  row_key       TEXT NOT NULL,
  op            TEXT NOT NULL CHECK (op IN ('insert','update','delete')),
  before_json   TEXT,
  after_json    TEXT
);""",
    "app_context": """CREATE TABLE app_context (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  actor         TEXT NOT NULL DEFAULT 'external',
  change_set_id INTEGER
);""",
    "setting": "CREATE TABLE setting (key TEXT PRIMARY KEY, value TEXT NOT NULL);",
    "sync_run": """CREATE TABLE sync_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
  calendars TEXT, added INTEGER, updated INTEGER, removed INTEGER, status TEXT, error TEXT,
  source_id TEXT
);""",
}

INDEXES: dict[str, list[str]] = {
    "item": [
        "CREATE INDEX item_dates ON item (start_date, end_date) WHERE deleted_at IS NULL AND hidden_at IS NULL;",
        "CREATE INDEX item_series ON item (outlook_series_id) WHERE outlook_series_id IS NOT NULL;",
        "CREATE INDEX item_source ON item (source_id) WHERE source_id IS NOT NULL;",
    ],
    "tag": ["CREATE INDEX tag_parent ON tag (parent_id) WHERE parent_id IS NOT NULL;"],
    "item_tag": ["CREATE INDEX item_tag_tag ON item_tag (tag_id);"],
    "file_version": ["CREATE INDEX file_version_hash ON file_version (sha256);"],
    "attachment": ["CREATE INDEX attachment_item ON attachment (item_id);"],
    "change": ["CREATE INDEX change_set_idx ON change (change_set_id);",
               "CREATE INDEX change_row_idx ON change (table_name, row_key);"],
}

VIEWS = {
    # Agent views (exclude deleted and hidden items; hidden *calendars* are a view setting and are NOT excluded)
    "v_item": """CREATE VIEW v_item AS
SELECT i.id, i.kind, i.title, i.start_date, i.end_date, i.notes, i.origin,
       (SELECT group_concat(t.name, '; ' ORDER BY it.position, t.name)
          FROM item_tag it JOIN tag t ON t.id = it.tag_id
         WHERE it.item_id = i.id) AS tags,
       (SELECT cs.name FROM calendar_source cs WHERE cs.id = i.source_id) AS calendar
FROM item i WHERE i.deleted_at IS NULL AND i.hidden_at IS NULL;""",
    # The tag tree with each tag's full path, e.g. 'Projects > ClientA > Line 2'
    "v_tag": """CREATE VIEW v_tag AS
WITH RECURSIVE p(id, path, depth) AS (
  SELECT id, name, 0 FROM tag WHERE parent_id IS NULL
  UNION ALL SELECT t.id, p.path || ' > ' || t.name, p.depth + 1 FROM tag t JOIN p ON t.parent_id = p.id)
SELECT t.id, t.name, t.parent_id, p.path, p.depth, t.retired_at IS NOT NULL AS retired,
       (SELECT count(*) FROM item_tag it WHERE it.tag_id = t.id) AS usage
FROM tag t JOIN p ON p.id = t.id;""",
}

# Order matters: a table must exist before another table's DDL references it in a fresh database.
TABLE_ORDER = ["calendar_source", "item", "outlook_hidden_series", "tag", "item_tag", "outlook_series_tag",
               "file_source", "file_version", "attachment", "change_set", "change", "app_context", "setting",
               "sync_run"]

# ------------------------------------------------------------------ generated logging triggers


def _json_obj(prefix: str, cols: list[str]) -> str:
    return "json_object(" + ", ".join(f"'{c}', {prefix}.{c}" for c in cols) + ")"


def _row_key(prefix: str, pk: list[str]) -> str:
    return " || '|' || ".join(f"{prefix}.{c}" for c in pk)


def change_triggers() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    ins = "INSERT INTO change (change_set_id, table_name, row_key, op, before_json, after_json)"
    for table, (cols, pk) in TABLES.items():
        out.append((f"trg_{table}_ai",
                    f"CREATE TRIGGER trg_{table}_ai AFTER INSERT ON {table} BEGIN\n"
                    f"  {ins}\n  VALUES ({_CTX}, '{table}', {_row_key('NEW', pk)}, 'insert', NULL, {_json_obj('NEW', cols)});\n"
                    f"END;"))
        out.append((f"trg_{table}_au",
                    f"CREATE TRIGGER trg_{table}_au AFTER UPDATE ON {table} BEGIN\n"
                    f"  {ins}\n  VALUES ({_CTX}, '{table}', {_row_key('NEW', pk)}, 'update', {_json_obj('OLD', cols)}, {_json_obj('NEW', cols)});\n"
                    f"END;"))
        out.append((f"trg_{table}_ad",
                    f"CREATE TRIGGER trg_{table}_ad AFTER DELETE ON {table} BEGIN\n"
                    f"  {ins}\n  VALUES ({_CTX}, '{table}', {_row_key('OLD', pk)}, 'delete', {_json_obj('OLD', cols)}, NULL);\n"
                    f"END;"))
    return out


# ------------------------------------------------------------------ protective and tag-tree triggers

def _ancestors(tag_expr: str) -> str:
    """Subquery: ids of every ancestor of the tag ``tag_expr`` (not the tag itself)."""
    return (f"(WITH RECURSIVE anc(id, d) AS (SELECT parent_id, 1 FROM tag WHERE id = {tag_expr} AND parent_id IS NOT NULL "
            f"UNION ALL SELECT t.parent_id, anc.d + 1 FROM tag t JOIN anc ON t.id = anc.id "
            f"WHERE t.parent_id IS NOT NULL AND anc.d < 64) SELECT id FROM anc)")


def _self_and_descendants(tag_expr: str) -> str:
    return (f"(WITH RECURSIVE des(id, d) AS (SELECT {tag_expr}, 0 UNION ALL SELECT t.id, des.d + 1 FROM tag t "
            f"JOIN des ON t.parent_id = des.id WHERE des.d < 64) SELECT id FROM des)")


# (tag_id, anc_id, d) for every tag and each of its ancestors, d = 1 for the parent
_CLOSURE = ("(WITH RECURSIVE cl(tag_id, anc_id, d) AS (SELECT id, parent_id, 1 FROM tag WHERE parent_id IS NOT NULL "
            "UNION ALL SELECT cl.tag_id, t.parent_id, cl.d + 1 FROM cl JOIN tag t ON t.id = cl.anc_id "
            "WHERE t.parent_id IS NOT NULL AND cl.d < 64) SELECT tag_id, anc_id, d FROM cl)")


def _base_position(item_expr: str) -> str:
    """Position after the item's explicit non-system tags (implied tags go there, R-111)."""
    return (f"(SELECT coalesce(max(p.position), -1) FROM item_tag p "
            f"WHERE p.item_id = {item_expr} AND p.position < {SYSTEM_TAG_POSITION})")


_LOCKED_COLS = ["title", "start_date", "end_date", "kind", "origin", "source_id", "outlook_uid", "ics_uid",
                "outlook_series_id", "outlook_calendar", "outlook_time", "outlook_location"]


def protective_triggers() -> list[tuple[str, str]]:
    locked = "\n      OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in _LOCKED_COLS)
    not_undo = f"{_ACTOR} <> 'undo'"
    return [
        # The log is protected in the database itself (spec 5.3).
        ("trg_change_bu", """CREATE TRIGGER trg_change_bu BEFORE UPDATE ON change
WHEN NOT (OLD.change_set_id IS NULL AND NEW.change_set_id IS NOT NULL
          AND NEW.id = OLD.id AND NEW.ts = OLD.ts AND NEW.table_name = OLD.table_name
          AND NEW.row_key = OLD.row_key AND NEW.op = OLD.op
          AND NEW.before_json IS OLD.before_json AND NEW.after_json IS OLD.after_json)
BEGIN SELECT RAISE(ABORT, 'change is append-only'); END;"""),
        ("trg_change_bd", """CREATE TRIGGER trg_change_bd BEFORE DELETE ON change
BEGIN SELECT RAISE(ABORT, 'change is append-only'); END;"""),
        ("trg_change_set_bu", """CREATE TRIGGER trg_change_set_bu BEFORE UPDATE ON change_set
WHEN NOT (OLD.id IS (SELECT change_set_id FROM app_context WHERE id = 1)
          AND NEW.id = OLD.id AND NEW.ts = OLD.ts AND NEW.actor = OLD.actor
          AND NEW.reverts_change_set_id IS OLD.reverts_change_set_id)
BEGIN SELECT RAISE(ABORT, 'change_set is append-only (only the open set label may change)'); END;"""),
        ("trg_change_set_bd", """CREATE TRIGGER trg_change_set_bd BEFORE DELETE ON change_set
BEGIN SELECT RAISE(ABORT, 'change_set is append-only'); END;"""),
        # Imported items (Outlook and ICS) have locked core fields (R-16)
        ("trg_item_lock_bu", f"""CREATE TRIGGER trg_item_lock_bu BEFORE UPDATE ON item
WHEN OLD.origin IN ('outlook', 'ics')
 AND {_ACTOR} NOT IN ('sync', 'undo')
 AND ({locked})
BEGIN SELECT RAISE(ABORT, 'Imported item: core fields are locked'); END;"""),
        ("trg_item_nodelete_bd", f"""CREATE TRIGGER trg_item_nodelete_bd BEFORE DELETE ON item
WHEN {_ACTOR} <> 'undo'
BEGIN SELECT RAISE(ABORT, 'Hard delete is not allowed: set deleted_at instead'); END;"""),
        # Captured versions are immutable and kept while anything refers to them (R-161, R-165)
        ("trg_file_version_bu", """CREATE TRIGGER trg_file_version_bu BEFORE UPDATE ON file_version
BEGIN SELECT RAISE(ABORT, 'file_version rows are immutable'); END;"""),
        ("trg_file_version_bd", """CREATE TRIGGER trg_file_version_bd BEFORE DELETE ON file_version
WHEN EXISTS (SELECT 1 FROM attachment WHERE pinned_version_id = OLD.id)
  OR EXISTS (SELECT 1 FROM change WHERE table_name = 'attachment'
             AND (instr(before_json, OLD.id) > 0 OR instr(after_json, OLD.id) > 0))
BEGIN SELECT RAISE(ABORT, 'file_version is still referenced by a link or the undo history'); END;"""),
        # Tag groups cannot contain themselves (R-110)
        ("trg_tag_cycle_bu", f"""CREATE TRIGGER trg_tag_cycle_bu BEFORE UPDATE OF parent_id ON tag
WHEN NEW.parent_id IS NOT NULL AND NEW.parent_id IN {_self_and_descendants('NEW.id')}
BEGIN SELECT RAISE(ABORT, 'A tag group cannot contain itself'); END;"""),
        # ---- tag tree (R-111, R-112); suppressed while undo replays the logged rows exactly
        ("trg_item_tag_tree_bd", f"""CREATE TRIGGER trg_item_tag_tree_bd BEFORE DELETE ON item_tag
WHEN {not_undo}
 AND EXISTS (SELECT 1 FROM item_tag x WHERE x.item_id = OLD.item_id AND x.implied = 0 AND x.tag_id <> OLD.tag_id
             AND OLD.tag_id IN {_ancestors('x.tag_id')})
BEGIN SELECT RAISE(ABORT, 'Remove the descendant tag first'); END;"""),
        ("trg_item_tag_tree_ai", f"""CREATE TRIGGER trg_item_tag_tree_ai AFTER INSERT ON item_tag
WHEN NEW.implied = 0 AND {not_undo}
BEGIN
  INSERT INTO item_tag (item_id, tag_id, position, implied)
  SELECT NEW.item_id, a.id, {_base_position('NEW.item_id')} + a.d, 1
    FROM (WITH RECURSIVE anc(id, d) AS (SELECT parent_id, 1 FROM tag WHERE id = NEW.tag_id AND parent_id IS NOT NULL
            UNION ALL SELECT t.parent_id, anc.d + 1 FROM tag t JOIN anc ON t.id = anc.id
            WHERE t.parent_id IS NOT NULL AND anc.d < 64) SELECT id, d FROM anc) a
   WHERE NOT EXISTS (SELECT 1 FROM item_tag x WHERE x.item_id = NEW.item_id AND x.tag_id = a.id);
END;"""),
        ("trg_item_tag_tree_ad", f"""CREATE TRIGGER trg_item_tag_tree_ad AFTER DELETE ON item_tag
WHEN OLD.implied = 0 AND {not_undo}
BEGIN
  DELETE FROM item_tag
   WHERE item_id = OLD.item_id AND implied = 1
     AND NOT EXISTS (SELECT 1 FROM item_tag x WHERE x.item_id = OLD.item_id AND x.implied = 0
                     AND item_tag.tag_id IN {_ancestors('x.tag_id')});
END;"""),
        ("trg_tag_tree_au", f"""CREATE TRIGGER trg_tag_tree_au AFTER UPDATE OF parent_id ON tag
WHEN OLD.parent_id IS NOT NEW.parent_id AND {not_undo}
BEGIN
  DELETE FROM item_tag
   WHERE implied = 1
     AND item_id IN (SELECT item_id FROM item_tag WHERE implied = 0 AND tag_id IN {_self_and_descendants('NEW.id')})
     AND NOT EXISTS (SELECT 1 FROM item_tag x WHERE x.item_id = item_tag.item_id AND x.implied = 0
                     AND item_tag.tag_id IN {_ancestors('x.tag_id')});
  INSERT INTO item_tag (item_id, tag_id, position, implied)
  SELECT x.item_id, c.anc_id, {_base_position('x.item_id')} + min(c.d), 1
    FROM item_tag x JOIN {_CLOSURE} c ON c.tag_id = x.tag_id
   WHERE x.implied = 0
     AND x.item_id IN (SELECT item_id FROM item_tag WHERE implied = 0 AND tag_id IN {_self_and_descendants('NEW.id')})
     AND NOT EXISTS (SELECT 1 FROM item_tag y WHERE y.item_id = x.item_id AND y.tag_id = c.anc_id)
   GROUP BY x.item_id, c.anc_id;
END;"""),
    ]


def all_triggers() -> list[tuple[str, str]]:
    return change_triggers() + protective_triggers()


# version-1 trigger names (dropped by migration 002 before the table rebuilds)
V1_TRIGGERS = [f"trg_{t}_{s}" for t in ("item", "tag", "item_tag", "outlook_series_tag", "outlook_hidden_series",
                                        "attachment") for s in ("ai", "au", "ad")] + [
    "trg_change_bu", "trg_change_bd", "trg_change_set_bu", "trg_change_set_bd", "trg_item_lock_bu",
    "trg_item_nodelete_bd"]


def build() -> str:
    parts = [
        "-- Reframe schema, generated by reframe/db/build_schema.py. Do not edit by hand.",
        f"-- user_version = {SCHEMA_VERSION}",
        "",
    ]
    for t in TABLE_ORDER:
        parts.append(DDL[t])
        parts.extend(INDEXES.get(t, []))
        if t == "app_context":
            parts.append("INSERT INTO app_context (id) VALUES (1);")
        parts.append("")
    parts.extend(VIEWS.values())
    parts.append("")
    parts.extend(sql for _, sql in all_triggers())
    return "\n".join(parts) + "\n"


def _rebuild(table: str) -> list[str]:
    """SQLite's create-copy-drop-rename table rebuild, copying the version-1 columns."""
    cols = ", ".join(V1_COLUMNS[table])
    insert = f"INSERT INTO {table}_v2 ({cols}) SELECT {cols} FROM {table};"
    if table == "item":
        # every imported item belongs to a calendar source (spec 13.7 D-06)
        insert = (f"INSERT INTO item_v2 ({cols}, source_id) SELECT {cols}, CASE WHEN origin = 'local' THEN NULL ELSE "
                  "(SELECT cs.id FROM calendar_source cs WHERE cs.kind = 'outlook' "
                  "AND cs.external_id = coalesce(item.outlook_calendar, '')) END FROM item;")
    return [
        DDL[table].replace(f"CREATE TABLE {table} (", f"CREATE TABLE {table}_v2 (", 1),
        insert,
        f"DROP TABLE {table};",
        f"ALTER TABLE {table}_v2 RENAME TO {table};",
        *INDEXES.get(table, []),
    ]


# one source per calendar of the version-1 selection (selected ones shown, as before), then one per other
# calendar id found on items (not connected, hidden).
_MIGRATE_SOURCES = [
    """INSERT OR IGNORE INTO calendar_source (kind, external_id, name, location, is_holiday, connected, visible)
SELECT 'outlook', json_extract(j.value, '$.id'), coalesce(json_extract(j.value, '$.name'), 'Calendar'),
       coalesce(json_extract(j.value, '$.path'), ''), coalesce(json_extract(j.value, '$.holidays'), 0) <> 0, 1,
       coalesce(json_extract(j.value, '$.selected'), 0) <> 0
  FROM setting s, json_each(s.value) j
 WHERE s.key = 'outlook_calendars' AND json_valid(s.value) AND json_extract(j.value, '$.id') IS NOT NULL;""",
    """INSERT OR IGNORE INTO calendar_source (kind, external_id, name, location, is_holiday, connected, visible)
SELECT DISTINCT 'outlook', coalesce(outlook_calendar, ''), coalesce(outlook_calendar, 'Outlook'), '', 0, 0, 0
  FROM item WHERE origin = 'outlook';""",
]


def build_migration_002() -> str:
    """Version 1 -> 2 (spec 13.7 D-05). Run with foreign_keys OFF inside one transaction (connection.migrate)."""
    parts = [
        "-- Migration 1 -> 2, generated by reframe/db/build_schema.py. Do not edit by hand.",
        "-- Applied by reframe.db.connection.init_db in one transaction with foreign_keys OFF and a",
        "-- foreign_key_check before COMMIT. Logging triggers are dropped first so the rebuild writes no change rows.",
        "",
        "DROP VIEW IF EXISTS v_item;",
        "DROP VIEW IF EXISTS v_tag;",
        *[f"DROP TRIGGER IF EXISTS {n};" for n in V1_TRIGGERS],
        "",
        DDL["calendar_source"],
        *_MIGRATE_SOURCES,
        DDL["file_source"],
        DDL["file_version"],
        *INDEXES["file_version"],
        "",
    ]
    for table in ("item", "tag", "item_tag", "attachment", "sync_run"):
        parts.extend(_rebuild(table))
        parts.append("")
    parts.extend(VIEWS.values())
    parts.append("")
    parts.extend(sql for _, sql in all_triggers())
    return "\n".join(parts) + "\n"


def schema_path() -> Path:
    return Path(__file__).with_name("schema.sql")


def migration_002_path() -> Path:
    return Path(__file__).with_name("migrations") / "002_v002.sql"


if __name__ == "__main__":
    schema_path().write_text(build(), encoding="utf-8", newline="\n")
    migration_002_path().parent.mkdir(exist_ok=True)
    migration_002_path().write_text(build_migration_002(), encoding="utf-8", newline="\n")
    print("wrote", schema_path(), "and", migration_002_path())
