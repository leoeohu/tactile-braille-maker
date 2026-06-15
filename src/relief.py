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

from env import BLENDER, BLENDER_SCRIPT, OUT_DIR, load_env

# Hard ceiling on grid subdivisions (longer side). res 2000 ~ 4M verts ~ 400MB STL;
# beyond this files/Blender become impractical and it is already far finer than any printer.
MAX_RES = 2000

# 'line' style: clean high-contrast black/white line art -> flat-topped raised relief.
TACTILE_STYLE = (
    "bold simple high-contrast black and white line illustration, "
    "thick clean solid black outlines on a pure white background, flat 2D, "
    "minimalist, no shading, no gradient, no greyscale fill, no color, no texture, "
    "no background scenery, single centered subject, clear bold recognizable silhouette, "
    "coloring-book / tactile diagram style for blind readers"
)

# 'relief' style: a smooth grayscale image (brighter = higher) -> sculptural bas-relief
# with varying depth, kept grayscale (no threshold) so heights differ across the subject.
RELIEF_STYLE = (
    "smooth white bas-relief sculpture of the subject carved in clay, photographed top-down "
    "with soft even lighting, grayscale height map where raised areas are bright and recessed "
    "areas are dark, gentle tonal gradients describing 3D form and depth, rounded smooth volumes, "
    "no hard outlines, no text or lettering, plain dark background, subject centered filling the frame"
)


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
        "Detect every text string in this image (labels, words, captions, titles). "
        "For each, give the exact text and its bounding box as [ymin, xmin, ymax, xmax] "
        "normalized to 0-1000. Include every distinct text item; if there is no text, "
        "return an empty array."
    )
    # Force a valid JSON array of {text, box_2d} so parsing never depends on prose/fences.
    schema = types.Schema(
        type=types.Type.ARRAY,
        items=types.Schema(
            type=types.Type.OBJECT,
            required=["text", "box_2d"],
            properties={
                "text": types.Schema(type=types.Type.STRING),
                "box_2d": types.Schema(type=types.Type.ARRAY,
                                       items=types.Schema(type=types.Type.NUMBER)),
            },
        ),
    )
    cfg = types.GenerateContentConfig(response_mime_type="application/json",
                                      response_schema=schema)
    client = genai.Client(api_key=key)

    def parse(text):
        m = re.search(r"\[.*\]", (text or "").strip(), re.S)
        if not m:
            return []
        out = []
        for it in json.loads(m.group(0), strict=False):
            t = str(it.get("text", "")).strip()
            b = it.get("box_2d") or it.get("box")
            if not t or not b or len(b) != 4:
                continue
            if not any(c.isalnum() for c in t):    # skip misread junk (○○, ⁇, □□ ...)
                continue
            ymin, xmin, ymax, xmax = [float(v) for v in b]
            s = 1000.0 if max(ymin, xmin, ymax, xmax) > 1.5 else 1.0
            out.append({"text": t, "box": (xmin / s, ymin / s, xmax / s, ymax / s)})
        return out

    import time
    last_err = None
    for attempt in range(3):                       # retry: Gemini sometimes returns nothing
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=data, mime_type=mime), prompt],
                config=cfg)
            out = parse(resp.text)
            if out:
                return out
        except Exception as e:                     # transient API / parse hiccup -> retry
            last_err = e
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:   # rate-limited: wait + retry
                d = re.search(r"retryDelay['\"]?:?\s*['\"]?(\d+)", msg)
                time.sleep(min(int(d.group(1)) if d else 8, 30))
    if last_err:
        raise last_err
    return []


def detect_text_tesseract(image_path: str, langs: str = "chi_sim+eng") -> list:
    """Local OCR (Tesseract) — no API. Returns [{text, box(0..1)}] grouped per text line."""
    import re
    import shutil
    import pytesseract
    from PIL import Image

    cmd = shutil.which("tesseract") or "/opt/homebrew/bin/tesseract"
    if Path(cmd).exists():
        pytesseract.pytesseract.tesseract_cmd = cmd
    img = Image.open(image_path)
    W, H = img.size
    d = pytesseract.image_to_data(img, lang=langs, output_type=pytesseract.Output.DICT)
    groups = {}
    for i in range(len(d["text"])):
        t = d["text"][i].strip()
        try:
            conf = float(d["conf"][i])
        except ValueError:
            conf = -1
        if not t or conf < 40:
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
        g = groups.setdefault(key, {"t": [], "x0": 1e9, "y0": 1e9, "x1": 0, "y1": 0})
        g["t"].append(t)
        g["x0"], g["y0"] = min(g["x0"], x), min(g["y0"], y)
        g["x1"], g["y1"] = max(g["x1"], x + w), max(g["y1"], y + h)
    out = []
    for g in groups.values():
        t = " ".join(g["t"]).strip()
        t = re.sub(r"(?<=[一-鿿])\s+(?=[一-鿿])", "", t)  # no spaces between CJK
        if not any(c.isalnum() for c in t):
            continue
        out.append({"text": t, "box": (g["x0"] / W, g["y0"] / H, g["x1"] / W, g["y1"] / H)})
    return out


def composite_braille(height_path: Path, detections: list, *,
                      plate_x: float, plate_y: float, margin: float, relief: float,
                      lang: str, dot_height: float = 0.6, dot_diam: float = 1.5,
                      dot_spacing: float = 2.5, cell_pitch: float = 6.0,
                      bg_value: float = 0.0, dot_value: float = None) -> int:
    """Erase printed text from the height map and stamp standard-spaced braille dots
    at those positions. Boxes are normalized 0..1 over the original image content.
    `bg_value`/`dot_value` are the heightmap levels of the surface and the dot apex
    (raise: 0 .. dot/relief; engrave: bg_gray .. 1, so dots still rise above the surface).
    Returns how many labels were placed."""
    import numpy as np
    from PIL import Image
    import braille as B

    hm = Image.open(height_path).convert("L")
    HW, HH = hm.size
    content_long = max(HW, HH) / (1 + 2 * margin)   # undo build_heightmap's margin pad
    mpx = round(margin * content_long)
    content_w, content_h = HW - 2 * mpx, HH - 2 * mpx
    ppm = HW / plate_x                              # px per mm (aspect preserved)
    ds = dot_spacing * ppm
    cp = cell_pitch * ppm
    dot_r = max(1.0, (dot_diam / 2) * ppm)
    if dot_value is None:
        dot_value = min(1.0, dot_height / relief)   # raise default: displaced height == dot_height

    arr = np.asarray(hm, dtype=np.float32) / 255.0
    R = int(np.ceil(dot_r))
    yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
    rr = np.sqrt(xx ** 2 + yy ** 2) / dot_r
    prof = np.sqrt(np.clip(1 - rr ** 2, 0.0, 1.0))           # spherical-cap profile 0..1
    stamp = bg_value + (dot_value - bg_value) * prof         # rises from surface to apex

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
        bx0, by0 = mpx + x0 * content_w, mpx + y0 * content_h
        bx1, by1 = mpx + x1 * content_w, mpx + y1 * content_h
        pad = int(0.35 * ds)
        arr[max(0, int(by0 - pad)):min(HH, int(by1 + pad)),
            max(0, int(bx0 - pad)):min(HW, int(bx1 + pad))] = bg_value   # erase printed text
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


def engrave_heightmap(height_path: Path, bg: float) -> None:
    """Turn a raise-convention height map (features bright) into an engrave one:
    features -> 0 (carved deepest), background/border -> bg gray (the top surface)."""
    import numpy as np
    from PIL import Image
    arr = np.asarray(Image.open(height_path).convert("L"), dtype=np.float32) / 255.0
    arr = bg * (1.0 - arr)                       # feature(1)->0, bg(0)->bg, grayscale scales
    Image.fromarray((np.clip(arr, 0, 1) * 255).astype("uint8"), "L").save(height_path)


# -------------------------------------------------------------- blender (solid)
def run_blender(heightmap: Path, out_stl: Path, *, size_x: float, size_y: float,
                mid_level: float, strength: float, bottom_z: float, res: int) -> None:
    if not Path(BLENDER).exists():
        sys.exit(f"Blender not found at {BLENDER!r}. Set $BLENDER to its path.")
    cmd = [BLENDER, "-b", "--python", str(BLENDER_SCRIPT), "--",
           str(heightmap), str(out_stl), str(size_x), str(size_y),
           str(mid_level), str(strength), str(bottom_z), str(res)]
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
    ap.add_argument("--relief", type=float, default=1.5, help="relief height/depth (mm)")
    ap.add_argument("--engrave", action="store_true",
                    help="carve the pattern INTO the plate (凹) instead of raising it (凸); "
                         "any braille labels still rise above the surface so they stay readable")
    ap.add_argument("--res", type=int, default=600,
                    help="precision: subdivisions along the longer side (higher = finer/slower)")
    ap.add_argument("--precision", type=float, default=None,
                    help=f"target detail in mm per vertex (overrides --res; clamped so "
                         f"subdivisions <= {MAX_RES}). Note: 3D printers resolve ~0.1mm (FDM) "
                         f"/ ~0.05mm (resin), so finer is not printable.")
    ap.add_argument("--style", choices=["line", "relief"], default="line",
                    help="line = black/white line art (flat-topped relief); "
                         "relief = grayscale bas-relief with varying depth")
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
    ap.add_argument("--text-engine", choices=["gemini", "tesseract"], default="gemini",
                    help="OCR for --braille-text: gemini (cloud) or tesseract (local, no API)")
    ap.add_argument("--braille-dot-height", type=float, default=0.6,
                    help="braille dot height in mm (standard ~0.6)")
    args = ap.parse_args()

    if not args.idea and not args.image:
        ap.error("provide --idea and/or --image")
    if args.use_image_directly and not args.image:
        ap.error("--use-image-directly requires --image")

    cfg = load_env()

    # ---- resolution / precision (subdivisions along the longer side)
    if args.precision:
        want = round(args.size / args.precision)
        res = max(2, min(MAX_RES, want))
        if want > res:
            print(f"   ⚠️ 精度 {args.precision}mm 需要 {want} 细分，超过上限 {MAX_RES}；已用最高可行 "
                  f"≈{args.size/res:.3f}mm/格（3D打印机约 0.1mm FDM / 0.05mm 树脂，再细也打不出来）")
    else:
        res = args.res

    # ---- style: black/white line art vs grayscale bas-relief
    if args.style == "relief":
        gen_style = RELIEF_STYLE
        h_invert, h_threshold, h_thicken, h_blur = False, None, 0, max(args.blur, 1.5)
    else:
        gen_style = TACTILE_STYLE
        h_invert = args.invert
        h_threshold = None if args.threshold < 0 else args.threshold
        h_thicken, h_blur = args.thicken, args.blur

    # ---- output naming
    slug_src = (args.idea or Path(args.image).stem or "tactile")
    slug = "".join(c if c.isalnum() else "_" for c in slug_src.lower())[:32].strip("_") or "tactile"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_stl = Path(args.out).expanduser() if args.out else OUT_DIR / f"{slug}_{stamp}.stl"
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
        prompt = f"{subject}. {gen_style}"
        if not cfg.get("POLLINATIONS_TOKEN"):
            sys.exit("POLLINATIONS_TOKEN missing (needed to generate the image).")
        print(f"[2/4] generating image via Pollination ({args.model}, style={args.style}) ...")
        generate_image(prompt, cfg["POLLINATIONS_TOKEN"], gen_png,
                       args.gen_size, args.gen_size, args.model, args.seed)
        print(f"      -> {gen_png}")
        source = gen_png

    # ---- 2. heightmap
    print(f"[3/4] building heightmap (style={args.style}) ...")
    build_heightmap(source, height_png, invert=h_invert, threshold=h_threshold,
                    thicken=h_thicken, blur=h_blur, margin=args.margin)

    # ---- plate dimensions follow the heightmap aspect ratio (no squishing)
    from PIL import Image as _Img
    with _Img.open(height_png) as _hm:
        hw, hh = _hm.size
    longer = max(hw, hh)
    plate_x = round(args.size * hw / longer, 2)
    plate_y = round(args.size * hh / longer, 2)

    # ---- upscale heightmap so fine features (esp. braille dots) aren't under-sampled
    if longer < res:
        sc = res / longer
        with _Img.open(height_png) as _hm:
            _hm.resize((round(hw * sc), round(hh * sc)), _Img.LANCZOS).save(height_png)
        print(f"      heightmap upscaled {longer}->{res}px to match precision")
    print(f"      -> {height_png}")

    # ---- displacement mode: raise (凸) vs engrave (凹). Braille always rises above surface.
    dot_h = args.braille_dot_height
    if args.engrave:
        bg_gray = args.relief / (args.relief + dot_h)        # plate top; features carve to -relief
        mid_level, strength, bottom_z = bg_gray, args.relief + dot_h, -(args.base + args.relief)
        engrave_heightmap(height_png, bg_gray)               # features->0 (deepest), bg/border->gray
        braille_bg, braille_top = bg_gray, 1.0               # dots rise from surface to +dot_h
    else:
        mid_level, strength, bottom_z = 0.0, args.relief, -args.base
        braille_bg, braille_top = 0.0, min(1.0, dot_h / args.relief)

    # ---- 2.5 detect text -> emboss braille (optional)
    if args.braille_text:
        engine = args.text_engine
        if engine == "gemini" and not cfg.get("GEMINI_API_KEY"):
            print("   ⚠️ 无 GEMINI_API_KEY，改用本地 OCR (tesseract)")
            engine = "tesseract"
        print(f"[3.5/4] detecting text ({engine}) -> braille ...")
        try:
            if engine == "tesseract":
                dets = detect_text_tesseract(str(source))
            else:
                dets = detect_text(str(source), cfg["GEMINI_API_KEY"], args.gemini_model)
        except Exception as e:
            dets = []
            print(f"   ⚠️ 文字检测失败: {e}")
        if dets:
            print("   找到文字: " + ", ".join(f"“{d['text']}”" for d in dets))
            n = composite_braille(height_png, dets,
                                  plate_x=plate_x, plate_y=plate_y, margin=args.margin,
                                  relief=args.relief, lang=args.braille_lang,
                                  dot_height=args.braille_dot_height,
                                  bg_value=braille_bg, dot_value=braille_top)
            print(f"   已嵌入 {n} 处盲文（原文字已抹平）")
        else:
            print("   未检测到文字，跳过盲文")

    # ---- 3. displace + solidify + export in Blender
    mode_label = "engrave 凹" if args.engrave else "raise 凸"
    print(f"[4/4] Blender displace -> STL "
          f"(plate={plate_x}×{plate_y}mm base={args.base}mm relief={args.relief}mm {mode_label} "
          f"res={res}, ≈{args.size/res:.3f}mm/格) ...")
    run_blender(height_png, out_stl, size_x=plate_x, size_y=plate_y,
                mid_level=mid_level, strength=strength, bottom_z=bottom_z, res=res)

    mb = out_stl.stat().st_size / 1e6
    verb = "carved into" if args.engrave else "raised on"
    print(f"\n✅ {out_stl}  ({mb:.1f} MB)")
    print(f"   plate {plate_x}×{plate_y} mm, base {args.base} mm, pattern {verb} the plate "
          f"({args.relief} mm) — print flat side down, no supports.")
    if args.keep and not args.use_image_directly:
        print(f"   intermediates: {gen_png.name}, {height_png.name}")


if __name__ == "__main__":
    main()
