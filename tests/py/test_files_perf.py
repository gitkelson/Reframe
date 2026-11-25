"""File timeline scale check (spec 4.2: budgets are measured, not guaranteed). 10,000 synthetic records, no disk:
index insert and the sorted compact payload for both date modes. Run with -s to see timings."""
import datetime as dt
import random
import time

from reframe.files.index import FileIndex
from reframe.files.scanner import FileRecord

N = 10_000


def synthetic(n, roots=2, seed=7):
    rnd = random.Random(seed)
    folders = ["Trips", "trips", "Work", "Family", "2019", "2020", "Scans", "Invoices", "Raw", "Edited", "Phone"]
    base = dt.datetime(2015, 1, 1).timestamp()
    out = []
    for i in range(n):
        root = i % roots
        depth = rnd.randint(0, 4)
        parts = tuple(rnd.choice(folders) for _ in range(depth))
        # every 10th file lands on one dense day
        t = dt.datetime(2022, 7, 4, 12).timestamp() if i % 10 == 0 else base + rnd.random() * 11 * 365 * 86400
        ext = rnd.choice([".jpg", ".jpg", ".png", ".pdf", ".docx", ".txt", ""])
        name = f"file{i:05d}{ext}"
        path = f"C:\\Root{root}\\" + "\\".join(parts + (name,))
        out.append(FileRecord(path, root, parts, name, rnd.randint(1, 10**7), t, t - 86400, 0, False))
    return out


def test_ten_thousand_entries_payload():
    recs = synthetic(N)
    t0 = time.perf_counter()
    idx = FileIndex()
    idx.reset(["C:\\Root0", "C:\\Root1"])
    idx.add_batch(recs, idx.begin())
    t1 = time.perf_counter()
    p_mod = idx.payload("modified")
    t3 = time.perf_counter()
    p_cre = idx.payload("created")
    t4 = time.perf_counter()
    assert len(p_mod["rows"]) == N and len(p_cre["rows"]) + len(p_cre["undated"]) == N
    print(f"\n[files perf] index insert {1000 * (t1 - t0):.0f} ms; "
          f"payload modified {1000 * (t3 - t1):.0f} ms; payload created {1000 * (t4 - t3):.0f} ms (N={N})")
    assert t4 - t0 < 10.0          # coarse guard only; the measured values are reported, not asserted tightly
