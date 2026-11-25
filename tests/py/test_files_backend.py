"""File timeline backend: roots, exclusions, scanner, dates/EXIF, index, thumbnails, service refresh."""
import ctypes
import datetime as dt
import hashlib
import io
import os
import subprocess
import time

import pytest
from PIL import Image

from reframe.files import dates as datesmod
from reframe.files import scanner as scannermod
from reframe.files.index import FileIndex
from reframe.files.roots import RootsError, is_within, normalize_root, root_name, validate_roots
from reframe.files.scanner import Exclusions, normalize_exclusion, scan, suggested_exclusions
from reframe.files.service import FileTimelineService, OpenRefused
from reframe.files.thumbs import ThumbCache, ThumbService, cache_key, make_thumbnail


def write(p, data=b"x", mtime=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def ts(y, m, d, hh=12):
    return dt.datetime(y, m, d, hh).timestamp()


def jpeg(exif_tags=None, size=(40, 30), color="red"):
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    if exif_tags:
        exif = Image.Exif()
        ifd = exif.get_ifd(0x8769)
        for k, v in exif_tags.items():
            ifd[k] = v
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "JPEG")
    return buf.getvalue()


def run_scan(roots, rules=(), include_hidden=False, **kw):
    got = []
    reports = scan(list(roots), exclusions=Exclusions(list(rules)), include_hidden=include_hidden,
                   on_batch=got.extend, **kw)
    return got, reports


@pytest.fixture
def svc_factory(conn, db_path, tmp_path):
    made = []

    class Launcher:
        def __init__(self):
            self.calls = []

        def open(self, p): self.calls.append(("open", p))
        def reveal(self, p): self.calls.append(("reveal", p))

    def make(**kw):
        kw.setdefault("watcher_factory", None)
        kw.setdefault("reconcile_s", 0)
        kw.setdefault("cache_dir", tmp_path / "thumbcache")
        kw.setdefault("launcher", Launcher())
        s = FileTimelineService(db_path, None, **kw)
        made.append(s)
        return s
    yield make
    for s in made:
        s.stop()


# ------------------------------------------------------------------ roots

def test_roots_refuse_duplicates_and_nesting(tmp_path):
    a, b = tmp_path / "A", tmp_path / "A" / "B"
    b.mkdir(parents=True)
    with pytest.raises(RootsError) as e:
        validate_roots([str(a), str(a).upper() + "\\"])
    assert "twice" in e.value.problems[0]
    with pytest.raises(RootsError) as e:
        validate_roots([str(a), str(b)])
    assert "inside the root" in e.value.problems[0]
    with pytest.raises(RootsError) as e:
        validate_roots([str(b), str(a)])
    assert "contains the root" in e.value.problems[0]
    with pytest.raises(RootsError):
        validate_roots(["relative\\path"])
    ok, warnings = validate_roots([str(a), str(tmp_path / "missing")])
    assert len(ok) == 2 and len(warnings) == 1 and "cannot be reached" in warnings[0]


def test_root_normalization_and_names():
    assert normalize_root("c:/Photos/") == "C:\\Photos"
    assert normalize_root("C:") == "C:\\"
    assert normalize_root("\\\\srv\\share\\") == "\\\\srv\\share"
    assert root_name("C:\\Users\\me\\Pictures") == "Pictures"
    assert root_name("\\\\srv\\share") == "share"
    assert is_within("C:\\a\\B\\c.txt", "c:\\A") and not is_within("C:\\ab\\c.txt", "C:\\a")


# --------------------------------------------------------------- exclusions

def test_exclusion_rules_dotless_multipart_patterns_and_names():
    assert normalize_exclusion("TMP") == ".tmp"
    assert normalize_exclusion("") == "(none)" and normalize_exclusion("(none)") == "(none)"
    assert normalize_exclusion("Thumbs.DB") == "thumbs.db"
    assert normalize_exclusion("..\\x") is None
    ex = Exclusions(["(none)", ".tar.gz", "~$*", "thumbs.db", ".TMP"])
    assert ex.excluded("README") and ex.excluded("Makefile")
    assert ex.excluded("backup.TAR.GZ") and not ex.excluded("backup.gz")
    assert ex.excluded("~$Report.docx") and not ex.excluded("Report.docx")
    assert ex.excluded("Thumbs.db") and not ex.excluded("mythumbs.db")
    assert ex.excluded("a.tmp") and ex.excluded("B.Tmp")
    assert not Exclusions([]).excluded("README")
    assert set(suggested_exclusions()) >= {".tmp", ".bak", ".lnk", "~$*", ".ini", "thumbs.db", "desktop.ini"}


def test_suggestions_are_not_applied_by_default(svc_factory, tmp_path):
    root = tmp_path / "R"
    write(root / "a.tmp")
    write(root / "Thumbs.db")
    s = svc_factory()
    s.set_options(roots=[str(root)])
    assert s.options()["excluded_ext"] == []
    s.wait_idle()
    assert s.index.count() == 2


def test_scan_applies_exclusions(tmp_path):
    root = tmp_path / "R"
    for n in ["README", "x.tar.gz", "~$doc.docx", "doc.docx", "thumbs.db", "y.TMP"]:
        write(root / n)
    got, reps = run_scan([str(root)], ["(none)", ".tar.gz", "~$*", "Thumbs.db", ".tmp"])
    assert sorted(r.name for r in got) == ["doc.docx"]
    assert reps[0].excluded == 5 and reps[0].status == "ok"


# ------------------------------------------------------------------ scanner

def _set_attrs(p, attrs):
    assert ctypes.windll.kernel32.SetFileAttributesW(str(p), attrs)


def test_hidden_and_system_toggle(tmp_path):
    root = tmp_path / "R"
    write(root / "visible.txt")
    h = write(root / "hidden.txt")
    s = write(root / "system.txt")
    hd = root / "HiddenDir"
    write(hd / "inside.txt")
    _set_attrs(h, 0x2)
    _set_attrs(s, 0x4)
    _set_attrs(hd, 0x2 | 0x10)
    try:
        got, reps = run_scan([str(root)])
        assert [r.name for r in got] == ["visible.txt"]
        assert reps[0].hidden_skipped == 3
        got, _ = run_scan([str(root)], include_hidden=True)
        assert sorted(r.name for r in got) == ["hidden.txt", "inside.txt", "system.txt", "visible.txt"]
    finally:
        for p in (h, s):
            _set_attrs(p, 0x80)
        _set_attrs(hd, 0x10)


def test_junction_not_followed(tmp_path):
    root, outside = tmp_path / "R", tmp_path / "Outside"
    write(root / "a.txt")
    write(outside / "secret.txt")
    link = root / "link"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
    if r.returncode != 0 or not link.exists():
        pytest.skip("cannot create a junction here")
    got, reps = run_scan([str(root)])
    assert [g.name for g in got] == ["a.txt"]
    assert reps[0].links_skipped == 1


def test_unreachable_root_and_denied_folder_reported(tmp_path, monkeypatch):
    root = tmp_path / "R"
    write(root / "ok" / "a.txt")
    write(root / "locked" / "b.txt")
    (root / "emptydir").mkdir()
    real = os.scandir

    def fake(p):
        if str(p).endswith("locked"):
            raise PermissionError(5, "Access is denied")
        return real(p)
    monkeypatch.setattr(scannermod.os, "scandir", fake)
    got, reps = run_scan([str(root), str(tmp_path / "gone")])
    assert [g.name for g in got] == ["a.txt"]
    assert reps[0].status == "partial" and reps[0].denied == ["locked"] and "denied" in reps[0].message
    assert reps[1].status == "unreachable" and "Cannot reach" in reps[1].message


def test_offline_share_root_reported_unreachable(monkeypatch):
    real = os.stat

    def fake(p, *a, **k):
        if str(p).startswith("\\\\offline"):
            raise OSError(53, "The network path was not found")
        return real(p, *a, **k)
    monkeypatch.setattr(scannermod.os, "stat", fake)
    _, reps = run_scan(["\\\\offline\\share"])
    assert reps[0].status == "unreachable" and "network path" in reps[0].message


def test_empty_root_distinguished(tmp_path):
    (tmp_path / "E").mkdir()
    _, reps = run_scan([str(tmp_path / "E")])
    assert reps[0].status == "empty"


def test_cloud_placeholder_listed_without_opening(tmp_path, monkeypatch):
    root = tmp_path / "R"
    p = write(root / "cloud.jpg", b"not really")
    write(root / "cloud.mp4", b"not really")
    assert p.exists()

    class St:
        def __init__(self, st):
            self._st = st
            self.st_file_attributes = st.st_file_attributes | scannermod.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS

        def __getattr__(self, k):
            return getattr(self._st, k)

    class E:
        def __init__(self, e): self._e = e
        def __getattr__(self, k): return getattr(self._e, k)
        def stat(self, follow_symlinks=True): return St(self._e.stat(follow_symlinks=follow_symlinks))

    class It:
        def __init__(self, it): self._it = it
        def __enter__(self): return self
        def __exit__(self, *a): self._it.close()
        def __iter__(self): return self
        def __next__(self): return E(next(self._it))

    real = os.scandir
    monkeypatch.setattr(scannermod.os, "scandir", lambda d: It(real(d)))
    got, reps = run_scan([str(root)])
    assert all(r.online_only for r in got) and reps[0].online_only == 2
    idx = FileIndex()
    idx.reset([str(root)])
    idx.add_batch(got, idx.begin())
    assert idx.pending_exif(10) == []             # never read for a date


# -------------------------------------------------------------------- dates

def test_created_vs_modified_and_exif_variants(tmp_path):
    taken = {0x9003: "2019:05:06 07:08:09"}
    with_offset = {0x9003: "2019:05:06 23:30:00", 0x9011: "-07:00"}
    digitized_only = {0x9004: "2018:01:02 03:04:05"}
    malformed = {0x9003: "0000:00:00 00:00:00"}
    root = tmp_path / "R"
    files = {
        "plain.txt": write(root / "plain.txt", b"t", ts(2020, 3, 4)),
        "taken.jpg": write(root / "taken.jpg", jpeg(taken), ts(2021, 1, 1)),
        "offset.jpg": write(root / "offset.jpg", jpeg(with_offset), ts(2021, 1, 1)),
        "digitized.jpg": write(root / "digitized.jpg", jpeg(digitized_only), ts(2021, 1, 1)),
        "bad.jpg": write(root / "bad.jpg", jpeg(malformed), ts(2021, 1, 1)),
        "noexif.jpg": write(root / "noexif.jpg", jpeg(), ts(2021, 1, 1)),
        "broken.jpg": write(root / "broken.jpg", b"\xff\xd8garbage", ts(2021, 1, 1)),
    }
    assert datesmod.read_exif_taken(str(files["taken.jpg"]))[1].day == dt.date(2019, 5, 6)
    off = datesmod.read_exif_taken(str(files["offset.jpg"]))[1]
    expected = dt.datetime(2019, 5, 6, 23, 30, tzinfo=dt.timezone(dt.timedelta(hours=-7))).astimezone().date()
    assert off.offset == "-07:00" and off.day == expected
    assert datesmod.read_exif_taken(str(files["digitized.jpg"]))[1].tag == "DateTimeDigitized"
    assert datesmod.read_exif_taken(str(files["bad.jpg"])) == ("none", None)
    assert datesmod.read_exif_taken(str(files["noexif.jpg"])) == ("none", None)
    assert datesmod.read_exif_taken(str(files["broken.jpg"]))[0] == "unreadable"

    got, _ = run_scan([str(root)])
    idx = FileIndex()
    idx.reset([str(root)])
    idx.add_batch(got, idx.begin())
    for eid, path, size, mtime in idx.pending_exif(100):
        st, t = datesmod.read_exif_taken(path)
        idx.set_exif(eid, size, mtime, "ok" if st == "ok" else st, t)
    today = dt.date.today()
    rows = {r[1]: r for r in idx.payload("created")["rows"]}
    day = lambda d: datesmod.day_number(d)  # noqa: E731
    assert rows["taken.jpg"][4] == day(dt.date(2019, 5, 6)) and rows["taken.jpg"][5] == 0
    assert rows["digitized.jpg"][4] == day(dt.date(2018, 1, 2))
    for n in ("plain.txt", "bad.jpg", "noexif.jpg", "broken.jpg"):
        assert rows[n][5] == 1 and rows[n][4] == day(today), n      # fell back to file created (today)
    mrows = {r[1]: r for r in idx.payload("modified")["rows"]}
    assert mrows["plain.txt"][4] == day(dt.date(2020, 3, 4)) and mrows["plain.txt"][5] == 2
    assert mrows["taken.jpg"][4] == day(dt.date(2021, 1, 1))


def test_undated_and_tie_order():
    assert datesmod.choose("modified", ctime=0, mtime=0, taken=None)[0] is None
    assert datesmod.choose("created", ctime=float("nan"), mtime=ts(2020, 1, 1), taken=None)[0] is None
    idx = FileIndex()
    idx.reset(["C:\\R"])
    same = ts(2022, 2, 2)
    recs = [scannermod.FileRecord(f"C:\\R\\{n}", 0, (), n, 1, same, same, 0, False) for n in ("b.txt", "A.txt", "c.txt")]
    recs.append(scannermod.FileRecord("C:\\R\\zero.txt", 0, (), "zero.txt", 1, 0.0, 0.0, 0, False))
    idx.add_batch(recs, idx.begin())
    p = idx.payload("modified")
    assert [r[1] for r in p["rows"]] == ["A.txt", "b.txt", "c.txt"]
    assert [r[1] for r in p["undated"]] == ["zero.txt"]


# --------------------------------------------------------------- thumbnails

def test_thumbnail_orientation_aspect_and_bomb_guard(tmp_path):
    p = tmp_path / "wide.jpg"
    img = Image.new("RGB", (400, 100), "blue")
    exif = Image.Exif()
    exif[0x0112] = 6                                  # rotate 90 degrees on display
    img.save(p, "JPEG", exif=exif)
    r = make_thumbnail(str(p))
    assert r.status == "ok" and r.mimetype == "image/jpeg"
    with Image.open(io.BytesIO(r.data)) as t:
        assert t.size[1] > t.size[0] and max(t.size) <= 256 and abs(t.size[1] / t.size[0] - 4) < 0.1
    bomb = tmp_path / "bomb.png"
    Image.new("1", (10000, 6000)).save(bomb)          # 60 MP, a few kB on disk
    assert bomb.stat().st_size < 200_000
    assert make_thumbnail(str(bomb)).reason == "too large"
    gif = tmp_path / "anim.gif"
    frames = [Image.new("P", (20, 10), i) for i in range(3)]
    frames[0].save(gif, save_all=True, append_images=frames[1:])
    assert make_thumbnail(str(gif)).status == "ok"
    bad = write(tmp_path / "bad.png", b"\x89PNG\r\n\x1a\nnope")
    assert make_thumbnail(str(bad)).status == "placeholder"
    assert make_thumbnail(str(tmp_path / "missing.jpg")).reason == "inaccessible"


def test_cache_eviction_never_touches_originals(tmp_path):
    src = tmp_path / "originals"
    originals = []
    for i in range(12):
        p = src / f"img{i}.jpg"
        p.parent.mkdir(exist_ok=True)
        Image.effect_noise((200, 200), 60 + i).convert("RGB").save(p, "JPEG", quality=95)
        originals.append((p, hashlib.sha1(p.read_bytes()).hexdigest()))
    cache_dir = tmp_path / "cache"
    svc = ThumbService(cache_dir, max_bytes=40_000, workers=2)
    try:
        for p, _ in originals:
            st = p.stat()
            assert svc.get(str(p), str(p).lower(), st.st_size, st.st_mtime).status == "ok"
    finally:
        svc.shutdown()
    total = sum(f.stat().st_size for f in cache_dir.iterdir())
    assert total <= 40_000
    assert len(list(cache_dir.iterdir())) < len(originals)
    for p, h in originals:
        assert p.exists() and hashlib.sha1(p.read_bytes()).hexdigest() == h
    # a cache that someone pointed at a folder with other files deletes only its own cache names
    stray = write(cache_dir / "keep-me.jpg", b"x" * 50_000)
    ThumbCache(cache_dir, max_bytes=1).evict()
    assert stray.exists()


def test_cache_key_changes_with_identity():
    assert cache_key("c:\\a.jpg", 1, 2.0) != cache_key("c:\\a.jpg", 1, 3.0)
    assert cache_key("c:\\a.jpg", 1, 2.0) != cache_key("c:\\a.jpg", 2, 2.0)


# ------------------------------------------------------------------ service

def test_refresh_after_create_rename_delete(svc_factory, tmp_path):
    root = tmp_path / "R"
    write(root / "keep.txt")
    write(root / "old.txt")
    write(root / "gone.txt")
    s = svc_factory()
    s.set_options(roots=[str(root)])
    assert s.wait_idle()
    ids = {e.name: e.id for e in s.index.by_id.values()}
    (root / "old.txt").rename(root / "new.txt")
    (root / "gone.txt").unlink()
    write(root / "sub" / "created.txt")
    s.rescan()
    assert s.wait_idle()
    now = {e.name: e.id for e in s.index.by_id.values()}
    assert sorted(now) == ["created.txt", "keep.txt", "new.txt"]
    assert now["keep.txt"] == ids["keep.txt"]           # stable id for unchanged path within the session
    assert s.status()["state"] == "idle" and s.status()["roots"][0]["status"] == "ok"


def test_options_persist_and_root_change_resets(svc_factory, tmp_path, conn):
    a, b = tmp_path / "A", tmp_path / "B"
    write(a / "x" / "1.txt")
    write(b / "2.txt")
    s = svc_factory()
    out = s.set_options(roots=[str(a)], excluded_ext=["TMP", "~$*"], include_hidden=True)
    assert out["options"]["excluded_ext"] == [".tmp", "~$*"]
    from reframe.domain import settings
    assert settings.get(conn, "files_roots") == [str(a)]
    assert settings.get(conn, "files_excluded_ext") == [".tmp", "~$*"]
    assert settings.get(conn, "files_include_hidden") is True
    s.wait_idle()
    old_id = next(iter(s.index.by_id))
    s.set_options(roots=[str(a), str(b)])
    s.wait_idle()
    assert s.index.get(old_id) is None
    p = s.entries("modified")
    assert {dict(zip(p["columns"], r))["dir"] for r in p["rows"]} == {"A\\x", "B"}
    with pytest.raises(ValueError):
        s.set_options(excluded_ext=["a\\b"])


def test_day_with_zero_images_and_day_with_many(svc_factory, tmp_path):
    root = tmp_path / "R"
    busy, quiet = ts(2023, 6, 1), ts(2023, 6, 2)
    for i in range(220):
        write(root / ("Cam" if i % 2 else "Phone") / f"IMG_{i:04d}.jpg", b"j", busy)
    for i in range(5):
        write(root / "Docs" / f"note{i}.txt", b"t", quiet)
    s = svc_factory(exif=False)
    s.set_options(roots=[str(root)])
    s.wait_idle()
    p = s.entries("modified")
    by_day = {}
    for r in p["rows"]:
        by_day.setdefault(r[4], []).append(r)
    d1, d2 = datesmod.day_number(dt.date(2023, 6, 1)), datesmod.day_number(dt.date(2023, 6, 2))
    assert len(by_day[d1]) == 220 and all(r[3] == 1 for r in by_day[d1])
    assert len(by_day[d2]) == 5 and all(r[3] == 0 for r in by_day[d2])
    assert sum(1 for r in by_day[d1] if dict(zip(p["columns"], r))["dir"] == "Cam") == 110
    assert all(s.details(r[0])["name"] == r[1] for r in by_day[d1])    # every entry reachable


def test_open_revalidates(svc_factory, tmp_path):
    root = tmp_path / "R"
    f = write(root / "a.txt")
    s = svc_factory()
    s.set_options(roots=[str(root)])
    s.wait_idle()
    eid = next(iter(s.index.by_id))
    assert s.open(eid) == str(f) and s.launcher.calls == [("open", str(f))]
    s.reveal(eid)
    assert s.launcher.calls[-1] == ("reveal", str(f))
    s.index.by_id[eid].path = str(tmp_path / "elsewhere.txt")   # an entry pointing outside the root
    with pytest.raises(OpenRefused) as e:
        s.open(eid)
    assert e.value.status == 403
    s.index.by_id[eid].path = str(f)
    f.unlink()
    with pytest.raises(OpenRefused) as e:
        s.open(eid)
    assert e.value.status == 410
    with pytest.raises(OpenRefused) as e:
        s.open("no-such-id")
    assert e.value.status == 404
    assert len(s.launcher.calls) == 2


def test_service_exif_resolves_and_marks_pending(svc_factory, tmp_path):
    root = tmp_path / "R"
    write(root / "p.jpg", jpeg({0x9003: "2015:07:04 10:00:00"}), ts(2021, 1, 1))
    s = svc_factory(exif=False)
    s.set_options(roots=[str(root)])
    s.wait_idle()
    row = s.entries("created")["rows"][0]
    assert row[9] == 1                                  # provisional until the date taken is read
    s.process_exif_now()
    row = s.entries("created")["rows"][0]
    assert row[9] == 0 and row[4] == datesmod.day_number(dt.date(2015, 7, 4)) and row[5] == 0
    d = s.details(row[0])
    assert d["modes"]["created"]["source_label"] == "Date taken (EXIF)"
    assert d["modes"]["modified"]["source_label"] == "File modified"
    assert d["taken"]["value"] == "2015-07-04 10:00:00"


def test_mp4_media_created_placement_fallback_and_modified_mode(svc_factory, tmp_path, monkeypatch):
    root = tmp_path / "R"
    for name in ("taken.mp4", "missing.MP4", "broken.mp4"):
        write(root / name, b"video", ts(2021, 1, 1))
    recorded = dt.datetime(2015, 7, 4, 1, 0, tzinfo=dt.timezone.utc)

    def media_date(path):
        if path.lower().endswith("broken.mp4"):
            raise OSError("metadata unreadable")
        return recorded if path.lower().endswith("taken.mp4") else None

    monkeypatch.setattr(datesmod, "_read_media_created", media_date)
    s = svc_factory(exif=False)
    s.set_options(roots=[str(root)])
    assert s.wait_idle()
    before = {r[1]: r for r in s.entries("created")["rows"]}
    assert all(r[9] == 1 for r in before.values())
    assert s.process_exif_now() == 3
    result = s.entries("created")
    rows = {r[1]: dict(zip(result["columns"], r)) for r in result["rows"]}
    assert rows["taken.mp4"]["day"] == datesmod.day_number(recorded.astimezone().date())
    assert result["sources"][str(rows["taken.mp4"]["src"])] == "Media created"
    assert rows["missing.MP4"]["src"] == 1 and rows["broken.mp4"]["src"] == 1
    assert all(r["pending"] == 0 for r in rows.values())
    details = s.details(rows["taken.mp4"]["id"])
    assert details["taken"]["value"] == "2015-07-04 01:00:00"
    assert details["taken"]["offset"] == "+00:00"
    assert details["modes"]["created"]["source_label"] == "Media created"
    assert s.details(rows["broken.mp4"]["id"])["taken"]["status"] == "unreadable"
    modified = s.entries("modified")
    assert all(r[4] == datesmod.day_number(dt.date(2021, 1, 1)) and r[5] == 2 for r in modified["rows"])


def test_mp4_media_created_rejects_missing_invalid_and_epoch_values(monkeypatch):
    for value in (None, dt.datetime(1904, 1, 1, tzinfo=dt.timezone.utc),
                  dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc), dt.datetime(2020, 1, 1)):
        monkeypatch.setattr(datesmod, "_read_media_created", lambda path: value)
        assert datesmod.read_media_taken("test.mp4") == ("none", None)


def test_watcher_injection_triggers_rescan(svc_factory, tmp_path):
    root = tmp_path / "R"
    write(root / "a.txt")
    made = []

    class FakeWatcher:
        def __init__(self, r, i, cb): self.cb, self.stopped = cb, False
        def stop(self): self.stopped = True

    def factory(r, i, cb):
        w = FakeWatcher(r, i, cb)
        made.append(w)
        return w
    s = svc_factory(watcher_factory=factory)
    s._debouncer.quiet_s = 0.05
    s.start()
    s.set_options(roots=[str(root)])
    s.wait_idle()
    assert len(made) == 1
    write(root / "b.txt")
    made[0].cb(0, "overflow")
    assert s.status()["stale"]
    time.sleep(0.3)
    assert s.wait_idle()
    assert s.index.count() == 2 and not s.status()["stale"]
    s.stop()
    assert made[0].stopped
