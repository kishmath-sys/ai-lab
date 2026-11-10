"""Sends the finished files to your own Gmail. Uses a Gmail app password, no fees."""
import json
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

MAX_BYTES = 20 * 1024 * 1024
SKIP_SUFFIX = {".json", ".css"}


def configured(cfg):
    return bool(cfg.get("email_address") and cfg.get("email_app_password"))


def _clean_password(p):
    return "".join(str(p).split())


def _send(cfg, msg):
    addr = cfg["email_address"].strip()
    msg["From"] = addr
    msg["To"] = addr
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=60) as s:
        s.login(addr, _clean_password(cfg["email_app_password"]))
        s.send_message(msg)


def friendly_error(err):
    text = str(err)
    if isinstance(err, smtplib.SMTPAuthenticationError) or "535" in text or "Username and Password" in text:
        return ("Gmail refused the sign-in. Use an app password (16 letters from myaccount.google.com/apppasswords), "
                "not your normal Gmail password. 2-Step Verification must be on.")
    if "getaddrinfo" in text or "timed out" in text or "Network is unreachable" in text:
        return "Could not reach Gmail. Check the internet connection (some college networks block email sending)."
    return text.split("\n")[0][:300]


def send_test(cfg):
    msg = EmailMessage()
    msg["Subject"] = "AI Lab test email"
    msg.set_content("This is a test from AI Lab. If you can read this on your phone, finished runs will reach you the same way.")
    try:
        _send(cfg, msg)
        return True, "Sent. Check your Gmail inbox."
    except Exception as err:  # noqa: BLE001
        return False, friendly_error(err)


def _attachments(run):
    files_dir = run / "files"
    found = []
    if files_dir.is_dir():
        paths = [p for p in sorted(files_dir.rglob("*")) if p.is_file()]
        stems = {p.stem for p in paths if p.suffix.lower() in (".docx", ".html", ".htm")}
        for p in paths:
            if p.suffix.lower() in SKIP_SUFFIX:
                continue
            # the .md text is the source of the Word file, so skip it when a Word or web copy exists
            if p.suffix.lower() == ".md" and p.stem in stems:
                continue
            found.append(p)
    for name in ("accessibility-report.md", "usage-report.md"):
        report = run / name
        if report.is_file():
            found.append(report)
    return found


def send_run(cfg, run, settings, ok, message, verdict_line=""):
    """Returns (ok, text). Never raises."""
    title = settings.get("title") or run.name
    msg = EmailMessage()
    lines = []
    attached, skipped = [], []
    if ok:
        msg["Subject"] = f"AI Lab finished: {title}"
        lines.append(f"Your run \"{title}\" is finished.")
        total = 0
        for p in _attachments(run):
            size = p.stat().st_size
            if total + size > MAX_BYTES:
                skipped.append(p.name)
                continue
            total += size
            attached.append(p)
        if verdict_line:
            lines.append(verdict_line)
        summary = run / "summary.md"
        if summary.is_file():
            try:
                lines.append("")
                lines.append(summary.read_text(encoding="utf-8-sig")[:1800])
            except OSError:
                pass
        lines.append("")
        lines.append("Attached: " + (", ".join(p.name for p in attached) or "nothing"))
        if skipped:
            lines.append("Too large to attach (find them on the computer): " + ", ".join(skipped))
        lines.append("Accessibility note: the scan is a screening, not a conformance test. Check with a keyboard and a screen reader before sharing.")
    else:
        msg["Subject"] = f"AI Lab stopped: {title}"
        lines.append(f"Your run \"{title}\" stopped.")
        lines.append(message or "")
        lines.append("Open AI Lab on the computer and press Retry. Finished steps are kept.")
    lines.append("")
    lines.append(f"Folder on the computer: {run}")
    msg.set_content("\n".join(lines))
    for p in attached:
        suffix = p.suffix.lower()
        kind = {
            ".docx": ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ".html": ("text", "html"), ".htm": ("text", "html"),
            ".md": ("text", "plain"), ".txt": ("text", "plain"), ".csv": ("text", "csv"),
        }.get(suffix, ("application", "octet-stream"))
        data = p.read_bytes()
        name = p.name
        if p.name in ("accessibility-report.md", "usage-report.md"):
            name = p.name[:-3] + ".txt"
            kind = ("text", "plain")
        msg.add_attachment(data, maintype=kind[0], subtype=kind[1], filename=name)
    try:
        _send(cfg, msg)
        return True, f"Emailed to {cfg['email_address']}"
    except Exception as err:  # noqa: BLE001
        return False, friendly_error(err)
