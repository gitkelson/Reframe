"""Create, repair or remove the Reframe shortcuts (R-171). Idempotent; touches only shortcuts it created.

    python tools\\install_shortcuts.py --start-menu --startup [--desktop]
    python tools\\install_shortcuts.py --remove
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reframe import shortcuts  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start-menu", action="store_true", help="Start menu shortcut")
    ap.add_argument("--desktop", action="store_true", help="Desktop shortcut")
    ap.add_argument("--startup", action="store_true", help="start hidden in the tray at sign-in (R-173)")
    ap.add_argument("--remove", action="store_true", help="remove every Reframe shortcut this tool created")
    args = ap.parse_args(argv)
    locs = shortcuts.locations()
    if args.remove:
        for key, path in locs.items():
            print(("removed  " if shortcuts.remove(path) else "absent   ") + str(path))
        return 0
    chosen = [k for k, on in (("start-menu", args.start_menu), ("desktop", args.desktop), ("startup", args.startup)) if on]
    if not chosen:
        ap.error("choose at least one of --start-menu, --desktop, --startup (or --remove)")
    for key in chosen:
        try:
            print("ok       " + str(shortcuts.create(locs[key], tray=key == "startup")))
        except FileExistsError as e:
            print("skipped  " + str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
