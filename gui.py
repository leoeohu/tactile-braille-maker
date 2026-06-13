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
PY = sys.executable                 # reuse the interpreter the GUI runs under
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

sys.path.insert(0, str(HERE))
import braille as B                  # for the live braille dot preview

try:
    from PIL import Image, ImageTk
except Exception:
    Image = ImageTk = None


def open_in_finder(path: Path):
    subprocess.run(["open", "-R", str(path)] if path.exists() else ["open", str(OUT)])


# precision presets for the relief plate: label -> (mesh subdivisions, generated-image px)
PRECISION = [
    ("标准 (快)", 600, 1024),
    ("高", 900, 1280),
    ("超高 (慢)", 1200, 1600),
]


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
        self.precision = tk.StringVar(value=PRECISION[0][0])
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
        ttk.Combobox(box, textvariable=self.precision, state="readonly", width=10,
                     values=[p[0] for p in PRECISION]).grid(
            row=1, column=1, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(box, text="（短边按图片比例自动）", foreground="#777").grid(
            row=1, column=5, columnspan=4, sticky="w", padx=(12, 0), pady=(8, 0))

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
        res, gen = next((r, g) for (lab, r, g) in PRECISION if lab == self.precision.get())
        cmd = [PY, str(HERE / "tactile.py"), "--keep",
               "--size", self.size.get(), "--base", self.base.get(), "--relief", self.relief.get(),
               "--res", str(res), "--gen-size", str(gen)]
        if idea:
            cmd += ["--idea", idea]
        if img:
            cmd += ["--image", img]
            if self.use_direct.get():
                cmd += ["--use-image-directly"]
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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("盲人教具生成器 · Tactile Teaching Material Maker")
        self.geometry("760x720"); self.minsize(680, 600)
        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, padx=10, pady=10)
        nb.add(PictureTab(nb), text="   图片浮雕板   ")
        nb.add(BrailleTab(nb), text="   盲文标签   ")


if __name__ == "__main__":
    if "--selftest" in sys.argv:        # build widgets then quit (CI / smoke test)
        app = App(); app.update(); print("GUI selftest OK"); app.destroy()
    else:
        App().mainloop()
