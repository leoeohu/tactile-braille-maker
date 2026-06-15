#!/usr/bin/env python3
"""Batch-generate tactile relief plates and/or braille labels from a list file.

Each non-empty, non-`#` line is one item. An optional `|` splits the picture idea
from the braille label, so you can pair an English-drawn picture with a Chinese label:

    butterfly
    a maple leaf | 枫叶
    狐狸 | 狐狸
    # lines starting with # are ignored

Modes:
  picture  — a relief plate per item (left side / whole line)
  braille  — a braille label per item (right side if given, else the line)
  both     — both, named so they pair up (great for vocabulary cards)

Examples
--------
  python batch.py words.txt --mode both --size 120 --lang auto
  python batch.py animals.txt --mode picture --style relief --precision 0.1
"""
import argparse
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


def read_items(path: Path):
    items = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            a, b = line.split("|", 1)
            items.append((a.strip(), b.strip()))
        else:
            items.append((line, line))
    return items


def main():
    ap = argparse.ArgumentParser(
        description="Batch-generate tactile plates / braille labels from a list file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("list", nargs="?", help="text file: one item per line ('idea | label')")
    ap.add_argument("--images", help="folder of images -> a faithful plate per image "
                                     "(use-image-directly); NO API needed for the picture")
    ap.add_argument("--mode", choices=["picture", "braille", "both"], default="both")
    ap.add_argument("--outdir", help="output folder (default: out/batch_<timestamp>)")
    # picture options (forwarded to tactile.py)
    ap.add_argument("--size", default="120")
    ap.add_argument("--base", default="3")
    ap.add_argument("--relief", default="1.5")
    ap.add_argument("--precision", default="0.1")
    ap.add_argument("--style", choices=["line", "relief"], default="line")
    ap.add_argument("--variants", default="",
                    help="comma styles to make per item, e.g. 'line,relief' -> one STL each")
    # braille options
    ap.add_argument("--lang", default="auto")
    ap.add_argument("--braille-text", action="store_true",
                    help="(--images) emboss the image's own text as braille in place")
    ap.add_argument("--text-engine", choices=["gemini", "tesseract"], default="tesseract",
                    help="OCR for --braille-text (tesseract = local, no API)")
    args = ap.parse_args()

    if not args.images and not args.list:
        ap.error("provide a list file or --images <folder>")
    styles = [s.strip() for s in args.variants.split(",") if s.strip()] or [args.style]
    outdir = Path(args.outdir).expanduser() if args.outdir else \
        HERE / "out" / ("batch_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    outdir.mkdir(parents=True, exist_ok=True)
    ok = fail = 0

    def make_picture(i, src_args, name, label=None):
        """src_args: the tactile.py input flags (idea or image). Makes one STL per style."""
        nonlocal ok, fail
        for st in styles:
            suf = f"_{st}" if len(styles) > 1 else ""
            out = outdir / f"{i:02d}_{slug(name)}{suf}.stl"
            cmd = [PY, str(HERE / "tactile.py"), *src_args, "--out", str(out),
                   "--size", args.size, "--base", args.base, "--relief", args.relief,
                   "--precision", args.precision, "--style", st]
            if args.braille_text:
                cmd += ["--braille-text", "--braille-lang", args.lang,
                        "--text-engine", args.text_engine]
            print(f"\n[{i}] 🛠 浮雕({st}): {name}", flush=True)
            r = subprocess.run(cmd)
            ok, fail = (ok + 1, fail) if r.returncode == 0 else (ok, fail + 1)

    def make_braille(i, label):
        nonlocal ok, fail
        out = outdir / f"{i:02d}_{slug(label)}_braille.stl"
        print(f"\n[{i}] ⠿ 盲文: {label}", flush=True)
        r = subprocess.run([PY, str(HERE / "braille.py"), label, "--lang", args.lang,
                            "--out", str(out)])
        ok, fail = (ok + 1, fail) if r.returncode == 0 else (ok, fail + 1)

    if args.images:                                  # ---- local folder of images (no API)
        exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        files = sorted(p for p in Path(args.images).expanduser().iterdir()
                       if p.suffix.lower() in exts)
        if not files:
            sys.exit(f"no images in {args.images}")
        print(f"== batch images: {len(files)} files × {len(styles)} 版本 -> {outdir} ==", flush=True)
        for i, f in enumerate(files, 1):
            if args.mode in ("picture", "both"):
                make_picture(i, ["--image", str(f), "--use-image-directly"], f.stem)
            if args.mode in ("braille", "both"):
                make_braille(i, f.stem)              # filename -> braille label
    else:                                            # ---- text list (ideas)
        items = read_items(Path(args.list).expanduser())
        if not items:
            sys.exit("no items found in the list file")
        print(f"== batch: {len(items)} items · mode={args.mode} × {len(styles)} 版本 -> {outdir} ==",
              flush=True)
        for i, (idea, label) in enumerate(items, 1):
            if args.mode in ("picture", "both"):
                make_picture(i, ["--idea", idea], idea)
            if args.mode in ("braille", "both"):
                make_braille(i, label)

    print(f"\n✅ 批量完成: {ok} 成功, {fail} 失败 -> {outdir}", flush=True)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
