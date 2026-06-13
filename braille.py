#!/usr/bin/env python3
"""Braille (盲文/点字) label generator — standard-spaced raised dots → printable STL.

Text  ──► liblouis translation ──► Unicode braille cells ──► dome dots on a plate ──► .stl
        (en Grade 1/2, 中文国家通用盲文 / 现行盲文)        (exact standard dot spacing)

Dots are placed on a rigid base plate at the standard braille geometry (Marburg
Medium / common 3D-print spec) and exported directly as a binary STL — no AI, no
Blender. Print dots-up (flat side on the bed); reads left-to-right as laid out.

Examples
--------
  python braille.py "Cat"                         # English Grade 1
  python braille.py "你好世界"                      # 中文国家通用盲文 (auto)
  python braille.py "猫 cat"                        # mixed, one pass
  python braille.py --lang en-g2 "the cat"         # English Grade 2 (contracted)
  python braille.py --lang zh-current "你好"        # 现行盲文 instead of 国家通用
  python braille.py "Line one\nLine two"           # two rows (\n)
  python braille.py --braille "⠉⠁⠞"                # supply Unicode braille directly
  python braille.py --dots "14 1 2345"             # supply explicit dot numbers per cell
  python braille.py "Exit" --size 80 --base 2.5    # fit onto a fixed 80mm-wide plate

Standard dimensions baked in (override with flags):
  dot spacing in a cell 2.5 mm · cell pitch 6.0 mm · line pitch 10.0 mm
  dot base Ø 1.5 mm · dot height 0.6 mm · base plate 2.0 mm
"""
import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _find_lou() -> str:
    """Locate lou_translate even when launched without a full PATH (e.g. from Finder)."""
    p = shutil.which("lou_translate")
    if p:
        return p
    for c in ("/opt/homebrew/bin/lou_translate", "/usr/local/bin/lou_translate"):
        if Path(c).exists():
            return c
    return "lou_translate"   # let it fail loudly with a clear message in translate()


LOU = _find_lou()

# liblouis tables (installed by `brew install liblouis`)
TABLES = {
    "en":          "en-us-g1.ctb",     # English, uncontracted (Grade 1)
    "en-g2":       "en-us-g2.ctb",     # English, contracted (Grade 2)
    "zh":          "zhcn-cbs.ctb",     # 中文 国家通用盲文 (2018) — DEFAULT for Chinese
    "zh-current":  "zh-chn.ctb",       # 中文 现行盲文 (无声调)
    "zh-toned":    "zhcn-g1.ctb",      # 中文 现行盲文，逐字标调
}

# Friendly scheme names (shown on every run so the active standard is unambiguous)
SCHEME = {
    "en":         "English Grade 1 (uncontracted)",
    "en-g2":      "English Grade 2 (contracted)",
    "zh":         "国家通用盲文 (2018)",
    "zh-current": "现行盲文 (无声调)",
    "zh-toned":   "现行盲文 (逐字标调)",
}

# Which concrete scheme `auto` resolves to. Chinese -> 国家通用盲文.
ZH_DEFAULT = "zh"


# ----------------------------------------------------------------- translation
def has_cjk(s: str) -> bool:
    return any("一" <= c <= "鿿" or "㐀" <= c <= "䶿" for c in s)


def resolve_lang(text: str, lang: str) -> str:
    """Turn 'auto' into a concrete scheme key: Chinese -> 国家通用盲文, else English."""
    if lang == "auto":
        return ZH_DEFAULT if has_cjk(text) else "en"
    return lang


def translate(text: str, lang: str) -> list[str]:
    """Return one Unicode-braille string per input line. `lang` is already resolved."""
    table = TABLES.get(lang, lang)  # allow passing a raw table name
    lines = []
    for line in text.split("\n"):
        if not line:
            lines.append("")
            continue
        try:
            res = subprocess.run(
                [LOU, f"unicode.dis,{table}"],
                input=line, capture_output=True, text=True)
        except FileNotFoundError:
            sys.exit("lou_translate not found — install liblouis:  brew install liblouis")
        if res.returncode != 0:
            sys.exit(f"lou_translate failed ({table}): {res.stderr.strip()}")
        lines.append(res.stdout.rstrip("\n"))
    return lines


def cells_from_braille(s: str) -> list[list[int]]:
    """Unicode braille string -> list of dot lists, e.g. '⠉' -> [1,4]."""
    out = []
    for ch in s:
        bits = ord(ch) - 0x2800
        if not (0 <= bits <= 0xFF):       # not a braille char -> treat as blank
            out.append([])
            continue
        out.append([i + 1 for i in range(6) if bits & (1 << i)])
    return out


def cells_from_dotspec(spec: str) -> list[list[int]]:
    """'14 1 2345' or '14-1-2345' -> [[1,4],[1],[2,3,4,5]]; 0 / _ = blank cell."""
    out = []
    for tok in spec.replace("-", " ").split():
        if tok in ("0", "_"):
            out.append([])
        else:
            out.append([int(d) for d in tok if d in "123456"])
    return [out]  # single line


# -------------------------------------------------------------------- geometry
def _normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    m = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return (nx / m, ny / m, nz / m)


def box_tris(x0, y0, z0, x1, y1, z1):
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]  # outward winding
    tris = []
    for a, b, c, d in quads:
        tris.append((v[a], v[b], v[c]))
        tris.append((v[a], v[c], v[d]))
    return tris


def dome_tris(cx, cy, top_z, r, h, sink, n_lon=28, n_lat=7):
    """Spherical-cap dot centered at (cx,cy): base ring at z=top_z, apex at top_z+h,
    skirt sunk to top_z-sink so it overlaps the plate -> robust union when sliced."""
    R = (r * r + h * h) / (2 * h)
    zc = top_z + h - R
    phi_max = math.acos(max(-1.0, min(1.0, (top_z - zc) / R)))
    rings = []
    for k in range(n_lat + 1):
        phi = phi_max * k / n_lat
        rings.append((zc + R * math.cos(phi), R * math.sin(phi)))

    def ring(rz, rr):
        return [(cx + rr * math.cos(2 * math.pi * j / n_lon),
                 cy + rr * math.sin(2 * math.pi * j / n_lon), rz) for j in range(n_lon)]

    rv = [ring(rz, rr) for rz, rr in rings]
    apex = (cx, cy, top_z + h)
    tris = []
    r1 = rv[1]
    for j in range(n_lon):                       # apex fan
        tris.append((apex, r1[j], r1[(j + 1) % n_lon]))
    for k in range(1, n_lat):                     # cap bands
        a, b = rv[k], rv[k + 1]
        for j in range(n_lon):
            j2 = (j + 1) % n_lon
            tris.append((a[j], b[j], b[j2]))
            tris.append((a[j], b[j2], a[j2]))
    base = rv[n_lat]                              # ring at z=top_z, radius r
    skirt = [(x, y, top_z - sink) for (x, y, _) in base]
    for j in range(n_lon):                        # vertical skirt into plate
        j2 = (j + 1) % n_lon
        tris.append((base[j], skirt[j], skirt[j2]))
        tris.append((base[j], skirt[j2], base[j2]))
    cbot = (cx, cy, top_z - sink)
    for j in range(n_lon):                         # bottom disc
        j2 = (j + 1) % n_lon
        tris.append((cbot, skirt[j2], skirt[j]))
    return tris


def write_stl(path, tris):
    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            f.write(struct.pack("<3f", *_normal(a, b, c)))
            f.write(struct.pack("<3f", *a))
            f.write(struct.pack("<3f", *b))
            f.write(struct.pack("<3f", *c))
            f.write(struct.pack("<H", 0))


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(
        description="Generate a 3D-printable braille (盲文) label with standard dot spacing.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("text", nargs="?", help="text to emboss (use \\n for new rows)")
    ap.add_argument("--braille", help="supply Unicode braille cells directly (skip translation)")
    ap.add_argument("--dots", help="supply explicit dot numbers per cell, e.g. '14 1 2345'")
    ap.add_argument("--lang", default="auto",
                    help="auto (中文→国家通用盲文, else English) | "
                         + " | ".join(TABLES) + " | <liblouis table name>")
    ap.add_argument("--out", help="output .stl (default: out/braille_<slug>.stl)")
    # standard geometry (mm) — change only with reason
    ap.add_argument("--dot-spacing", type=float, default=2.5, help="dot centres within a cell")
    ap.add_argument("--cell-pitch", type=float, default=6.0, help="cell-to-cell (dot1→dot1)")
    ap.add_argument("--line-pitch", type=float, default=10.0, help="line-to-line (dot1→dot1)")
    ap.add_argument("--dot-diam", type=float, default=1.5, help="dot base diameter")
    ap.add_argument("--dot-height", type=float, default=0.6, help="dot height above plate")
    ap.add_argument("--base", type=float, default=2.0, help="base plate thickness")
    ap.add_argument("--margin", type=float, default=6.0, help="edge-to-nearest-dot margin")
    ap.add_argument("--size", type=float, help="fix plate WIDTH (mm); text centred (else auto)")
    ap.add_argument("--height", type=float, help="fix plate HEIGHT (mm); text centred (else auto)")
    args = ap.parse_args()

    # ---- cells (list of lines, each a list of dot-lists)
    scheme_note = "raw input (no translation)"
    if args.dots:
        grid = cells_from_dotspec(args.dots)
        shown = args.dots
    elif args.braille:
        grid = [cells_from_braille(line) for line in args.braille.split("\\n")]
        shown = args.braille
    else:
        if not args.text:
            ap.error("provide text, or --braille / --dots")
        text = args.text.replace("\\n", "\n")
        lang = resolve_lang(text, args.lang)
        braille_lines = translate(text, lang)
        grid = [cells_from_braille(line) for line in braille_lines]
        shown = "  /  ".join(braille_lines)
        auto = " (auto)" if args.lang == "auto" else ""
        scheme_note = f"{SCHEME.get(lang, lang)}{auto}  [{TABLES.get(lang, lang)}]"

    ncols = max((len(line) for line in grid), default=0)
    nrows = len(grid)
    if ncols == 0:
        sys.exit("nothing to emboss")

    DS, CP, LP = args.dot_spacing, args.cell_pitch, args.line_pitch
    text_w = (ncols - 1) * CP + DS
    text_h = (nrows - 1) * LP + 2 * DS
    plate_w = args.size if args.size else text_w + 2 * args.margin
    plate_h = args.height if args.height else text_h + 2 * args.margin
    if text_w > plate_w or text_h > plate_h:
        print(f"⚠️  text block {text_w:.1f}×{text_h:.1f}mm exceeds plate "
              f"{plate_w:.1f}×{plate_h:.1f}mm — it will overflow.", file=sys.stderr)

    x_left = (plate_w - text_w) / 2          # x of left dot column
    y_top = (plate_h + text_h) / 2 - DS      # y of top dot row (r=0)

    # ---- build geometry
    tris = box_tris(0, 0, 0, plate_w, plate_h, args.base)
    r = args.dot_diam / 2
    sink = min(0.4, args.base * 0.5)
    n_dots = 0
    for i, line in enumerate(grid):
        for j, dots in enumerate(line):
            for d in dots:
                c = 0 if d in (1, 2, 3) else 1          # column
                rrow = (d - 1) % 3                       # row 0/1/2
                cx = x_left + j * CP + c * DS
                cy = y_top - (i * LP + rrow * DS)
                tris += dome_tris(cx, cy, args.base, r, args.dot_height, sink)
                n_dots += 1

    # ---- output
    slug_src = (args.text or args.braille or args.dots or "label")
    slug = "".join(ch if ch.isalnum() else "_" for ch in slug_src.lower())[:28].strip("_") or "label"
    out = Path(args.out).expanduser() if args.out else HERE / "out" / f"braille_{slug}.stl"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_stl(out, tris)

    print(f"方案/scheme: {scheme_note}")
    print(f"braille: {shown}")
    print(f"cells: {ncols}×{nrows}  dots: {n_dots}  triangles: {len(tris)}")
    print(f"plate: {plate_w:.1f}×{plate_h:.1f}×{args.base}mm  "
          f"(dot Ø{args.dot_diam} h{args.dot_height}, spacing {DS}/{CP}/{LP})")
    print(f"✅ {out}  ({out.stat().st_size/1024:.0f} KB) — print dots-up, flat side on bed")


if __name__ == "__main__":
    main()
