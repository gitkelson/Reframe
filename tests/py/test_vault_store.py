"""Vault store (§10.3 candidate c): staged writes, dedup, verification, recovery."""
import errno
import hashlib
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from reframe.vault import store as storemod
from reframe.vault.store import BlobCorrupt, VaultStore, is_synced_folder


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_blob_named_by_sha_of_original_and_deduplicated(tmp_path):
    s = VaultStore(tmp_path / "v")
    data = b"hello vault " * 1000
    h = s.put_bytes(data)
    assert h == sha(data) and s.has(h)
    p = s.blob_path(h)
    assert p.parent.name == h[:2] and p.name == h[2:] + ".zlib"
    mtime = p.stat().st_mtime_ns
    assert s.put_bytes(data) == h and p.stat().st_mtime_ns == mtime        # existing blob is never rewritten
    assert s.open_reader(h).read() == data and s.verify(h)
    info = s.size_info()
    assert info["blobs"] == 1 and 0 < info["stored_bytes"] < len(data)


def test_incompressible_data_stored_without_deflate_cost(tmp_path):
    import os
    s = VaultStore(tmp_path / "v")
    data = os.urandom(600_000)
    h = s.put_bytes(data)
    assert s.blob_path(h).stat().st_size < len(data) * 1.001
    assert s.open_reader(h).read() == data


def test_corrupt_blob_detected(tmp_path):
    s = VaultStore(tmp_path / "v")
    h = s.put_bytes(b"x" * 10_000)
    p = s.blob_path(h)
    raw = bytearray(p.read_bytes())
    raw[-6] ^= 0xFF
    p.write_bytes(bytes(raw))
    assert not s.verify(h)
    with pytest.raises(BlobCorrupt):
        s.restore_to(h, tmp_path / "out.bin")
    assert not (tmp_path / "out.bin").exists()


def test_restore_never_overwrites(tmp_path):
    s = VaultStore(tmp_path / "v")
    h = s.put_bytes(b"content")
    dest = tmp_path / "exists.txt"
    dest.write_bytes(b"mine")
    with pytest.raises(FileExistsError):
        s.restore_to(h, dest)
    assert dest.read_bytes() == b"mine"
    assert s.restore_to(h, tmp_path / "new.txt").read_bytes() == b"content"
    assert [p.name for p in tmp_path.glob("*.partial")] == []


def test_disk_full_leaves_no_partial_object(tmp_path, monkeypatch):
    s = VaultStore(tmp_path / "v")

    def full(fh):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(storemod, "_fsync", full)
    with pytest.raises(OSError) as ei:
        s.put_bytes(b"data" * 1000)
    assert ei.value.errno == errno.ENOSPC
    assert list(s.staging.iterdir()) == [] and list(s.iter_shas()) == []


def test_killed_mid_write_then_recover(tmp_path):
    s = VaultStore(tmp_path / "v")
    good = s.put_bytes(b"already stored")
    src = Path(storemod.__file__).resolve().parents[2]
    child = textwrap.dedent(f"""
        import sys, time, io
        sys.path.insert(0, {str(src)!r})
        from reframe.vault.store import VaultStore
        class Slow(io.RawIOBase):
            def readable(self): return True
            def read(self, n=-1):
                time.sleep(0.05); return b"z" * 65536
        VaultStore({str(s.root)!r}).stage_stream(Slow())
    """)
    proc = subprocess.Popen([sys.executable, "-c", child])
    try:
        deadline = time.time() + 30
        while time.time() < deadline and not list(s.staging.glob("*.part")):
            time.sleep(0.05)
        time.sleep(0.3)
    finally:
        proc.kill()
        proc.wait()
    assert list(s.staging.glob("*.part")), "child never started writing"
    assert list(s.iter_shas()) == [good]                   # no partial object became visible
    assert s.recover() == 1
    assert list(s.staging.iterdir()) == [] and s.verify(good)


def test_synced_folder_warning(monkeypatch, tmp_path):
    monkeypatch.setenv("OneDrive", str(tmp_path / "od"))
    assert is_synced_folder(tmp_path / "od" / "vault")
    assert is_synced_folder(r"C:\Users\x\OneDrive - Contoso\vault")
    monkeypatch.delenv("OneDrive")
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    assert not is_synced_folder(tmp_path / "local" / "vault")
