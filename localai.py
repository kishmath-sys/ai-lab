"""Finds and talks to an AI that runs on this computer (Ollama or LM Studio).

Everything here talks only to 127.0.0.1. Nothing goes to the internet.
Run  python localai.py  to see what it finds on this computer.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

NO_WINDOW = 0x08000000 if os.name == "nt" else 0

PROVIDERS = {
    "ollama": {"label": "Ollama", "base": "http://127.0.0.1:11434"},
    "lmstudio": {"label": "LM Studio", "base": "http://127.0.0.1:1234"},
}

# Never use the system proxy for local calls, so nothing can be routed outside this computer.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _call(url, payload=None, timeout=5):
    if not url.startswith("http://127.0.0.1"):
        raise ValueError("Local AI calls must stay on this computer.")
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _ping(kind, timeout=1.5):
    base = PROVIDERS[kind]["base"]
    try:
        _call(base + ("/api/tags" if kind == "ollama" else "/v1/models"), timeout=timeout)
        return True
    except Exception:
        return False


def _ollama_exe():
    found = shutil.which("ollama")
    if found:
        return found
    for var in ("LOCALAPPDATA", "ProgramFiles"):
        base = os.environ.get(var)
        if base:
            p = Path(base) / "Programs" / "Ollama" / "ollama.exe"
            if p.exists():
                return str(p)
            p = Path(base) / "Ollama" / "ollama.exe"
            if p.exists():
                return str(p)
    return None


def _lms_exe():
    found = shutil.which("lms")
    if found:
        return found
    home = Path.home() / ".lmstudio" / "bin"
    for name in ("lms.exe", "lms"):
        if (home / name).exists():
            return str(home / name)
    return None


def _lmstudio_installed():
    base = os.environ.get("LOCALAPPDATA")
    if base and (Path(base) / "Programs" / "LM Studio").exists():
        return True
    return _lms_exe() is not None


def find_local():
    """Returns a list of dicts: kind, label, installed, running. Empty list means no local AI was found."""
    out = []
    ollama_running = _ping("ollama")
    if ollama_running or _ollama_exe():
        out.append({"kind": "ollama", "label": "Ollama", "installed": True, "running": ollama_running})
    lm_running = _ping("lmstudio")
    if lm_running or _lmstudio_installed():
        out.append({"kind": "lmstudio", "label": "LM Studio", "installed": True, "running": lm_running})
    return out


def ensure_running(kind, wait=25):
    """Starts the local server if it is installed but not running. Returns True when it answers."""
    if _ping(kind):
        return True
    try:
        if kind == "ollama":
            exe = _ollama_exe()
            if not exe:
                return False
            subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        else:
            exe = _lms_exe()
            if not exe:
                return False
            subprocess.Popen([exe, "server", "start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except OSError:
        return False
    end = time.time() + wait
    while time.time() < end:
        if _ping(kind, timeout=1.5):
            return True
        time.sleep(1)
    return False


def list_models(kind):
    """Names of chat models available in that program. Embedding models are left out."""
    base = PROVIDERS[kind]["base"]
    try:
        if kind == "ollama":
            names = [m.get("name", "") for m in _call(base + "/api/tags", timeout=5).get("models", [])]
        else:
            names = [m.get("id", "") for m in _call(base + "/v1/models", timeout=5).get("data", [])]
    except Exception:
        return []
    return [n for n in names if n and "embed" not in n.lower()]


def all_choices():
    """Every usable model on this computer as (kind, label, model). Starts servers that are installed but off."""
    choices = []
    for p in find_local():
        kind = p["kind"]
        if not p["running"] and not ensure_running(kind):
            continue
        for m in list_models(kind):
            choices.append((kind, p["label"], m))
    return choices


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def chat(kind, model, system, user, timeout=600):
    """One question to a local model. Returns the answer text. Raises on failure."""
    base = PROVIDERS[kind]["base"]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if kind == "ollama":
        data = _call(base + "/api/chat", {
            "model": model, "messages": messages, "stream": False,
            "options": {"num_ctx": 8192, "temperature": 0.2},
        }, timeout=timeout)
        text = (data.get("message") or {}).get("content", "")
    else:
        data = _call(base + "/v1/chat/completions", {
            "model": model, "messages": messages, "temperature": 0.2, "stream": False,
        }, timeout=timeout)
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    return _THINK.sub("", text).strip()


REVIEW_SYSTEM = (
    "You check a request before it is sent to other AI tools. Do NOT carry out the request. "
    "The request will be researched, then turned into a Word document and an interactive web page for college course materials, "
    "and the result will be checked for accessibility (WCAG 2.1 AA). "
    "Reply in exactly this format and nothing else:\n"
    "IMPROVED REQUEST:\n"
    "(The request rewritten so it is clear and complete. Keep the person's meaning, words and level of detail. "
    "Do not invent facts, standards or numbers. Write it as the person would, in plain direct language.)\n"
    "QUESTIONS:\n"
    "(Up to 5 short bullets starting with a dash for things that are missing or unclear, such as audience, course level, length, "
    "number of problems, or format. Write None if the request is already clear.)\n"
    "PRIVACY CHECK:\n"
    "(Write Clear if nothing needs attention. Otherwise list anything that should not be sent to online AI services, such as "
    "student names, student ID numbers, grades, email addresses, phone numbers or other personal details.)"
)


def review_request(kind, model, request, attachment_names=None, timeout=600):
    """Returns dict with improved, questions, privacy, raw. Missing parts are empty strings."""
    note = ""
    if attachment_names:
        note = "\n\nFiles the person attached (names only): " + ", ".join(attachment_names)
    raw = chat(kind, model, REVIEW_SYSTEM, "REQUEST:\n" + request.strip() + note, timeout=timeout)
    return parse_review(raw)


def parse_review(raw):
    parts = {"improved": "", "questions": "", "privacy": "", "raw": raw}
    pattern = re.compile(r"^\s*\**\s*(IMPROVED REQUEST|QUESTIONS|PRIVACY CHECK)\s*\**\s*:?\s*\**\s*$", re.I | re.M)
    marks = list(pattern.finditer(raw))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(raw)
        body = raw[m.end():end].strip()
        key = {"improved request": "improved", "questions": "questions", "privacy check": "privacy"}[m.group(1).lower()]
        parts[key] = body
    return parts


# ---------------------------------------------------------------- helpers for chunking

def chunks(text, size=6000):
    """Splits text at paragraph breaks into pieces of at most about `size` characters."""
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        while len(para) > size:
            if cur:
                out.append(cur); cur = ""
            out.append(para[:size]); para = para[size:]
        if len(cur) + len(para) + 2 > size and cur:
            out.append(cur); cur = ""
        cur = (cur + "\n\n" + para) if cur else para
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- personal information check

_PATTERNS = [
    ("email address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("phone number", re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")),
    ("Social Security number", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    ("student or ID number", re.compile(r"(?i)\b(?:student|stu|banner|campus|college|emplid|id)\s*(?:id|number|no\.?|#)?\s*[:#]?\s*[A-Za-z]?\d{6,9}\b")),
    ("date of birth", re.compile(r"(?i)\b(?:dob|date of birth|birth\s?date)\b\s*[:\-]?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}")),
]


def _mask(value):
    value = value.strip()
    if len(value) <= 4:
        return "****"
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def scan_personal(text, ignore=()):
    """Fast pattern check. Returns {label: (count, [masked examples])}. Values are masked so the report is safe to show."""
    ignore = {i.lower() for i in ignore if i}
    found = {}
    for label, pat in _PATTERNS:
        hits = [m.group(0) for m in pat.finditer(text) if m.group(0).lower() not in ignore]
        if hits:
            found[label] = (len(hits), [_mask(h) for h in hits[:3]])
    return found


PERSONAL_SYSTEM = (
    "You look for personal information about students or other private people in a document. Do NOT summarize the document. "
    "Report ONLY these: a person's name that appears together with a grade, score, student ID, email, phone number or home address; "
    "class rosters; gradebook rows; and health or disability accommodation details tied to a named person. "
    "Do NOT report the course instructor, authors, historical figures, or made up names used inside math word problems. "
    "Reply with one short line per finding, for example: roster with student names. If there is nothing, reply with the single word NONE."
)


def ai_find_personal(kind, model, text, max_chunks=3, timeout=600, progress=None):
    """Asks the local AI to look for names tied to private details. Returns a list of short findings."""
    findings = []
    parts = chunks(text, 6000)[:max_chunks]
    for i, part in enumerate(parts, start=1):
        if progress:
            progress(f"Local AI reading part {i} of {len(parts)}...")
        answer = chat(kind, model, PERSONAL_SYSTEM, part, timeout=timeout)
        for line in answer.splitlines():
            line = line.strip(" -*\t")
            if line and line.upper().strip(". ") != "NONE" and line not in findings:
                findings.append(line[:200])
    return findings


# ---------------------------------------------------------------- condensing long files

CONDENSE_SYSTEM = (
    "You shorten one section of a long document so another AI can use it as source material. "
    "Keep every number, formula, equation, definition, standard or outcome code, requirement, deadline, problem statement and source name exactly as written. "
    "Remove repetition, filler, headers, footers and page numbers. Do not add anything that is not in the section. Do not comment. "
    "Reply with plain text only."
)


def condense_text(kind, model, text, progress=None, cancelled=None, timeout=900):
    """Condenses a long text one chunk at a time. Returns the condensed text. Raises RuntimeError if cancelled."""
    parts = chunks(text, 6000)
    out = []
    for i, part in enumerate(parts, start=1):
        if cancelled and cancelled():
            raise RuntimeError("cancelled")
        if progress:
            progress(f"Condensing part {i} of {len(parts)}...")
        out.append(chat(kind, model, CONDENSE_SYSTEM, part, timeout=timeout))
    return "\n\n".join(o for o in out if o)


# ---------------------------------------------------------------- plain language accessibility report

A11Y_SYSTEM = (
    "You explain an automatic accessibility scan to a college instructor who is not technical. "
    "Write a short numbered to-do list in plain language. For each item say where the problem is, what is wrong, and how to fix it in Word or in the web page. "
    "Put errors first, then warnings. Use only what the report says. Do not invent problems. Do not say the files are compliant or accessible. "
    "End with one line: This scan is a screening only. Test with a keyboard and a screen reader before publishing."
)


def explain_accessibility(kind, model, report_text, timeout=600):
    return chat(kind, model, A11Y_SYSTEM, report_text[:12000], timeout=timeout)


# ---------------------------------------------------------------- choosing and comparing

def pick_default(choices):
    """Picks a sensible model without asking. Prefers about 3 to 9 billion parameters: fast enough, good enough."""
    sized = []
    for c in choices:
        m = re.search(r"(\d+(?:\.\d+)?)\s*b\b", c[2].lower())
        sized.append((float(m.group(1)) if m else None, c))
    mid = [x for x in sized if x[0] is not None and 3 <= x[0] <= 9]
    if mid:
        return max(mid, key=lambda x: x[0])[1]
    small = [x for x in sized if x[0] is not None and x[0] < 3]
    if small:
        return max(small, key=lambda x: x[0])[1]
    big = [x for x in sized if x[0] is not None]
    if big:
        return min(big, key=lambda x: x[0])[1]
    return choices[0] if choices else None


def parse_questions(text):
    """Turns the QUESTIONS section into a clean list of questions."""
    out = []
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s*", "", line).strip()
        if line and line.lower().strip(". ") not in ("none", "n/a", "no questions"):
            out.append(line)
    return out


_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
          "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "fifteen": "15", "twenty": "20"}


def _digit_tokens(text):
    toks = set()
    for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-/]*", text):
        w = w.strip(".-/").lower()
        if w in _WORDS:
            toks.add(_WORDS[w])
        elif any(ch.isdigit() for ch in w):
            toks.add(w)
    return toks


def check_changes(original, improved):
    """Accuracy check that needs no AI: numbers and codes added to, or dropped from, the original."""
    a, b = _digit_tokens(original), _digit_tokens(improved)
    return sorted(b - a), sorted(a - b)


def agreement(text_a, text_b):
    """0 to 100: how much two wordings agree."""
    import difflib
    wa, wb = text_a.lower().split(), text_b.lower().split()
    if not wa or not wb:
        return 0
    return int(round(100 * difflib.SequenceMatcher(None, wa, wb).ratio()))


if __name__ == "__main__":
    found = find_local()
    if not found:
        print("No local AI found (looked for Ollama and LM Studio).")
        sys.exit(0)
    for p in found:
        print(p["label"], "installed", "running" if p["running"] else "not running")
        if p["running"] or ensure_running(p["kind"]):
            models = list_models(p["kind"])
            print("  models:", ", ".join(models) if models else "none downloaded yet")
