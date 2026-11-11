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
