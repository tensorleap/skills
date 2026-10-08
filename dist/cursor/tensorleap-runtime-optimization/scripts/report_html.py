"""The published form of the runtime report: one self-contained HTML file (no external assets,
so it can be mailed or posted as is) rendered from report.md, with collapsed appendices, plus the
closing line that lists the run's files.

Stdlib only. Imported by tl_perf.py (`tl_perf report`)."""
import html
import json
import os
import re


# --------------------------------------------------------------------------- #
# Run files: what the skill left in the output folder
# --------------------------------------------------------------------------- #

def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


RUN_FILES = ("preflight.json", "floor.json", "fit.json", "static.json", "baseline/", "runs/", "score.json",
             "compare.json", "optimization-log.md", "online/analysis.json")


def run_files_line(out):
    found = [f for f in RUN_FILES if os.path.exists(os.path.join(out, f.rstrip("/")))]
    analysis = _load(os.path.join(out, "online", "analysis.json")) or {}
    job = analysis.get("push_job") or analysis.get("job")
    if job and os.path.isdir(os.path.join(out, "online", job)):
        found.insert(found.index("online/analysis.json"), "online/%s/" % job)
    if not found:
        return ""
    return "_Run files, in `tensorleap/runtime-optimization/`: %s._" % " · ".join("`%s`" % f for f in found)


# --------------------------------------------------------------------------- #
# Markdown (the subset report.md uses) -> one self-contained HTML page
# --------------------------------------------------------------------------- #

CSS = """
:root { --bg:#ffffff; --fg:#1d2433; --muted:#5b6475; --line:#e3e7ee; --head:#f4f6fa; --accent:#2f5bea;
        --code:#f1f3f7; --chip:#eef2ff; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#12151c; --fg:#e6e9ef; --muted:#9aa3b2; --line:#2a303c; --head:#1a1f29; --accent:#7c9bff;
          --code:#1d222c; --chip:#1e2540; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 1120px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 28px; margin: 0 0 8px; }
h2 { font-size: 21px; margin: 40px 0 12px; padding-top: 12px; border-top: 1px solid var(--line); }
h3 { font-size: 17px; margin: 28px 0 10px; }
p, li { color: var(--fg); }
.muted { color: var(--muted); font-size: 13px; }
a { color: var(--accent); }
code { background: var(--code); padding: 1px 5px; border-radius: 4px; font-size: 12.5px;
       font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; word-break: break-word; }
.table { overflow-x: auto; margin: 10px 0 16px; border: 1px solid var(--line); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { background: var(--head); font-weight: 600; white-space: nowrap; }
tr:last-child td { border-bottom: none; }
ul { padding-left: 22px; margin: 6px 0 12px; }
li { margin: 3px 0; }
nav { background: var(--head); border: 1px solid var(--line); border-radius: 8px; padding: 12px 16px; margin: 18px 0; }
nav ol { margin: 4px 0 0; padding-left: 20px; font-size: 14px; }
nav ol ul { list-style: none; padding-left: 0; margin: 2px 0 8px; display: flex; flex-wrap: wrap; gap: 2px 14px;
            font-size: 13px; }
details { margin: 28px 0 10px; border: 1px solid var(--line); border-radius: 8px; padding: 2px 14px 8px; }
summary { cursor: pointer; font-weight: 600; font-size: 17px; padding: 8px 0; }
@media (max-width: 640px) { h1 { font-size: 23px; } }
"""


def _inline(text):
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", lambda m: "<code>%s</code>" % m.group(1), out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])_([^_]+?)_(?![\w])", r"<em>\1</em>", out)
    return out


def _slug(text, used):
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "section"
    slug, i = base, 2
    while slug in used:
        slug, i = "%s-%d" % (base, i), i + 1
    used.add(slug)
    return slug


def _table(rows):
    cells = [[c.strip() for c in re.split(r"(?<!\\)\|", r.strip().strip("|"))] for r in rows]
    head, body = cells[0], [c for c in cells[2:]]
    out = ['<div class="table"><table><thead><tr>%s</tr></thead><tbody>' %
           "".join("<th>%s</th>" % _inline(c.replace("\\|", "|")) for c in head)]
    for r in body:
        out.append("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c.replace("\\|", "|")) for c in r))
    out.append("</tbody></table></div>")
    return "".join(out)


def markdown_to_html(md, title, subtitle=""):
    lines = md.splitlines()
    body, toc, used, details = [], [], set(), []
    para, list_stack = [], []          # list_stack: indent levels of open <ul>

    def flush_para():
        if para:
            body.append("<p>%s</p>" % _inline(" ".join(para)))
            del para[:]

    def close_lists(to_level=-1):
        while list_stack and list_stack[-1] > to_level:
            body.append("</li></ul>")
            list_stack.pop()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_para()
            i += 1
            continue
        if stripped.startswith("|"):
            flush_para()
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            if len(block) >= 2 and re.match(r"^\|?\s*:?-{2,}", block[1].strip()):
                body.append(_table(block))
            else:
                body.append("<p>%s</p>" % _inline(" ".join(b.strip() for b in block)))
            continue
        m = re.match(r"^(#{1,3}) (.*)$", line)
        if m:
            flush_para()
            close_lists()
            level, text = len(m.group(1)), m.group(2)
            if level == 1:
                i += 1
                continue                     # the page header carries the title
            slug = _slug(text, used)
            toc.append((level, slug, text))
            if details:
                body.append("</details>")
                details.pop()
            if level == 3 and text.startswith("Appendix"):
                body.append('<details id="%s"><summary>%s</summary>' % (slug, _inline(text)))
                details.append(slug)
            else:
                body.append('<h%d id="%s">%s</h%d>' % (level, slug, _inline(text), level))
            i += 1
            continue
        m = re.match(r"^(\s*)- (.*)$", line)
        if m:
            flush_para()
            indent = len(m.group(1))
            if not list_stack or indent > list_stack[-1]:
                body.append("<ul><li>")
                list_stack.append(indent)
            else:
                close_lists(indent)
                body.append("</li><li>")
            body.append(_inline(m.group(2)))
            i += 1
            continue
        if list_stack and line.startswith(" "):
            body.append(" " + _inline(stripped))      # a wrapped list item
            i += 1
            continue
        close_lists()
        para.append(stripped)
        i += 1
    flush_para()
    close_lists()
    if details:
        body.append("</details>")
    groups = []
    for level, slug, text in toc:
        if level == 2 or not groups:
            groups.append(((slug, text), []))
        else:
            groups[-1][1].append((slug, text))
    nav = ""
    if groups:
        nav = "<nav><strong>Contents</strong><ol>%s</ol></nav>" % "".join(
            '<li><a href="#%s">%s</a>%s</li>' % (
                s, _inline(t), "<ul>%s</ul>" % "".join('<li><a href="#%s">%s</a></li>' % (cs, _inline(ct))
                                                       for cs, ct in kids) if kids else "")
            for (s, t), kids in groups)
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>%s</title><style>%s</style></head><body><main>"
            "<h1>%s</h1><div class=\"muted\">%s</div>%s%s</main></body></html>\n" % (
                html.escape(title), CSS, html.escape(title), html.escape(subtitle), nav, "\n".join(body)))


def write_html(md, out, title, subtitle=""):
    path = os.path.join(out, "report.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(markdown_to_html(md, title, subtitle))
    return path
