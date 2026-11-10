"""
convert.py  File helpers for AI Lab.

Reading (so the AIs can see your files):
  extract_text(path)  -> (text, note)   .pdf .docx .txt .md .html .csv .json .tex and similar
Writing (so you get finished files):
  md_to_docx(markdown_text, out_path)   Word document from Markdown
  md_to_html(markdown_text, title)      styled web page text from Markdown
  html_to_pdf(html_path, pdf_path)      PDF made with Microsoft Edge (or Chrome) in the background

Needs: python-docx, pypdf, markdown   (the installer adds them)
"""
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TEXT_EXT = {".txt", ".md", ".markdown", ".html", ".htm", ".css", ".csv", ".json", ".tex", ".svg", ".xml"}
SUPPORTED_INPUT = sorted(TEXT_EXT | {".pdf", ".docx"})
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ---------------------------------------------------------------- reading

def extract_text(path):
    """Return (text, note). note is '' when all is well, or a short warning for the person."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf_text(path)
    if ext == ".docx":
        return _docx_text(path)
    if ext == ".doc":
        return "", "Old .doc files are not supported. In Word choose Save As and pick .docx, then attach that."
    if ext in TEXT_EXT:
        return path.read_text(encoding="utf-8-sig", errors="replace"), ""
    return "", f"Skipped {path.name}: {ext or 'this'} files are not supported."


def _pdf_text(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", "PDF reading needs the pypdf package. Run the installer again."
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return "", f"{path.name} is password protected."
        pages = []
        for number, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            pages.append(f"[Page {number}]\n{text}")
    except Exception as err:
        return "", f"Could not read {path.name}: {err}"
    body = "\n\n".join(pages)
    letters = len(re.findall(r"[A-Za-z]", body))
    note = ""
    if letters < 40 * max(1, len(pages)) / 4:
        note = (f"{path.name} has almost no selectable text. It is probably a scan or pictures of pages. "
                "The AIs cannot read pictures here. Use a text version if you can.")
    return body, note


def _iter_docx_blocks(doc):
    """Yield paragraphs and tables in the order they appear in the document."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(child, doc)
        elif tag == "tbl":
            yield Table(child, doc)


def _docx_text(path):
    try:
        import docx
    except ImportError:
        return "", "Word reading needs the python-docx package. Run the installer again."
    try:
        doc = docx.Document(str(path))
    except Exception as err:
        return "", f"Could not read {path.name}: {err}"
    lines = []
    for block in _iter_docx_blocks(doc):
        if block.__class__.__name__ == "Paragraph":
            text = block.text.strip()
            if not text:
                lines.append("")
                continue
            style = (block.style.name or "") if block.style is not None else ""
            m = re.match(r"Heading (\d)", style)
            if m:
                lines.append("#" * int(m.group(1)) + " " + text)
            elif style == "Title":
                lines.append("# " + text)
            elif "List Bullet" in style:
                lines.append("- " + text)
            elif "List Number" in style:
                lines.append("1. " + text)
            else:
                lines.append(text)
        else:
            rows = []
            for row in block.rows:
                cells = []
                for cell in row.cells:
                    cells.append(cell.text.strip().replace("\n", " ").replace("|", "/"))
                rows.append(cells)
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                lines.append("")
                lines.append("| " + " | ".join(rows[0]) + " |")
                lines.append("|" + "---|" * width)
                for r in rows[1:]:
                    lines.append("| " + " | ".join(r) + " |")
                lines.append("")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    note = ""
    if not text:
        note = f"{path.name} had no readable text."
    return text, note


# ---------------------------------------------------------------- Word output

INLINE_RE = re.compile(r"(\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))")


def _add_runs(paragraph, text):
    """Add text to a Word paragraph, honoring **bold**, *italic*, `code`, and [links](url)."""
    from docx.shared import Pt
    for piece in INLINE_RE.split(text):
        if not piece:
            continue
        if piece.startswith("***") and piece.endswith("***") and len(piece) > 6:
            run = paragraph.add_run(piece[3:-3]); run.bold = True; run.italic = True
        elif (piece.startswith("**") and piece.endswith("**") and len(piece) > 4) or \
             (piece.startswith("__") and piece.endswith("__") and len(piece) > 4):
            run = paragraph.add_run(piece[2:-2]); run.bold = True
        elif piece.startswith("*") and piece.endswith("*") and len(piece) > 2:
            run = paragraph.add_run(piece[1:-1]); run.italic = True
        elif piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
            run = paragraph.add_run(piece[1:-1]); run.font.name = "Consolas"; run.font.size = Pt(10)
        elif piece.startswith("[") and "](" in piece:
            label, url = piece[1:-1].split("](", 1)
            paragraph.add_run(f"{label} ({url})")
        else:
            paragraph.add_run(piece)


def _split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_separator(line):
    return bool(re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", line)) and "-" in line


def md_to_docx(markdown_text, out_path, title=None):
    """Write a Word document from Markdown. Handles headings, paragraphs, bullet and numbered
    lists (two levels), tables, block quotes, code blocks, rules, bold, italic, code, links."""
    import docx
    from docx.enum.text import WD_BREAK
    from docx.shared import Pt, Inches, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    document = docx.Document()
    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    for section in document.sections:
        section.left_margin = section.right_margin = Inches(1)
        section.top_margin = section.bottom_margin = Inches(1)
    first_heading = next((re.sub(r"[#*`_]", "", ln).strip() for ln in markdown_text.splitlines() if ln.startswith("# ")), "")
    document.core_properties.title = title or first_heading or "Document"
    document.core_properties.language = "en-US"
    document.core_properties.author = "AI Lab"
    lang = OxmlElement("w:lang")
    lang.set(qn("w:val"), "en-US")
    normal.element.get_or_add_rPr().append(lang)

    lines = markdown_text.replace("\r\n", "\n").split("\n")
    counters = [0, 0]
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        is_list_line = bool(re.match(r"^\s*([-*+]|\d+[.)])\s+", line))
        if stripped and not is_list_line:
            counters = [0, 0]

        if stripped.startswith("```"):
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            para = document.add_paragraph()
            run = para.add_run("\n".join(code))
            run.font.name = "Consolas"; run.font.size = Pt(9.5)
            para.paragraph_format.left_indent = Inches(0.3)
            continue

        if not stripped:
            i += 1
            continue

        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            para = document.add_paragraph()
            pPr = para._p.get_or_add_pPr()
            border = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            for key, val in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "999999")):
                bottom.set(qn(key), val)
            border.append(bottom); pPr.append(border)
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", stripped)
        if m:
            level = len(m.group(1))
            heading = document.add_heading(level=min(level, 4) if level > 1 else 1)
            _add_runs(heading, m.group(2))
            i += 1
            continue

        if "|" in stripped and i + 1 < len(lines) and _is_separator(lines[i + 1]):
            header = _split_row(stripped)
            rows = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            width = max([len(header)] + [len(r) for r in rows])
            table = document.add_table(rows=1 + len(rows), cols=width)
            table.style = "Table Grid"
            for c in range(width):
                cell = table.rows[0].cells[c]
                cell.text = ""
                para = cell.paragraphs[0]
                _add_runs(para, header[c] if c < len(header) else "")
                for run in para.runs:
                    run.bold = True
                shade = OxmlElement("w:shd")
                shade.set(qn("w:val"), "clear"); shade.set(qn("w:color"), "auto"); shade.set(qn("w:fill"), "E7EEF7")
                cell._tc.get_or_add_tcPr().append(shade)
            for r, row in enumerate(rows, 1):
                for c in range(width):
                    cell = table.rows[r].cells[c]
                    cell.text = ""
                    _add_runs(cell.paragraphs[0], row[c] if c < len(row) else "")
            # header row repeats on each page
            trPr = table.rows[0]._tr.get_or_add_trPr()
            repeat = OxmlElement("w:tblHeader"); repeat.set(qn("w:val"), "true"); trPr.append(repeat)
            document.add_paragraph()
            continue

        m = re.match(r"^(\s*)([-*+])\s+(.*)$", line)
        n = re.match(r"^(\s*)(\d+)[.)]\s+(.*)$", line)
        if m or n:
            hit = m or n
            indent = len(hit.group(1).replace("\t", "    "))
            deep = indent >= 2
            depth = 1 if deep else 0
            if m:
                para = document.add_paragraph(style="List Bullet 2" if deep else "List Bullet")
                _add_runs(para, hit.group(3))
            else:
                counters[depth] += 1
                if depth == 0:
                    counters[1] = 0
                para = document.add_paragraph()
                para.paragraph_format.left_indent = Inches(0.5 + 0.3 * depth)
                para.paragraph_format.first_line_indent = Inches(-0.25)
                para.paragraph_format.space_after = Pt(3)
                para.add_run(f"{counters[depth]}.\t")
                para.paragraph_format.tab_stops.add_tab_stop(Inches(0.5 + 0.3 * depth))
                _add_runs(para, hit.group(3))
            i += 1
            continue

        if stripped.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            para = document.add_paragraph()
            _add_runs(para, " ".join(quote))
            para.paragraph_format.left_indent = Inches(0.4)
            for run in para.runs:
                run.italic = True
                run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
            continue

        # paragraph: gather following plain lines
        block = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(("#", "```", ">", "|")) or re.match(r"^([-*+]|\d+[.)])\s+", nxt)
                    or re.match(r"^(-{3,}|\*{3,}|_{3,})$", nxt)):
                break
            block.append(nxt)
            i += 1
        para = document.add_paragraph()
        _add_runs(para, " ".join(block))

    document.save(str(out_path))
    return Path(out_path)


# ---------------------------------------------------------------- web page and PDF output

PAGE_CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1d2733;--muted:#5a6675;--line:#d5dce5;--accent:#1a5fb4;--head:#eef3f9;--code:#f3f5f8}
@media (prefers-color-scheme:dark){:root{--bg:#14181e;--fg:#e6ebf1;--muted:#a3adba;--line:#2e3641;--accent:#7db3ff;--head:#1d2531;--code:#1b2029}}
@media print{:root{--bg:#fff;--fg:#000;--muted:#333;--line:#999;--accent:#000;--head:#eee;--code:#f4f4f4}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 Calibri,"Segoe UI",system-ui,sans-serif}
main{max-width:46rem;margin:0 auto;padding:2rem 1rem 4rem}
h1,h2,h3,h4{line-height:1.25;margin:1.6em 0 .5em}h1{font-size:2rem;margin-top:0}h2{font-size:1.5rem;border-bottom:1px solid var(--line);padding-bottom:.2em}
a{color:var(--accent)}table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border:1px solid var(--line);padding:.45rem .6rem;text-align:left;vertical-align:top}th{background:var(--head)}
code{background:var(--code);padding:.1em .3em;border-radius:3px;font-family:Consolas,monospace;font-size:.92em}pre{background:var(--code);padding:.8rem;overflow:auto;border-radius:6px}pre code{padding:0;background:none}
blockquote{margin:1rem 0;padding:.2rem 1rem;border-left:4px solid var(--line);color:var(--muted)}hr{border:0;border-top:1px solid var(--line);margin:2rem 0}
tr,img,pre{break-inside:avoid}h1,h2,h3{break-after:avoid}
"""


def _prepare_markdown(text):
    """python-markdown wants 4 spaces for nested lists and a blank line before a list. AIs write 2 spaces."""
    out, prev_kind, in_code = [], "blank", False
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append(line)
            prev_kind = "other"
            continue
        if in_code:
            out.append(line)
            continue
        m = re.match(r"^( *)([-*+]|\d+[.)])\s+", line)
        if m:
            indent = len(m.group(1))
            if 1 <= indent < 4:
                line = " " * 4 + line[indent:]
            elif indent >= 4:
                line = " " * (indent * 2 if indent < 8 else indent) + line[indent:]
            if prev_kind == "para":
                out.append("")
            prev_kind = "list"
        elif not line.strip():
            prev_kind = "blank"
        else:
            prev_kind = "para" if not line.lstrip().startswith(("#", ">", "|")) else "other"
        out.append(line)
    return "\n".join(out)


def md_to_html(markdown_text, title="Document"):
    import markdown
    body = markdown.markdown(_prepare_markdown(markdown_text), extensions=["tables", "fenced_code", "sane_lists", "toc"])
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{html.escape(title)}</title>\n<style>{PAGE_CSS}</style>\n</head>\n<body>\n<main>\n"
        f"{body}\n</main>\n</body>\n</html>\n"
    )


def find_browser():
    """Microsoft Edge or Chrome (any OS). Returns a path or None."""
    candidates = []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LocalAppData")):
            if not base:
                continue
            candidates += [
                Path(base) / "Microsoft/Edge/Application/msedge.exe",
                Path(base) / "Google/Chrome/Application/chrome.exe",
            ]
    for name in ("msedge", "microsoft-edge", "google-chrome", "chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    extra = os.environ.get("AILAB_BROWSER")
    if extra:
        candidates.insert(0, Path(extra))
    for path in candidates:
        if path and Path(path).exists():
            return str(path)
    return None


def html_to_pdf(html_path, pdf_path, timeout=120):
    """Print an HTML file to PDF with a headless browser. Raises RuntimeError on failure."""
    browser = find_browser()
    if not browser:
        raise RuntimeError("No Microsoft Edge or Chrome found, so a PDF could not be made.")
    html_path, pdf_path = Path(html_path).resolve(), Path(pdf_path).resolve()
    if pdf_path.exists():
        pdf_path.unlink()
    profile = tempfile.mkdtemp(prefix="ailab-pdf-")
    base = [browser, "--headless", "--disable-gpu", "--no-first-run",
            "--no-pdf-header-footer", f"--user-data-dir={profile}",
            f"--print-to-pdf={pdf_path}", html_path.as_uri()]
    if os.name != "nt":
        base.insert(2, "--no-sandbox")  # needed when running as root on Linux test machines
    try:
        last = ""
        # Newer browsers use --headless; older Edge wants --headless=old or just --headless. Try both.
        for flag in ("--headless", "--headless=new"):
            cmd = list(base)
            cmd[1] = flag
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
            except subprocess.TimeoutExpired:
                last = "the browser did not finish in time"
                continue
            if pdf_path.exists() and pdf_path.stat().st_size > 500:
                return pdf_path
            last = (result.stderr or result.stdout or "no output").strip()[:300]
        raise RuntimeError(f"PDF was not created ({last})")
    finally:
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    # tiny self test:  python convert.py
    sample = "# Test\n\nHello **world**.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n- one\n- two\n"
    out = Path(tempfile.mkdtemp(prefix="ailab-selftest-"))
    md_to_docx(sample, out / "t.docx")
    (out / "t.html").write_text(md_to_html(sample, "Test"), encoding="utf-8")
    text, _ = extract_text(out / "t.docx")
    good = "world" in text
    print("docx ok" if good else "docx FAILED")
    shutil.rmtree(out, ignore_errors=True)
    sys.exit(0 if good else 1)
