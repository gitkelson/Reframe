# Vault backend evaluation gate (spec §10.3) — results

Run: 2026-11-24, `python tools/vault_bench/bench.py` (Windows 11, NTFS SSD, Python 3.12.10, git 2.53.0).
Raw numbers of the last run: `last_run.json`. **Synthetic files only** — no private documents were read.

## Decision

**Candidate (c): a content-addressed store of whole-file zlib blobs named by the SHA-256 of the original content**
(`src/reframe/vault/store.py`). No external executable, no password or key, no new Python dependency (stdlib
`zlib` + `hashlib`).

Settings chosen from the measurements:

- zlib **level 1**, plus a 256 KB compressibility probe: data that deflate cannot shrink by ≥ 5 % (PDF, Office,
  images, archives) is written as zlib *stored blocks* (level 0, same file format). The probe cut the 50 MB
  binary capture from **5.35 s to 0.53 s** with no size penalty (stored size = logical + 0.01 %).
- Level 6 stores text ~29 % smaller (345 KB vs 485 KB per 2 MB) but captures ~2.6× slower (297 ms vs 113 ms);
  most attachments are already compressed, so level 1 was kept. `ZLIB_LEVEL` is one constant if this changes.

## Selection rule (§10.3 step 3) — how each candidate fares

| Criterion | (a) restic | (b) Git object store | (c) CAS zlib blobs |
|---|---|---|---|
| Available here | **No** — `restic` is not on PATH; would need a bundled `restic.exe` | Yes (`git.exe` on PATH, but not a declared Reframe runtime dependency) | Yes — stdlib only |
| External secret | **Yes** — repository password; losing it locks the owner out | No | No |
| Verified restore of every version | (not measurable) | Yes (all versions verified) | Yes (all versions verified, hash re-checked on every restore) |
| Atomic, crash-safe staged write | Own locking/transactions | Loose objects written via temp + rename; packs need `gc` | Yes — staging + fsync + re-hash + `os.replace`; kill test below |
| Dedup of identical content | Chunk-level | Whole-object (blob id) | Whole-file (SHA-256) |
| Incremental size after small edits | Chunk dedup (expected best) | **Best after `git gc`** (delta packs) | A full compressed copy per distinct version |
| Measurable size reporting | `restic stats` | Directory size; packs mix objects | Exact per blob |
| Coordinated backup (§8.4) | Separate repo copy / `restic copy` | Copy repo; packs rewritten by `gc` | Copy only new blobs by name (simple incremental) |
| Identity | restic ids | **SHA-1** of `blob <n>\0` header — needs a mapping to the spec's SHA-256 | SHA-256 of content = file name |
| Extra operations | process management, `forget/prune` | process management, `gc` (4.9 s on 250 MB), read-only pack files | none |

(c) is the simplest candidate that meets every criterion. Its known limit is storage growth for large binaries
that change often (each distinct version costs one compressed copy); (b) is ~5× smaller in that case only after
`git gc`. This is recorded as an operational limit (spec risk 4) and does not justify an external process, SHA-1
mapping and pack maintenance. Revisit only if measured vault growth becomes a real problem.

## Measurements (5 versions per file: v0 + 4 edits, then an identical copy under another name)

### (c) CAS, zlib level 1 + compressibility probe — **selected**

| File | first copy | incr. v1–v4 (each) | stored / logical | duplicate adds | capture v0 (stable read) | restore avg | verified |
|---|---|---|---|---|---|---|---|
| text (2 MB) | 485 KB of 1.91 MB | 485 KB | 2.37 MB / 9.54 MB | 0 B | 113 ms | 26 ms | yes |
| PDF-like (5 MB) | 5.01 MB of 5.01 MB | 5.00 MB | 25.00 MB / 25.00 MB | 0 B | 90 ms | 34 ms | yes |
| Office-like zip (1.1 MB) | 1.08 MB of 1.08 MB | 1.08 MB | 5.41 MB / 5.41 MB | 0 B | 73 ms | 18 ms | yes |
| binary (50 MB, 4 KB edits) | 50.00 MB of 50.00 MB | 50.00 MB | 250.02 MB / 250.00 MB | 0 B | 528 ms | 250 ms | yes |

Capture time is the full production path: stat → stream + hash + compress into staging → fsync → stat again →
second full hash (stability re-read) → verify staged blob (decompress + re-hash) → `os.replace`.

### (c) CAS, zlib level 6 + probe

| File | first copy | incr. (each) | stored / logical | dup | capture v0 | restore avg |
|---|---|---|---|---|---|---|
| text (2 MB) | 345 KB | 345 KB | 1.68 MB / 9.54 MB | 0 B | 297 ms | 21 ms |
| PDF-like (5 MB) | 5.01 MB | 5.00 MB | 25.00 MB / 25.00 MB | 0 B | 115 ms | 34 ms |
| Office-like zip | 1.08 MB | 1.08 MB | 5.41 MB / 5.41 MB | 0 B | 85 ms | 18 ms |
| binary (50 MB) | 50.00 MB | 50.00 MB | 250.02 MB / 250.00 MB | 0 B | 472 ms | 236 ms |

Without the probe (first run, level 1): binary 50 MB capture **5354 ms**, PDF-like 202 ms, Office-like 190 ms.

### (b) Git object store (`git hash-object -w` into a bare repo; refs keep blobs reachable; then `git gc`)

| File | first copy | incr. (each, loose) | stored after gc / logical | dup | capture v0 | restore avg |
|---|---|---|---|---|---|---|
| text (2 MB) | 485 KB | 485 KB | 350 KB / 9.54 MB (gc 0.7 s) | 0 B | 194 ms | 65 ms |
| PDF-like (5 MB) | 5.01 MB | 5.00 MB | 5.26 MB / 25.00 MB (gc 1.1 s) | 0 B | 305 ms | 93 ms |
| Office-like zip | 1.08 MB | 1.08 MB | 1.14 MB / 5.41 MB (gc 0.5 s) | 0 B | 170 ms | 95 ms |
| binary (50 MB) | 50.02 MB | 50.02 MB | 50.04 MB / 250.00 MB (gc 4.9 s) | 0 B | 1814 ms | 136 ms |

Git captures had no stability re-read (a fair comparison would add one hash pass). Its PDF-like and Office-like
gains depend on edits leaving most bytes in place; a real re-saved PDF/DOCX often recompresses whole streams.

### (a) restic

Not installed on this machine (`restic` not on PATH), so not measured. It would also require a repository
password (an external secret that could lock the owner out, §10.3) and a bundled executable — both trade-offs that
must be presented to the owner before adoption. Not needed: (c) meets the criteria.

## Failure handling (candidate c, production code paths)

| Case | Result |
|---|---|
| Locked file (other handle with share mode 0) | `PermissionError` winerror 32 → status **locked**; nothing written; retry after release succeeded |
| Unicode + long path (355 chars, `\\?\` prefix) | capture + restore verified |
| Unavailable network path `\\reframe-bench-nohost.invalid\share\…` | winerror 53 after 0.11 s → **unreachable**; remote paths are probed on a helper thread with a 3 s bound |
| Disk full (ENOSPC simulated at flush) | `OSError` errno 28 → **failed: "Disk full: the vault folder has no free space"**; staging empty; no object |
| Process killed mid-write | 1 leftover file in `staging/`, no partial object in `objects/`; `recover()` removed it; all blobs verify |
| Changed during read | observed for real once: a file rewritten just before capture reported an mtime that moved between the two stats (NTFS lazy timestamp) → **changed**, retried 1 s later and captured |
| Duplicate content (second path) | 0 bytes added |

## Operational limits recorded

- Storage: each distinct version of a large, already-compressed file costs ~its full size; the first protected
  copy roughly doubles the disk use of that file. Shown in Settings ▸ Attachments (stored size, savings).
- Capture reads each file twice (stability re-read) plus a verify pass over the staged blob; ~0.5 s per 50 MB on SSD.
- The vault must not be in a OneDrive/synced folder (`is_synced_folder` warns).
- Capture is best effort (R-163): edits made while Reframe is not running, or overwritten before capture, may not be
  preserved.
