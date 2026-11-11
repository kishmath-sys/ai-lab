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
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

# Drag and drop is optional. It needs the tkinterdnd2 package. Without it the Add files button still works.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    BaseTk = TkinterDnD.Tk
except Exception:
    DND_FILES = None
    BaseTk = tk.Tk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
RUNS = HERE / "runs"
AILAB = HERE / "ailab.py"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MAX_ATTACH_BYTES = 25_000_000

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


def make_run(request, title, fmt, steps, attachments, email=False):
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
    if not title:
        words = request.strip().split()
        title = " ".join(words[:8]).strip(" .,:;")
    (folder / "brief.md").write_text(request.strip() + "\n", encoding="utf-8")
    (folder / "run.json").write_text(json.dumps({"format": fmt, "steps": steps, "email": email, "title": title}, indent=2), encoding="utf-8")
    return folder


# ------------------------------------------------------------------ the window

class App(BaseTk):
    def __init__(self):
        super().__init__()
        self.dnd_ok = False
        self.title("AI Lab")
        self.configure(bg=C["bg"])
        self.minsize(760, 560)
        self.geometry("980x780")
        families = set(tkfont.families())
        self.family = "Segoe UI" if "Segoe UI" in families else ("Helvetica" if "Helvetica" in families else "TkDefaultFont")
        self.f_body = (self.family, 10)
        self.f_small = (self.family, 9)
        self.f_bold = (self.family, 10, "bold")
        self.f_h1 = (self.family, 20, "bold")
        self.f_h2 = (self.family, 12, "bold")
        self.attachments = []
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
        self.request = tk.Text(box, height=7, wrap="word", font=self.f_body, relief="solid", borderwidth=1,
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
        self.file_list = tk.Listbox(row, height=3, font=self.f_body, relief="solid", borderwidth=1, highlightthickness=0,
                                    activestyle="none", selectmode="extended")
        self.file_list.pack(side="left", fill="x", expand=True)
        btns = tk.Frame(row, bg=C["card"])
        btns.pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Add files...", command=self.add_files).pack(fill="x")
        ttk.Button(btns, text="Remove", command=self.remove_files).pack(fill="x", pady=(6, 0))
        self.setup_drop([box2, top, row, self.file_list])

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

        bar = tk.Frame(self.input_view, bg=C["bg"])
        bar.pack(fill="x", pady=(16, 0))
        self.start_button = ttk.Button(bar, text="Start", style="Accent.TButton", command=lambda: self.start(mock=False))
        self.start_button.pack(side="left")
        ttk.Button(bar, text="Practice run (uses nothing)", command=lambda: self.start(mock=True)).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Check sign-ins", command=self.check_signins).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Gemini model", command=self.gemini_model).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Past runs", command=self.past_runs).pack(side="right")
        ttk.Button(bar, text="Usage", command=self.show_usage).pack(side="right", padx=(0, 10))

        self.note = tk.Label(self.input_view, text="Runs take a few minutes. You can leave. The finished files are saved for you.",
                             font=self.f_small, bg=C["bg"], fg=C["muted"])
        self.note.pack(anchor="w", pady=(10, 0))

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
        if problems:
            messagebox.showinfo("Some files were not added", "\n\n".join(problems))

    def setup_drop(self, widgets):
        """Lets you drag files from File Explorer onto the files box. Quietly does nothing if the add-on is missing."""
        if DND_FILES is None:
            return
        try:
            for w in widgets:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self.on_drop)
            self.dnd_ok = True
            self.files_hint.configure(text="  PDF, Word (.docx), text, HTML, CSV.  You can drag files here.")
        except Exception:
            self.dnd_ok = False

    def on_drop(self, event):
        try:
            paths = list(self.tk.splitlist(event.data))
        except Exception:
            paths = [event.data]
        self.add_paths(paths)
        return event.action

    def remove_files(self):
        for index in reversed(self.file_list.curselection()):
            self.file_list.delete(index)
            del self.attachments[index]

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
        try:
            self.folder = make_run(request, self.title_var.get().strip(), fmt, steps, self.attachments,
                                   email=(self.email_var.get() and not mock))
        except Exception as err:
            messagebox.showerror("Could not start", str(err))
            return
        self.mock = mock
        self.launch(first=True)

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
