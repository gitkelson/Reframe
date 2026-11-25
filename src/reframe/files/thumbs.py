"""Thumbnails for the File timeline (R-153, R-154).

Images are untrusted: decoding happens on a small bounded worker pool, never on a request thread's critical path
without a bound, with a maximum pixel count (decompression-bomb guard), EXIF orientation, aspect ratio preserved,
the first frame of animations, and a size cap. Results are cached on disk under ``<data_dir>/cache/thumbs`` keyed by
sha1(path key | size | mtime), bounded in total size with least-recently-used eviction. Only cache files are
ever deleted; originals are opened read-only. Failures become placeholder reasons, never exceptions.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import threading
import time
import warnings
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

THUMB_PX = 256
MAX_PIXELS = 50_000_000
CACHE_MAX_BYTES = 200 * 1024 * 1024
WORKERS = 3
QUEUE_CAP = 48
WAIT_S = 20.0

REASON_TOO_LARGE = "too large"
REASON_UNSUPPORTED = "unsupported"
REASON_CORRUPT = "corrupt"
REASON_INACCESSIBLE = "inaccessible"
REASON_ONLINE_ONLY = "online-only"
REASON_BUSY = "busy"
REASON_CANCELLED = "cancelled"

_CACHE_NAME = re.compile(r"^[0-9a-f]{40}\.(jpg|png)$")

try:
    from PIL import Image
    warnings.simplefilter("ignore", Image.DecompressionBombWarning)
except Exception:  # pragma: no cover - Pillow is a declared dependency
    Image = None


@dataclass(frozen=True)
class ThumbResult:
    status: str                  # 'ok' | 'placeholder'
    data: bytes = b""
    mimetype: str = ""
    reason: str = ""


def cache_key(path_key: str, size: int, mtime: float) -> str:
    return hashlib.sha1(f"{path_key}|{size}|{mtime!r}".encode("utf-8", "surrogatepass")).hexdigest()


def make_thumbnail(path: str, *, px: int = THUMB_PX, max_pixels: int = MAX_PIXELS) -> ThumbResult:
    """Decode one image into a JPEG (opaque) or PNG (with transparency) no larger than px x px."""
    from PIL import Image as PILImage, ImageOps, UnidentifiedImageError

    try:
        with PILImage.open(path) as im:
            w, h = im.size
            if w <= 0 or h <= 0:
                return ThumbResult("placeholder", reason=REASON_CORRUPT)
            if w * h > max_pixels:
                return ThumbResult("placeholder", reason=REASON_TOO_LARGE)
            if getattr(im, "is_animated", False):
                im.seek(0)
            try:
                im.draft("RGB", (px, px))          # JPEG: decode at reduced scale
            except Exception:
                pass
            im.load()
            img = ImageOps.exif_transpose(im) or im
            img.thumbnail((px, px))
            alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
            buf = io.BytesIO()
            if alpha:
                img.convert("RGBA").save(buf, "PNG", optimize=False)
                return ThumbResult("ok", buf.getvalue(), "image/png")
            img.convert("RGB").save(buf, "JPEG", quality=82)
            return ThumbResult("ok", buf.getvalue(), "image/jpeg")
    except PILImage.DecompressionBombError:
        return ThumbResult("placeholder", reason=REASON_TOO_LARGE)
    except UnidentifiedImageError:
        return ThumbResult("placeholder", reason=REASON_UNSUPPORTED)
    except (PermissionError, FileNotFoundError):
        return ThumbResult("placeholder", reason=REASON_INACCESSIBLE)
    except OSError:
        return ThumbResult("placeholder", reason=REASON_CORRUPT)
    except (MemoryError, ValueError, SyntaxError, EOFError, IndexError, TypeError):
        return ThumbResult("placeholder", reason=REASON_CORRUPT)
    except Exception:
        return ThumbResult("placeholder", reason=REASON_CORRUPT)


class ThumbCache:
    """Bounded on-disk cache. Eviction removes the least recently used cache files only."""

    def __init__(self, folder: Path, max_bytes: int = CACHE_MAX_BYTES):
        self.folder = Path(folder)
        self.max_bytes = max_bytes
        self.lock = threading.Lock()
        self._total: int | None = None

    def _files(self) -> list[os.DirEntry]:
        try:
            with os.scandir(self.folder) as it:
                return [e for e in it if e.is_file(follow_symlinks=False) and _CACHE_NAME.match(e.name)]
        except OSError:
            return []

    def total_bytes(self) -> int:
        with self.lock:
            if self._total is None:
                self._total = sum(e.stat().st_size for e in self._files())
            return self._total

    def get(self, key: str) -> ThumbResult | None:
        for ext, mime in (("jpg", "image/jpeg"), ("png", "image/png")):
            p = self.folder / f"{key}.{ext}"
            try:
                data = p.read_bytes()
            except OSError:
                continue
            try:
                now = time.time()
                os.utime(p, (now, p.stat().st_mtime))       # atime = last use (NTFS may not track it)
            except OSError:
                pass
            return ThumbResult("ok", data, mime)
        return None

    def put(self, key: str, res: ThumbResult) -> None:
        if res.status != "ok":
            return
        ext = "png" if res.mimetype == "image/png" else "jpg"
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self.folder / f"{key}.{ext}.part"
            tmp.write_bytes(res.data)
            os.replace(tmp, self.folder / f"{key}.{ext}")
        except OSError:
            return
        self.total_bytes()
        with self.lock:
            self._total = (self._total or 0) + len(res.data)
            over = self._total > self.max_bytes
        if over:
            self.evict()

    def evict(self, target_ratio: float = 0.9) -> int:
        """Delete least-recently-used cache files until the cache is under target_ratio x max. Returns files removed."""
        with self.lock:
            files = []
            for e in self._files():
                try:
                    st = e.stat()
                except OSError:
                    continue
                files.append((st.st_atime, e.name, st.st_size))
            total = sum(f[2] for f in files)
            removed = 0
            target = self.max_bytes * target_ratio
            for _, name, size in sorted(files):
                if total <= target:
                    break
                p = self.folder / name
                if p.parent != self.folder or not _CACHE_NAME.match(p.name):
                    continue
                try:
                    p.unlink()
                    total -= size
                    removed += 1
                except OSError:
                    pass
            self._total = total
            return removed


class ThumbService:
    """Bounded decoding pool + disk cache + small negative cache. ``get`` blocks the calling request thread only
    while its own job runs (bounded by WAIT_S); when the queue is full it answers 'busy' immediately."""

    def __init__(self, cache_dir: Path, *, workers: int = WORKERS, queue_cap: int = QUEUE_CAP,
                 max_pixels: int = MAX_PIXELS, px: int = THUMB_PX, max_bytes: int = CACHE_MAX_BYTES):
        self.cache = ThumbCache(cache_dir, max_bytes)
        self.max_pixels, self.px, self.queue_cap = max_pixels, px, queue_cap
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="files-thumb")
        self._lock = threading.Lock()
        self._queued = 0
        self._generation = 0
        self._inflight: dict[str, Future] = {}
        self._failed: OrderedDict[str, str] = OrderedDict()
        self.decoded = 0

    def note_generation(self, gen: int) -> None:
        """A newer client generation makes queued (not yet started) jobs of older generations stale (R-153)."""
        with self._lock:
            if gen > self._generation:
                self._generation = gen

    def _job(self, key: str, path: str, gen: int) -> ThumbResult:
        with self._lock:
            self._queued -= 1
            stale = gen and gen < self._generation
        if stale:
            return ThumbResult("placeholder", reason=REASON_CANCELLED)
        res = make_thumbnail(path, px=self.px, max_pixels=self.max_pixels)
        self.decoded += 1
        if res.status == "ok":
            self.cache.put(key, res)
        else:
            with self._lock:
                self._failed[key] = res.reason
                while len(self._failed) > 2000:
                    self._failed.popitem(last=False)
        return res

    def get(self, path: str, path_key: str, size: int, mtime: float, *, gen: int = 0) -> ThumbResult:
        key = cache_key(path_key, size, mtime)
        hit = self.cache.get(key)
        if hit:
            return hit
        with self._lock:
            reason = self._failed.get(key)
            if reason:
                return ThumbResult("placeholder", reason=reason)
            if gen:
                self._generation = max(self._generation, gen)
            fut = self._inflight.get(key)
            if fut is None:
                if self._queued >= self.queue_cap:
                    return ThumbResult("placeholder", reason=REASON_BUSY)
                self._queued += 1
                fut = self._pool.submit(self._job, key, path, gen)
                self._inflight[key] = fut
                fut.add_done_callback(lambda _f, k=key: self._done(k))
        try:
            return fut.result(timeout=WAIT_S)
        except Exception:
            return ThumbResult("placeholder", reason=REASON_BUSY)

    def _done(self, key: str) -> None:
        with self._lock:
            self._inflight.pop(key, None)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
