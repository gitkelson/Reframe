"""Shipped files must not contain paths into a particular person's machine, and defaults must be generic."""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
EXEMPT_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}
TEXT_SUFFIXES = {".py", ".pyw", ".js", ".css", ".html", ".md", ".txt", ".json", ".ini", ".sql", ".toml", ".ps1", ".bat", ".sh", ""}

# A home-folder path is acceptable only with an obviously generic user name.
GENERIC = r"(?:me|x|you|user|username|name|someone|<[^>]+>|%[^%]+%|\$\{?[A-Za-z_]+\}?)"
PATTERNS = [
    re.compile(r"[A-Za-z]:\\{1,2}Users\\{1,2}(?!" + GENERIC + r"(?:\\|\b))[^\\\s\"'`<>*]+", re.I),
    re.compile(r"/Users/(?!" + GENERIC + r"(?:/|\b))[^/\s\"'`<>*]+"),
    re.compile(r"/home/(?!" + GENERIC + r"(?:/|\b))[^/\s\"'`<>*]+"),
    re.compile(r"\.apps[\\/]"),
    re.compile(r"AppData\\{1,2}Local\\{1,2}Python"),
]


def shipped_files() -> list[Path]:
    out = []
    for p in APP.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if EXEMPT_DIRS & set(p.relative_to(APP).parts):
            continue
        out.append(p)
    return out


def test_no_personal_paths():
    hits = []
    for p in shipped_files():
        if p == Path(__file__):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if any(rx.search(line) for rx in PATTERNS):
                hits.append(f"{p.relative_to(APP)}:{n}: {line.strip()[:120]}")
    assert not hits, "paths into a personal machine found:\n" + "\n".join(hits)


def test_default_paths(monkeypatch, tmp_path):
    from reframe.db import connection
    from reframe.domain import backup
    monkeypatch.delenv("REFRAME_DATA_DIR", raising=False)
    monkeypatch.delenv("REFRAME_BACKUP_DIR", raising=False)
    monkeypatch.delenv("REFRAME_DB", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert connection.data_dir() == tmp_path / "Reframe"
    assert connection.default_db_path() == tmp_path / "Reframe" / "reframe.sqlite"
    assert backup.default_backup_dir().name == "Reframe Backups"
