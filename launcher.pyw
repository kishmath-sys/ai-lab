"""
launcher.pyw   AI Lab

Type what you want, attach files if you like, pick what to make, press Start.
ChatGPT researches, Claude checks the research and builds the result, ChatGPT checks it.
The finished files (Word, PDF, web page) appear in the run's files folder and are listed here.
Double-click it, or use the desktop shortcut the installer made.
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

try:
    import localai
except Exception:
    localai = None


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
RUNS = HERE / "runs"
AILAB = HERE / "ailab.py"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MAX_ATTACH_BYTES = 25_000_000
CONDENSE_OVER = 40_000   # characters of text in one file before condensing is offered

try:
    import convert
    ATTACH_TYPES = convert.SUPPORTED_INPUT
except Exception:  # the window still opens so the problem can be shown
    convert = None
    ATTACH_TYPES = [".txt", ".md", ".html", ".csv", ".json"]

FORMATS = {
    "doc_web": "Word document and interactive web page",
    "doc":     "Word document",
    "web":     "Interactive web page",
    "text":    "Just the answer (text file)",
}
DEFAULT_FORMAT = "doc_web"

C = {
    "bg": "#eef1f5", "card": "#ffffff", "ink": "#1d2733", "muted": "#5a6675", "line": "#d5dce5",
    "accent": "#1a5fb4", "accent_dark": "#144c91", "ok": "#17703a", "bad": "#b3261e", "warn": "#8a5a00",
    "working": "#1a5fb4", "wait": "#9aa5b3",
}
AGENT_COLOR = {"chatgpt": "#0f7b6c", "claude": "#b4552d", "gemini": "#3b6fd4", "program": "#5a6675"}
PRETTY = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini", "program": "Program"}
PLACEHOLDER = ("Example: Make a one page study guide on factoring trinomials with six practice problems and an answer key. "
               "Use current standards from the Illinois Articulation Initiative.")


# ------------------------------------------------------------------ helpers

def slug(text, fallback="request"):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:40].strip("-")) or fallback


def python_for_child():
    python = sys.executable
    if python.lower().endswith("pythonw.exe"):
        console = python[: -len("pythonw.exe")] + "python.exe"
        if os.path.exists(console):
            python = console
    return python


def keep_awake(on):
    if os.name != "nt":
        return
    try:
        import ctypes
        flags = 0x80000000 | (0x00000001 if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def open_path(path):
    path = str(path)
    try:
        if os.name == "nt":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as err:
        messagebox.showerror("Could not open", f"{path}\n\n{err}")


def show_in_folder(path):
    path = Path(path)
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        open_path(path.parent)


def tool_status():
    missing = [name for name, exe in (("Claude", "claude"), ("ChatGPT (Codex)", "codex")) if not shutil.which(exe)]
    return missing


def gemini_present():
    try:
        import ailab
        return bool(ailab.find_agy())
    except Exception:
        return bool(shutil.which("agy"))


def make_run(request, title, fmt, steps, attachments, email=False, originals=()):
    RUNS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    base = RUNS / f"{stamp}_{slug(title or request.strip().splitlines()[0])}"
    folder, n = base, 2
    while folder.exists():
        folder = Path(f"{base}-{n}")
        n += 1
    (folder / "inputs").mkdir(parents=True)
    used = set()
    for src in attachments:
        src = Path(src)
        name, k = src.name, 2
        while name.lower() in used:
            name = f"{src.stem}-{k}{src.suffix}"
            k += 1
        used.add(name.lower())
        shutil.copy2(src, folder / "inputs" / name)
    if originals:
        (folder / "originals_not_sent").mkdir()
        for src in originals:
            shutil.copy2(src, folder / "originals_not_sent" / Path(src).name)
    if not title:
        words = request.strip().split()
        title = " ".join(words[:8]).strip(" .,:;")
    (folder / "brief.md").write_text(request.strip() + "\n", encoding="utf-8")
    (folder / "run.json").write_text(json.dumps({"format": fmt, "steps": steps, "email": email, "title": title}, indent=2), encoding="utf-8")
    return folder


# ------------------------------------------------------------------ the window

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.dnd_ok = False
        self.title("AI Lab")
        self.configure(bg=C["bg"])
        self.minsize(760, 560)
        self.geometry("980x840")
        families = set(tkfont.families())
        self.family = "Segoe UI" if "Segoe UI" in families else ("Helvetica" if "Helvetica" in families else "TkDefaultFont")
        self.f_body = (self.family, 10)
        self.f_small = (self.family, 9)
        self.f_bold = (self.family, 10, "bold")
        self.f_h1 = (self.family, 20, "bold")
        self.f_h2 = (self.family, 12, "bold")
        self.attachments = []
        self.local_found = False
        self.precheck_var = tk.BooleanVar(value=self.lcfg().get("precheck", True))
        self.proc = None
        self.folder = None
        self.queue = queue.Queue()
        self.rows = {}
        self.fix_row = None
        self.verdict = None
        self.a11y = None
        self.email_info = None
        self.files_made = []
        self.stopped = False
        self.ended = False
        self.spin_i = 0
        self.mock = False
        self.end_state = ""
        self.end_message = ""
        self.working_rows = set()
        self.style_setup()
        self.build_header()
        self.container = tk.Frame(self, bg=C["bg"])
        self.container.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        self.build_input_view()
        self.build_run_view()
        self.show_input()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(150, self.pump)
        self.after(200, self.spin)
        self.after(300, self.check_tools)
        self.after(600, self.detect_local)

    # ---- styling
    def style_setup(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TFrame", background=C["card"])
        st.configure("TLabel", background=C["card"], foreground=C["ink"], font=self.f_body)
        st.configure("TCheckbutton", background=C["card"], foreground=C["ink"], font=self.f_body)
        st.map("TCheckbutton", background=[("active", C["card"])])
        st.configure("TRadiobutton", background=C["card"], foreground=C["ink"], font=self.f_body)
        st.map("TRadiobutton", background=[("active", C["card"])])
        st.configure("TCombobox", font=self.f_body, padding=4)
        st.configure("TEntry", padding=4)
        st.configure("TButton", font=self.f_body, padding=(12, 6), background="#e3e8ef", foreground=C["ink"], borderwidth=0)
        st.map("TButton", background=[("active", "#d3dae4"), ("disabled", "#eceff3")], foreground=[("disabled", "#9aa5b3")])
        st.configure("Accent.TButton", font=(self.family, 11, "bold"), padding=(22, 10), background=C["accent"], foreground="white")
        st.map("Accent.TButton", background=[("active", C["accent_dark"]), ("disabled", "#a9bfdc")], foreground=[("disabled", "white")])
        st.configure("Danger.TButton", background="#f6dedc", foreground=C["bad"])
        st.map("Danger.TButton", background=[("active", "#efc9c5"), ("disabled", "#f4eceb")])
        st.configure("Horizontal.TProgressbar", troughcolor="#e3e8ef", background=C["accent"], borderwidth=0)

    def card(self, parent, **kw):
        outer = tk.Frame(parent, bg=C["line"])
        inner = tk.Frame(outer, bg=C["card"], padx=kw.get("padx", 16), pady=kw.get("pady", 12))
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return outer, inner

    def build_header(self):
        head = tk.Frame(self, bg=C["bg"])
        head.pack(fill="x", padx=20, pady=(16, 8))
        tk.Label(head, text="AI Lab", font=self.f_h1, bg=C["bg"], fg=C["ink"]).pack(side="left")
        tk.Label(head, text="  ChatGPT, Claude and Gemini in the order you choose. Accessibility scanned.", font=self.f_body,
                 bg=C["bg"], fg=C["muted"]).pack(side="left", pady=(8, 0))
        self.tools_label = tk.Label(self, text="", font=self.f_small, bg=C["bg"], fg=C["muted"], anchor="w")
        self.tools_label.pack(fill="x", padx=22, pady=(0, 8))

    # ---- input view
    def build_input_view(self):
        self.input_view = tk.Frame(self.container, bg=C["bg"])
        outer, box = self.card(self.input_view)
        outer.pack(fill="x")
        tk.Label(box, text="1. What do you want?", font=self.f_h2, bg=C["card"], fg=C["ink"]).pack(anchor="w")
        self.request = tk.Text(box, height=5, wrap="word", font=self.f_body, relief="solid", borderwidth=1,
                               highlightthickness=0, padx=8, pady=6, undo=True)
        self.request.pack(fill="x", pady=(6, 0))
        self.request.bind("<FocusIn>", self.clear_placeholder)
        self.request.bind("<FocusOut>", self.restore_placeholder)
        self.placeholder_on = False
        self.restore_placeholder()

        outer2, box2 = self.card(self.input_view)
        outer2.pack(fill="x", pady=(12, 0))
        top = tk.Frame(box2, bg=C["card"])
        top.pack(fill="x")
        tk.Label(top, text="2. Files to use (optional)", font=self.f_h2, bg=C["card"], fg=C["ink"]).pack(side="left")
        self.files_hint = tk.Label(top, text="  PDF, Word (.docx), text, HTML, CSV", font=self.f_small, bg=C["card"], fg=C["muted"])
        self.files_hint.pack(side="left", pady=(3, 0))
        row = tk.Frame(box2, bg=C["card"])
        row.pack(fill="x", pady=(6, 0))
        self.file_list = tk.Listbox(row, height=2, font=self.f_body, relief="solid", borderwidth=1, highlightthickness=0,
                                    activestyle="none", selectmode="extended")
        self.file_list.pack(side="left", fill="x", expand=True)
        self.file_scroll = ttk.Scrollbar(row, orient="vertical", command=self.file_list.yview)
        self.file_list.configure(yscrollcommand=self.file_scroll.set)
        btns = tk.Frame(row, bg=C["card"])
        btns.pack(side="left", padx=(8, 0))
        self.file_btns = btns
        ttk.Button(btns, text="Add files...", command=self.add_files).pack(fill="x")
        ttk.Button(btns, text="Remove", command=self.remove_files).pack(fill="x", pady=(6, 0))
        self.after(300, self.setup_drop)

        outer3, box3 = self.card(self.input_view)
        outer3.pack(fill="x", pady=(12, 0))
        tk.Label(box3, text="3. What should I make?", font=self.f_h2, bg=C["card"], fg=C["ink"]).grid(row=0, column=0, columnspan=3, sticky="w")
        self.format_var = tk.StringVar(value=FORMATS[DEFAULT_FORMAT])
        ttk.Combobox(box3, textvariable=self.format_var, values=list(FORMATS.values()), state="readonly", width=34).grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        tk.Label(box3, text="File name (optional)", font=self.f_small, bg=C["card"], fg=C["muted"]).grid(row=0, column=1, sticky="w", padx=(18, 0))
        self.title_var = tk.StringVar()
        ttk.Entry(box3, textvariable=self.title_var, width=34).grid(row=1, column=1, sticky="w", padx=(18, 0), pady=(6, 0))
        import ailab
        self.presets = ailab.get_presets()
        self.preset_var = tk.StringVar(value=ailab.default_preset_name())
        self.order = [list(x) for x in self.presets[self.preset_var.get()]]
        order_row = tk.Frame(box3, bg=C["card"])
        order_row.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        tk.Label(order_row, text="Order of steps:", font=self.f_body, bg=C["card"], fg=C["ink"]).pack(side="left")
        self.preset_box = ttk.Combobox(order_row, textvariable=self.preset_var, values=list(self.presets), state="readonly", width=28)
        self.preset_box.pack(side="left", padx=(8, 0))
        self.preset_box.bind("<<ComboboxSelected>>", self.preset_chosen)
        ttk.Button(order_row, text="Edit steps...", command=self.edit_steps).pack(side="left", padx=(10, 0))
        self.order_text = tk.Label(box3, text="", font=self.f_small, bg=C["card"], fg=C["muted"], wraplength=760, justify="left")
        self.order_text.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.refresh_order_text()

        mail_row = tk.Frame(box3, bg=C["card"])
        mail_row.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.email_var = tk.BooleanVar(value=self.email_ready())
        ttk.Checkbutton(mail_row, text="Email the finished files to my Gmail when done",
                        variable=self.email_var, command=self.email_toggled).pack(side="left")
        ttk.Button(mail_row, text="Set up email", command=self.setup_email).pack(side="left", padx=(12, 0))

        priv_row = tk.Frame(box3, bg=C["card"])
        priv_row.grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.priv_top = tk.Frame(priv_row, bg=C["card"])
        self.priv_top.pack(anchor="w")
        ttk.Checkbutton(self.priv_top, text="Check my request before sending", variable=self.precheck_var,
                        command=self.precheck_toggled).pack(side="left")
        self.precheck_note = tk.Label(priv_row, text="Looks for personal information such as emails, phone numbers and ID numbers.",
                                      font=self.f_small, bg=C["card"], fg=C["muted"], anchor="w")
        self.precheck_note.pack(anchor="w", padx=(24, 0))

        bar = tk.Frame(self.input_view, bg=C["bg"])
        bar.pack(fill="x", pady=(16, 0))
        self.start_button = ttk.Button(bar, text="Start", style="Accent.TButton", command=lambda: self.start(mock=False))
        self.start_button.pack(side="left")
        ttk.Button(bar, text="Practice run (uses nothing)", command=lambda: self.start(mock=True)).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Check sign-ins", command=self.check_signins).pack(side="left", padx=(12, 0))
        self.gemini_btn = ttk.Button(bar, text="Gemini model", command=self.gemini_model)
        self.gemini_btn.pack(side="left", padx=(12, 0))
        self.bar = bar
        self.local_btn = None
        ttk.Button(bar, text="Past runs", command=self.past_runs).pack(side="right")
        ttk.Button(bar, text="Usage", command=self.show_usage).pack(side="right", padx=(0, 10))

        self.note = tk.Label(self.input_view, text="Runs take a few minutes. You can leave. The finished files are saved for you.",
                             font=self.f_small, bg=C["bg"], fg=C["muted"])
        self.note.pack(anchor="w", pady=(10, 0))

    # ---- local AI (optional, only shown when one is found on this computer)
    def detect_local(self):
        if localai is None:
            return
        def work():
            try:
                found = localai.find_local()
            except Exception:
                found = []
            if found:
                self.after(0, self.local_detected)
        threading.Thread(target=work, daemon=True).start()

    def local_detected(self):
        if self.local_found:
            return
        self.local_found = True
        self.local_btn = ttk.Button(self.priv_top, text="Local AI settings...", command=self.local_settings)
        self.local_btn.pack(side="left", padx=(12, 0))
        self.precheck_note.configure(text="Looks for personal information and has your local AI improve the request. Stays on this computer.")

    def lcfg(self):
        import ailab
        return ailab.load_config()

    def save_lcfg(self, **kw):
        import ailab
        cfg = ailab.load_config()
        for k, v in kw.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v
        ailab.save_config(cfg)

    def precheck_toggled(self):
        self.save_lcfg(precheck=bool(self.precheck_var.get()))

    def pick_models(self, choices):
        """Returns (main, second). Each is (kind, label, model) or None. Uses saved choices, otherwise picks a sensible default."""
        cfg = self.lcfg()
        def find(saved):
            if saved and saved.get("kind") and saved.get("model"):
                for c in choices:
                    if c[0] == saved["kind"] and c[2] == saved["model"]:
                        return c
            return None
        main = find(cfg.get("local_ai"))
        if main is None and choices:
            main = localai.pick_default(choices)
            if main is not None:
                self.save_lcfg(local_ai={"kind": main[0], "model": main[2]})
        second = find(cfg.get("local_ai2"))
        if second is not None and main is not None and (second[0], second[2]) == (main[0], main[2]):
            second = None
        return main, second

    def with_local(self, win, say, ready):
        """Finds the local AI (no questions asked) and calls ready(kind, model)."""
        say("Looking for your local AI...")
        def work():
            try:
                choices = localai.all_choices()
            except Exception:
                choices = []
            main, _second = self.pick_models(choices) if choices else (None, None)
            def done():
                try:
                    if not win.winfo_exists():
                        return
                except tk.TclError:
                    return
                if main is None:
                    found = localai.find_local()
                    say(("Found " + " and ".join(p["label"] for p in found) + " but no downloaded chat models. Download a model in that program first.")
                        if found else "No local AI found.", C["warn"])
                    return
                ready(main[0], main[2])
            self.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def local_window(self, title, intro, height=600):
        """A simple window with a status line, a text area and a button bar. Returns (win, say, show, bar, alive)."""
        win = tk.Toplevel(self)
        win.title(title)
        win.configure(bg=C["card"], padx=20, pady=16)
        win.transient(self)
        win.geometry(f"760x{height}")
        tk.Label(win, text=title, font=self.f_h2, bg=C["card"], fg=C["ink"]).pack(anchor="w")
        tk.Label(win, text=intro, font=self.f_small, bg=C["card"], fg=C["muted"], wraplength=700, justify="left").pack(anchor="w", pady=(2, 8))
        status = tk.Label(win, text="", font=self.f_body, bg=C["card"], fg=C["muted"], anchor="w", wraplength=700, justify="left")
        status.pack(fill="x")
        box = tk.Text(win, wrap="word", font=self.f_body, relief="solid", borderwidth=1, highlightthickness=0, padx=8, pady=6, height=18)
        box.pack(fill="both", expand=True, pady=(8, 8))
        box.configure(state="disabled")
        bar = tk.Frame(win, bg=C["card"])
        bar.pack(fill="x")

        def alive():
            try:
                return win.winfo_exists()
            except tk.TclError:
                return False

        def say(text, color=None):
            if alive():
                status.config(text=text, fg=color or C["muted"])

        def show(text):
            if alive():
                box.configure(state="normal")
                box.delete("1.0", "end")
                box.insert("1.0", text)
                box.configure(state="disabled")
        return win, say, show, bar, alive

    LOCAL_PRIVATE = "This runs on this computer. Nothing is sent anywhere and nothing is used from your ChatGPT, Claude or Gemini plans."

    # ---- local AI settings (one small window, only needed to change the defaults)
    def local_settings(self):
        win = tk.Toplevel(self)
        win.title("Local AI settings")
        win.configure(bg=C["card"], padx=20, pady=16)
        win.transient(self)
        tk.Label(win, text="Local AI settings", font=self.f_h2, bg=C["card"], fg=C["ink"]).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(win, text=("AI Lab already picked a local AI for you, so you only need this to change it. "
                            "Names with 3b to 9b are a good balance of speed and quality. Bigger ones are slower."),
                 font=self.f_small, bg=C["card"], fg=C["muted"], wraplength=560, justify="left").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))
        status = tk.Label(win, text="Looking for local AI models...", font=self.f_small, bg=C["card"], fg=C["muted"], anchor="w")
        status.grid(row=2, column=0, columnspan=2, sticky="w")
        tk.Label(win, text="Main local AI", font=self.f_body, bg=C["card"], fg=C["ink"]).grid(row=3, column=0, sticky="w", pady=(8, 0))
        main_var, second_var = tk.StringVar(), tk.StringVar(value="None")
        main_box = ttk.Combobox(win, textvariable=main_var, state="readonly", width=44)
        main_box.grid(row=3, column=1, sticky="w", padx=(10, 0), pady=(8, 0))
        tk.Label(win, text="Second AI to compare", font=self.f_body, bg=C["card"], fg=C["ink"]).grid(row=4, column=0, sticky="w", pady=(8, 0))
        second_box = ttk.Combobox(win, textvariable=second_var, state="readonly", width=44)
        second_box.grid(row=4, column=1, sticky="w", padx=(10, 0), pady=(8, 0))
        tk.Label(win, text=("With a second AI, both review your request. You see both versions side by side, how closely they agree, "
                            "and a check for numbers that were added or dropped. It takes about twice as long."),
                 font=self.f_small, bg=C["card"], fg=C["muted"], wraplength=560, justify="left").grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 10))
        cfg = self.lcfg()
        scan_var = tk.BooleanVar(value=bool(cfg.get("local_files_scan")))
        cond_var = tk.BooleanVar(value=bool(cfg.get("local_condense")))
        ttk.Checkbutton(win, text="Also have the local AI read my attached files for names and other personal details (slower)",
                        variable=scan_var).grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(win, text="Condense very long files first so less is sent online (may lose detail, originals are kept)",
                        variable=cond_var).grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))
        choices_holder = {"list": []}

        def label_of(c):
            return f"{c[1]}:  {c[2]}"

        def fill(choices):
            try:
                if not win.winfo_exists():
                    return
            except tk.TclError:
                return
            choices_holder["list"] = choices
            if not choices:
                found = localai.find_local()
                status.configure(text=("Found " + " and ".join(p["label"] for p in found) + " but no downloaded chat models. Download one in that program first.")
                                 if found else "No local AI found.", fg=C["warn"])
                return
            main, second = self.pick_models(choices)
            labels = [label_of(c) for c in choices]
            main_box.configure(values=labels)
            second_box.configure(values=["None"] + labels)
            main_var.set(label_of(main) if main else labels[0])
            second_var.set(label_of(second) if second else "None")
            status.configure(text=f"Found {len(choices)} model(s) on this computer.", fg=C["ok"])

        def work():
            try:
                choices = localai.all_choices()
            except Exception:
                choices = []
            self.after(0, lambda: fill(choices))
        threading.Thread(target=work, daemon=True).start()

        def save():
            chosen = {label_of(c): c for c in choices_holder["list"]}
            m = chosen.get(main_var.get())
            s2 = chosen.get(second_var.get())
            kw = {"local_files_scan": bool(scan_var.get()), "local_condense": bool(cond_var.get())}
            if m:
                kw["local_ai"] = {"kind": m[0], "model": m[2]}
            kw["local_ai2"] = {"kind": s2[0], "model": s2[2]} if (s2 and m and (s2[0], s2[2]) != (m[0], m[2])) else None
            self.save_lcfg(**kw)
            win.destroy()
        bar = tk.Frame(win, bg=C["card"])
        bar.grid(row=8, column=0, columnspan=2, sticky="we", pady=(14, 0))
        ttk.Button(bar, text="Save", style="Accent.TButton", command=save).pack(side="left")
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="left", padx=(10, 0))

    # ---- the check before sending (one window: privacy, improved request, questions)
    def check_request(self, request, proceed):
        """Calls proceed(request_text, files) when it is time to start. Does nothing if the person cancels."""
        files = list(self.attachments)
        names = [Path(f).name for f in files]
        use_local = self.local_found and localai is not None
        state = {"findings": [], "versions": {}, "qrows": [], "orig": request, "ready": False}

        if not use_local:
            # No local AI: a quick silent pattern check. A window only appears if something is found.
            def quiet():
                findings = self.pattern_findings(request, files)
                def done():
                    if findings:
                        text = "\n".join(findings[:12]) + ("\n..." if len(findings) > 12 else "")
                        if not messagebox.askyesno("Possible personal information",
                                                   "This may contain personal information that would be sent to online AI services:\n\n" + text +
                                                   "\n\nSend anyway?\n(Choose No to go back and remove it.)", default="no"):
                            self.start_button.state(["!disabled"])
                            return
                    proceed(request, files)
                self.after(0, done)
            self.start_button.state(["disabled"])
            threading.Thread(target=quiet, daemon=True).start()
            return

        self.start_button.state(["disabled"])
        win = tk.Toplevel(self)
        win.title("Check before sending")
        win.configure(bg=C["card"], padx=20, pady=16)
        win.transient(self)
        win.geometry("820x780")
        tk.Label(win, text="Check before sending", font=self.f_h2, bg=C["card"], fg=C["ink"]).pack(anchor="w")
        tk.Label(win, text="This check runs on this computer. Nothing has been sent online yet.", font=self.f_small, bg=C["card"], fg=C["muted"]).pack(anchor="w")
        status = tk.Label(win, text="Starting...", font=self.f_body, bg=C["card"], fg=C["muted"], anchor="w", wraplength=760, justify="left")
        status.pack(fill="x", pady=(8, 0))
        priv = tk.Label(win, text="", font=self.f_body, bg=C["card"], fg=C["ok"], anchor="w", wraplength=760, justify="left")
        priv.pack(fill="x", pady=(4, 0))
        tk.Label(win, text="Your request. You can edit it here.", font=self.f_bold, bg=C["card"], fg=C["ink"]).pack(anchor="w", pady=(10, 0))
        vers = tk.Frame(win, bg=C["card"])
        vers.pack(fill="x")
        box = tk.Text(win, wrap="word", font=self.f_body, relief="solid", borderwidth=1, highlightthickness=0, padx=8, pady=6, height=8, undo=True)
        box.pack(fill="x", pady=(4, 0))
        box.insert("1.0", request)
        accuracy = tk.Label(win, text="", font=self.f_small, bg=C["card"], fg=C["muted"], anchor="w", wraplength=760, justify="left")
        accuracy.pack(fill="x", pady=(4, 0))
        agree = tk.Label(win, text="", font=self.f_small, bg=C["card"], fg=C["muted"], anchor="w", wraplength=760, justify="left")
        agree.pack(fill="x")
        qhead = tk.Label(win, text="", font=self.f_bold, bg=C["card"], fg=C["ink"])
        qhead.pack(anchor="w", pady=(10, 0))
        qframe = tk.Frame(win, bg=C["card"])
        qframe.pack(fill="x")
        bar2 = tk.Frame(win, bg=C["card"])
        bar2.pack(side="bottom", fill="x", pady=(8, 0))
        bar = tk.Frame(win, bg=C["card"])
        bar.pack(side="bottom", fill="x", pady=(12, 0))
        start_btn = ttk.Button(bar, text="Start with this request", style="Accent.TButton", state="disabled")
        start_btn.pack(side="left")
        keep_btn = ttk.Button(bar, text="Keep editing in the main window")
        keep_btn.pack(side="left", padx=(10, 0))
        orig_btn = ttk.Button(bar, text="Use my original wording")
        orig_btn.pack(side="left", padx=(10, 0))
        skip_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar2, text="Skip this check next time (you can turn it back on in the main window)", variable=skip_var).pack(side="left")

        def alive():
            try:
                return win.winfo_exists()
            except tk.TclError:
                return False

        def post(fn):
            self.after(0, lambda: fn() if alive() else None)

        def say(text, color=None):
            status.configure(text=text, fg=color or C["muted"])

        def show_privacy():
            f = state["findings"]
            if f:
                priv.configure(text="Possible personal information. Check before sending:\n" + "\n".join(f[:10]) + ("\n..." if len(f) > 10 else ""), fg=C["warn"])
                start_btn.configure(text="Start anyway")
            else:
                priv.configure(text="Privacy: nothing found.", fg=C["ok"])
                start_btn.configure(text="Start with this request")

        def final_text():
            text = box.get("1.0", "end").strip()
            extra = []
            for q, e in state["qrows"]:
                a = e.get().strip()
                if a:
                    extra.append(f"- {q} {a}")
            if extra:
                text += "\n\nAnswers to open questions:\n" + "\n".join(extra)
            return text

        def finish(action):
            if skip_var.get():
                self.precheck_var.set(False)
                self.precheck_toggled()
            text = final_text() if action != "orig" else request
            self.start_button.state(["!disabled"])
            win.destroy()
            if action == "keep":
                self.request.delete("1.0", "end")
                self.request.insert("1.0", text)
                self.placeholder_on = False
                self.request.configure(fg=C["ink"])
                return
            proceed(text, files)

        start_btn.configure(command=lambda: finish("start"))
        keep_btn.configure(command=lambda: finish("keep"))
        orig_btn.configure(command=lambda: finish("orig"))
        def on_close():
            self.start_button.state(["!disabled"])
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", on_close)

        def pick_version(tag):
            if tag == "orig":
                text = state["orig"]
            else:
                text = state["versions"][tag]["text"]
            box.delete("1.0", "end")
            box.insert("1.0", text)
            added, missing = localai.check_changes(state["orig"], text)
            if tag == "orig":
                accuracy.configure(text="", fg=C["muted"])
            elif not added and not missing:
                accuracy.configure(text="Accuracy check: the numbers and codes match your original.", fg=C["ok"])
            else:
                parts = []
                if added:
                    parts.append("added, not in your original: " + ", ".join(added[:8]))
                if missing:
                    parts.append("dropped from your original: " + ", ".join(missing[:8]))
                accuracy.configure(text="Accuracy check: " + "; ".join(parts) + ". Make sure this is what you meant.", fg=C["warn"])

        def show_review(main, second, results):
            for tag, c, res in results:
                if res and res["improved"]:
                    state["versions"][tag] = {"label": c[2], "text": res["improved"], "qs": localai.parse_questions(res["questions"]), "privacy": res["privacy"]}
            if not state["versions"]:
                say("The local AI did not give a usable review, so your original request is shown. You can still start.", C["warn"])
                start_btn.configure(state="normal")
                return
            first = "a" if "a" in state["versions"] else next(iter(state["versions"]))
            choice = tk.StringVar(value=first)
            if len(state["versions"]) > 1:
                tk.Label(vers, text="Version from:", font=self.f_small, bg=C["card"], fg=C["muted"]).pack(side="left")
                for tag, v in state["versions"].items():
                    ttk.Radiobutton(vers, text=v["label"], value=tag, variable=choice, command=lambda: pick_version(choice.get())).pack(side="left", padx=(8, 0))
                ttk.Radiobutton(vers, text="My original", value="orig", variable=choice, command=lambda: pick_version(choice.get())).pack(side="left", padx=(8, 0))
                a, b = (state["versions"][t]["text"] for t in list(state["versions"])[:2])
                pct = localai.agreement(a, b)
                agree.configure(text=(f"The two AIs agree {pct}% on the wording." if pct >= 70 else
                                      f"The two AIs differ noticeably ({pct}% agreement). Read both versions before you choose."),
                                fg=C["ok"] if pct >= 70 else C["warn"])
            pick_version(first)
            seen, qs = set(), []
            for v in state["versions"].values():
                for q in v["qs"]:
                    if q.lower() not in seen:
                        seen.add(q.lower()); qs.append(q)
                note = (v["privacy"] or "").strip()
                if note and note.lower().strip(". ") not in ("clear", "none", "nothing"):
                    state["findings"].append(f"local AI ({v['label']}): " + note.replace("\n", " ")[:200])
            show_privacy()
            if qs:
                qhead.configure(text="A few questions (optional, answer what you can)")
                for q in qs[:6]:
                    row = tk.Frame(qframe, bg=C["card"])
                    row.pack(fill="x", pady=2)
                    tk.Label(row, text=q, font=self.f_body, bg=C["card"], fg=C["ink"], anchor="w", wraplength=760, justify="left").pack(fill="x")
                    e = ttk.Entry(row)
                    e.pack(fill="x", pady=(1, 0))
                    state["qrows"].append((q, e))
                state["qrows"][0][1].focus_set()
            else:
                qhead.configure(text="No questions. Your request looks clear.")
            names_used = " and ".join(c[2] for _t, c, _r in results)
            say(f"Reviewed by {names_used}. Edit anything above, then press Start.", C["ok"])
            start_btn.configure(state="normal")

        def work():
            findings = self.pattern_findings(request, files)
            state["findings"] = list(findings)
            post(show_privacy)
            post(lambda: say("Looking for your local AI..."))
            try:
                choices = localai.all_choices()
            except Exception:
                choices = []
            main, second = self.pick_models(choices) if choices else (None, None)
            if main is None:
                post(lambda: say("No local AI model is available, so only the privacy pattern check ran. You can still start.", C["warn"]))
                post(lambda: start_btn.configure(state="normal"))
                return
            results = []
            for tag, c in (("a", main), ("b", second)):
                if c is None:
                    continue
                post(lambda c=c: say(f"Reading your request with {c[2]}. The first answer can take a minute or two while the model loads..."))
                try:
                    res = localai.review_request(c[0], c[2], request, names)
                except Exception as err:
                    res = None
                    post(lambda err=err, c=c: say(f"{c[2]} did not answer: {str(err)[:200]}", C["bad"]))
                results.append((tag, c, res))
            if self.lcfg().get("local_files_scan") and files:
                for f in files:
                    try:
                        text, _n = convert.extract_text(Path(f))
                        for line in localai.ai_find_personal(main[0], main[2], text,
                                                              progress=lambda t: post(lambda t=t: say(t))):
                            state["findings"].append(f"{Path(f).name}: {line} (local AI, may be a false alarm)")
                    except Exception:
                        pass
            post(lambda: show_review(main, second, results))
        threading.Thread(target=work, daemon=True).start()

    def pattern_findings(self, request, files):
        """Fast check with no AI. Returns a list of plain sentences. Values are partly hidden."""
        import ailab
        ignore = [ailab.load_config().get("email_address", "")]
        pieces = [("your request", request)]
        for f in files:
            try:
                text, _note = convert.extract_text(Path(f))
            except Exception:
                text = ""
            pieces.append((Path(f).name, text))
        out = []
        for name, text in pieces:
            for label, (count, ex) in localai.scan_personal(text, ignore).items():
                out.append(f"{name}: {count} possible {label}  ({', '.join(ex)})")
        return out

    # ---- condense long files (only if turned on in Local AI settings)
    def condense_stage(self, request, files, go):
        """Calls go(request, attachments, originals)."""
        if not (self.local_found and localai is not None and files and self.lcfg().get("local_condense")):
            go(request, files, [])
            return
        win, say, show, bar, alive = self.local_window("Condensing long files", "Runs on this computer. Nothing is sent online yet.", height=360)
        cancel = {"on": False}
        def close():
            cancel["on"] = True
            if alive():
                win.destroy()
        win.protocol("WM_DELETE_WINDOW", close)
        ttk.Button(bar, text="Cancel", command=close).pack(side="right")
        ttk.Button(bar, text="Skip condensing and start", command=lambda: (close(), go(request, files, []))).pack(side="left")

        def ready(kind, model):
            def work():
                final, originals = list(files), []
                try:
                    for i, f in enumerate(files):
                        text, _note = convert.extract_text(Path(f))
                        if len(text) < CONDENSE_OVER:
                            continue
                        self.after(0, lambda f=f: say(f"Condensing {Path(f).name}. This can take several minutes..."))
                        short = localai.condense_text(kind, model, text, progress=lambda t: self.after(0, lambda t=t: say(t)),
                                                      cancelled=lambda: cancel["on"])
                        if not short.strip():
                            continue
                        out_dir = HERE / "_condensed"
                        out_dir.mkdir(exist_ok=True)
                        out = out_dir / (Path(f).stem + " (condensed).md")
                        out.write_text("# Condensed from " + Path(f).name + "\n\nA local AI shortened this long file. Some detail may be missing.\n\n" + short + "\n", encoding="utf-8")
                        final[i] = str(out)
                        originals.append(f)
                except Exception as err:
                    if cancel["on"]:
                        return
                    self.after(0, lambda: say("Condensing did not finish: " + str(err)[:200] + " Starting with the original files.", C["warn"]))
                    final, originals = list(files), []
                if cancel["on"]:
                    return
                def done():
                    if alive():
                        win.destroy()
                    go(request, final, originals)
                self.after(0, done)
            threading.Thread(target=work, daemon=True).start()
        self.with_local(win, say, ready)

    # ---- plain language accessibility report
    def explain_a11y(self):
        report = self.folder / "accessibility-report.md"
        if not report.is_file():
            messagebox.showinfo("No report", "There is no accessibility report for this run.")
            return
        win, say, show, bar, alive = self.local_window("Accessibility to-do list (local AI)", self.LOCAL_PRIVATE, height=560)
        open_btn = ttk.Button(bar, text="Open the saved to-do list", state="disabled")
        open_btn.pack(side="left")
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")
        target = self.folder / "accessibility-todo.md"
        open_btn.configure(command=lambda: open_path(target))

        def ready(kind, model):
            say(f"Reading the report with {model}...")
            def work():
                try:
                    text = localai.explain_accessibility(kind, model, report.read_text(encoding="utf-8", errors="replace"))
                except Exception as err:
                    self.after(0, lambda: say("The local AI did not answer: " + str(err)[:300], C["bad"]))
                    return
                def done():
                    if not alive():
                        return
                    if not text:
                        say("The local AI returned nothing.", C["warn"])
                        return
                    try:
                        target.write_text("# Accessibility to-do list (written by a local AI from the scan report)\n\n" + text + "\n", encoding="utf-8")
                        open_btn.configure(state="normal")
                    except OSError:
                        pass
                    show(text)
                    say("Done. A local AI wrote this from the scan, so check it against accessibility-report.md.", C["ok"])
                self.after(0, done)
            threading.Thread(target=work, daemon=True).start()
        self.with_local(win, say, ready)

    # ---- Gemini model
    def gemini_model(self):
        import ailab
        win = tk.Toplevel(self)
        win.title("Gemini model")
        win.configure(bg=C["card"], padx=20, pady=16)
        win.transient(self)
        tk.Label(win, text="Which Gemini model should AI Lab use?", font=self.f_h2, bg=C["card"], fg=C["ink"]).pack(anchor="w")
        tk.Label(win, text=("Click a model in the list, then press Save and test. The list comes from your Gemini tool. "
                            "Leave the name empty to use the tool's own default."),
                 font=self.f_small, bg=C["card"], fg=C["muted"], wraplength=600, justify="left").pack(anchor="w", pady=(2, 8))
        frame = tk.Frame(win, bg=C["card"])
        frame.pack(anchor="w")
        lb = tk.Listbox(frame, width=76, height=12, font=("Consolas", 10), activestyle="none", exportselection=False)
        sb = ttk.Scrollbar(frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left")
        sb.pack(side="left", fill="y")
        show_all = tk.BooleanVar(value=False)
        lines = []

        def refill():
            lb.delete(0, "end")
            for ln in lines:
                low = ln.lower()
                if show_all.get() or "gemini" in low or "argon" in low:
                    lb.insert("end", ln)
            if lb.size() == 0 and lines:
                lb.insert("end", "(No Gemini models found in the list. Tick Show all models.)")
        ttk.Checkbutton(win, text="Show all models (includes Claude and others)", variable=show_all, command=refill).pack(anchor="w", pady=(6, 0))
        row = tk.Frame(win, bg=C["card"])
        row.pack(anchor="w", pady=(8, 0))
        tk.Label(row, text="Model name:", font=self.f_body, bg=C["card"], fg=C["ink"]).pack(side="left")
        current = tk.StringVar(value=str(ailab.load_config().get("gemini_model", "")))
        ttk.Entry(row, textvariable=current, width=40).pack(side="left", padx=(6, 0))
        note = tk.Label(win, text="", font=self.f_small, bg=C["card"], fg=C["muted"], wraplength=600, justify="left")
        note.pack(anchor="w", pady=(8, 0))

        def picked(_e=None):
            sel = lb.curselection()
            if not sel:
                return
            line = lb.get(sel[0]).strip()
            ids = re.findall(r"[A-Za-z0-9][A-Za-z0-9._]*(?:-[A-Za-z0-9._]+)+", line)
            current.set(ids[0] if ids else line.split()[0] if line.split() else "")
            note.config(text="Selected: " + line + "\nIf the name box is not right, edit it by hand before saving.", fg=C["muted"])
        lb.bind("<<ListboxSelect>>", picked)

        def fetch():
            exe = ailab.find_agy()
            if not exe:
                out = "Gemini (agy) is not installed on this computer."
            else:
                try:
                    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                    scratch = ailab.HERE / "_scratch"
                    r = subprocess.run([exe, "models"], capture_output=True, text=True, timeout=60,
                                       cwd=str(scratch) if scratch.is_dir() else None, creationflags=flags)
                    out = (r.stdout or r.stderr or "(no output)").strip()
                except Exception as err:
                    out = f"Could not list models: {err}"

            def done():
                lines[:] = [ln for ln in out.splitlines() if ln.strip()]
                refill()
            self.after(0, done)
        lb.insert("end", "Asking the Gemini tool for its models...")
        threading.Thread(target=fetch, daemon=True).start()

        def save():
            cfg = ailab.load_config()
            value = current.get().strip()
            if value:
                cfg["gemini_model"] = value
            else:
                cfg.pop("gemini_model", None)
            ailab.save_config(cfg)
            note.config(text=("Saved. Gemini steps will ask for: " + value) if value else "Saved. Gemini will use the tool's default.", fg=C["ok"])

        def test():
            save()
            note.config(text="Testing, this can take a minute...", fg=C["muted"])

            def work():
                ok, text = ailab.ping("gemini")
                self.after(0, lambda: note.config(text=("Gemini answered with this model: " if ok else "Gemini problem: ") + text, fg=C["ok"] if ok else C["bad"]))
            threading.Thread(target=work, daemon=True).start()
        b = tk.Frame(win, bg=C["card"])
        b.pack(anchor="w", pady=(10, 0))
        ttk.Button(b, text="Save", command=save).pack(side="left")
        ttk.Button(b, text="Save and test", command=test).pack(side="left", padx=(8, 0))
        ttk.Button(b, text="Close", command=win.destroy).pack(side="left", padx=(8, 0))

    # ---- usage
    def show_usage(self):
        import ailab
        win = tk.Toplevel(self)
        win.title("Usage")
        win.configure(bg=C["card"], padx=20, pady=16)
        win.transient(self)
        tk.Label(win, text="Used by AI Lab on this computer (estimated)", font=self.f_h2, bg=C["card"], fg=C["ink"]).grid(row=0, column=0, columnspan=4, sticky="w")
        tk.Label(win, text="Estimates in tokens, from the text sent and received. Real plan use is higher. Practice runs are not counted.",
                 font=self.f_small, bg=C["card"], fg=C["muted"], wraplength=560, justify="left").grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 8))
        data = ailab.usage_windows(RUNS)
        heads = ["", "Last 5 hours", "Last 24 hours", "Last 7 days"]
        for j, h in enumerate(heads):
            tk.Label(win, text=h, font=(self.family, 10, "bold"), bg=C["card"], fg=C["ink"]).grid(row=2, column=j, sticky="w", padx=(0, 18))
        for i, agent in enumerate(("claude", "chatgpt", "gemini"), start=3):
            tk.Label(win, text=PRETTY[agent], font=self.f_body, bg=C["card"], fg=AGENT_COLOR[agent]).grid(row=i, column=0, sticky="w", padx=(0, 18), pady=2)
            for j, span in enumerate(("5 hours", "24 hours", "7 days"), start=1):
                cell = data.get(span, {}).get(agent, {"tokens": 0, "steps": 0})
                tk.Label(win, text=f"{cell['tokens']:,}  ({cell['steps']} steps)", font=self.f_body, bg=C["card"], fg=C["ink"]).grid(row=i, column=j, sticky="w", padx=(0, 18), pady=2)
        tk.Label(win, text="Your real allowance left", font=self.f_h2, bg=C["card"], fg=C["ink"]).grid(row=7, column=0, columnspan=4, sticky="w", pady=(18, 0))
        tk.Label(win, text=("Each tool shows its own remaining usage inside its own window. Press a button, then type the command shown. "
                            "Close that window when you are done. Gemini has no official way to show this."),
                 font=self.f_small, bg=C["card"], fg=C["muted"], wraplength=560, justify="left").grid(row=8, column=0, columnspan=4, sticky="w", pady=(2, 8))
        row = tk.Frame(win, bg=C["card"])
        row.grid(row=9, column=0, columnspan=4, sticky="w")

        def open_tool(cmd, hint):
            try:
                if sys.platform == "win32":
                    subprocess.Popen(["cmd.exe", "/k", cmd], creationflags=subprocess.CREATE_NEW_CONSOLE)
                else:
                    raise OSError("Windows only")
                messagebox.showinfo("Check usage", hint, parent=win)
            except Exception as err:
                messagebox.showerror("Could not open it", f"{err}\nOpen PowerShell yourself, type {cmd}, then {hint.split(chr(39))[1]}.", parent=win)
        ttk.Button(row, text="Claude: open usage", command=lambda: open_tool("claude.cmd", "A window opened. When Claude is ready, type '/usage' and press Enter.")).pack(side="left")
        ttk.Button(row, text="ChatGPT: open usage", command=lambda: open_tool("codex.cmd", "A window opened. When ChatGPT is ready, type '/status' and press Enter.")).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Close", command=win.destroy).pack(side="left", padx=(18, 0))

    # ---- order of steps
    def steps_text(self, order):
        import ailab
        parts = []
        for st in order:
            job = ailab.JOB_LABELS.get(st[1], st[1])
            parts.append(f"{ailab.PRETTY_NAME.get(st[0], st[0])}: {job}")
        return "  >  ".join(parts) + "    (an automatic accessibility scan runs after each build and each fix)"

    def refresh_order_text(self):
        self.order_text.config(text=self.steps_text(self.order))

    def preset_chosen(self, _e=None):
        name = self.preset_var.get()
        if name in self.presets:
            self.order = [list(x) for x in self.presets[name]]
            self.refresh_order_text()

    def reload_presets(self, select=None):
        import ailab
        self.presets = ailab.get_presets()
        names = list(self.presets)
        self.preset_box.configure(values=names + ["Custom (edited)"])
        if select:
            self.preset_var.set(select)

    def edit_steps(self):
        import ailab
        win = tk.Toplevel(self)
        win.title("Edit steps")
        win.configure(bg=C["card"], padx=16, pady=14)
        win.transient(self)
        win.grab_set()
        draft = [list(x) + [""] * (3 - len(x)) for x in self.order]
        names = {"ChatGPT": "chatgpt", "Claude": "claude", "Gemini": "gemini"}
        agent_names = list(names)
        job_labels = list(ailab.JOB_LABELS.values())
        job_keys = list(ailab.JOB_LABELS)
        tk.Label(win, text="Each step runs in order. Put the checker after the work it checks.", font=self.f_small,
                 bg=C["card"], fg=C["muted"]).pack(anchor="w")
        body = tk.Frame(win, bg=C["card"])
        body.pack(fill="x", pady=(8, 0))
        msg = tk.Label(win, text="", font=self.f_small, bg=C["card"], fg=C["bad"], wraplength=700, justify="left")
        msg.pack(anchor="w", pady=(8, 0))

        def check():
            problems = ailab.validate_steps(draft)
            if len(draft) > 7 and not problems:
                msg.config(text=f"{len(draft)} steps will use a lot of your allowances and take longer.", fg=C["warn"])
            else:
                msg.config(text=" ".join(problems), fg=C["bad"])
            return problems

        def redraw():
            for w in body.winfo_children():
                w.destroy()
            for i, st in enumerate(draft):
                tk.Label(body, text=f"{i + 1}.", font=self.f_body, bg=C["card"], fg=C["ink"], width=3).grid(row=i, column=0, sticky="w", pady=2)
                av = tk.StringVar(value=ailab.PRETTY_NAME[st[0]])
                ac = ttk.Combobox(body, textvariable=av, values=agent_names, state="readonly", width=9)
                ac.grid(row=i, column=1, pady=2)
                jv = tk.StringVar(value=ailab.JOB_LABELS[st[1]])
                jc = ttk.Combobox(body, textvariable=jv, values=job_labels, state="readonly", width=34)
                jc.grid(row=i, column=2, padx=(6, 0), pady=2)
                nv = tk.StringVar(value=st[2])
                ne = ttk.Entry(body, textvariable=nv, width=40)
                ne.grid(row=i, column=3, padx=(6, 0), pady=2)
                ne.configure(state="normal" if st[1] == "custom" else "disabled")

                def on_agent(_e=None, i=i, av=av):
                    draft[i][0] = names[av.get()]; check()

                def on_job(_e=None, i=i, jv=jv):
                    draft[i][1] = job_keys[job_labels.index(jv.get())]; redraw(); check()

                def on_note(*_a, i=i, nv=nv):
                    draft[i][2] = nv.get(); check()
                ac.bind("<<ComboboxSelected>>", on_agent)
                jc.bind("<<ComboboxSelected>>", on_job)
                nv.trace_add("write", on_note)

                def move(d, i=i):
                    j = i + d
                    if 0 <= j < len(draft):
                        draft[i], draft[j] = draft[j], draft[i]
                        redraw(); check()

                def remove(i=i):
                    del draft[i]
                    redraw(); check()
                ttk.Button(body, text="Up", width=4, command=lambda m=move: m(-1)).grid(row=i, column=4, padx=(8, 0))
                ttk.Button(body, text="Down", width=5, command=lambda m=move: m(1)).grid(row=i, column=5, padx=(2, 0))
                ttk.Button(body, text="Remove", width=8, command=remove).grid(row=i, column=6, padx=(2, 0))
            check()

        redraw()
        add_row = tk.Frame(win, bg=C["card"])
        add_row.pack(anchor="w", pady=(6, 0))

        def add_step():
            draft.append(["chatgpt", "review_full", ""])
            redraw()
        ttk.Button(add_row, text="Add a step", command=add_step).pack(side="left")
        tk.Label(add_row, text="Custom review: choose it, then type exactly what that reviewer should look for.",
                 font=self.f_small, bg=C["card"], fg=C["muted"]).pack(side="left", padx=(10, 0))

        save_row = tk.Frame(win, bg=C["card"])
        save_row.pack(anchor="w", pady=(12, 0))
        tk.Label(save_row, text="Save as:", font=self.f_body, bg=C["card"], fg=C["ink"]).pack(side="left")
        name_var = tk.StringVar(value="" if self.preset_var.get() == "Custom (edited)" else self.preset_var.get())
        ttk.Entry(save_row, textvariable=name_var, width=28).pack(side="left", padx=(6, 0))

        def save_preset(make_default=False):
            if check():
                messagebox.showerror("Fix the steps first", msg.cget("text"), parent=win)
                return
            name = name_var.get().strip()
            if not name:
                messagebox.showinfo("Name needed", "Type a name for this order first.", parent=win)
                return
            if name in ailab.DEFAULT_PRESETS and [list(x[:3]) for x in draft] != ailab.DEFAULT_PRESETS[name]:
                messagebox.showinfo("Pick another name", "That name belongs to a built-in order. Choose a different name.", parent=win)
                return
            cfg = ailab.load_config()
            cfg.setdefault("presets", {})[name] = [list(x[:3]) for x in draft]
            if make_default:
                cfg["default_preset"] = name
            ailab.save_config(cfg)
            self.reload_presets(select=name)
            self.order = [list(x[:3]) for x in draft]
            self.refresh_order_text()
            messagebox.showinfo("Saved", f"Saved as {name}." + (" It is now your default." if make_default else ""), parent=win)

        def delete_preset():
            name = name_var.get().strip()
            cfg = ailab.load_config()
            if name in (cfg.get("presets") or {}):
                del cfg["presets"][name]
                if cfg.get("default_preset") == name:
                    cfg.pop("default_preset")
                ailab.save_config(cfg)
                self.reload_presets(select=ailab.default_preset_name())
                self.preset_chosen()
                messagebox.showinfo("Deleted", f"Deleted {name}.", parent=win)
            else:
                messagebox.showinfo("Not deleted", "Only orders you saved can be deleted.", parent=win)
        ttk.Button(save_row, text="Save", command=save_preset).pack(side="left", padx=(8, 0))
        ttk.Button(save_row, text="Save and make default", command=lambda: save_preset(True)).pack(side="left", padx=(6, 0))
        ttk.Button(save_row, text="Delete", command=delete_preset).pack(side="left", padx=(6, 0))

        end_row = tk.Frame(win, bg=C["card"])
        end_row.pack(anchor="w", pady=(14, 0))

        def use():
            if check():
                messagebox.showerror("Fix the steps first", msg.cget("text"), parent=win)
                return
            self.order = [list(x[:3]) for x in draft]
            match = next((n for n, v in self.presets.items() if v == self.order), None)
            self.preset_var.set(match or "Custom (edited)")
            if not match:
                self.preset_box.configure(values=list(self.presets) + ["Custom (edited)"])
            self.refresh_order_text()
            win.destroy()
        ttk.Button(end_row, text="Use these steps", style="Accent.TButton", command=use).pack(side="left")
        ttk.Button(end_row, text="Cancel", command=win.destroy).pack(side="left", padx=(10, 0))

    def show_email_info(self):
        if not self.email_info:
            return
        ok, text = self.email_info
        try:
            self.note.config(text=("Email: " if ok else "Email problem: ") + text, fg=C["ok"] if ok else C["bad"])
        except Exception:
            pass
        try:
            self.log.configure(state="normal")
            self.log.insert("end", ("Email: " if ok else "Email problem: ") + text + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        except Exception:
            pass

    def email_ready(self):
        try:
            import ailab, mailer
            return mailer.configured(ailab.load_config())
        except Exception:
            return False

    def email_toggled(self):
        if self.email_var.get() and not self.email_ready():
            self.setup_email()
            self.email_var.set(self.email_ready())

    def setup_email(self):
        import ailab, mailer
        cfg = ailab.load_config()
        win = tk.Toplevel(self)
        win.title("Set up email")
        win.configure(bg=C["card"], padx=20, pady=16)
        win.transient(self)
        tk.Label(win, text="Email finished files to yourself", font=self.f_h2, bg=C["card"], fg=C["ink"]).grid(row=0, column=0, columnspan=2, sticky="w")
        steps = ("One time setup:\n"
                 "1. Go to myaccount.google.com/apppasswords and sign in (2-Step Verification must be on).\n"
                 "2. Make an app password named AI Lab. Google shows 16 letters.\n"
                 "3. Paste your Gmail address and those 16 letters below, then press Save and send test.\n"
                 "The app password only lives in the AI Lab folder on this computer. You can delete it any time from the same Google page.")
        tk.Label(win, text=steps, font=self.f_small, bg=C["card"], fg=C["muted"], justify="left", wraplength=520).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 10))
        tk.Label(win, text="Gmail address", font=self.f_small, bg=C["card"], fg=C["ink"]).grid(row=2, column=0, sticky="w")
        addr = tk.StringVar(value=cfg.get("email_address", ""))
        ttk.Entry(win, textvariable=addr, width=34).grid(row=2, column=1, sticky="w", pady=2)
        tk.Label(win, text="App password", font=self.f_small, bg=C["card"], fg=C["ink"]).grid(row=3, column=0, sticky="w")
        pw = tk.StringVar(value=cfg.get("email_app_password", ""))
        ttk.Entry(win, textvariable=pw, width=34, show="*").grid(row=3, column=1, sticky="w", pady=2)
        status = tk.Label(win, text="", font=self.f_small, bg=C["card"], fg=C["muted"], wraplength=520, justify="left")
        status.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def save():
            new = ailab.load_config()
            new["email_address"] = addr.get().strip()
            new["email_app_password"] = "".join(pw.get().split())
            ailab.save_config(new)
            status.config(text="Sending a test email...", fg=C["muted"])
            win.update_idletasks()

            def work():
                ok, text = mailer.send_test(new)
                self.after(0, lambda: done(ok, text))
            threading.Thread(target=work, daemon=True).start()

        def done(ok, text):
            status.config(text=text, fg=C["ok"] if ok else C["bad"])
            if ok:
                self.email_var.set(True)

        row = tk.Frame(win, bg=C["card"])
        row.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(row, text="Save and send test", style="Accent.TButton", command=save).pack(side="left")
        ttk.Button(row, text="Close", command=win.destroy).pack(side="left", padx=(10, 0))

    def clear_placeholder(self, _e=None):
        if self.placeholder_on:
            self.request.delete("1.0", "end")
            self.request.configure(fg=C["ink"])
            self.placeholder_on = False

    def restore_placeholder(self, _e=None):
        if not self.request.get("1.0", "end").strip():
            self.request.delete("1.0", "end")
            self.request.insert("1.0", PLACEHOLDER)
            self.request.configure(fg=C["wait"])
            self.placeholder_on = True

    def request_text(self):
        return "" if self.placeholder_on else self.request.get("1.0", "end").strip()

    def add_files(self):
        kinds = " ".join("*" + e for e in ATTACH_TYPES)
        paths = filedialog.askopenfilenames(title="Choose files", filetypes=[("Supported files", kinds), ("All files", "*.*")])
        self.add_paths(paths)

    def add_paths(self, paths):
        problems = []
        for p in paths:
            path = Path(p)
            if path.is_dir():
                problems.append(f"{path.name}\nFolders cannot be added. Open the folder and drop the files inside it.")
                continue
            ext = path.suffix.lower()
            if ext not in ATTACH_TYPES:
                problems.append(f"{path.name}\n{ext or 'This type'} files cannot be read. "
                                "Supported: PDF, Word (.docx), and text-type files. For an old .doc file, open it in Word and Save As .docx.")
                continue
            try:
                too_big = path.stat().st_size > MAX_ATTACH_BYTES
            except OSError:
                problems.append(f"{path.name}\nThis file could not be opened.")
                continue
            if too_big:
                problems.append(f"{path.name}\nThis file is larger than 25 MB.")
                continue
            if str(path) not in self.attachments:
                self.attachments.append(str(path))
                self.file_list.insert("end", path.name)
        self.refit_files()
        if problems:
            messagebox.showinfo("Some files were not added", "\n\n".join(problems))

    def setup_drop(self):
        """Lets you drag files from File Explorer onto the AI Lab window (Windows only, no add-on needed).
        If anything goes wrong it quietly does nothing and the Add files button still works.
        A note about what happened is saved in dnd-log.txt next to the program."""
        self.dnd_ok = False
        if os.name != "nt":
            return
        log = HERE / "dnd-log.txt"
        def note(text):
            try:
                with open(log, "a", encoding="utf-8") as fh:
                    fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
            except OSError:
                pass
        try:
            import ctypes
            from ctypes import wintypes
            user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
            WM_DROPFILES, WM_COPYGLOBALDATA, GWLP_WNDPROC = 0x0233, 0x0049, -4
            LRESULT = ctypes.c_ssize_t
            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
            if hasattr(user32, "SetWindowLongPtrW"):
                setlong = user32.SetWindowLongPtrW
                setlong.restype = ctypes.c_void_p
                setlong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            else:
                setlong = user32.SetWindowLongW
                setlong.restype = ctypes.c_long
                setlong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
            call = user32.CallWindowProcW
            call.restype = LRESULT
            call.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
            shell32.DragFinish.argtypes = [ctypes.c_void_p]
            prev = {}

            def handler(hwnd, msg, wparam, lparam):
                if msg == WM_DROPFILES:
                    paths = []
                    try:
                        count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                        for i in range(count):
                            n = shell32.DragQueryFileW(wparam, i, None, 0)
                            buf = ctypes.create_unicode_buffer(n + 1)
                            shell32.DragQueryFileW(wparam, i, buf, n + 1)
                            paths.append(buf.value)
                    finally:
                        shell32.DragFinish(wparam)
                    if paths:
                        self.after(0, lambda p=paths: self.add_paths(p))
                    return 0
                return call(prev.get(hwnd), hwnd, msg, wparam, lparam)

            self._drop_proc = WNDPROC(handler)  # keep a reference so it is not freed
            proc_ptr = ctypes.cast(self._drop_proc, ctypes.c_void_p)

            def hook(hwnd):
                if not hwnd or hwnd in prev:
                    return False
                try:
                    user32.ChangeWindowMessageFilterEx(hwnd, WM_DROPFILES, 1, None)
                    user32.ChangeWindowMessageFilterEx(hwnd, WM_COPYGLOBALDATA, 1, None)
                except Exception:
                    pass
                old = setlong(hwnd, GWLP_WNDPROC, proc_ptr)
                if not old:
                    return False
                prev[hwnd] = old
                shell32.DragAcceptFiles(hwnd, True)
                return True

            self.update_idletasks()
            tops = []
            def walk(w):
                for child in w.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        continue
                    try:
                        hook(child.winfo_id())
                    except Exception:
                        pass
                    walk(child)
            # the window itself, its outer frame, and every part inside it, so a drop works anywhere on the window
            try:
                hook(self.winfo_id())
                outer = int(self.wm_frame(), 16)
                if outer != self.winfo_id():
                    hook(outer)
            except Exception as err:
                note("could not hook the window itself: " + repr(err))
            walk(self)
            self.dnd_ok = len(prev) > 0
            note(f"drag and drop hooked on {len(prev)} window parts")
            if self.dnd_ok:
                self.files_hint.configure(text="  PDF, Word (.docx), text, HTML, CSV.  You can drag files onto this window.")
        except Exception as err:
            self.dnd_ok = False
            note("drag and drop could not start: " + repr(err))

    def remove_files(self):
        for index in reversed(self.file_list.curselection()):
            self.file_list.delete(index)
            del self.attachments[index]
        self.refit_files()

    def refit_files(self):
        """Makes the files box tall enough for what is in it (up to 6 rows, then it scrolls) and grows the window to fit."""
        n = len(self.attachments)
        self.file_list.configure(height=max(2, min(6, n)))
        if n > 6:
            self.file_scroll.pack(side="left", fill="y", before=self.file_btns)
        else:
            self.file_scroll.pack_forget()
        self.update_idletasks()
        need = self.winfo_reqheight()
        if need > self.winfo_height():
            self.geometry(f"{self.winfo_width()}x{need}")

    # ---- run view
    def build_run_view(self):
        self.run_view = tk.Frame(self.container, bg=C["bg"])
        outer, box = self.card(self.run_view)
        outer.pack(fill="x")
        self.run_title = tk.Label(box, text="", font=self.f_h2, bg=C["card"], fg=C["ink"], anchor="w", justify="left", wraplength=780)
        self.run_title.pack(anchor="w")
        self.steps_frame = tk.Frame(box, bg=C["card"])
        self.steps_frame.pack(fill="x", pady=(8, 0))
        self.status = tk.Label(box, text="", font=self.f_body, bg=C["card"], fg=C["muted"], anchor="w", justify="left", wraplength=780)
        self.status.pack(anchor="w", pady=(10, 0))

        bar = tk.Frame(self.run_view, bg=C["bg"])
        bar.pack(fill="x", pady=(10, 0))
        self.stop_button = ttk.Button(bar, text="Stop", style="Danger.TButton", command=self.stop)
        self.stop_button.pack(side="left")
        self.retry_button = ttk.Button(bar, text="Retry from where it stopped", command=self.retry)
        self.retry_button.pack(side="left", padx=(10, 0))
        self.details_button = ttk.Button(bar, text="Show details", command=self.toggle_log)
        self.details_button.pack(side="left", padx=(10, 0))
        self.new_button = ttk.Button(bar, text="New request", command=self.new_request)
        self.new_button.pack(side="right")

        self.log_frame = tk.Frame(self.run_view, bg=C["bg"])
        self.log = tk.Text(self.log_frame, height=7, wrap="word", font=("Consolas" if os.name == "nt" else "TkFixedFont", 9),
                           state="disabled", relief="solid", borderwidth=1, bg="#fafbfc", highlightthickness=0)
        scroll = ttk.Scrollbar(self.log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log_visible = False

        self.results_outer, self.results = self.card(self.run_view)
        # packed when a run finishes

    def toggle_log(self):
        if self.log_visible:
            self.log_frame.pack_forget()
            self.details_button.configure(text="Show details")
        else:
            shown = self.results_outer.winfo_ismapped()
            if shown:
                self.results_outer.pack_forget()
            self.log_frame.pack(fill="both", expand=False, pady=(10, 0))
            if shown:
                self.results_outer.pack(fill="x", pady=(12, 0))
            self.details_button.configure(text="Hide details")
        self.log_visible = not self.log_visible

    def log_line(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip("\n") + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def show_input(self):
        self.run_view.pack_forget()
        self.input_view.pack(fill="both", expand=True)

    def show_run(self):
        self.input_view.pack_forget()
        self.run_view.pack(fill="both", expand=True)

    # ---- progress rows
    def make_rows(self, plan):
        for child in self.steps_frame.winfo_children():
            child.destroy()
        self.rows = {}
        for i, (key, agent, label) in enumerate(plan, 1):
            glyph = tk.Label(self.steps_frame, text="○", font=(self.family, 14), bg=C["card"], fg=C["wait"], width=2)
            glyph.grid(row=i, column=0, sticky="w")
            badge = tk.Label(self.steps_frame, text=PRETTY.get(agent, agent), font=(self.family, 9, "bold"), bg=AGENT_COLOR.get(agent, C["muted"]),
                             fg="white", padx=8, pady=1)
            badge.grid(row=i, column=1, sticky="w", padx=(4, 10), pady=3)
            text = tk.Label(self.steps_frame, text=label, font=self.f_body, bg=C["card"], fg=C["muted"], anchor="w")
            text.grid(row=i, column=2, sticky="w")
            detail = tk.Label(self.steps_frame, text="", font=self.f_small, bg=C["card"], fg=C["muted"], anchor="w")
            detail.grid(row=i, column=3, sticky="w", padx=(12, 0))
            self.rows[i] = {"glyph": glyph, "text": text, "detail": detail, "key": key}

    def set_row(self, n, state, detail=""):
        row = self.rows.get(n)
        if not row:
            return
        glyph, colour, ink = {
            "wait": ("○", C["wait"], C["muted"]), "work": ("◐", C["working"], C["ink"]), "done": ("✔", C["ok"], C["ink"]),
            "skip": ("–", C["wait"], C["muted"]), "fail": ("✕", C["bad"], C["bad"]),
        }[state]
        row["glyph"].configure(text=glyph, fg=colour)
        row["text"].configure(fg=ink, font=self.f_bold if state == "work" else self.f_body)
        row["detail"].configure(text=detail, fg=C["bad"] if state == "fail" else C["muted"])
        if state == "work":
            self.working_rows.add(n)
        else:
            self.working_rows.discard(n)

    def spin(self):
        frames = "◐◓◑◒"
        self.spin_i = (self.spin_i + 1) % 4
        for n in list(self.working_rows):
            self.rows[n]["glyph"].configure(text=frames[self.spin_i])
        self.after(250, self.spin)

    def set_status(self, text, colour=None):
        self.status.configure(text=text, fg=colour or C["muted"])

    # ---- starting and running
    def check_tools(self):
        missing = tool_status()
        if missing:
            self.tools_label.configure(text="Not found: " + ", ".join(missing) + ". Run the installer again.", fg=C["bad"])
        else:
            if gemini_present():
                self.tools_label.configure(text="Claude, ChatGPT and Gemini found", fg=C["ok"])
            else:
                self.tools_label.configure(text="Claude and ChatGPT found. Gemini not found (its review will be skipped).", fg=C["warn"])
        if convert is None:
            self.tools_label.configure(text="convert.py is missing or broken. Run the installer again.", fg=C["bad"])

    def start(self, mock):
        request = self.request_text()
        if not request:
            messagebox.showinfo("Type a request", "Type what you want in the first box.")
            return
        if not mock:
            missing = tool_status()
            if missing:
                messagebox.showerror("A tool is missing", "Not found: " + ", ".join(missing) + ".\nRun the installer again.")
                return
        fmt = next((k for k, v in FORMATS.items() if v == self.format_var.get()), DEFAULT_FORMAT)
        import ailab
        steps = [list(x[:3]) for x in self.order]
        if not mock and any(st[0] == "gemini" for st in steps) and not gemini_present():
            if not messagebox.askyesno("Gemini not found", "Gemini (the agy tool) is not installed on this computer.\n\n"
                                       "Continue without the Gemini steps?"):
                return
            steps = [st for st in steps if st[0] != "gemini"]
        problems = ailab.validate_steps(steps)
        if problems:
            messagebox.showerror("The steps need a fix", "\n".join(problems) + "\n\nPress Edit steps to change them.")
            return
        if len(steps) > 7 and not messagebox.askyesno("Many steps", f"This order has {len(steps)} steps. It will use a lot of your allowances and take longer.\n\nContinue?"):
            return
        email = self.email_var.get() and not mock
        title = self.title_var.get().strip()

        def go(final_request, attachments, originals):
            try:
                self.folder = make_run(final_request, title, fmt, steps, attachments, email=email, originals=originals)
            except Exception as err:
                messagebox.showerror("Could not start", str(err))
                return
            self.mock = mock
            self.launch(first=True)

        def after_check(final_request, files):
            self.start_button.state(["!disabled"])
            if mock:
                go(final_request, files, [])
            else:
                self.condense_stage(final_request, files, go)

        if mock or not self.precheck_var.get():
            after_check(request, list(self.attachments))
        else:
            self.check_request(request, after_check)

    def launch(self, first):
        self.show_run()
        self.results_outer.pack_forget()
        self.retry_button.state(["disabled"])
        self.new_button.state(["disabled"])
        self.stop_button.state(["!disabled"])
        self.verdict = None
        self.a11y = None
        self.email_info = None
        self.files_made = []
        self.stopped = False
        self.ended = False
        self.end_state = ""
        self.end_message = ""
        self.working_rows.clear()
        self.run_title.configure(text=("Practice run: " if self.mock else "") + self.folder.name)
        if first:
            self.log.configure(state="normal"); self.log.delete("1.0", "end"); self.log.configure(state="disabled")
        self.log_line(f"Run folder: {self.folder}")
        self.set_status("Starting...")
        cmd = [python_for_child(), "-u", str(AILAB), str(self.folder)]
        if self.mock:
            cmd.append("--mock")
        try:
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         text=True, encoding="utf-8", errors="replace", cwd=str(HERE), env=env,
                                         creationflags=NO_WINDOW)
        except Exception as err:
            self.proc = None
            messagebox.showerror("Could not start", str(err))
            self.show_input()
            return
        keep_awake(True)
        threading.Thread(target=self.reader, args=(self.proc,), daemon=True).start()

    def reader(self, proc):
        for line in proc.stdout:
            self.queue.put(("line", line.rstrip("\n")))
        proc.wait()
        self.queue.put(("exit", proc.returncode))

    def pump(self):
        try:
            while True:
                kind, value = self.queue.get_nowait()
                if kind == "line":
                    self.handle_line(value)
                else:
                    self.handle_exit(value)
        except queue.Empty:
            pass
        self.after(150, self.pump)

    def handle_line(self, line):
        if not line.startswith("@@ "):
            if line.strip():
                self.log_line(line)
            return
        parts = line[3:].split("|")
        tag = parts[0]
        if tag == "plan":
            self.make_rows(json.loads(parts[1]))
        elif tag == "stage":
            action, n = parts[1], int(parts[2])
            note = parts[3] if len(parts) > 3 else ""
            if action == "start":
                self.set_row(n, "work", "")
                self.set_status(note + "...")
            elif action == "done":
                self.set_row(n, "done", note)
            elif action == "reused":
                self.set_row(n, "done", "kept from earlier")
            elif action == "skip":
                self.set_row(n, "skip", note)
            elif action == "fail":
                self.set_row(n, "fail", note)
        elif tag == "making":
            self.set_status(parts[1] + "...")
        elif tag == "file":
            self.files_made.append(parts[1])
        elif tag == "verdict":
            self.verdict = parts[1]
        elif tag == "a11y":
            self.a11y = (int(parts[1]), int(parts[2]), int(parts[3]))
        elif tag == "email":
            self.email_info = (parts[1] == "ok", parts[2] if len(parts) > 2 else "")
            self.after(300, self.show_email_info)
        elif tag == "end":
            self.ended = True
            self.end_state = parts[1]
            self.end_message = parts[2] if len(parts) > 2 else ""

    def handle_exit(self, code):
        keep_awake(False)
        self.proc = None
        self.stop_button.state(["disabled"])
        self.new_button.state(["!disabled"])
        for n in list(self.working_rows):
            self.set_row(n, "fail" if not self.stopped else "wait", "stopped" if self.stopped else "")
        if self.stopped:
            self.set_status("Stopped. Press Retry to continue from where it stopped.", C["warn"])
            self.retry_button.state(["!disabled"])
        elif self.ended and self.end_state == "ok":
            self.finish_ok()
        else:
            message = self.end_message if self.ended else "The program closed unexpectedly. Press Details to see why."
            self.set_status(message or "Something went wrong. Press Show details.", C["bad"])
            self.retry_button.state(["!disabled"])
            if not self.log_visible:
                self.toggle_log()

    def finish_ok(self):
        verdict = self.verdict
        fixed = any(r["key"] == "fix" and r["glyph"].cget("text") == "✔" for r in self.rows.values())
        if verdict == "PASS":
            line = "ChatGPT checked the result and found no problems that need fixing."
        elif verdict == "FIX NEEDED" and fixed:
            line = "A reviewer found problems and fixes were applied. Read the review and fix notes before you use it."
        elif verdict == "FIX NEEDED":
            line = "A reviewer found problems that were not fixed. Read the review notes before you use the file."
        else:
            line = ""
        self.set_status("Done. " + line, C["ok"] if verdict == "PASS" else C["warn"])
        for w in self.results.winfo_children():
            w.destroy()
        files_dir = self.folder / "files"
        tk.Label(self.results, text="Your files", font=self.f_h2, bg=C["card"], fg=C["ink"]).pack(anchor="w")
        order = {".docx": 0, ".html": 1, ".htm": 1, ".md": 2}
        names = sorted(set(self.files_made), key=lambda f: (order.get(Path(f).suffix.lower(), 9), f))
        for rel in names:
            path = self.folder / rel
            row = tk.Frame(self.results, bg=C["card"])
            row.pack(fill="x", pady=3)
            tk.Label(row, text=Path(rel).name, font=self.f_body, bg=C["card"], fg=C["ink"]).pack(side="left")
            ttk.Button(row, text="Show in folder", command=lambda p=path: show_in_folder(p)).pack(side="right")
            ttk.Button(row, text="Open", command=lambda p=path: open_path(p)).pack(side="right", padx=(0, 8))
        if self.a11y is not None:
            errs, warns, count = self.a11y
            colour = C["ok"] if errs == 0 else C["warn"]
            text = (f"Accessibility scan: {errs} error(s), {warns} warning(s) in {count} file(s). "
                    "This is a screening, not an ADA conformance test. Test with a keyboard and a screen reader before publishing.")
            tk.Label(self.results, text=text, font=self.f_small, bg=C["card"], fg=colour, wraplength=760, justify="left").pack(anchor="w", pady=(8, 0))
        tail = tk.Frame(self.results, bg=C["card"])
        tail.pack(fill="x", pady=(10, 0))
        ttk.Button(tail, text="Open files folder", command=lambda: open_path(files_dir)).pack(side="left")
        ttk.Button(tail, text="Open summary", command=lambda: open_path(self.folder / "summary.md")).pack(side="left", padx=(8, 0))
        if self.a11y is not None:
            ttk.Button(tail, text="Open accessibility report", command=lambda: open_path(self.folder / "accessibility-report.md")).pack(side="left", padx=(8, 0))
        if self.a11y is not None and self.local_btn is not None:
            ttk.Button(tail, text="Explain in plain language (local AI)", command=self.explain_a11y).pack(side="left", padx=(8, 0))
        if (self.folder / "usage-report.md").is_file():
            ttk.Button(tail, text="Open usage report", command=lambda: open_path(self.folder / "usage-report.md")).pack(side="left", padx=(8, 0))
        ttk.Button(tail, text="Open all steps", command=lambda: open_path(self.folder / "stages")).pack(side="left", padx=(8, 0))
        tk.Label(self.results, text=str(files_dir), font=self.f_small, bg=C["card"], fg=C["muted"]).pack(anchor="w", pady=(8, 0))
        self.results_outer.pack(fill="x", pady=(12, 0))

    def stop(self):
        if not self.proc:
            return
        self.stopped = True
        self.set_status("Stopping...", C["warn"])
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], creationflags=NO_WINDOW,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                self.proc.terminate()
        except Exception:
            pass

    def retry(self):
        if self.folder and not self.proc:
            self.launch(first=False)

    def new_request(self):
        self.show_input()

    def on_close(self):
        if self.proc:
            if not messagebox.askyesno("A run is going", "A run is still going. Stop it and close?"):
                return
            self.stop()
        keep_awake(False)
        self.destroy()

    # ---- extras
    def check_signins(self):
        missing = tool_status()
        if missing:
            messagebox.showerror("A tool is missing", "Not found: " + ", ".join(missing) + ".\nRun the installer again.")
            return
        self.note.configure(text="Checking sign-ins. This takes a minute or two and uses very little...", fg=C["muted"])

        def work():
            results = []
            try:
                import ailab
            except Exception as err:
                self.after(0, lambda: self.signins_done([f"Could not load ailab.py: {err}"]))
                return
            for key, name in (("claude", "Claude"), ("chatgpt", "ChatGPT"), ("gemini", "Gemini")):
                if key == "gemini" and not gemini_present():
                    results.append("Gemini: not installed (optional)")
                    continue
                ok, message = ailab.ping(key)
                results.append(f"{name}: signed in and working" if ok else f"{name}: problem. {message[:200]}")
            self.after(0, lambda: self.signins_done(results))

        threading.Thread(target=work, daemon=True).start()

    def signins_done(self, results):
        self.note.configure(text="Runs take a few minutes. You can leave. The finished files are saved for you.")
        messagebox.showinfo("Sign-in check", "\n\n".join(results) + "\n\nIf one has a problem, open PowerShell and type  claude  "
                            "(for Claude),  codex login  (for ChatGPT), or  cd C:\\ai-lab\\_scratch  then  agy  (for Gemini), then try again.")

    def past_runs(self):
        if not RUNS.exists():
            messagebox.showinfo("Past runs", "There are no runs yet.")
            return
        win = tk.Toplevel(self)
        win.title("Past runs")
        win.geometry("640x380")
        win.configure(bg=C["bg"])
        runs = sorted([p for p in RUNS.iterdir() if p.is_dir()], reverse=True)
        lb = tk.Listbox(win, font=self.f_body, activestyle="none", relief="solid", borderwidth=1)
        lb.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        for p in runs:
            lb.insert("end", p.name)
        bar = tk.Frame(win, bg=C["bg"])
        bar.pack(fill="x", padx=12, pady=(0, 12))

        def chosen():
            sel = lb.curselection()
            return runs[sel[0]] if sel else None

        def act(sub):
            p = chosen()
            if p:
                target = p / sub if sub else p
                open_path(target if target.exists() else p)

        ttk.Button(bar, text="Open files", command=lambda: act("files")).pack(side="left")
        ttk.Button(bar, text="Open summary", command=lambda: act("summary.md")).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Open run folder", command=lambda: act("")).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    try:
        App().mainloop()
    except Exception:
        import traceback
        text = traceback.format_exc()
        try:
            (HERE / "launcher-error.txt").write_text(text, encoding="utf-8")
        except OSError:
            pass
        try:
            r = tk.Tk(); r.withdraw()
            messagebox.showerror("AI Lab could not start", text[-1500:])
        except Exception:
            pass


if __name__ == "__main__":
    main()
