#!/usr/bin/env python3
"""Extract figures from a PDF **locally** (PyMuPDF) — NO API, no quota.

Pulls every embedded image out of the PDF (textbook figures are embedded rasters),
skipping tiny icons. You then curate the folder (delete what you don't want, rename
files to nice titles) and feed it to:  batch.py --images <folder>

For diagrams drawn as vectors (no embedded raster), add --pages to also render each
whole page as a PNG.

Examples
--------
  python pdf_extract.py 选图.pdf                  # -> 选图_figures/  (one PNG per figure)
  python pdf_extract.py 选图.pdf --min 300        # skip images under 300 px
  python pdf_extract.py 选图.pdf --pages          # also render full pages
"""
import argparse
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(
        description="Extract figures from a PDF locally (no API) for tactile batch making.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--out", help="output folder (default: <pdf>_figures)")
    ap.add_argument("--min", type=int, default=200, help="skip images smaller than this (px)")
    ap.add_argument("--pages", action="store_true",
                    help="also render each full page (for vector diagrams)")
    ap.add_argument("--dpi", type=int, default=150, help="DPI for --pages renders")
    args = ap.parse_args()

    import fitz

    pdf = Path(args.pdf).expanduser()
    out = Path(args.out).expanduser() if args.out else pdf.with_name(pdf.stem + "_figures")
    out.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf))

    seen, n, skipped = set(), 0, 0
    for pno in range(len(doc)):
        for img in doc[pno].get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            d = doc.extract_image(xref)
            if d["width"] < args.min or d["height"] < args.min:
                skipped += 1
                continue
            n += 1
            (out / f"p{pno + 1:02d}_{xref}.{d['ext']}").write_bytes(d["image"])

    if args.pages:
        for pno in range(len(doc)):
            doc[pno].get_pixmap(dpi=args.dpi).save(str(out / f"page{pno + 1:02d}.png"))

    print(f"✅ {n} 张图 -> {out}  ({skipped} 个小图标已跳过, {len(doc)} 页)")
    print("   请打开文件夹整理：删掉不要的、把文件名改成中文标题（文件名会用作盲文标签），")
    print(f"   然后：  python batch.py --images '{out}'  --variants line,relief")


if __name__ == "__main__":
    main()
