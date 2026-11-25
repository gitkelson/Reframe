"""Seed a database with realistic demo data (development, UI tests and the performance check, spec 11).

python -m reframe.seed --db C:\\temp\\demo.sqlite --count 300
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
from pathlib import Path

from .db.changelog import writer
from .db.connection import init_db
from .domain import tags as tagmod

PROJECTS = ["PRJ-20", "PRJ-21", "Spares", "Gantry B", "Cell 4", "Tooling", "Training", "Safety", "Quote 8841", "Audit"]
VERBS = ["Material delivered", "Spares order due", "Drawing release", "Design review", "Install window",
         "Vendor visit", "FAT", "SAT", "Software freeze", "Shipping", "Customer call", "PO placed", "Inspection"]
LOG_VERBS = ["Reviewed drawings", "Called vendor", "Updated schedule", "Met with controls team", "Wrote test plan",
             "Tracked shipment", "Answered RFI", "Checked wiring"]


def seed(path: str | Path, count: int = 300, *, today: dt.date | None = None, rng_seed: int = 7,
         recurring: int = 0, flat: bool = False) -> dict[str, int]:
    """Insert ``count`` local items around ``today`` plus ``recurring`` imported-looking weekly meetings."""
    rng = random.Random(rng_seed)
    today = today or dt.date.today()
    conn = init_db(path)
    made = 0
    conn.execute("INSERT OR IGNORE INTO calendar_source (id, kind, external_id, name, location, connected, visible) "
                 "VALUES ('seed-outlook', 'outlook', 'seed', 'Calendar', '\\\\demo\\Calendar', 1, 1)")
    conn.execute("INSERT OR IGNORE INTO calendar_source (id, kind, external_id, name, location, connected, visible) "
                 "VALUES ('seed-ics', 'ics', 'https://calendar.example/team.ics', 'Team feed', "
                 "'https://calendar.example/team.ics', 1, 0)")
    with writer(conn, "agent", f"Seed {count} demo items"):
        tag_ids = [tagmod.get_or_create_tag(conn, p)[0] for p in PROJECTS]
        if not flat:   # a small tag tree (R-110) and one retired tag (R-113)
            group = tagmod.get_or_create_tag(conn, "Projects")[0]
            for t in tag_ids[:4]:
                tagmod.move_tag(conn, t, group)
            tagmod.set_retired(conn, tag_ids[-1], True)
        meeting = tagmod.get_or_create_tag(conn, "meeting")[0]
        rows = []
        for i in range(count):
            start = today + dt.timedelta(days=rng.randint(-200, 420))
            is_log = rng.random() < 0.25
            if is_log:
                title, span = rng.choice(LOG_VERBS), 1
            else:
                title = f"{rng.choice(VERBS)} {rng.choice(PROJECTS)}"
                span = rng.choice([1, 1, 1, 2, 3, 5, 10, 21, 45])
            end = start + dt.timedelta(days=span - 1)
            rows.append((("log" if is_log else "plan"), title, start.isoformat(), end.isoformat(), f"Note {i}"))
        for kind, title, s, e, n in rows:
            iid = conn.execute("INSERT INTO item (kind,title,start_date,end_date,notes) VALUES (?,?,?,?,?) RETURNING id",
                               (kind, title, s, e, n)).fetchone()[0]
            for pos, t in enumerate(rng.sample(tag_ids, rng.choice([0, 1, 1, 1, 2, 3]))):
                conn.execute("INSERT INTO item_tag (item_id, tag_id, position) VALUES (?,?,?)", (iid, t, pos))
            made += 1
        for r in range(recurring):
            series = f"seed|S{r}"
            first = today + dt.timedelta(days=-60 + r % 7)
            for k in range(0, 100):
                d = first + dt.timedelta(days=7 * k)
                iid = conn.execute(
                    "INSERT INTO item (kind,title,start_date,end_date,origin,source_id,outlook_uid,outlook_series_id,outlook_time) "
                    "VALUES ('plan',?,?,?,'outlook','seed-outlook',?,?,'09:00-10:00') RETURNING id",
                    (f"Weekly sync {r}", d.isoformat(), d.isoformat(), f"{series}|{d.isoformat()}", series)).fetchone()[0]
                conn.execute("INSERT INTO item_tag (item_id, tag_id, position) VALUES (?,?,1000)", (iid, meeting))
                made += 1
    conn.close()
    return {"items": made}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--count", type=int, default=300)
    ap.add_argument("--recurring", type=int, default=0, help="number of weekly series of 100 occurrences each")
    ap.add_argument("--flat", action="store_true", help="no tag groups and no retired tag")
    args = ap.parse_args()
    print(seed(args.db, args.count, recurring=args.recurring, flat=args.flat))


if __name__ == "__main__":
    main()
