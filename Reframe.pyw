"""Double-click launcher (R-170): run by pythonw.exe, so no console window ever appears.

    pythonw.exe Reframe.pyw [--tray]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from reframe.launch import run  # noqa: E402

raise SystemExit(run(sys.argv[1:]))
