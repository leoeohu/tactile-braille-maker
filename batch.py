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
PY = sys.executable


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
    ap.add_argument("list", help="text file: one item per line ('idea | label' optional)")
    ap.add_argument("--mode", choices=["picture", "braille", "both"], default="both")
    ap.add_argument("--outdir", help="output folder (default: out/batch_<timestamp>)")
    # picture options (forwarded to tactile.py)
    ap.add_argument("--size", default="120")
    ap.add_argument("--base", default="3")
    ap.add_argument("--relief", default="1.5")
    ap.add_argument("--precision", default="0.1")
    ap.add_argument("--style", choices=["line", "relief"], default="line")
    # braille options (forwarded to braille.py)
    ap.add_argument("--lang", default="auto")
    args = ap.parse_args()

    items = read_items(Path(args.list).expanduser())
    if not items:
        sys.exit("no items found in the list file")
    outdir = Path(args.outdir).expanduser() if args.outdir else \
        HERE / "out" / ("batch_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"== batch: {len(items)} items · mode={args.mode} -> {outdir} ==", flush=True)
    ok = fail = 0
    for i, (idea, label) in enumerate(items, 1):
        tag = f"[{i}/{len(items)}]"
        if args.mode in ("picture", "both"):
            out = outdir / f"{i:02d}_{slug(idea)}.stl"
            print(f"\n{tag} 🛠 浮雕: {idea}", flush=True)
            r = subprocess.run(
                [PY, str(HERE / "tactile.py"), "--idea", idea, "--out", str(out),
                 "--size", args.size, "--base", args.base, "--relief", args.relief,
                 "--precision", args.precision, "--style", args.style])
            ok, fail = (ok + 1, fail) if r.returncode == 0 else (ok, fail + 1)
        if args.mode in ("braille", "both"):
            out = outdir / f"{i:02d}_{slug(label)}_braille.stl"
            print(f"\n{tag} ⠿ 盲文: {label}", flush=True)
            r = subprocess.run(
                [PY, str(HERE / "braille.py"), label, "--lang", args.lang, "--out", str(out)])
            ok, fail = (ok + 1, fail) if r.returncode == 0 else (ok, fail + 1)

    print(f"\n✅ 批量完成: {ok} 成功, {fail} 失败 -> {outdir}", flush=True)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
