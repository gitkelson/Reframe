"""File timeline roots (R-140): normalization, validation and containment checks. Pure, no I/O except
the optional reachability probe in ``validate_roots``."""
from __future__ import annotations

import ntpath
import os


class RootsError(ValueError):
    """Raised when a root list is refused; ``problems`` lists every reason (one sentence each)."""

    def __init__(self, problems: list[str]):
        super().__init__(" ".join(problems))
        self.problems = problems


def _is_unc(drive: str) -> bool:
    return drive.startswith("\\\\")


def normalize_root(path: str) -> str:
    """Normalized form for display and storage: no quotes, backslashes, no trailing separator except 'C:\\'."""
    p = str(path or "").strip().strip('"').strip()
    if not p:
        return ""
    p = ntpath.normpath(p.replace("/", "\\"))
    drive, rest = ntpath.splitdrive(p)
    if len(drive) == 2 and drive[1] == ":":
        drive = drive.upper()
        if rest in ("", "."):
            rest = "\\"
    if _is_unc(drive) and rest == "\\":
        rest = ""
    elif len(rest) > 1:
        rest = rest.rstrip("\\")
    return drive + rest


def path_key(path: str) -> str:
    """Case-insensitive comparison key (Windows paths compare without case)."""
    return ntpath.normcase(ntpath.normpath(str(path))).rstrip("\\")


def is_absolute(path: str) -> bool:
    drive, rest = ntpath.splitdrive(path)
    if _is_unc(drive):
        return len([s for s in drive.split("\\") if s]) >= 2
    return len(drive) == 2 and rest.startswith("\\")


def is_within(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` itself or lies below it (lexically, case-insensitive)."""
    pk, rk = path_key(path), path_key(root)
    if not rk:
        return False
    return pk == rk or pk.startswith(rk + "\\")


def root_name(root: str) -> str:
    """The root's own folder name ('Photos'), 'C:' for a drive root, 'share' for '\\\\server\\share'."""
    p = normalize_root(root)
    base = ntpath.basename(p.rstrip("\\"))
    if base:
        return base
    drive, _ = ntpath.splitdrive(p)
    parts = [s for s in drive.split("\\") if s]
    return parts[-1] if parts else p


def validate_roots(roots: list[str], *, probe: bool = True) -> tuple[list[str], list[str]]:
    """Normalize and check a root list. Returns (normalized roots, warnings); raises RootsError when refused.

    Refused: non-strings, empty or relative paths, duplicates, and roots nested inside another root (either
    direction). Roots that cannot be reached right now (offline share, unplugged drive) are accepted with a
    warning, because the scan reports them as unreachable and they come back when the share does (R-141).
    """
    if not isinstance(roots, list):
        raise RootsError(["Roots must be a list of folder paths."])
    problems: list[str] = []
    out: list[str] = []
    for raw in roots:
        if not isinstance(raw, str):
            problems.append(f"{raw!r} is not a folder path.")
            continue
        p = normalize_root(raw)
        if not p:
            problems.append("An empty folder path was given.")
            continue
        if not is_absolute(p):
            problems.append(f"'{raw}' is not a full path. Use a path such as C:\\Users\\me\\Pictures "
                            "or \\\\server\\share\\folder.")
            continue
        dup = next((o for o in out if path_key(o) == path_key(p)), None)
        if dup is not None:
            problems.append(f"'{p}' is listed twice. Each folder can be a root only once.")
            continue
        outer = next((o for o in out if is_within(p, o)), None)
        if outer is not None:
            problems.append(f"'{p}' is inside the root '{outer}', so its files are already included. "
                            "Remove one of them.")
            continue
        inner = next((o for o in out if is_within(o, p)), None)
        if inner is not None:
            problems.append(f"'{p}' contains the root '{inner}', so the two would overlap. Remove one of them.")
            continue
        out.append(p)
    if problems:
        raise RootsError(problems)
    warnings: list[str] = []
    if probe:
        for p in out:
            try:
                reachable = os.path.isdir(p)
            except OSError:
                reachable = False
            if not reachable:
                warnings.append(f"'{p}' cannot be reached right now; it will be scanned when it is available.")
    return out, warnings
