"""Build-time download of the bundled UI fonts (latin subset, woff2) and their licences. The app itself never
uses the network. Every family below is under the SIL Open Font License 1.1 (confirmed from the downloaded
LICENSE file, which is kept next to the font files); a font whose licence cannot be confirmed is not listed.

Sources:
  * Fontsource npm packages via jsDelivr (latin subset, one file per weight).
  * D-DIN (Datto Inc., SIL OFL 1.1) is not on Fontsource; it comes from the pinned npm package
    typeface-d-din@0.0.67 (full character set, not subset), whose "SIL Open Font License.txt" carries Datto's copyright and OFL text.

    python tools\\fetch_fonts.py
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

DEST = Path(__file__).resolve().parents[1] / "src" / "reframe" / "web" / "css" / "fonts"
CDN = "https://cdn.jsdelivr.net/npm/@fontsource"

FAMILIES = {
    "dm-sans": [400, 500, 700],
    "dm-mono": [400, 500],
    "dm-serif-display": [400],
    "barlow": [400, 500, 600],
    "barlow-condensed": [600, 700],
    "inter": [400, 500, 600, 700],
    "cormorant-garamond": [500, 600],
    "jetbrains-mono": [400, 500],
    "open-sans": [400, 500, 600],
    "ibm-plex-sans": [400, 500, 600],
    "ibm-plex-mono": [400, 500],
}

# Families not on Fontsource: (local file name, url); licence (local name, url).
D_DIN = "https://cdn.jsdelivr.net/npm/typeface-d-din@0.0.67"
OTHER = {
    "d-din": (
        [("d-din-400-normal.woff2", f"{D_DIN}/files/d-din-400.woff2"),
         ("d-din-700-normal.woff2", f"{D_DIN}/files/d-din-700.woff2")],
        ("LICENSE-d-din.txt", f"{D_DIN}/SIL%20Open%20Font%20License.txt"),
    ),
}


def fetch(url: str, target: Path) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            target.write_bytes(r.read())
        return True
    except Exception as e:  # report and continue: the app falls back to Segoe UI
        print(f"FAILED {url}: {e}")
        return False


def check_licence(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    ok = "SIL Open Font License" in text or "Apache License" in text
    print(("licence ok " if ok else "LICENCE NOT CONFIRMED "), path.name)


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    for fam, weights in FAMILIES.items():
        for w in weights:
            name = f"{fam}-latin-{w}-normal.woff2"
            ok = fetch(f"{CDN}/{fam}/files/{name}", DEST / name)
            print(("ok     " if ok else "missing"), name)
        lic = DEST / f"LICENSE-{fam}.txt"
        fetch(f"{CDN}/{fam}/LICENSE", lic)
        check_licence(lic)
    for files, (lic_name, lic_url) in OTHER.values():
        for name, url in files:
            ok = fetch(url, DEST / name)
            print(("ok     " if ok else "missing"), name)
        fetch(lic_url, DEST / lic_name)
        check_licence(DEST / lic_name)


if __name__ == "__main__":
    main()
