"""The published form of the runtime report: one self-contained HTML file (no external assets,
so it can be mailed or posted as is) rendered from report.md, plus the "How this report was made"
section that lists every offline and online step the skill ran, from the artifacts each step left.

Stdlib only. Imported by tl_perf.py (`tl_perf report`)."""
import html
import json
import os
import re


# --------------------------------------------------------------------------- #
# How this report was made: one row per step that left an artifact
# --------------------------------------------------------------------------- #

def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _ms(seconds):
    if seconds is None:
        return "-"
    return "%.3f ms" % (1000 * seconds) if seconds < 1 else "%.1f s" % seconds


def steps_section(doc, out):
    """Markdown lines: the steps behind this report, in the order the skill runs them, each with
    what it did, its result and the artifact it left. Steps that left nothing are not listed."""
    rows = []
    pre = _load(os.path.join(out, "preflight.json"))
    if pre:
        rows.append(["Phase 0 — preflight", "checked the machine, the model runtime and the integration",
                     "exit %s, %d finding(s)" % (pre.get("exit_code"), len(pre.get("findings") or [])),
                     "preflight.json"])
    floor, fit = _load(os.path.join(out, "floor.json")), _load(os.path.join(out, "fit.json"))
    if floor or fit:
        rows.append(["Phase 1 — floor and fit", "measured the model's inference floor and the batch size that "
                     "fits in memory",
                     "floor %s per sample; batch %s" % (_ms((floor or {}).get("t_inf_per_sample_mean_seconds")),
                                                        (fit or floor or {}).get("recommended_batch_size", "-")),
                     ", ".join(f for f, d in (("floor.json", floor), ("fit.json", fit)) if d)])
    static = _load(os.path.join(out, "static.json"))
    if static is not None:
        rows.append(["Phase 2 — read the code", "listed cost hypotheses from the integration's code",
                     "%d hypothesis(es)" % len(static), "static.json"])
    runs_dir = os.path.join(out, "runs")
    runs = sorted(d for d in os.listdir(runs_dir) if os.path.isdir(os.path.join(runs_dir, d))) \
        if os.path.isdir(runs_dir) else []
    if os.path.isdir(os.path.join(out, "baseline")) or runs:
        rows.append(["Phase 3 — baseline profile", "profiled every component (generation, inference, metrics, "
                     "visualizers, start-up) on a fixed sample set",
                     "per-component timings (see Runtime breakdown)",
                     "baseline/" if os.path.isdir(os.path.join(out, "baseline")) else "runs/%s" % runs[0]])
    opts = doc.get("optimizations") or []
    if opts or runs:
        rows.append(["Phase 4 — lossless optimization loop", "changed one thing at a time, re-profiled, and kept "
                     "a change only if every output stayed identical and it was faster",
                     "%d change(s) kept over %d profile run(s)" % (len(opts), len(runs)),
                     "optimization-log.md, compare.json"])
    lossy = doc.get("lossy_options") or []
    if lossy:
        rows.append(["Phase 5 — options that change outputs", "listed faster options that would change outputs; "
                     "none applied without consent",
                     "%d option(s); %s" % (len(lossy), ", ".join(sorted({(o.get("decision") or "-").split(" (")[0]
                                                                         for o in lossy}))),
                     "report.json"])
    sv = doc.get("server_validation") or {}
    if sv:
        mode = sv.get("mode") or "smoke"
        rows.append(["Phase 6%s — server %s" % ("B" if mode == "diagnostics" else "A",
                                                 "diagnostics" if mode == "diagnostics" else "smoke validation"),
                     "pushed the integration to the Tensorleap server and ran an evaluation" +
                     (" (authorized by the user)" if sv.get("authorized_by_user") else ""),
                     "%s, %s" % (sv.get("status") or "-", sv.get("duration") or "-"),
                     "report.json (server_validation)"])
    online = os.path.join(out, "online")
    analysis = _load(os.path.join(online, "analysis.json"))
    if analysis:
        job_dir = os.path.join(online, analysis.get("push_job") or analysis.get("job") or "")
        col = _load(os.path.join(job_dir, "collect.json")) or {}
        cov = analysis.get("coverage") or {}
        steady = col.get("visualization_steady")
        rows.append(["Phase 6B — collect", "followed the run and kept the server's logs while it ran",
                     "%s poll(s), %s gap(s)%s" % (cov.get("polls", col.get("polls", "-")), cov.get("gaps", "-"),
                                                  "; the user was asked to stop the run once the visualization "
                                                  "pace was steady (%.2f samples/s)" % steady["rate"] if steady else ""),
                     "online/%s/" % os.path.basename(job_dir) if os.path.isdir(job_dir) else "online/"])
        prim = next((f for f in analysis.get("findings") or [] if f["id"] == analysis.get("primary")), None)
        rows.append(["Phase 6B — analyze", "found what set the pace in the evaluation and the visualization, its "
                     "root cause, and whether the server's settings fit the run",
                     "primary: %s; %d root cause(s); confidence %s" % (
                         (prim or {}).get("title", "none"), len([r for r in analysis.get("root_causes") or []
                                                                 if not r.get("error")]),
                         analysis.get("confidence")), "online/analysis.json"])
    rows.append(["Phase 7 — report", "wrote this report", "-", "report.json, report.md, report.html"])
    lines = ["## How this report was made", "",
             "Every step the runtime-optimization skill ran, in order, with what it left behind (paths are "
             "relative to `tensorleap/runtime-optimization/`).", "",
             "| step | what was done | result | artifact |", "|---|---|---|---|"]
    lines += ["| %s |" % " | ".join(str(c).replace("|", "\\|") for c in r) for r in rows]
    return lines + [""]


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
nav ol { margin: 4px 0 0; padding-left: 20px; columns: 2; font-size: 14px; }
@media (max-width: 640px) { nav ol { columns: 1; } h1 { font-size: 23px; } }
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
    body, toc, used = [], [], set()
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
            if level == 2:
                toc.append((slug, text))
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
    nav = ""
    if toc:
        nav = "<nav><strong>Contents</strong><ol>%s</ol></nav>" % "".join(
            '<li><a href="#%s">%s</a></li>' % (s, _inline(t)) for s, t in toc)
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
