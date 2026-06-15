#!/usr/bin/env python3
"""Faithfully make tactile plates from a PDF by EXTRACTING each figure (not redrawing).

Reads a worklist JSON (from pdf_analyze.py — each item has page + box_2d), renders that
page, crops the figure, and runs it through tactile.py with --use-image-directly so the
real diagram becomes the relief, plus --braille-text so the figure's own labels are
embossed as braille in place. Crops are saved for review.

Examples
--------
  python pdf_analyze.py 选图.pdf                       # -> 选图.worklist.json
  python pdf_make.py 选图.pdf 选图.worklist.json        # extract + make each
  python pdf_make.py 选图.pdf wl.json --size 180 --no-braille
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _env import python_with_deps  # noqa: E402
PY = python_with_deps()


def slug(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s.lower())[:32].strip("_") or "item"


def main():
    ap = argparse.ArgumentParser(
        description="Extract figures from a PDF and make faithful tactile plates with braille labels.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("worklist", help="worklist.json from pdf_analyze.py (needs page + box_2d)")
    ap.add_argument("--outdir", help="output folder (default: out/pdf_<timestamp>)")
    ap.add_argument("--dpi", type=int, default=200, help="page render DPI for the crop")
    ap.add_argument("--pad", type=float, default=0.02, help="extra crop padding (fraction of box)")
    ap.add_argument("--size", default="160")
    ap.add_argument("--base", default="3")
    ap.add_argument("--relief", default="1.5")
    ap.add_argument("--precision", default="0.12")
    ap.add_argument("--lang", default="auto")
    ap.add_argument("--text-engine", choices=["gemini", "tesseract"], default="tesseract",
                    help="OCR for in-place braille (tesseract = local, no API)")
    ap.add_argument("--no-braille", action="store_true", help="do not emboss labels as braille")
    args = ap.parse_args()

    import fitz
    from PIL import Image

    items = json.loads(Path(args.worklist).expanduser().read_text(encoding="utf-8"))
    doc = fitz.open(str(Path(args.pdf).expanduser()))
    outdir = Path(args.outdir).expanduser() if args.outdir else \
        HERE / "out" / ("pdf_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    crops = outdir / "_crops"
    crops.mkdir(parents=True, exist_ok=True)

    print(f"== PDF extract: {len(items)} items -> {outdir} ==", flush=True)
    ok = fail = 0
    for i, it in enumerate(items, 1):
        title = it.get("title") or f"item{i}"
        box = it.get("box_2d")
        page = it.get("page")
        tag = f"[{i}/{len(items)}]"
        if not box or not page or len(box) != 4 or not (1 <= page <= len(doc)):
            print(f"{tag} ⚠️ 跳过(无页码/框): {title}", flush=True)
            fail += 1
            continue
        pix = doc[page - 1].get_pixmap(dpi=args.dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        W, H = img.size
        ymin, xmin, ymax, xmax = box
        bw, bh = xmax - xmin, ymax - ymin
        x0 = max(0, int((xmin - args.pad * bw) / 1000 * W))
        y0 = max(0, int((ymin - args.pad * bh) / 1000 * H))
        x1 = min(W, int((xmax + args.pad * bw) / 1000 * W))
        y1 = min(H, int((ymax + args.pad * bh) / 1000 * H))
        if x1 - x0 < 8 or y1 - y0 < 8:
            print(f"{tag} ⚠️ 跳过(框太小): {title}", flush=True)
            fail += 1
            continue
        crop_path = crops / f"{i:02d}_{slug(title)}.png"
        img.crop((x0, y0, x1, y1)).save(crop_path)

        out = outdir / f"{i:02d}_{slug(title)}.stl"
        cmd = [PY, str(HERE / "tactile.py"), "--image", str(crop_path), "--use-image-directly",
               "--out", str(out), "--size", args.size, "--base", args.base,
               "--relief", args.relief, "--precision", args.precision]
        if not args.no_braille:
            cmd += ["--braille-text", "--braille-lang", args.lang, "--text-engine", args.text_engine]
        print(f"\n{tag} 🖼→⠿ 抠原图: {title} (p{page})", flush=True)
        r = subprocess.run(cmd)
        ok, fail = (ok + 1, fail) if r.returncode == 0 else (ok, fail + 1)

    print(f"\n✅ 完成: {ok} 成功, {fail} 失败 -> {outdir}\n   裁剪图见 {crops}", flush=True)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
