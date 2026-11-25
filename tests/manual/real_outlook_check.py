"""Manual check of the real COM adapter against the owner's classic Outlook (read-only; writes a temp DB only).

Prints counts and dates only, never titles.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
os.environ["REFRAME_BACKUP_DIR"] = tempfile.mkdtemp(prefix="reframe-manual-bk-")

from reframe.db.connection import init_db
from reframe.domain import settings
from reframe.outlook import sync
from reframe.outlook.adapter import ComAdapter, OutlookUnavailable

tmp = Path(tempfile.mkdtemp(prefix="reframe-manual-")) / "manual.sqlite"
conn = init_db(tmp)
try:
    with ComAdapter() as ad:
        t0 = time.perf_counter()
        cals = ad.list_calendars()
        print(f"{len(cals)} calendars in {time.perf_counter() - t0:.1f}s")
        for c in cals:
            print(f"  - {c.name!r:30} items={c.item_count} default={c.is_default} holidays={c.looks_like_holidays} path={c.path}")
        chosen = [{"id": c.id, "name": c.name, "selected": True, "holidays": c.looks_like_holidays} for c in cals]
        settings.set_many(conn, {"outlook_calendars": chosen})
        t0 = time.perf_counter()
        r = sync.run_sync(conn, ad)
        print(f"sync {time.perf_counter() - t0:.1f}s: {r['message']} status={r['status']}")
        for c in r["calendars"]:
            print(f"   {c['name']!r}: events={c['events']} complete={c['complete']} error={c['error']}")
        print("warnings:", r["warnings"])
        row = conn.execute("SELECT count(*), min(start_date), max(end_date), sum(outlook_series_id IS NOT NULL) FROM item").fetchone()
        print("items, min start, max end, recurring:", tuple(row))
        r2 = sync.run_sync(conn, ad)
        print("second sync:", r2["message"], r2["status"])
        bad = conn.execute("SELECT count(*) FROM item WHERE end_date < start_date").fetchone()[0]
        print("bad ranges:", bad)
except OutlookUnavailable as e:
    print("Outlook unavailable:", e)
