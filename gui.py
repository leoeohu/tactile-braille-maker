#!/usr/bin/env python3
"""Simple GUI for the tactile teaching-material tools (盲人教具生成器).

Two tabs:
  • 图片浮雕板  — idea/image → relief plate STL   (wraps tactile.py)
  • 盲文标签    — text → braille label STL         (wraps braille.py, live dot preview)

Zero extra dependencies (tkinter is in the Python standard library). Launch with the
project's venv so Pillow / google-genai / pypinyin are available:

    ~/gemini-tex/.venv/bin/python gui.py
"""
import os
import sys
import queue
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

sys.path.insert(0, str(HERE))
from _env import python_with_deps    # noqa: E402
PY = python_with_deps()             # interpreter that has the deps (not necessarily this one)
import braille as B                  # for the live braille dot preview

try:
    from PIL import Image, ImageTk
except Exception:
    Image = ImageTk = None


def open_in_finder(path: Path):
    subprocess.run(["open", "-R", str(path)] if path.exists() else ["open", str(OUT)])


# relief style: label -> tactile.py --style value
STYLES = [("线条 (黑白)", "line"), ("灰度浮雕 (深浅不一)", "relief")]


class Runner:
    """Run a subprocess in a thread, stream its stdout into a Tk text widget."""
    def __init__(self, widget, log, on_done):
        self.widget, self.log, self.on_done = widget, log, on_done
        self.q: queue.Queue = queue.Queue()

    def start(self, cmd):
        self.log.config(state="normal"); self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        threading.Thread(target=self._work, args=(cmd,), daemon=True).start()
        self.widget.after(80, self._drain)

    def _work(self, cmd):
        try:
            p = subprocess.Popen(cmd, cwd=str(HERE), text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for line in p.stdout:
                self.q.put(("log", line))
            p.wait()
            self.q.put(("done", p.returncode))
        except Exception as e:
            self.q.put(("log", f"ERROR: {e}\n"))
            self.q.put(("done", 1))

    def _drain(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log.config(state="normal")
                    self.log.insert("end", val); self.log.see("end")
                    self.log.config(state="disabled")
                else:
                    self.on_done(val); return
        except queue.Empty:
            pass
        self.widget.after(80, self._drain)


# --------------------------------------------------------------------- picture
class PictureTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)
        self.image_path = tk.StringVar()
        self.use_direct = tk.BooleanVar(value=False)
        self.size = tk.StringVar(value="120")
        self.base = tk.StringVar(value="3")
        self.relief = tk.StringVar(value="1.5")
        self.precision = tk.StringVar(value="0.1")     # mm per vertex
        self.style = tk.StringVar(value=STYLES[0][0])
        self.braille_text = tk.BooleanVar(value=False)
        self._preview_img = None

        ttk.Label(self, text="想法 / Idea（要做成浮雕的内容）").grid(row=0, column=0, columnspan=4, sticky="w")
        self.idea = tk.Text(self, height=3, wrap="word")
        self.idea.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(2, 8))
        self.idea.insert("1.0", "a butterfly")

        ttk.Label(self, text="参考图片（可选）").grid(row=2, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.image_path).grid(row=2, column=1, sticky="ew", padx=4)
        ttk.Button(self, text="选择…", command=self._pick).grid(row=2, column=2, padx=2)
        ttk.Checkbutton(self, text="直接用此图(不重画)", variable=self.use_direct).grid(row=2, column=3, sticky="w")

        box = ttk.Frame(self); box.grid(row=3, column=0, columnspan=4, sticky="w", pady=8)
        for i, (lab, var, unit) in enumerate([("板长边", self.size, "mm"),
                                              ("底板厚", self.base, "mm"),
                                              ("凸起高", self.relief, "mm")]):
            ttk.Label(box, text=lab).grid(row=0, column=i*3, padx=(0 if i == 0 else 12, 2))
            ttk.Entry(box, textvariable=var, width=6).grid(row=0, column=i*3+1)
            ttk.Label(box, text=unit).grid(row=0, column=i*3+2, padx=(2, 0))
        ttk.Label(box, text="精度").grid(row=1, column=0, pady=(8, 0), sticky="w")
        ttk.Entry(box, textvariable=self.precision, width=6).grid(row=1, column=1, pady=(8, 0), sticky="w")
        ttk.Label(box, text="mm/格 (越小越细; 打印机极限≈0.1)", foreground="#777").grid(
            row=1, column=2, columnspan=4, sticky="w", padx=(2, 0), pady=(8, 0))
        ttk.Label(box, text="风格").grid(row=2, column=0, pady=(8, 0), sticky="w")
        ttk.Combobox(box, textvariable=self.style, state="readonly", width=16,
                     values=[s[0] for s in STYLES]).grid(
            row=2, column=1, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(box, text="（短边按图片比例自动）", foreground="#777").grid(
            row=2, column=5, columnspan=4, sticky="w", padx=(12, 0), pady=(8, 0))
        ttk.Checkbutton(box, text="把图中所有文字翻译成盲文（带文字的图建议配合“直接用此图”）",
                        variable=self.braille_text).grid(
            row=3, column=0, columnspan=9, sticky="w", pady=(8, 0))

        self.btn = ttk.Button(self, text="🛠  生成浮雕 STL", command=self._go)
        self.btn.grid(row=4, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Button(self, text="📂 打开输出文件夹", command=lambda: open_in_finder(OUT)).grid(
            row=4, column=2, columnspan=2, sticky="w")

        self.log = tk.Text(self, height=8, state="disabled", bg="#111", fg="#9f9", wrap="word")
        self.log.grid(row=5, column=0, columnspan=4, sticky="nsew", pady=6)
        self.preview = ttk.Label(self, text="（生成后这里显示高度图预览）", anchor="center")
        self.preview.grid(row=6, column=0, columnspan=4, sticky="nsew")
        self.columnconfigure(1, weight=1); self.rowconfigure(5, weight=1); self.rowconfigure(6, weight=1)
        self.runner = Runner(self, self.log, self._done)

    def _pick(self):
        p = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.webp"), ("All", "*.*")])
        if p:
            self.image_path.set(p)

    def _go(self):
        idea = self.idea.get("1.0", "end").strip()
        img = self.image_path.get().strip()
        if not idea and not img:
            messagebox.showwarning("缺少输入", "请填写想法或选择图片"); return
        style = dict(STYLES)[self.style.get()]
        cmd = [PY, str(HERE / "tactile.py"), "--keep",
               "--size", self.size.get(), "--base", self.base.get(), "--relief", self.relief.get(),
               "--precision", self.precision.get(), "--style", style]
        if idea:
            cmd += ["--idea", idea]
        if img:
            cmd += ["--image", img]
            if self.use_direct.get():
                cmd += ["--use-image-directly"]
        if self.braille_text.get():
            cmd += ["--braille-text"]
        self.btn.config(state="disabled", text="⏳ 生成中…")
        self.runner.start(cmd)

    def _done(self, code):
        self.btn.config(state="normal", text="🛠  生成浮雕 STL")
        if code != 0:
            messagebox.showerror("失败", "生成失败，请看下方日志"); return
        hmaps = sorted(OUT.glob("*_heightmap.png"), key=lambda p: p.stat().st_mtime)
        if hmaps and ImageTk:
            im = Image.open(hmaps[-1]); im.thumbnail((360, 360))
            self._preview_img = ImageTk.PhotoImage(im)
            self.preview.config(image=self._preview_img, text="")


# --------------------------------------------------------------------- braille
SCHEMES = [
    ("自动 (中文→国家通用盲文)", "auto"),
    ("中文 · 国家通用盲文 (2018)", "zh"),
    ("中文 · 现行盲文", "zh-current"),
    ("中文 · 现行盲文(逐字标调)", "zh-toned"),
    ("英文 · Grade 1", "en"),
    ("英文 · Grade 2 (缩写)", "en-g2"),
]


class BrailleTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)
        self.scheme = tk.StringVar(value=SCHEMES[0][0])
        self.base = tk.StringVar(value="2")
        self.dot_h = tk.StringVar(value="0.6")
        self.size = tk.StringVar(value="")     # blank = auto

        ttk.Label(self, text="文字 / Text（多行用回车分行）").grid(row=0, column=0, columnspan=4, sticky="w")
        self.text = tk.Text(self, height=3, wrap="word")
        self.text.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(2, 8))
        self.text.insert("1.0", "你好世界")
        self.text.bind("<KeyRelease>", lambda e: self._preview())

        ttk.Label(self, text="盲文方案").grid(row=2, column=0, sticky="w")
        cb = ttk.Combobox(self, textvariable=self.scheme, state="readonly",
                          values=[s[0] for s in SCHEMES], width=28)
        cb.grid(row=2, column=1, sticky="w", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._preview())

        box = ttk.Frame(self); box.grid(row=3, column=0, columnspan=4, sticky="w", pady=8)
        for i, (lab, var, unit) in enumerate([("底板厚", self.base, "mm"),
                                              ("点高", self.dot_h, "mm"),
                                              ("固定板宽(可空)", self.size, "mm")]):
            ttk.Label(box, text=lab).grid(row=0, column=i*3, padx=(0 if i == 0 else 12, 2))
            ttk.Entry(box, textvariable=var, width=7).grid(row=0, column=i*3+1)
            ttk.Label(box, text=unit).grid(row=0, column=i*3+2, padx=(2, 0))

        self.btn = ttk.Button(self, text="⠿  生成盲文 STL", command=self._go)
        self.btn.grid(row=4, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Button(self, text="📂 打开输出文件夹", command=lambda: open_in_finder(OUT)).grid(
            row=4, column=2, columnspan=2, sticky="w")

        ttk.Label(self, text="点字预览（实时）").grid(row=5, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.canvas = tk.Canvas(self, height=150, bg="white", highlightthickness=1,
                                highlightbackground="#ccc")
        self.canvas.grid(row=6, column=0, columnspan=4, sticky="ew")
        self.info = ttk.Label(self, text="", foreground="#555")
        self.info.grid(row=7, column=0, columnspan=4, sticky="w", pady=(2, 4))
        self.log = tk.Text(self, height=6, state="disabled", bg="#111", fg="#9f9", wrap="word")
        self.log.grid(row=8, column=0, columnspan=4, sticky="nsew", pady=6)
        self.columnconfigure(1, weight=1); self.rowconfigure(8, weight=1)
        self.runner = Runner(self, self.log, self._done)
        self.after(200, self._preview)

    def _lang(self):
        return dict(SCHEMES)[self.scheme.get()]

    def _cells(self):
        text = self.text.get("1.0", "end").strip()
        if not text:
            return [], "auto"
        lang = B.resolve_lang(text, self._lang())
        lines = B.translate(text, lang)
        return [B.cells_from_braille(l) for l in lines], lang

    def _preview(self):
        try:
            grid, lang = self._cells()
        except Exception as e:
            self.info.config(text=f"翻译出错: {e}"); return
        c = self.canvas; c.delete("all")
        DS, CP, LP, m = 15, 36, 60, 18
        for i, line in enumerate(grid):
            for j, dots in enumerate(line):
                x0 = m + j * CP; y0 = m + i * LP
                for d in range(1, 7):
                    col = 0 if d in (1, 2, 3) else 1
                    row = (d - 1) % 3
                    cx = x0 + col * DS; cy = y0 + row * DS
                    if d in dots:
                        c.create_oval(cx-6, cy-6, cx+6, cy+6, fill="#1a1a1a", outline="")
                    else:
                        c.create_oval(cx-2, cy-2, cx+2, cy+2, fill="#e8e8e8", outline="")
        cells = sum(len(l) for l in grid)
        c.configure(scrollregion=c.bbox("all"))
        self.info.config(text=f"方案: {B.SCHEME.get(lang, lang)}   ·   {cells} 格   ·   "
                              + "  ".join("".join(chr(0x2800 + sum(1 << (d-1) for d in dd)) for dd in line)
                                          for line in grid))

    def _go(self):
        text = self.text.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("缺少输入", "请填写文字"); return
        cmd = [PY, str(HERE / "braille.py"), text.replace("\n", "\\n"),
               "--lang", self._lang(), "--base", self.base.get(), "--dot-height", self.dot_h.get()]
        if self.size.get().strip():
            cmd += ["--size", self.size.get().strip()]
        self.btn.config(state="disabled", text="⏳ 生成中…")
        self.runner.start(cmd)

    def _done(self, code):
        self.btn.config(state="normal", text="⠿  生成盲文 STL")
        if code != 0:
            messagebox.showerror("失败", "生成失败，请看下方日志")


# ---------------------------------------------------------------------- batch
BATCH_MODES = [("两者都要 (图+盲文)", "both"), ("仅图片浮雕", "picture"), ("仅盲文标签", "braille")]
# how to make each picture: redraw from description (clean) vs extract the PDF figure (faithful)
BATCH_METHODS = [("重画 (描述→AI)", "redraw"),
                 ("抠原图 (Gemini定位)", "extract"),
                 ("本地图片文件夹 (免API)", "local")]


class BatchTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)
        self.mode = tk.StringVar(value=BATCH_MODES[0][0])
        self.method = tk.StringVar(value=BATCH_METHODS[0][0])
        self.size = tk.StringVar(value="120")
        self.precision = tk.StringVar(value="0.1")
        self.style = tk.StringVar(value=STYLES[0][0])
        self.lang = tk.StringVar(value=SCHEMES[0][0])
        self.pdf_path = None        # set when a PDF is analyzed (needed for 抠原图)
        self.worklist = []          # full worklist dicts (with page+box) from pdf_analyze
        self.figures_dir = None     # folder of locally-extracted figures (本地 method)
        self.variants = tk.BooleanVar(value=False)   # make line+relief versions

        ttk.Label(self, text="清单：每行一个；「想法 | 盲文标签」分别指定图片与盲文（或用下方“从 PDF 分析”自动填入）").grid(
            row=0, column=0, columnspan=4, sticky="w")
        self.list = tk.Text(self, height=8, wrap="word")
        self.list.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(2, 8))
        self.list.insert("1.0", "butterfly | 蝴蝶\na maple leaf | 枫叶\nsun | 太阳\n")

        opt = ttk.Frame(self); opt.grid(row=2, column=0, columnspan=4, sticky="w")
        ttk.Label(opt, text="模式").grid(row=0, column=0, sticky="w")
        ttk.Combobox(opt, textvariable=self.mode, state="readonly", width=16,
                     values=[m[0] for m in BATCH_MODES]).grid(row=0, column=1, padx=(2, 12))
        ttk.Label(opt, text="风格").grid(row=0, column=2)
        ttk.Combobox(opt, textvariable=self.style, state="readonly", width=15,
                     values=[s[0] for s in STYLES]).grid(row=0, column=3, padx=(2, 0))
        ttk.Label(opt, text="盲文方案").grid(row=1, column=0, pady=(6, 0), sticky="w")
        ttk.Combobox(opt, textvariable=self.lang, state="readonly", width=16,
                     values=[s[0] for s in SCHEMES]).grid(row=1, column=1, padx=(2, 12), pady=(6, 0))
        ttk.Label(opt, text="板长边").grid(row=1, column=2, pady=(6, 0))
        ttk.Entry(opt, textvariable=self.size, width=6).grid(row=1, column=3, sticky="w", pady=(6, 0))
        ttk.Label(opt, text="精度 mm/格").grid(row=2, column=0, pady=(6, 0), sticky="w")
        ttk.Entry(opt, textvariable=self.precision, width=6).grid(row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Label(opt, text="图片做法").grid(row=2, column=2, pady=(6, 0))
        ttk.Combobox(opt, textvariable=self.method, state="readonly", width=18,
                     values=[m[0] for m in BATCH_METHODS]).grid(row=2, column=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(opt, text="多版本（线条+灰度各出一个）", variable=self.variants).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))

        self.pdfbtn = ttk.Button(self, text="📄 从PDF分析(Gemini)", command=self._pick_pdf)
        self.pdfbtn.grid(row=3, column=0, sticky="w", pady=8)
        self.exbtn = ttk.Button(self, text="📁 PDF→图片(本地)", command=self._extract_pdf)
        self.exbtn.grid(row=3, column=1, sticky="w", pady=8)
        self.btn = ttk.Button(self, text="📦 批量生成", command=self._go)
        self.btn.grid(row=3, column=2, sticky="w", pady=8)
        ttk.Button(self, text="📂 输出", command=lambda: open_in_finder(OUT)).grid(
            row=3, column=3, sticky="w")
        self.log = tk.Text(self, height=12, state="disabled", bg="#111", fg="#9f9", wrap="word")
        self.log.grid(row=4, column=0, columnspan=4, sticky="nsew", pady=6)
        self.columnconfigure(0, weight=1); self.rowconfigure(1, weight=1); self.rowconfigure(4, weight=2)
        self.runner = Runner(self, self.log, self._done)

    def _go(self):
        import tempfile
        import json
        method = dict(BATCH_METHODS)[self.method.get()]
        mode = dict(BATCH_MODES)[self.mode.get()]
        tmp = Path(tempfile.gettempdir())
        common = ["--size", self.size.get(), "--precision", self.precision.get(),
                  "--lang", dict(SCHEMES)[self.lang.get()]]
        variants = ["--variants", "line,relief"] if self.variants.get() else \
                   ["--style", dict(STYLES)[self.style.get()]]

        if method == "local":                         # 本地文件夹：免 API
            if not self.figures_dir or not Path(self.figures_dir).is_dir():
                messagebox.showwarning("需要图片文件夹", "请先点「📁 PDF→图片(本地)」提取，并整理好文件夹。")
                return
            cmd = [PY, str(HERE / "batch.py"), "--images", self.figures_dir,
                   "--mode", mode, *common, *variants]
        elif method == "extract":                     # 抠原图：Gemini 定位 + 裁剪
            items = self.list.get("1.0", "end").strip()
            if not self.pdf_path or not self.worklist:
                messagebox.showwarning("需要 PDF", "“抠原图”需先点「📄 从PDF分析」选择 PDF。"); return
            keep = {ln.split("|")[-1].strip() for ln in items.splitlines() if ln.strip()}
            sel = [it for it in self.worklist
                   if it.get("title", "").strip() in keep and it.get("box_2d")]
            if not sel:
                messagebox.showwarning("无可抠图项", "选中的项缺少图框（box_2d），无法抠原图。"); return
            wl = tmp / "tactile_pdf_worklist.json"
            wl.write_text(json.dumps(sel, ensure_ascii=False), encoding="utf-8")
            cmd = [PY, str(HERE / "pdf_make.py"), self.pdf_path, str(wl), *common]
            if mode == "picture":
                cmd += ["--no-braille"]
        else:                                         # 重画：从描述生成
            items = self.list.get("1.0", "end").strip()
            if not items:
                messagebox.showwarning("缺少清单", "请输入清单（每行一个）"); return
            tf = tmp / "tactile_batch_list.txt"
            tf.write_text(items, encoding="utf-8")
            cmd = [PY, str(HERE / "batch.py"), str(tf), "--mode", mode, *common, *variants]
        self.btn.config(state="disabled", text="⏳ 批量生成中…")
        self.runner.start(cmd)

    # ---- local PDF -> figures (PyMuPDF, no API)
    def _extract_pdf(self):
        p = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf"), ("All", "*.*")])
        if not p:
            return
        self.exbtn.config(state="disabled", text="⏳ 提取中…")
        self._logln(f"📁 本地提取图片: {p}")
        threading.Thread(target=self._extract, args=(p,), daemon=True).start()

    def _extract(self, pdf):
        r = subprocess.run([PY, str(HERE / "pdf_extract.py"), pdf],
                           cwd=str(HERE), capture_output=True, text=True)
        figdir = str(Path(pdf).with_name(Path(pdf).stem + "_figures"))
        self.after(0, self._extracted, r, figdir)

    def _extracted(self, r, figdir):
        self.exbtn.config(state="normal", text="📁 PDF→图片(本地)")
        self._logln((r.stdout or r.stderr).strip())
        if r.returncode != 0 or not Path(figdir).is_dir():
            messagebox.showerror("提取失败", "PDF 图片提取失败，请看日志"); return
        self.figures_dir = figdir
        self.method.set(BATCH_METHODS[2][0])          # switch 图片做法 -> 本地文件夹
        open_in_finder(Path(figdir))
        messagebox.showinfo("提取完成",
                            f"已提取到：\n{figdir}\n\n请在 Finder 里整理（删掉不要的、把文件名改成中文标题），"
                            "然后点「📦 批量生成」。\n（图片做法已自动切到“本地文件夹(免API)”。）")

    def _done(self, code):
        self.btn.config(state="normal", text="📦 批量生成")
        messagebox.showinfo("完成", "批量生成结束，请看日志与输出文件夹"
                            if code == 0 else "部分项目失败，请看日志")

    # ---- PDF -> worklist (Gemini analyzes the PDF, fills the list for review)
    def _logln(self, s):
        self.log.config(state="normal"); self.log.insert("end", s + "\n"); self.log.see("end")
        self.log.config(state="disabled")

    def _pick_pdf(self):
        p = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf"), ("All", "*.*")])
        if not p:
            return
        self.pdf_path = p
        self.pdfbtn.config(state="disabled", text="⏳ 分析中…")
        self._logln(f"📄 分析 PDF: {p}")
        threading.Thread(target=self._analyze, args=(p,), daemon=True).start()

    def _analyze(self, pdf):
        r = subprocess.run([PY, str(HERE / "pdf_analyze.py"), pdf],
                           cwd=str(HERE), capture_output=True, text=True)
        self.after(0, self._analyzed, r)

    def _analyzed(self, r):
        self.pdfbtn.config(state="normal", text="📄 从 PDF 分析")
        if r.returncode != 0 or not r.stdout.strip():
            self._logln("分析失败:\n" + (r.stderr[-800:] or "(no output)"))
            messagebox.showerror("分析失败", "PDF 分析失败，请看日志"); return
        self.list.delete("1.0", "end")
        self.list.insert("1.0", r.stdout.strip() + "\n")
        n = len([ln for ln in r.stdout.splitlines() if ln.strip()])
        try:                                          # load full worklist (page+box) for 抠原图
            import json
            self.worklist = json.loads(
                Path(self.pdf_path).with_suffix(".worklist.json").read_text(encoding="utf-8"))
        except Exception:
            self.worklist = []
        self._logln((r.stderr.strip() or "") + f"\n✓ 已填入 {n} 项，检查/删减后点「批量生成」")
        messagebox.showinfo("分析完成", f"Gemini 识别出 {n} 项，已填入清单。\n"
                            "检查/删减后点「批量生成」。\n（“抠原图”做法可用原图，“重画”做法用描述。）")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("盲人教具生成器 · Tactile Teaching Material Maker")
        self.geometry("760x720"); self.minsize(680, 600)
        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, padx=10, pady=10)
        nb.add(PictureTab(nb), text="   图片浮雕板   ")
        nb.add(BrailleTab(nb), text="   盲文标签   ")
        nb.add(BatchTab(nb), text="   批量   ")


if __name__ == "__main__":
    if "--selftest" in sys.argv:        # build widgets then quit (CI / smoke test)
        app = App(); app.update(); print("GUI selftest OK"); app.destroy()
    else:
        App().mainloop()
