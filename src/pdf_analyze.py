#!/usr/bin/env python3
"""Analyze a PDF with Gemini and produce a tactile-material worklist.

Gemini reads the whole PDF and lists every image/diagram to turn into a tactile print.
Output is one line per item in batch.py's `idea | label` format:

    <english description to draw> | <中文标题>

so you can review/edit it and feed it straight to batch.py (or the GUI 批量 tab).
A `<pdf>.worklist.json` sidecar with full details (page, labels, notes) is also written.

Examples
--------
  python pdf_analyze.py 选图.pdf                 # prints lines, writes 选图.worklist.txt/.json
  python pdf_analyze.py 选图.pdf --out work.txt
  python pdf_analyze.py 选图.pdf | python batch.py /dev/stdin --mode both
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import env  # noqa: E402  (for load_env)

PROMPT = (
    "This PDF curates images/diagrams to turn into tactile (raised-relief) 3D prints for "
    "blind students. List EVERY distinct item to be made. For each item give: "
    "title (the Chinese name), grade (e.g. 七年级上册 if shown), page (1-based page in THIS pdf "
    "where its main figure is), box_2d ([ymin,xmin,ymax,xmax] normalized 0-1000, tightly around "
    "ONLY the single best figure image for that item on that page — exclude captions and body "
    "paragraphs; if a note says to use a particular version, box that one), "
    "description (ONE concise English sentence describing the figure so it can be redrawn as a "
    "simple bold black-and-white tactile line diagram), "
    "labels (the text labels appearing in that figure, as a list), and note (any Chinese "
    "preference note, e.g. which version to use). Return a JSON array."
)


def analyze(pdf_path: str, key: str, model: str) -> list:
    from google import genai
    from google.genai import types

    data = Path(pdf_path).expanduser().read_bytes()
    schema = types.Schema(
        type=types.Type.ARRAY,
        items=types.Schema(
            type=types.Type.OBJECT, required=["title", "description"],
            properties={
                "title": types.Schema(type=types.Type.STRING),
                "grade": types.Schema(type=types.Type.STRING),
                "page": types.Schema(type=types.Type.INTEGER),
                "box_2d": types.Schema(type=types.Type.ARRAY,
                                       items=types.Schema(type=types.Type.NUMBER)),
                "description": types.Schema(type=types.Type.STRING),
                "labels": types.Schema(type=types.Type.ARRAY,
                                       items=types.Schema(type=types.Type.STRING)),
                "note": types.Schema(type=types.Type.STRING),
            }))
    import re
    import time
    client = genai.Client(api_key=key)
    last = None
    for _ in range(3):                                   # retry: occasional empty/parse/rate-limit
        try:
            r = client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=data, mime_type="application/pdf"), PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=schema))
            items = json.loads(r.text, strict=False)
            if items:
                return items
        except Exception as e:
            last = e
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                d = re.search(r"retryDelay['\"]?:?\s*['\"]?(\d+)", msg)
                time.sleep(min(int(d.group(1)) if d else 8, 30))
    if last:
        if "429" in str(last) or "RESOURCE_EXHAUSTED" in str(last):
            sys.exit("Gemini 配额用尽（免费层每天约 20 次请求）。请稍后再试，或在 Google AI Studio "
                     "为该 API key 开通计费以提高额度。")
        raise last
    return []


def main():
    ap = argparse.ArgumentParser(
        description="Gemini → tactile worklist from a PDF (lines: 'description | 标题').",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("pdf", help="PDF file to analyze")
    ap.add_argument("--out", help="worklist .txt path (default: <pdf>.worklist.txt)")
    ap.add_argument("--gemini-model", default="gemini-2.5-flash")
    args = ap.parse_args()

    cfg = env.load_env()
    if not cfg.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY missing (needed to read the PDF).")

    items = analyze(args.pdf, cfg["GEMINI_API_KEY"], args.gemini_model)
    out = Path(args.out).expanduser() if args.out else \
        Path(args.pdf).expanduser().with_suffix(".worklist.txt")
    out.with_suffix(".json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = []
    for it in items:
        desc = (it.get("description") or "").strip().replace("|", "/").replace("\n", " ")
        title = (it.get("title") or "").strip().replace("|", "/")
        if not title:
            continue
        lines.append(f"{desc} | {title}" if desc else title)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"# {len(lines)} items  ->  {out.name}  (details: {out.with_suffix('.json').name})",
          file=sys.stderr)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
