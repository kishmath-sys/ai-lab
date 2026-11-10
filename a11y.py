"""
a11y.py  Automatic accessibility screening for AI Lab files.

What it is: a set of fixed, repeatable checks against common WCAG 2.1 A and AA problems, for
  - HTML files (structure, names, labels, tables, keyboard traps in markup, focus styles, contrast from CSS colors)
  - Word .docx files (title, language, heading order, table header rows, image descriptions, colors, link text)

What it is NOT: a conformance test or legal certification. It reads the files as text. It does not render
the page, so contrast is calculated from the colors written in the CSS, and it cannot judge reading order,
wording quality, or how a screen reader actually behaves. A person still has to test with a keyboard and
a screen reader before the files are published.

Use:  python a11y.py file1.html file2.docx      prints a report
"""
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path

ERROR, WARN, INFO = "error", "warning", "info"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
GENERIC_LINKS = {"click here", "here", "read more", "more", "link", "this link", "learn more", "click", "details", "this"}
NON_INTERACTIVE = {"div", "span", "p", "li", "img", "td", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}
SKIP_INPUT_TYPES = {"hidden", "submit", "reset", "button", "image"}


class Issue:
    def __init__(self, severity, rule, message, where="", wcag=""):
        self.severity, self.rule, self.message, self.where, self.wcag = severity, rule, message, where, wcag

    def line(self):
        loc = f" ({self.where})" if self.where else ""
        crit = f" [WCAG {self.wcag}]" if self.wcag else ""
        return f"{self.message}{loc}{crit}"


# ------------------------------------------------------------------ HTML tree

class Node:
    def __init__(self, tag, attrs, line, parent=None):
        self.tag, self.attrs, self.line, self.parent = tag, attrs, line, parent
        self.children, self.text, self.raw = [], [], ""

    def get(self, name, default=None):
        return self.attrs.get(name, default)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def inner_text(self):
        parts = list(self.text)
        for c in self.children:
            if c.tag in ("script", "style"):
                continue
            parts.append(c.inner_text())
        return " ".join(p for p in parts if p)

    def has_ancestor(self, *tags):
        n = self.parent
        while n:
            if n.tag in tags:
                return True
            n = n.parent
        return False


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, 0)
        self.cur = self.root
        self.doctype = None

    def handle_decl(self, decl):
        self.doctype = decl

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.getpos()[0], self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.getpos()[0], self.cur)
        self.cur.children.append(node)

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root:
            if n.tag == tag:
                self.cur = n.parent
                return
            n = n.parent

    def handle_data(self, data):
        if self.cur.tag in ("script", "style"):
            self.cur.raw += data
        elif data.strip():
            self.cur.text.append(re.sub(r"\s+", " ", data.strip()))


def parse_html(text):
    builder = TreeBuilder()
    builder.feed(text)
    builder.close()
    return builder


# ------------------------------------------------------------------ colors and CSS

NAMED = {"black": "#000000", "white": "#ffffff", "red": "#ff0000", "blue": "#0000ff", "green": "#008000",
         "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0", "yellow": "#ffff00", "orange": "#ffa500",
         "navy": "#000080", "maroon": "#800000", "purple": "#800080", "teal": "#008080", "lime": "#00ff00",
         "transparent": None}


def parse_color(value):
    if not value:
        return None
    v = value.strip().lower().replace("!important", "").strip()
    if v in NAMED:
        v = NAMED[v]
        if v is None:
            return None
    m = re.fullmatch(r"#([0-9a-f]{3})", v)
    if m:
        h = m.group(1)
        return tuple(int(c * 2, 16) for c in h)
    m = re.fullmatch(r"#([0-9a-f]{6})([0-9a-f]{2})?", v)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = re.fullmatch(r"rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)\s*(?:[,/]\s*[\d.]+%?)?\s*\)", v)
    if m:
        return tuple(min(255, int(m.group(i))) for i in (1, 2, 3))
    return None


def luminance(rgb):
    def chan(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def parse_css(css, context="light"):
    """Return a list of (context, selector, {property: value}). Handles nested @media blocks."""
    css = strip_comments(css)
    rules, i, n = [], 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j == -1:
            break
        head = css[i:j].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
            k += 1
        body = css[j + 1:k - 1]
        if head.startswith("@media"):
            low = head.lower()
            if "print" in low and "screen" not in low:
                inner = "print"
            elif "prefers-color-scheme" in low and "dark" in low:
                inner = "dark"
            elif "prefers-color-scheme" in low and "light" in low:
                inner = "light"
            else:
                inner = context
            rules += parse_css(body, inner)
        elif head.startswith("@"):
            pass  # @font-face, @keyframes and so on
        else:
            decls = {}
            for part in body.split(";"):
                if ":" in part:
                    prop, val = part.split(":", 1)
                    decls[prop.strip().lower()] = val.strip()
            for sel in head.split(","):
                rules.append((context, sel.strip(), decls))
        i = k
    return rules


def resolve(value, variables, depth=0):
    if value is None or depth > 5:
        return value
    m = re.search(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]+))?\)", value)
    if not m:
        return value
    repl = variables.get(m.group(1), m.group(2))
    if repl is None:
        return None
    return resolve(value[:m.start()] + repl.strip() + value[m.end():], variables, depth + 1)


def font_px(value):
    if not value:
        return None
    m = re.match(r"([\d.]+)\s*(px|rem|em|pt)", value.strip().lower())
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    return n * {"px": 1, "rem": 16, "em": 16, "pt": 1.333}[unit]


def css_checks(css_text, issues):
    rules = parse_css(css_text)
    if not rules:
        return
    contexts = sorted({r[0] for r in rules if r[0] != "print"})
    for ctx in contexts:
        variables = {}
        for c, sel, d in rules:
            if c in ("light", ctx) and re.fullmatch(r"(:root|html|body|\*)", sel.strip()) or (c == ctx and sel.strip() in (":root", "html", "body")):
                for k, v in d.items():
                    if k.startswith("--"):
                        variables[k] = v
        page_bg, page_fg = (255, 255, 255) if ctx == "light" else None, (0, 0, 0) if ctx == "light" else None
        for c, sel, d in rules:
            if c != ctx or sel.strip() not in ("html", "body", ":root", "main"):
                continue
            bg = parse_color(resolve(d.get("background-color") or d.get("background"), variables))
            fg = parse_color(resolve(d.get("color"), variables))
            if bg:
                page_bg = bg
            if fg:
                page_fg = fg
        for c, sel, d in rules:
            if c != ctx:
                continue
            fg = parse_color(resolve(d.get("color"), variables))
            bgv = d.get("background-color") or d.get("background")
            bg = parse_color(resolve(bgv, variables)) if bgv and "gradient" not in bgv and "url(" not in bgv else None
            fg_use, bg_use = fg or page_fg, bg or page_bg
            if not (fg or bg) or fg_use is None or bg_use is None:
                continue
            size = font_px(d.get("font-size"))
            bold = (d.get("font-weight", "") in ("bold", "bolder", "600", "700", "800", "900"))
            need = 3.0 if (size and (size >= 24 or (size >= 18.66 and bold))) else 4.5
            ratio = contrast(fg_use, bg_use)
            if ratio + 1e-9 < need:
                label = "dark mode" if ctx == "dark" else "normal"
                issues.append(Issue(WARN, "contrast", f"Low contrast {ratio:.2f}:1 (needs {need}:1) for '{sel}' in {label} colors, "
                                    "calculated from the CSS colors", sel, "1.4.3"))
        # focus indicators
        for c, sel, d in rules:
            if c != ctx:
                continue
            if ":focus" in sel:
                continue
            outline = d.get("outline", "") + " " + d.get("outline-style", "")
            if re.search(r"\b(none|0)\b", outline) and "outline" in d or d.get("outline-width", "") == "0":
                issues.append(Issue(WARN, "focus-visible", f"'{sel}' removes the focus outline. Make sure another visible focus style exists",
                                    sel, "2.4.7"))
    text = strip_comments(css_text)
    if re.search(r"(animation|transition|scroll-behavior\s*:\s*smooth)", text) and "prefers-reduced-motion" not in text:
        issues.append(Issue(WARN, "reduced-motion", "Animations or transitions are used but there is no prefers-reduced-motion rule", "", "2.3.3 (AAA)/best practice"))
    for c, sel, d in rules:
        px = font_px(d.get("font-size"))
        if px is not None and px < 12 and c != "print":
            issues.append(Issue(WARN, "small-text", f"Text size under 12px for '{sel}'", sel, "1.4.4"))
        for prop in ("min-height", "height", "min-width", "width"):
            pass
        if any(k in sel for k in ("button", ".btn", "[role=button]", "summary")):
            for prop in ("height", "min-height", "width", "min-width"):
                m = re.fullmatch(r"([\d.]+)px", d.get(prop, "").strip().lower())
                if m and float(m.group(1)) < 24:
                    issues.append(Issue(WARN, "target-size", f"'{sel}' sets {prop} under 24px, which is below the minimum target size", sel, "2.5.8"))
    body_lh = None
    for c, sel, d in rules:
        if sel.strip() in ("body", "html", ":root") and "line-height" in d:
            body_lh = d["line-height"]
    if body_lh:
        m = re.fullmatch(r"([\d.]+)", body_lh.strip())
        if m and float(m.group(1)) < 1.4:
            issues.append(Issue(INFO, "line-height", f"Body line height {body_lh} is tight. 1.5 is recommended", "body", "1.4.12"))


# ------------------------------------------------------------------ HTML checks

def accessible_name(node, ids):
    labelled = node.get("aria-labelledby")
    if labelled:
        parts = []
        for ref in labelled.split():
            target = ids.get(ref)
            if target:
                parts.append(target.inner_text())
        if any(p.strip() for p in parts):
            return " ".join(parts).strip()
    if node.get("aria-label", "").strip():
        return node.get("aria-label").strip()
    if node.tag == "img":
        return node.get("alt", "").strip()
    if node.tag == "input":
        t = node.get("type", "text").lower()
        if t in ("button", "submit", "reset"):
            return node.get("value", "").strip() or node.get("title", "").strip()
        if t == "image":
            return node.get("alt", "").strip()
    pieces = [node.inner_text()]
    for d in node.walk():
        if d is not node and d.tag == "img":
            pieces.append(d.get("alt", ""))
        if d is not node and d.get("aria-label"):
            pieces.append(d.get("aria-label"))
        if d is not node and d.tag == "svg":
            for t in d.children:
                if t.tag == "title":
                    pieces.append(t.inner_text())
    name = " ".join(p for p in pieces if p).strip()
    return name or node.get("title", "").strip()


def html_checks(text):
    issues = []
    builder = parse_html(text)
    root = builder.root
    nodes = list(root.walk())
    ids, dup = {}, set()
    for n in nodes:
        i = n.get("id")
        if i:
            if i in ids:
                dup.add(i)
            ids[i] = n
    for i in sorted(dup):
        issues.append(Issue(ERROR, "duplicate-id", f"The id '{i}' is used more than once", f"id={i}", "4.1.2"))

    if not builder.doctype or "html" not in builder.doctype.lower():
        issues.append(Issue(WARN, "doctype", "No <!doctype html>. Browsers may use quirks mode", "", "best practice"))
    html = next((n for n in nodes if n.tag == "html"), None)
    lang = (html.get("lang", "") if html else "").strip()
    if not lang:
        issues.append(Issue(ERROR, "lang", "The <html> element has no lang attribute", "", "3.1.1"))
    elif not re.fullmatch(r"[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*", lang):
        issues.append(Issue(WARN, "lang", f"The lang value '{lang}' does not look valid", "", "3.1.1"))
    title = next((n for n in nodes if n.tag == "title" and n.has_ancestor("head")), None)
    if not title or not title.inner_text().strip():
        issues.append(Issue(ERROR, "title", "The page has no <title> or it is empty", "", "2.4.2"))
    for n in nodes:
        if n.tag == "meta" and n.get("name", "").lower() == "viewport":
            content = n.get("content", "").lower().replace(" ", "")
            if "user-scalable=no" in content or "user-scalable=0" in content:
                issues.append(Issue(ERROR, "zoom", "The viewport blocks pinch zoom (user-scalable=no)", f"line {n.line}", "1.4.4"))
            m = re.search(r"maximum-scale=([\d.]+)", content)
            if m and float(m.group(1)) < 2:
                issues.append(Issue(ERROR, "zoom", f"The viewport limits zoom (maximum-scale={m.group(1)})", f"line {n.line}", "1.4.4"))
        if n.tag == "meta" and n.get("http-equiv", "").lower() == "refresh":
            issues.append(Issue(ERROR, "refresh", "The page refreshes or redirects automatically", f"line {n.line}", "2.2.1"))
        if n.tag in ("marquee", "blink"):
            issues.append(Issue(ERROR, "moving-content", f"<{n.tag}> blinking or scrolling content", f"line {n.line}", "2.2.2"))

    body = next((n for n in nodes if n.tag == "body"), root)
    if not any(n.tag == "main" or n.get("role") == "main" for n in nodes):
        issues.append(Issue(WARN, "landmark-main", "There is no <main> landmark", "", "1.3.1 / 2.4.1"))
    links_total = sum(1 for n in nodes if n.tag == "a" and n.get("href"))
    first_link = next((n for n in nodes if n.tag == "a" and n.get("href")), None)
    has_skip = bool(first_link and first_link.get("href", "").startswith("#") and re.search(r"skip|main|content", first_link.inner_text(), re.I))
    if links_total >= 5 and not has_skip and any(n.tag == "nav" for n in nodes):
        issues.append(Issue(INFO, "skip-link", "There is a navigation block but no 'skip to main content' link at the top", "", "2.4.1"))

    # headings
    headings = [n for n in nodes if re.fullmatch(r"h[1-6]", n.tag)]
    h1s = [h for h in headings if h.tag == "h1"]
    if not h1s:
        issues.append(Issue(ERROR, "h1", "The page has no <h1> heading", "", "1.3.1 / 2.4.6"))
    elif len(h1s) > 1:
        issues.append(Issue(WARN, "h1", f"The page has {len(h1s)} <h1> headings. One is expected", f"line {h1s[1].line}", "1.3.1"))
    prev = 0
    for h in headings:
        level = int(h.tag[1])
        if not accessible_name(h, ids):
            issues.append(Issue(ERROR, "empty-heading", f"Empty <{h.tag}> heading", f"line {h.line}", "2.4.6"))
        if prev and level > prev + 1:
            issues.append(Issue(WARN, "heading-order", f"Heading level jumps from h{prev} to h{level}: '{h.inner_text()[:40]}'", f"line {h.line}", "1.3.1"))
        prev = level

    # images and svg
    for n in nodes:
        if n.tag == "img":
            if "alt" not in n.attrs and n.get("role") not in ("presentation", "none") and n.get("aria-hidden") != "true":
                issues.append(Issue(ERROR, "img-alt", "An image has no alt attribute", f"line {n.line}", "1.1.1"))
            elif re.search(r"\.(png|jpe?g|gif|svg|webp)$", n.get("alt", "").strip(), re.I) or n.get("alt", "").strip().lower() in ("image", "picture", "photo", "graphic"):
                issues.append(Issue(WARN, "img-alt", f"The alt text '{n.get('alt')}' does not describe the image", f"line {n.line}", "1.1.1"))
        if n.tag == "svg" and n.get("aria-hidden") != "true" and n.get("role") in ("img",) and not accessible_name(n, ids):
            issues.append(Issue(ERROR, "svg-name", "An <svg role=img> has no accessible name", f"line {n.line}", "1.1.1"))
        if n.tag == "svg" and n.get("aria-hidden") != "true" and n.get("role") is None and not accessible_name(n, ids) and not n.has_ancestor("button", "a"):
            issues.append(Issue(WARN, "svg-name", "An <svg> has no role or name. Add role=img with a label, or aria-hidden=true if decorative", f"line {n.line}", "1.1.1"))

    # links and buttons
    for n in nodes:
        if n.tag == "a" and n.get("href") is not None:
            name = accessible_name(n, ids)
            if not name:
                issues.append(Issue(ERROR, "link-name", "A link has no text or accessible name", f"line {n.line}", "2.4.4 / 4.1.2"))
            elif name.lower().strip(" .") in GENERIC_LINKS:
                issues.append(Issue(WARN, "link-purpose", f"Link text '{name}' does not say where it goes", f"line {n.line}", "2.4.4"))
            if n.get("href", "").strip() in ("#", "") and n.get("onclick"):
                issues.append(Issue(WARN, "link-as-button", "A link with href='#' and a click handler should be a <button>", f"line {n.line}", "4.1.2"))
        if n.tag == "a" and n.get("href") is None and n.get("onclick"):
            issues.append(Issue(ERROR, "keyboard", "A link without href is clickable but not keyboard focusable", f"line {n.line}", "2.1.1"))
        if n.tag == "button" and not accessible_name(n, ids):
            issues.append(Issue(ERROR, "button-name", "A button has no text or accessible name", f"line {n.line}", "4.1.2"))
        if n.tag in NON_INTERACTIVE and (n.get("onclick") or n.get("onkeydown")) and n.get("role") not in ("button", "link", "checkbox", "tab", "switch", "menuitem") :
            issues.append(Issue(ERROR, "keyboard", f"A <{n.tag}> has a click handler but no button role. Use a real <button>", f"line {n.line}", "2.1.1 / 4.1.2"))
        if n.tag in NON_INTERACTIVE and n.get("role") in ("button", "link") and n.get("tabindex") is None:
            issues.append(Issue(ERROR, "keyboard", f"A <{n.tag}> with role={n.get('role')} is not focusable (no tabindex)", f"line {n.line}", "2.1.1"))
        if n.get("onmouseover") and not n.get("onfocus"):
            issues.append(Issue(WARN, "hover-only", "Something appears on hover but there is no focus equivalent", f"line {n.line}", "1.4.13 / 2.1.1"))
        ti = n.get("tabindex")
        if ti and re.fullmatch(r"\d+", ti.strip()) and int(ti) > 0:
            issues.append(Issue(WARN, "tabindex", f"tabindex={ti} changes the natural tab order", f"line {n.line}", "2.4.3"))
        if n.get("aria-hidden") == "true" and (n.tag in ("a", "button", "input", "select", "textarea") or (n.get("tabindex") not in (None, "-1"))):
            issues.append(Issue(ERROR, "aria-hidden-focus", f"A focusable <{n.tag}> is hidden from assistive technology with aria-hidden", f"line {n.line}", "4.1.2"))
        for attr in ("aria-labelledby", "aria-describedby", "aria-controls"):
            for ref in n.get(attr, "").split():
                if ref not in ids:
                    issues.append(Issue(ERROR, "aria-ref", f"{attr} points to a missing id '{ref}'", f"line {n.line}", "1.3.1 / 4.1.2"))
        if n.tag == "details" and not any(c.tag == "summary" for c in n.children):
            issues.append(Issue(WARN, "summary", "<details> has no <summary>", f"line {n.line}", "4.1.2"))
        if n.tag == "iframe" and not (n.get("title") or n.get("aria-label")):
            issues.append(Issue(ERROR, "iframe-title", "An <iframe> has no title", f"line {n.line}", "4.1.2"))
        if n.tag in ("video", "audio"):
            if "autoplay" in n.attrs:
                issues.append(Issue(ERROR, "autoplay", f"<{n.tag}> plays automatically", f"line {n.line}", "1.4.2"))
            if n.tag == "video" and not any(c.tag == "track" for c in n.walk()):
                issues.append(Issue(WARN, "captions", "A <video> has no captions track", f"line {n.line}", "1.2.2"))

    # form controls
    labels_for = {n.get("for") for n in nodes if n.tag == "label" and n.get("for")}
    for n in nodes:
        if n.tag in ("input", "select", "textarea"):
            if n.tag == "input" and n.get("type", "text").lower() in SKIP_INPUT_TYPES:
                continue
            has = (n.get("id") in labels_for or n.has_ancestor("label") or n.get("aria-label", "").strip()
                   or n.get("aria-labelledby") or n.get("title"))
            if not has:
                extra = " Placeholder text is not a label." if n.get("placeholder") else ""
                issues.append(Issue(ERROR, "form-label", f"A form field has no label.{extra}", f"line {n.line}", "1.3.1 / 3.3.2 / 4.1.2"))
        if n.tag == "label" and n.get("for") and n.get("for") not in ids:
            issues.append(Issue(WARN, "label-for", f"A label points to a missing id '{n.get('for')}'", f"line {n.line}", "1.3.1"))

    # tables
    for t in (n for n in nodes if n.tag == "table"):
        if t.get("role") in ("presentation", "none"):
            continue
        ths = [d for d in t.walk() if d.tag == "th"]
        if not ths:
            issues.append(Issue(ERROR, "table-headers", "A data table has no header cells (<th>)", f"line {t.line}", "1.3.1"))
        else:
            if not any(d.get("scope") or d.get("headers") for d in ths) and len({d.parent for d in ths}) > 1:
                issues.append(Issue(WARN, "table-scope", "Table headers have no scope attribute", f"line {t.line}", "1.3.1"))
        has_caption = any(c.tag == "caption" and c.inner_text().strip() for c in t.walk()) or t.get("aria-label") or t.get("aria-labelledby")
        if not has_caption:
            issues.append(Issue(WARN, "table-caption", "A table has no caption or label", f"line {t.line}", "1.3.1 / 2.4.6"))

    # scripts and live updates
    scripts = [n for n in nodes if n.tag == "script"]
    inline_js = any(s.raw.strip() for s in scripts)
    if inline_js:
        has_live = any(n.get("aria-live") or n.get("role") in ("status", "alert", "log") for n in nodes) or "aria-live" in " ".join(s.raw for s in scripts)
        if not has_live:
            issues.append(Issue(WARN, "live-region", "The page uses JavaScript but has no live region (role=status or aria-live) to announce changes", "", "4.1.3"))
        for s in scripts:
            if re.search(r"setTimeout|setInterval", s.raw):
                issues.append(Issue(INFO, "timing", "Timers are used. Check that nothing expires or moves without user control", f"line {s.line}", "2.2.1 / 2.2.2"))
                break
        if not any(n.tag == "noscript" for n in nodes):
            issues.append(Issue(INFO, "no-js", "Check that the content is still readable if JavaScript is off", "", "best practice"))
        if re.search(r"alert\(|confirm\(|prompt\(", " ".join(s.raw for s in scripts)):
            issues.append(Issue(WARN, "dialogs", "Browser alert(), confirm() or prompt() dialogs are used. Prefer on-page messages", "", "3.3.1 / best practice"))

    # css
    css_total = "\n".join(n.raw for n in nodes if n.tag == "style")
    for n in nodes:
        if n.get("style"):
            css_total += f"\n[inline] {{ {n.get('style')} }}"
    css_checks(css_total, issues)
    if css_total.strip() and not re.search(r":focus", css_total) and re.search(r"outline\s*:\s*(none|0)", css_total):
        issues.append(Issue(WARN, "focus-visible", "Outlines are removed and no :focus style exists", "", "2.4.7"))
    return issues


# ------------------------------------------------------------------ Word checks

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_checks(path):
    import xml.etree.ElementTree as ET
    issues = []
    try:
        z = zipfile.ZipFile(path)
        doc = ET.fromstring(z.read("word/document.xml"))
        styles = ET.fromstring(z.read("word/styles.xml")) if "word/styles.xml" in z.namelist() else None
        core = z.read("docProps/core.xml").decode("utf-8", "replace") if "docProps/core.xml" in z.namelist() else ""
    except Exception as err:
        return [Issue(ERROR, "docx-read", f"Could not open the Word file: {err}")]
    m = re.search(r"<dc:title>(.*?)</dc:title>", core, re.S)
    if not m or not m.group(1).strip():
        issues.append(Issue(WARN, "doc-title", "The document has no title in its properties", "", "2.4.2"))
    has_lang = False
    if styles is not None:
        for el in styles.iter(W + "lang"):
            if el.get(W + "val"):
                has_lang = True
    if not has_lang:
        issues.append(Issue(WARN, "doc-language", "No document language is set", "", "3.1.1"))
    body = doc.find(W + "body")
    paragraphs = list(body.iter(W + "p"))
    prev_level, heading_count, text_len = 0, 0, 0
    empty_run = 0
    for p in paragraphs:
        style_el = p.find(f"{W}pPr/{W}pStyle")
        style = style_el.get(W + "val", "") if style_el is not None else ""
        text = "".join(t.text or "" for t in p.iter(W + "t")).strip()
        text_len += len(text)
        if not text and p.find(f".//{W}drawing") is None:
            empty_run += 1
            if empty_run == 4:
                issues.append(Issue(INFO, "empty-paragraphs", "Several empty paragraphs in a row. Use spacing settings instead", "", "1.3.1"))
        else:
            empty_run = 0
        mh = re.fullmatch(r"Heading(\d)|heading(\d)", style.replace(" ", ""))
        if mh:
            level = int(mh.group(1) or mh.group(2))
            heading_count += 1
            if not text:
                issues.append(Issue(ERROR, "empty-heading", "An empty heading paragraph", "", "2.4.6"))
            if prev_level and level > prev_level + 1:
                issues.append(Issue(WARN, "heading-order", f"Heading level jumps from {prev_level} to {level}: '{text[:40]}'", "", "1.3.1"))
            prev_level = level
        for run in p.iter(W + "r"):
            color = run.find(f"{W}rPr/{W}color")
            if color is not None and color.get(W + "val", "").lower() not in ("", "auto"):
                rgb = parse_color("#" + color.get(W + "val"))
                if rgb and contrast(rgb, (255, 255, 255)) < 4.5:
                    issues.append(Issue(WARN, "doc-contrast", f"Text color #{color.get(W + 'val')} has low contrast on white", text[:30], "1.4.3"))
                    break
            size = run.find(f"{W}rPr/{W}sz")
            if size is not None and size.get(W + "val", "").isdigit() and int(size.get(W + "val")) < 20:
                issues.append(Issue(INFO, "doc-size", "Text smaller than 10 pt", text[:30], "1.4.4"))
                break
    if heading_count == 0 and text_len > 1500:
        issues.append(Issue(WARN, "doc-headings", "A long document has no real heading styles", "", "1.3.1 / 2.4.6"))
    for i, tbl in enumerate(body.iter(W + "tbl"), 1):
        first = tbl.find(W + "tr")
        header = first is not None and first.find(f"{W}trPr/{W}tblHeader") is not None
        if not header:
            issues.append(Issue(WARN, "table-header-row", f"Table {i} has no header row marked", "", "1.3.1"))
        if tbl.find(f".//{W}gridSpan") is not None or tbl.find(f".//{W}vMerge") is not None:
            issues.append(Issue(WARN, "table-merged", f"Table {i} has merged cells, which are hard for screen readers", "", "1.3.1"))
    for dp in doc.iter("{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr"):
        if not (dp.get("descr") or "").strip() and dp.get("hidden") != "1":
            issues.append(Issue(ERROR, "doc-alt", "An image or shape has no alternative text", dp.get("name", ""), "1.1.1"))
    for h in doc.iter(W + "hyperlink"):
        t = "".join(x.text or "" for x in h.iter(W + "t")).strip().lower()
        if t in GENERIC_LINKS:
            issues.append(Issue(WARN, "link-purpose", f"Link text '{t}' does not say where it goes", "", "2.4.4"))
    return issues


# ------------------------------------------------------------------ report

def scan_file(path):
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".html", ".htm"):
        return html_checks(path.read_text(encoding="utf-8-sig", errors="replace"))
    if ext == ".docx":
        return docx_checks(path)
    return None


def scan_files(paths):
    """Returns (results, errors, warnings). results is a list of (name, issues)."""
    results, errs, warns = [], 0, 0
    for p in paths:
        issues = scan_file(p)
        if issues is None:
            continue
        results.append((Path(p).name, issues))
        errs += sum(1 for i in issues if i.severity == ERROR)
        warns += sum(1 for i in issues if i.severity == WARN)
    return results, errs, warns


DISCLAIMER = (
    "This is an automatic screening against common WCAG 2.1 A and AA problems. It is not an ADA or WCAG conformance "
    "test and not legal advice. It reads the files as text and does not render them: contrast is calculated from the "
    "colors written in the CSS, and it cannot judge reading order, wording, or how a screen reader or keyboard actually "
    "behaves. Test the final files with a keyboard and a screen reader before publishing."
)


def report_markdown(results, errs, warns, title="Accessibility scan"):
    lines = [f"# {title}", "", DISCLAIMER, ""]
    if not results:
        lines.append("No HTML or Word files were found to scan.")
        return "\n".join(lines) + "\n"
    lines += [f"Totals: {errs} error(s), {warns} warning(s) across {len(results)} file(s).", ""]
    for name, issues in results:
        e = [i for i in issues if i.severity == ERROR]
        w = [i for i in issues if i.severity == WARN]
        n = [i for i in issues if i.severity == INFO]
        lines.append(f"## {name}")
        lines.append("")
        if not issues:
            lines.append("No problems found by the automatic checks.")
            lines.append("")
            continue
        for label, group in (("Errors (fix these)", e), ("Warnings (review)", w), ("Notes", n)):
            if group:
                lines.append(f"**{label}**")
                lines += [f"- {i.line()}" for i in group]
                lines.append("")
    return "\n".join(lines) + "\n"


def main(argv):
    results, errs, warns = scan_files(argv)
    print(report_markdown(results, errs, warns))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
