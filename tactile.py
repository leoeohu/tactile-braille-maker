#!/usr/bin/env python3
"""Tactile teaching-plate generator.

Idea (text) and/or reference image  ->  bold black-and-white image (Pollination)
                                    ->  height map  (PIL post-processing)
                                    ->  embossed, flat-bottomed solid  (Blender displace)
                                    ->  printable .stl

APIs
----
* Pollination  (image GENERATION only)  -> draws the tactile picture from a prompt.
* Gemini       (image READING only)     -> describes a supplied reference image so its
                                            description can be redrawn by Pollination
                                            (Pollination cannot accept image uploads).

Examples
--------
  python tactile.py --idea "a butterfly"
  python tactile.py --idea "the water cycle, sun cloud rain arrows"
  python tactile.py --image ~/photo.jpg                 # describe + redraw as tactile
  python tactile.py --image ~/clean_bw.png --use-image-directly
  python tactile.py --idea "a maple leaf" --size 100 --base 2.5 --relief 1.8 --out ~/leaf.stl

Run `python tactile.py -h` for all knobs.
"""
import argparse
import os
import sys
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLENDER = os.environ.get(
    "BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")

# Style appended to every generation prompt: enforces a clean, high-contrast,
# dark-subject-on-white-background picture that turns into good raised relief.
TACTILE_STYLE = (
    "bold simple high-contrast black and white line illustration, "
    "thick clean solid black outlines on a pure white background, flat 2D, "
    "minimalist, no shading, no gradient, no greyscale fill, no color, no texture, "
    "no background scenery, single centered subject, clear bold recognizable silhouette, "
    "coloring-book / tactile diagram style for blind readers"
)


# --------------------------------------------------------------------------- env
def load_env() -> dict:
    cfg = {}
    envp = HERE / ".env"
    if envp.exists():
        for line in envp.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("POLLINATIONS_TOKEN", "GEMINI_API_KEY"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


# ----------------------------------------------------------------- gemini (read)
def describe_image(path: str, key: str, model: str) -> str:
    from google import genai
    from google.genai import types

    data = Path(path).read_bytes()
    ext = Path(path).suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/jpeg")
    instruction = (
        "Describe the single main subject of this image in one concise sentence, "
        "focusing on overall shape, outline and the few most recognizable features. "
        "It will be redrawn as a simple bold black-and-white tactile line diagram for "
        "blind readers, so ignore color, lighting, background and fine texture. "
        "Output only the description sentence."
    )
    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=data, mime_type=mime), instruction],
    )
    return (resp.text or "").strip()


# ----------------------------------------------------------- pollination (write)
def generate_image(prompt: str, token: str, out: Path, w: int, h: int,
                   model: str, seed: int) -> Path:
    params = urllib.parse.urlencode(
        {"width": w, "height": h, "model": model, "nologo": "true", "seed": seed})
    url = f"https://gen.pollinations.ai/image/{urllib.parse.quote(prompt)}?{params}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0",
                      "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"Pollination HTTP {e.code}: "
                 f"{e.read().decode(errors='replace')[:400]}")
    if len(data) < 1000:
        sys.exit(f"Pollination returned suspiciously small response "
                 f"({len(data)} bytes): {data[:200]!r}")
    out.write_bytes(data)
    return out


# --------------------------------------------------------------- heightmap (PIL)
def build_heightmap(src: Path, dst: Path, *, invert: bool, threshold,
                    thicken: int, blur: float, margin: float) -> Path:
    from PIL import Image, ImageOps, ImageFilter

    im = Image.open(src).convert("L")
    im = ImageOps.autocontrast(im, cutoff=1)
    if threshold is not None:                       # crisp, uniform-height features
        im = im.point(lambda p: 255 if p > threshold else 0)
    if invert:                                      # dark subject -> bright = raised
        im = ImageOps.invert(im)
    if thicken > 0:                                 # widen raised lines so fingers feel them
        im = im.filter(ImageFilter.MaxFilter(thicken * 2 + 1))
    if blur > 0:                                    # soften edges -> printable slopes
        im = im.filter(ImageFilter.GaussianBlur(blur))
    if margin > 0:                                  # flat (black) border, ASPECT PRESERVED
        w, h = im.size
        mpx = int(round(max(w, h) * margin))
        canvas = Image.new("L", (w + 2 * mpx, h + 2 * mpx), 0)
        canvas.paste(im, (mpx, mpx))               # no resize -> no squishing
        im = canvas
    im.save(dst)
    return dst


# --------------------------------------------------- text -> braille on the plate
def detect_text(image_path: str, key: str, model: str) -> list:
    """Use Gemini to find every text string + its bounding box (normalized 0..1)."""
    from google import genai
    from google.genai import types
    import json
    import re

    data = Path(image_path).read_bytes()
    ext = Path(image_path).suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/png")
    prompt = (
        "Detect ALL text strings in this image (labels, words, captions, titles). "
        "Return ONLY a JSON array, each element "
        '{"text": "<exact text>", "box_2d": [ymin, xmin, ymax, xmax]} '
        "with box coordinates normalized to 0-1000. Include every distinct text item. "
        "No prose, no code fences."
    )
    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=model, contents=[types.Part.from_bytes(data=data, mime_type=mime), prompt])
    m = re.search(r"\[.*\]", (resp.text or "").strip(), re.S)
    if not m:
        return []
    out = []
    for it in json.loads(m.group(0)):
        t = str(it.get("text", "")).strip()
        b = it.get("box_2d") or it.get("box")
        if not t or not b or len(b) != 4:
            continue
        ymin, xmin, ymax, xmax = [float(v) for v in b]
        s = 1000.0 if max(ymin, xmin, ymax, xmax) > 1.5 else 1.0
        out.append({"text": t, "box": (xmin / s, ymin / s, xmax / s, ymax / s)})
    return out


def composite_braille(height_path: Path, src_w: int, src_h: int, detections: list, *,
                      plate_x: float, plate_y: float, margin: float, relief: float,
                      lang: str, dot_height: float = 0.6, dot_diam: float = 1.5,
                      dot_spacing: float = 2.5, cell_pitch: float = 6.0) -> int:
    """Erase printed text from the height map and stamp standard-spaced braille dots
    at those positions. Returns how many labels were placed."""
    import numpy as np
    from PIL import Image
    import braille as B

    hm = Image.open(height_path).convert("L")
    HW, HH = hm.size
    mpx = round(max(src_w, src_h) * margin)        # padding added by build_heightmap
    ppm = HW / plate_x                              # px per mm (aspect preserved)
    ds = dot_spacing * ppm
    cp = cell_pitch * ppm
    dot_r = max(1.0, (dot_diam / 2) * ppm)
    peak = min(1.0, dot_height / relief)            # so displaced dot height == dot_height

    arr = np.asarray(hm, dtype=np.float32) / 255.0
    R = int(np.ceil(dot_r))
    yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
    rr = np.sqrt(xx ** 2 + yy ** 2) / dot_r
    stamp = peak * np.sqrt(np.clip(1 - rr ** 2, 0.0, 1.0))   # spherical-cap dome

    def paste_max(cx, cy):
        xi, yi = int(round(cx)) - R, int(round(cy)) - R
        sx0, sy0 = max(0, -xi), max(0, -yi)
        dx0, dy0 = max(0, xi), max(0, yi)
        dx1, dy1 = min(HW, xi + stamp.shape[1]), min(HH, yi + stamp.shape[0])
        if dx1 <= dx0 or dy1 <= dy0:
            return
        sub = stamp[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
        arr[dy0:dy1, dx0:dx1] = np.maximum(arr[dy0:dy1, dx0:dx1], sub)

    placed = 0
    for det in detections:
        x0, y0, x1, y1 = det["box"]
        bx0, by0 = mpx + x0 * src_w, mpx + y0 * src_h
        bx1, by1 = mpx + x1 * src_w, mpx + y1 * src_h
        pad = int(0.35 * ds)
        arr[max(0, int(by0 - pad)):min(HH, int(by1 + pad)),
            max(0, int(bx0 - pad)):min(HW, int(bx1 + pad))] = 0.0   # erase printed text
        lng = B.resolve_lang(det["text"], lang)
        lines = B.translate(det["text"], lng)
        cells = B.cells_from_braille(lines[0]) if lines else []
        cy = (by0 + by1) / 2
        top = cy - ds                                  # center the 3 dot rows on cy
        for j, dots in enumerate(cells):
            for d in dots:
                col = 0 if d in (1, 2, 3) else 1
                row = (d - 1) % 3
                paste_max(bx0 + j * cp + col * ds, top + row * ds)
        if cells:
            placed += 1
            if bx0 + len(cells) * cp > HW:
                print(f"   ⚠️ 盲文“{det['text']}”可能超出右边缘（盲文比原文宽）")
    Image.fromarray((np.clip(arr, 0, 1) * 255).astype("uint8"), "L").save(height_path)
    return placed


# -------------------------------------------------------------- blender (solid)
def run_blender(heightmap: Path, out_stl: Path, *, size_x: float, size_y: float,
                base: float, relief: float, res: int) -> None:
    if not Path(BLENDER).exists():
        sys.exit(f"Blender not found at {BLENDER!r}. Set $BLENDER to its path.")
    cmd = [BLENDER, "-b", "--python", str(HERE / "blender_displace.py"), "--",
           str(heightmap), str(out_stl),
           str(size_x), str(size_y), str(base), str(relief), str(res)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if line.startswith("[blender]"):
            print("  " + line)
    if proc.returncode != 0 or not out_stl.exists():
        sys.stderr.write(proc.stdout[-2000:] + "\n" + proc.stderr[-2000:] + "\n")
        sys.exit("Blender step failed.")


# ------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate a 3D-printable tactile relief plate from an idea and/or image.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--idea", help="text description of what to depict")
    ap.add_argument("--image", help="reference image (described via Gemini, then redrawn)")
    ap.add_argument("--use-image-directly", action="store_true",
                    help="skip generation; use --image straight as the heightmap "
                         "(best for an image you already made clean black-and-white)")
    ap.add_argument("--out", help="output .stl path "
                                  "(default: out/<slug>_<timestamp>.stl)")
    # physical dimensions (mm)
    ap.add_argument("--size", type=float, default=120.0,
                    help="LONGER plate side (mm); shorter side follows the image aspect")
    ap.add_argument("--base", type=float, default=3.0, help="solid base thickness (mm)")
    ap.add_argument("--relief", type=float, default=1.5, help="max relief height (mm)")
    ap.add_argument("--res", type=int, default=600,
                    help="precision: subdivisions along the longer side (higher = finer/slower)")
    # image generation
    ap.add_argument("--model", default="flux", help="Pollination model")
    ap.add_argument("--gemini-model", default="gemini-2.5-flash", help="Gemini vision model")
    ap.add_argument("--gen-size", type=int, default=1024, help="generated image px (square)")
    ap.add_argument("--seed", type=int, default=42, help="generation seed (reproducible)")
    # heightmap shaping
    ap.add_argument("--no-invert", dest="invert", action="store_false",
                    help="do NOT invert (use when the subject is already light-on-dark)")
    ap.add_argument("--threshold", type=int, default=128,
                    help="binarize threshold 0-255; use -1 to keep grayscale relief")
    ap.add_argument("--thicken", type=int, default=2,
                    help="dilate raised features by N px (0 = off)")
    ap.add_argument("--blur", type=float, default=1.0, help="edge-smoothing blur px")
    ap.add_argument("--margin", type=float, default=0.06,
                    help="flat border as fraction of side (each edge)")
    ap.add_argument("--keep", action="store_true",
                    help="keep intermediate generated.png / heightmap.png next to the STL")
    # text -> braille
    ap.add_argument("--braille-text", action="store_true",
                    help="detect text in the image and emboss it as braille instead of glyphs "
                         "(best with --use-image-directly so positions match)")
    ap.add_argument("--braille-lang", default="auto",
                    help="braille scheme for --braille-text (auto/zh/en/...; 中文→国家通用盲文)")
    ap.add_argument("--braille-dot-height", type=float, default=0.6,
                    help="braille dot height in mm (standard ~0.6)")
    args = ap.parse_args()

    if not args.idea and not args.image:
        ap.error("provide --idea and/or --image")
    if args.use_image_directly and not args.image:
        ap.error("--use-image-directly requires --image")

    cfg = load_env()
    threshold = None if args.threshold is not None and args.threshold < 0 else args.threshold

    # ---- output naming
    slug_src = (args.idea or Path(args.image).stem or "tactile")
    slug = "".join(c if c.isalnum() else "_" for c in slug_src.lower())[:32].strip("_") or "tactile"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_stl = Path(args.out).expanduser() if args.out else HERE / "out" / f"{slug}_{stamp}.stl"
    out_stl.parent.mkdir(parents=True, exist_ok=True)
    work = out_stl.parent if args.keep else Path(
        os.environ.get("TMPDIR", "/tmp"))
    gen_png = work / f"{slug}_{stamp}_generated.png"
    height_png = work / f"{slug}_{stamp}_heightmap.png"

    print(f"== tactile plate: {out_stl.name} ==")

    # ---- 1. decide the heightmap source image
    if args.use_image_directly:
        print(f"[1/4] using supplied image directly: {args.image}")
        source = Path(args.image).expanduser()
    else:
        subject = args.idea or ""
        if args.image:
            if not cfg.get("GEMINI_API_KEY"):
                sys.exit("GEMINI_API_KEY missing (needed to read --image).")
            print(f"[1/4] reading reference image via Gemini ({args.gemini_model}) ...")
            desc = describe_image(str(Path(args.image).expanduser()),
                                  cfg["GEMINI_API_KEY"], args.gemini_model)
            print(f"      -> {desc}")
            subject = f"{subject}, {desc}" if subject else desc
        prompt = f"{subject}. {TACTILE_STYLE}"
        if not cfg.get("POLLINATIONS_TOKEN"):
            sys.exit("POLLINATIONS_TOKEN missing (needed to generate the image).")
        print(f"[2/4] generating image via Pollination ({args.model}) ...")
        generate_image(prompt, cfg["POLLINATIONS_TOKEN"], gen_png,
                       args.gen_size, args.gen_size, args.model, args.seed)
        print(f"      -> {gen_png}")
        source = gen_png

    # ---- 2. heightmap
    print("[3/4] building heightmap ...")
    build_heightmap(source, height_png, invert=args.invert, threshold=threshold,
                    thicken=args.thicken, blur=args.blur, margin=args.margin)
    print(f"      -> {height_png}")

    # ---- plate dimensions follow the heightmap aspect ratio (no squishing)
    from PIL import Image as _Img
    with _Img.open(height_png) as _hm:
        hw, hh = _hm.size
    longer = max(hw, hh)
    plate_x = round(args.size * hw / longer, 2)
    plate_y = round(args.size * hh / longer, 2)

    # ---- 2.5 detect text -> emboss braille (optional)
    if args.braille_text:
        if not cfg.get("GEMINI_API_KEY"):
            print("   ⚠️ 跳过盲文翻译: 缺少 GEMINI_API_KEY")
        else:
            from PIL import Image as _SI
            with _SI.open(source) as _si:
                src_w, src_h = _si.size
            print(f"[3.5/4] detecting text via Gemini ({args.gemini_model}) -> braille ...")
            try:
                dets = detect_text(str(source), cfg["GEMINI_API_KEY"], args.gemini_model)
            except Exception as e:
                dets = []
                print(f"   ⚠️ 文字检测失败: {e}")
            if dets:
                print("   找到文字: " + ", ".join(f"“{d['text']}”" for d in dets))
                n = composite_braille(height_png, src_w, src_h, dets,
                                      plate_x=plate_x, plate_y=plate_y, margin=args.margin,
                                      relief=args.relief, lang=args.braille_lang,
                                      dot_height=args.braille_dot_height)
                print(f"   已嵌入 {n} 处盲文（原文字已抹平）")
            else:
                print("   未检测到文字，跳过盲文")

    # ---- 3. displace + solidify + export in Blender
    print(f"[4/4] Blender displace -> STL "
          f"(plate={plate_x}×{plate_y}mm base={args.base}mm relief={args.relief}mm res={args.res}) ...")
    run_blender(height_png, out_stl, size_x=plate_x, size_y=plate_y,
                base=args.base, relief=args.relief, res=args.res)

    mb = out_stl.stat().st_size / 1e6
    print(f"\n✅ {out_stl}  ({mb:.1f} MB)")
    print(f"   plate {plate_x}×{plate_y} mm, base {args.base} mm, "
          f"relief up to {args.relief} mm — print flat side down, no supports.")
    if args.keep and not args.use_image_directly:
        print(f"   intermediates: {gen_png.name}, {height_png.name}")


if __name__ == "__main__":
    main()
