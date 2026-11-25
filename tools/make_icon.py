"""Render assets/reframe.ico (16-256 px) from the tray tile artwork (R-174).

    python tools\\make_icon.py
"""
from __future__ import annotations

import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from reframe.tray import draw_icon_image  # noqa: E402

SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def main() -> Path:
    out = APP / "assets" / "reframe.ico"
    out.parent.mkdir(exist_ok=True)
    big = draw_icon_image(256)
    big.save(out, format="ICO", sizes=[(s, s) for s in SIZES])
    print("wrote", out)
    return out


if __name__ == "__main__":
    main()
