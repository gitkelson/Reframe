"""Vault backend evaluation gate (spec §10.3) -- SYNTHETIC files only, no private documents.

Run from the app root:  python tools/vault_bench/bench.py  [--big-mb 50]

Candidates
  (a) restic repository          -- measured only if ``restic`` is on PATH
  (b) separate Git object store  -- ``git hash-object -w`` into a throw-away bare repo (+ ``git gc`` for deltas)
  (c) content-addressed store    -- whole-file zlib blobs named by SHA-256 (``reframe.vault.store.VaultStore``),
                                     at zlib level 1 and level 6

Everything is written under ``tools/vault_bench/_work`` and deleted at the end. Results are printed as Markdown
and saved to ``tools/vault_bench/last_run.json``.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import io
import json
import os
import random
import shutil
import stat
import subprocess
import sys
import textwrap
import time
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parents[1]
sys.path.insert(0, str(APP / "src"))

from reframe.vault import capture             # noqa: E402
from reframe.vault import store as storemod   # noqa: E402
from reframe.vault.store import VaultStore     # noqa: E402

WORK = HERE / "_work"
RETRIES: list[str] = []
RNG = random.Random(20261124)
WORDS = ("torque bolt preload flange shaft bearing motor gear ratio load stress strain margin weld plate "
         "drawing revision check approve design study test report fixture clamp spindle axis servo").split()


# ------------------------------------------------------------------ synthetic data

def text_bytes(n: int, seed: int) -> bytes:
    r = random.Random(seed)
    out = io.StringIO()
    while out.tell() < n:
        out.write(" ".join(r.choice(WORDS) for _ in range(r.randint(6, 16))).capitalize() + ".\n")
    return out.getvalue()[:n].encode()


def pdf_like(n_streams: int, seed: int) -> bytes:
    """%PDF header + many deflated streams of random bytes: incompressible like real PDFs with images."""
    r = random.Random(seed)
    parts = [b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"]
    for i in range(n_streams):
        payload = zlib.compress(r.randbytes(64 * 1024), 6)
        parts.append(b"%d 0 obj\n<< /Length %d /Filter /FlateDecode >>\nstream\n" % (i + 1, len(payload)))
        parts.append(payload + b"\nendstream\nendobj\n")
    parts.append(b"trailer\n<< /Root 1 0 R >>\n%%EOF\n")
    return b"".join(parts)


def office_like(paragraphs: list[str], image: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        z.writestr("word/document.xml", "<w:document><w:body>" + "".join(
            f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs) + "</w:body></w:document>")
        z.writestr("word/styles.xml", "<w:styles>" + "<w:style/>" * 2000 + "</w:styles>")
        z.writestr("word/media/image1.png", image)
    return buf.getvalue()


def make_series(big_mb: int) -> dict[str, list[bytes]]:
    """Five versions of each file type (v0 + four edits)."""
    series: dict[str, list[bytes]] = {}
    t = text_bytes(2_000_000, 1)
    series["text (2 MB)"] = [t] + [t[: 500_000 * k] + text_bytes(2000, 10 + k) + t[500_000 * k:] for k in range(1, 5)]
    p = pdf_like(80, 2)
    pv = [p]
    for k in range(1, 5):
        prev = pv[-1]
        cut = len(prev) // 2
        pv.append(prev[:cut] + zlib.compress(RNG.randbytes(64 * 1024)) + prev[cut + 70_000:])
    series["PDF-like (5 MB)"] = pv
    paras = [" ".join(RNG.choice(WORDS) for _ in range(40)) for _ in range(3000)]
    img = RNG.randbytes(1_000_000)
    series["Office-like zip (~1.6 MB)"] = [office_like(paras[: 3000 - 10 * (4 - k)] + [f"edit {k}"], img) for k in range(5)]
    big = bytearray(RNG.randbytes(big_mb * 1024 * 1024))
    bv = [bytes(big)]
    for k in range(1, 5):
        off = (len(big) // 5) * k
        big[off:off + 4096] = RNG.randbytes(4096)
        bv.append(bytes(big))
    series[f"binary ({big_mb} MB, 4 KB edits)"] = bv
    return series


# ------------------------------------------------------------------ candidates

def rmtree(p) -> None:
    """shutil.rmtree that also removes read-only files (git packs) and long paths."""
    def onexc(func, path, exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    target = "\\\\?\\" + str(Path(p).resolve())
    if os.path.exists(target):
        shutil.rmtree(target, onexc=onexc)


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


class CasCandidate:
    def __init__(self, root: Path, level: int):
        self.level = level
        self.store = VaultStore(root)
        self.store.ensure()
        self.name = f"(c) CAS zlib-{level}"

    def capture(self, path: Path) -> str:
        storemod.ZLIB_LEVEL = self.level
        for attempt in range(5):        # the service retries a 'changed during read' capture later
            try:
                staged, _ = capture.read_stable(self.store, str(path))   # real path: stat + stage + re-read hash
                break
            except capture.CaptureError as e:
                RETRIES.append(f"{path.name}: {e}")
                time.sleep(1.0)
        else:
            raise RuntimeError("capture never stabilised")
        self.store.commit(staged)
        return staged.sha256

    def restore(self, ref: str, dest: Path) -> None:
        self.store.restore_to(ref, dest)

    def size(self) -> int:
        return dir_size(self.store.objects)

    def finish(self) -> None:
        pass


class GitCandidate:
    name = "(b) Git object store"

    def __init__(self, root: Path):
        self.root = root
        subprocess.run(["git", "init", "--bare", "-q", str(root)], check=True)
        self.env = dict(os.environ, GIT_DIR=str(root))
        self.n = 0

    def capture(self, path: Path) -> str:
        sha = subprocess.run(["git", "hash-object", "-w", "--no-filters", str(path)], env=self.env,
                             capture_output=True, check=True, text=True).stdout.strip()
        self.n += 1
        subprocess.run(["git", "update-ref", f"refs/v/{self.n}", sha], env=self.env, check=True)
        return sha

    def restore(self, ref: str, dest: Path) -> None:
        with open(dest, "xb") as out:
            subprocess.run(["git", "cat-file", "blob", ref], env=self.env, stdout=out, check=True)

    def size(self) -> int:
        return dir_size(self.root / "objects")

    def finish(self) -> float:
        t = time.perf_counter()
        subprocess.run(["git", "gc", "-q", "--prune=now"], env=self.env, check=True)
        return time.perf_counter() - t


# ------------------------------------------------------------------ measurements

def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def run_candidate(make, series: dict[str, list[bytes]]) -> dict:
    res: dict = {"files": {}}
    for label, versions in series.items():
        slug = label.split(" ")[0].lower().replace("-", "")
        root = WORK / f"repo-{abs(hash((label, id(make))))}"
        cand = make(root)
        res["name"] = cand.name
        src_dir = WORK / "src" / slug
        src_dir.mkdir(parents=True, exist_ok=True)
        path = src_dir / f"{slug}.bin"
        rows = []
        refs = []
        for i, data in enumerate(versions):
            path.write_bytes(data)
            before = cand.size()
            t = time.perf_counter()
            ref = cand.capture(path)
            dt = time.perf_counter() - t
            refs.append((ref, data))
            rows.append({"v": i, "logical": len(data), "added": cand.size() - before, "capture_s": dt})
        # duplicate: identical bytes under another name
        dup = src_dir / "copy-of-v4.bin"
        dup.write_bytes(versions[-1])
        before = cand.size()
        t = time.perf_counter()
        cand.capture(dup)
        dup_added, dup_s = cand.size() - before, time.perf_counter() - t
        # restore every version and verify
        restore_s = []
        ok = True
        for i, (ref, data) in enumerate(refs):
            dest = src_dir / f"restored-{i}.bin"
            t = time.perf_counter()
            cand.restore(ref, dest)
            restore_s.append(time.perf_counter() - t)
            ok &= sha(dest.read_bytes()) == sha(data)
        loose = cand.size()
        gc_s = cand.finish()
        res["files"][label] = {
            "versions": rows, "dup_added": dup_added, "dup_capture_s": dup_s, "restore_s": restore_s,
            "restore_verified": ok, "total_stored": cand.size(), "stored_before_gc": loose,
            "gc_s": gc_s, "logical_total": sum(len(v) for v in versions)}
        rmtree(root)
        rmtree(src_dir)
    return res


# ------------------------------------------------------------------ failure handling (candidate c)

def failure_checks() -> dict:
    out: dict = {}
    store = VaultStore(WORK / "fail-vault")
    store.ensure()

    # locked file: another handle with share mode 0
    p = WORK / "locked.txt"
    p.write_bytes(b"locked content")
    try:
        import win32con
        import win32file
        h = win32file.CreateFile(str(p), win32con.GENERIC_READ, 0, None, win32con.OPEN_EXISTING, 0, None)
        try:
            try:
                capture.read_stable(store, str(p))
                out["locked"] = "UNEXPECTED: read succeeded"
            except OSError as e:
                out["locked"] = (f"{type(e).__name__} winerror={getattr(e, 'winerror', None)} -> status "
                                 f"{capture.classify(e)[0]!r}; nothing written")
            gitr = subprocess.run(["git", "hash-object", str(p)], capture_output=True, text=True)
            out["locked_git"] = f"git exit {gitr.returncode}: {gitr.stderr.strip()[:90]}"
        finally:
            h.Close()
        out["locked_retry"] = "ok" if store.put_file(p)[0] == sha(b"locked content") else "FAILED"
    except ImportError:
        out["locked"] = "pywin32 missing"
    out["locked_staging_clean"] = not any(store.staging.iterdir())

    # Unicode + long path
    deep = WORK / "Ünïcødé – 日本語 ✓" / ("x" * 120) / ("y" * 120)
    long_dir = "\\\\?\\" + str(deep)
    os.makedirs(long_dir, exist_ok=True)
    up = long_dir + "\\Prüfbericht ✓.txt"
    with open(up, "wb") as fh:
        fh.write("Unicode-Inhalt ✓".encode())
    s, _, _ = store.put_file(up)
    store.restore_to(s, WORK / "unicode-restored.txt")
    out["unicode_long_path"] = (f"path length {len(up) - 4}; capture+restore verified="
                                f"{(WORK / 'unicode-restored.txt').read_bytes() == 'Unicode-Inhalt ✓'.encode()}")

    # unavailable network path
    unc = r"\\reframe-bench-nohost.invalid\share\file.txt"
    t = time.perf_counter()
    try:
        capture.read_stable(store, unc)
        out["unc"] = "UNEXPECTED: opened"
    except (OSError, capture.CaptureError) as e:
        out["unc"] = (f"{type(e).__name__} winerror={getattr(e, 'winerror', None)} after "
                      f"{time.perf_counter() - t:.2f} s -> status {capture.classify(e)[0]!r} (probe bounded by "
                      f"{capture.REMOTE_TIMEOUT_S:.0f} s; capture runs on a worker thread, never the UI)")

    # disk full while staging (simulated ENOSPC at flush)
    real = storemod._fsync

    def full(fh):
        raise OSError(errno.ENOSPC, "No space left on device")
    storemod._fsync = full
    try:
        store.put_bytes(b"disk full test" * 1000)
        out["disk_full"] = "UNEXPECTED: write succeeded"
    except OSError as e:
        out["disk_full"] = f"OSError errno={e.errno} ({errno.errorcode.get(e.errno)}) raised -> reported 'failed: disk full'"
    finally:
        storemod._fsync = real
    out["disk_full_staging_clean"] = not any(store.staging.iterdir())
    out["disk_full_no_object"] = not store.has(sha(b"disk full test" * 1000))

    # killed mid-write
    child = textwrap.dedent(f"""
        import sys, time, io
        sys.path.insert(0, {str(APP / 'src')!r})
        from reframe.vault.store import VaultStore
        class Slow(io.RawIOBase):
            def readable(self): return True
            def read(self, n=-1):
                time.sleep(0.05); return b"z" * 65536
        VaultStore({str(store.root)!r}).stage_stream(Slow())
    """)
    proc = subprocess.Popen([sys.executable, "-c", child])
    deadline = time.time() + 20
    while time.time() < deadline and not any(store.staging.glob("*.part")):
        time.sleep(0.05)
    time.sleep(0.5)
    proc.kill()
    proc.wait()
    leftovers = len(list(store.staging.glob("*.part")))
    objects_before = sum(1 for _ in store.iter_shas())
    removed = store.recover()
    out["killed"] = (f"after kill: {leftovers} staging file(s), 0 partial objects "
                     f"(objects unchanged={objects_before == sum(1 for _ in store.iter_shas())}); "
                     f"recover() removed {removed}; all blobs verify={all(store.verify(x) for x in store.iter_shas())}")
    return out


# ------------------------------------------------------------------ report

def fmt_b(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return str(n)


def report(results: list[dict], fails: dict, tools: dict) -> str:
    lines = ["## Tools", "", *(f"- `{k}`: {v}" for k, v in tools.items()), ""]
    for r in results:
        lines += [f"## {r['name']}", "",
                  "| File | first copy | incr. v1-v4 (each) | stored total / logical | dup add | capture v0 | restore (avg) | verified |",
                  "|---|---|---|---|---|---|---|---|"]
        for label, f in r["files"].items():
            v = f["versions"]
            incr = ", ".join(fmt_b(x["added"]) for x in v[1:])
            extra = f" (before gc {fmt_b(f['stored_before_gc'])}, gc {f['gc_s']:.1f} s)" if f.get("gc_s") else ""
            lines.append(
                f"| {label} | {fmt_b(v[0]['added'])} of {fmt_b(v[0]['logical'])} | {incr} | "
                f"{fmt_b(f['total_stored'])} / {fmt_b(f['logical_total'])}{extra} | {fmt_b(f['dup_added'])} | "
                f"{v[0]['capture_s'] * 1000:.0f} ms | {sum(f['restore_s']) / len(f['restore_s']) * 1000:.0f} ms | "
                f"{f['restore_verified']} |")
        lines.append("")
    lines += ["## Failure handling (candidate c)", "", *(f"- **{k}**: {v}" for k, v in fails.items()), "",
              "## 'Changed during read' retries observed during the run", "",
              *(f"- {r}" for r in RETRIES or ["none"]), ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--big-mb", type=int, default=50)
    ap.add_argument("--skip-git", action="store_true")
    args = ap.parse_args()
    rmtree(WORK)
    WORK.mkdir(parents=True)
    try:
        tools = {"restic": shutil.which("restic") or "NOT on PATH (candidate a not measurable here)",
                 "git": shutil.which("git") or "NOT on PATH"}
        if shutil.which("git"):
            tools["git"] += " - " + subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
        series = make_series(args.big_mb)
        results = [run_candidate(lambda r: CasCandidate(r, 1), series),
                   run_candidate(lambda r: CasCandidate(r, 6), series)]
        if shutil.which("git") and not args.skip_git:
            results.append(run_candidate(GitCandidate, series))
        fails = failure_checks()
        md = report(results, fails, tools)
        print(md)
        (HERE / "last_run.json").write_text(json.dumps({"tools": tools, "results": results, "failures": fails},
                                                       indent=1), encoding="utf-8")
    finally:
        storemod.ZLIB_LEVEL = 1
        rmtree(WORK)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
