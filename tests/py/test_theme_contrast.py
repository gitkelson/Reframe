"""WCAG contrast guard for all ten theme token files (spec 7.3, R-91, R-182).

Parses css/tokens-*.css, resolves var() references and rgba() over the surface it sits on, and asserts:
  * 4.5:1 for text (body, secondary, ruler, panel header, button, banner, tag labels on all 8 solid slots,
    hatched-label backing chip, implied/retired chips, active filter, protection states, file names, placeholder
    and count badge)
  * 3:1 for non-text graphics (focus ring, today marker / now line, minimap frame, bars and File-density colours,
    lane separator, selection and drop-target outlines, file marker, input border)
  * adjacent tag slots differ in hue (>= 30 degrees) and in lightness (contrast between neighbours >= 1.3:1)
  * every file exposes exactly the same role variables as tokens-meridian.css (its raw palette block is private),
    declares color-scheme (dark for ferrari, spacex, spotify), and no longer defines the past-gutter tokens

Run `python tests/py/test_theme_contrast.py` to print the full per-pair table. Measured on 2026-11-24 (min ratios):

| group | meridian | catalog | claude | dell-1996 | dji | ferrari | spacex | spotify | ibm |
|---|---|---|---|---|---|---|---|---|---|
| body / secondary text on canvas | 15.99 / 6.44 | 15.33 / 7.57 | 17.50 / 5.13 | 21.00 / 7.46 | 15.13 / 7.00 | 17.76 / 6.00 | 21.00 / 18.55 | 18.73 / 8.93 | 18.10 / 7.81 |
| body / secondary text on panel | 18.49 / 7.45 | 15.33 / 7.57 | 16.25 / 4.77 | 21.00 / 7.46 | 14.33 / 6.63 | 15.52 / 5.25 | 19.80 / 17.49 | 17.76 / 8.47 | 16.45 / 7.10 |
| tag labels, worst solid slot | 6.29 | 6.72 | 5.06 | 5.43 | 4.91 | 4.67 | 5.81 | 5.26 | 7.35 |
| focus ring, worst surface | 6.44 | 6.41 | 16.25 | 9.40 | 4.65 | 13.27 | 19.80 | 8.59 | 4.55 |
| today marker / now line on ruler | 4.57 / 6.51 | 7.56 / 6.52 | 3.11 / 5.47 | 4.49 / 4.67 | 4.91 / 14.94 | 3.65 / 3.65 | 21.00 / 21.00 | 9.76 / 9.76 | 5.00 / 7.68 |
| minimap frame / bars / lane separator | 6.51 / 7.12 / 5.05 | 6.41 / 4.27 / 3.40 | 8.50 / 5.26 / 3.31 | 12.86 / 9.90 / 3.66 | 14.94 / 4.44 / 4.44 | 13.64 / 5.49 / 3.48 | 21.00 / 6.12 / 3.91 | 9.76 / 4.49 / 3.46 | 18.10 / 5.38 / 3.60 |
| protection states, worst | 5.84 | 6.25 | 5.45 | 6.03 | 4.65 | 5.56 | 7.63 | 6.36 | 7.02 |
| implied chip / retired / active filter | 7.24 / 11.65 / 9.29 | 7.57 / 6.53 / 6.75 | 4.77 / 8.32 / 15.59 | 7.46 / 11.54 / 12.86 | 6.42 / 12.35 / 13.12 | 5.25 / 13.20 / 13.20 | 7.63 / 11.31 / 21.00 | 7.86 / 10.70 / 10.94 | 5.92 / 13.71 / 7.09 |
| file name / placeholder / badge | 15.99 / 5.54 / 6.51 | 15.33 / 6.53 / 15.33 | 17.50 / 9.02 / 17.00 | 21.00 / 11.54 / 12.86 | 15.13 / 6.42 / 15.13 | 17.76 / 6.09 / 17.76 | 21.00 / 6.68 / 21.00 | 18.73 / 7.03 / 21.00 | 18.10 / 5.92 / 18.10 |
| tightest pair vs its minimum | 5.54 (thumbnail placeholder) | 3.40 (lane separator) | 3.11 (now line on canvas) | 4.94 (error banner) | 4.65 (protection state: capturing) | 4.67 (tag slot 2 label) | 5.81 (tag slot 2 label) | 3.46 (lane separator) | 4.55 (links on panel) |
"""
from __future__ import annotations

import colorsys
import re
from pathlib import Path

import pytest

CSS_DIR = Path(__file__).resolve().parents[2] / "src" / "reframe" / "web" / "css"
THEME_IDS = ["meridian", "catalog", "claude", "dell-1996", "dji", "ferrari", "spacex", "spotify",
             "ibm"]
THEMES = {t: f"tokens-{t}.css" for t in THEME_IDS}
DARK_THEMES = {"ferrari", "spacex", "spotify"}

TEXT, GRAPHIC = 4.5, 3.0

# (foreground token, background token, minimum ratio, what it is)
PAIRS = [
    ("--text", "--canvas-bg", TEXT, "body text on canvas"),
    ("--text-secondary", "--canvas-bg", TEXT, "secondary text on canvas"),
    ("--text", "--today-shade", TEXT, "body text on today's day cell"),
    ("--text", "--panel-bg", TEXT, "body text on side panel"),
    ("--text-secondary", "--panel-bg", TEXT, "secondary text on side panel"),
    ("--text", "--input-bg", TEXT, "text in inputs"),
    ("--lane-header-text", "--lane-header-bg", TEXT, "lane header text"),
    ("--ruler-text", "--ruler-bg", TEXT, "ruler text"),
    ("--ruler-text-dim", "--ruler-bg", TEXT, "dim ruler text"),
    ("--panel-header-text", "--panel-header-bg", TEXT, "panel header labels"),
    ("--on-dark-text", "--panel-header-bg", TEXT, "toolbar text"),
    ("--on-dark-dim", "--panel-header-bg", TEXT, "toolbar secondary text"),
    ("--on-dark-active-fg", "--on-dark-active-bg", TEXT, "pressed toggle in toolbar"),
    ("--active-fg", "--active-bg", TEXT, "selected chip"),
    ("--button-primary-fg", "--button-primary-bg", TEXT, "primary button"),
    ("--button-primary-fg", "--button-primary-hover", TEXT, "primary button hover"),
    ("--button-primary-fg", "--button-primary-pressed", TEXT, "primary button pressed"),
    ("--tooltip-fg", "--tooltip-bg", TEXT, "tooltip"),
    ("--text", "--popup-bg", TEXT, "menu text"),
    ("--link", "--panel-bg", TEXT, "links on panel"),
    ("--info-fg", "--info-bg", TEXT, "info banner"),
    ("--warn-fg", "--warn-bg", TEXT, "warning banner"),
    ("--error-fg", "--error-bg", TEXT, "error banner"),
    ("--danger", "--panel-bg", TEXT, "danger text on panel"),
    ("--bar-untagged-fg", "--bar-untagged", TEXT, "untagged bar label"),
    ("--hatch-label-fg", "--hatch-label-bg", TEXT, "hatched bar label on backing chip"),
    *[(f"--tag-{i}-fg", f"--tag-{i}", TEXT, f"tag slot {i} label") for i in range(8)],
    ("--chip-implied-fg", "--chip-implied-bg", TEXT, "implied-tag chip"),
    ("--retired-fg", "--retired-bg", TEXT, "retired-tag marker"),
    ("--filter-active-fg", "--filter-active-bg", TEXT, "active-filter highlight"),
    ("--protect-ok", "--panel-bg", TEXT, "protection state: protected"),
    ("--protect-pending", "--panel-bg", TEXT, "protection state: capturing"),
    ("--protect-bad", "--panel-bg", TEXT, "protection state: not protected"),
    ("--file-entry-fg", "--canvas-bg", TEXT, "file name on File timeline"),
    ("--file-placeholder-fg", "--file-placeholder-bg", TEXT, "thumbnail placeholder"),
    ("--file-badge-fg", "--file-badge-bg", TEXT, "file count badge"),
    ("--focus-ring", "--canvas-bg", GRAPHIC, "focus ring on canvas"),
    ("--focus-ring", "--panel-bg", GRAPHIC, "focus ring on panel"),
    ("--focus-ring", "--input-bg", GRAPHIC, "focus ring on inputs"),
    ("--focus-ring-on-dark", "--panel-header-bg", GRAPHIC, "focus ring in toolbar"),
    ("--now-line", "--canvas-bg", GRAPHIC, "now line on canvas"),
    ("--today-marker", "--canvas-bg", GRAPHIC, "today marker on canvas"),
    ("--now-line-on-ruler", "--ruler-bg", GRAPHIC, "now line on ruler"),
    ("--minimap-frame", "--minimap-bg", GRAPHIC, "minimap frame"),
    ("--minimap-bar", "--minimap-bg", GRAPHIC, "minimap bars"),
    ("--minimap-files-image", "--minimap-bg", GRAPHIC, "File minimap density: images"),
    ("--minimap-files-other", "--minimap-bg", GRAPHIC, "File minimap density: other files"),
    ("--lane-separator", "--minimap-bg", GRAPHIC, "lane separator"),
    ("--bar-selected-outline", "--canvas-bg", GRAPHIC, "selected-bar outline on canvas"),
    ("--drop-target", "--canvas-bg", GRAPHIC, "drop-target outline"),
    ("--file-entry", "--canvas-bg", GRAPHIC, "file marker on File timeline"),
    ("--file-selected", "--canvas-bg", GRAPHIC, "selected file outline"),
    ("--input-border", "--input-bg", GRAPHIC, "input border"),
    ("--marquee-stroke", "--canvas-bg", GRAPHIC, "selection marquee"),
]

Color = tuple[float, float, float, float]


def parse_tokens(theme: str) -> dict[str, str]:
    css = (CSS_DIR / THEMES[theme]).read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", css)}


def parse_color(text: str, tokens: dict[str, str], depth: int = 0) -> Color:
    text = text.strip()
    if depth > 10:
        raise ValueError(f"var() loop at {text}")
    m = re.fullmatch(r"var\((--[\w-]+)\)", text)
    if m:
        return parse_color(tokens[m.group(1)], tokens, depth + 1)
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", text)
    if m:
        v = m.group(1)
        return (int(v[0:2], 16) / 255, int(v[2:4], 16) / 255, int(v[4:6], 16) / 255, 1.0)
    m = re.fullmatch(r"rgba?\(([^)]+)\)", text)
    if m:
        p = [x.strip() for x in m.group(1).split(",")]
        a = float(p[3]) if len(p) > 3 else 1.0
        return (float(p[0]) / 255, float(p[1]) / 255, float(p[2]) / 255, a)
    raise ValueError(f"cannot parse colour: {text!r}")


def composite(fg: Color, bg: Color) -> Color:
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3)) + (1.0,)  # type: ignore[return-value]


def luminance(c: Color) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])


def ratio(a: Color, b: Color) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def solid(token: str, tokens: dict[str, str], backdrop: Color) -> Color:
    return composite(parse_color(f"var({token})", tokens), backdrop)


def measure(theme: str) -> list[tuple[str, str, float, float, str]]:
    tokens = parse_tokens(theme)
    canvas = solid("--canvas-bg", tokens, (1, 1, 1, 1))
    rows = []
    for fg_t, bg_t, minimum, what in PAIRS:
        bg = solid(bg_t, tokens, canvas)
        fg = solid(fg_t, tokens, bg)
        rows.append((fg_t, bg_t, ratio(fg, bg), minimum, what))
    return rows


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_contrast_pairs(theme):
    failures = [f"{what}: {fg} on {bg} = {r:.2f}:1 (< {minimum}:1)"
                for fg, bg, r, minimum, what in measure(theme) if r < minimum]
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_adjacent_tag_slots_are_distinct(theme):
    tokens = parse_tokens(theme)
    slots = [solid(f"--tag-{i}", tokens, (1, 1, 1, 1)) for i in range(8)]
    problems = []
    for i in range(8):
        a, b = slots[i], slots[(i + 1) % 8]
        ha = colorsys.rgb_to_hsv(*a[:3])[0] * 360
        hb = colorsys.rgb_to_hsv(*b[:3])[0] * 360
        dh = min(abs(ha - hb), 360 - abs(ha - hb))
        lr = ratio(a, b)
        if dh < 30 or lr < 1.3:
            problems.append(f"slots {i}/{(i + 1) % 8}: hue diff {dh:.0f} deg, lightness ratio {lr:.2f}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_all_tag_variables_present(theme):
    tokens = parse_tokens(theme)
    for i in range(8):
        assert f"--tag-{i}" in tokens and f"--tag-{i}-fg" in tokens


def role_names(theme: str) -> set[str]:
    """Variable names a theme exposes, minus its own raw palette block (between the "palette (raw)" and
    "roles" comments): palette names are private to each file, role names are the contract."""
    css = (CSS_DIR / THEMES[theme]).read_text(encoding="utf-8")
    css = re.sub(r"/\*\s*palette \(raw\)\s*\*/.*?/\*\s*roles\s*\*/", "", css, flags=re.S)
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return set(re.findall(r"(--[\w-]+)\s*:", css))


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_every_theme_defines_the_same_role_variables(theme):
    reference = role_names("meridian")
    names = role_names(theme)
    assert names == reference, (f"missing: {sorted(reference - names)}; extra: {sorted(names - reference)}")


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_theme_rule_and_color_scheme(theme):
    css = (CSS_DIR / THEMES[theme]).read_text(encoding="utf-8")
    assert f':root[data-theme="{theme}"]' in css
    m = re.search(r"(?<![\w-])color-scheme\s*:\s*(light|dark)\s*;", css)
    assert m, "color-scheme not declared"
    assert m.group(1) == ("dark" if theme in DARK_THEMES else "light")


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_file_minimap_colours_are_distinguishable(theme):
    tokens = parse_tokens(theme)
    bg = solid("--minimap-bg", tokens, (1, 1, 1, 1))
    a = solid("--minimap-files-image", tokens, bg)
    b = solid("--minimap-files-other", tokens, bg)
    ha, sa, _ = colorsys.rgb_to_hsv(*a[:3])
    hb, sb, _ = colorsys.rgb_to_hsv(*b[:3])
    dh = min(abs(ha - hb), 1 - abs(ha - hb)) * 360
    by_hue = sa >= 0.25 and sb >= 0.25 and dh >= 30
    assert by_hue or ratio(a, b) >= 1.4, f"hue diff {dh:.0f} deg, lightness ratio {ratio(a, b):.2f}"


def test_past_gutter_tokens_removed():
    for theme in THEMES:
        names = set(parse_tokens(theme))
        assert not names & {"--past-gutter-bg", "--past-gutter-text"}, theme


if __name__ == "__main__":
    for t in sorted(THEMES):
        print(f"\n{t}")
        print("| foreground | background | ratio | min | what |")
        print("|---|---|---|---|---|")
        for fg, bg, r, minimum, what in measure(t):
            print(f"| {fg} | {bg} | {r:.2f} | {minimum} | {what}{'' if r >= minimum else '  FAIL'} |")
