#!/usr/bin/env python3
"""tensorleap-analysis report plumbing.

Data comes from the Tensorleap MCP server (`leap mcp`): tl_export_analysis
writes a version's analysis to a directory with a manifest.json. This script
turns that directory into the report:

  tl_api.py digest DIR              # manifest.json -> insights.json (the digest the skill reads)
  tl_api.py summarize DIR           # per-insight composition stats from samples.csv
  tl_api.py render-charts DIR       # chart.png / boxes.jpg for non-image payloads, thumbnails
  tl_api.py build-report DIR        # assemble report.html + report.txt from DIR/report.json
  tl_api.py inline-html FILE.html   # embed <img src> files as data URIs, in place

Exit codes:
  0  ok
  2  bad arguments
  5  DIR has no manifest.json or no insights
  6  matplotlib unavailable (render-charts only, fall back to html tables)
  7  inline-html: some src paths did not resolve (listed on stderr)
  8  build-report: report.json invalid or referenced images missing (listed on stderr)
"""
import argparse
import base64
import csv
import html
import io
import json
import os
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

try:
    from skill_run import skill_run
except ImportError:  # source tree: build/generate.py copies the shared helper into scripts/
    def skill_run(*args, **kwargs):
        pass


def image_size(path):
    """(width, height) from a PNG/JPEG/GIF header. Stdlib only, no PIL."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", head[16:24])
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return struct.unpack("<HH", head[6:10])
            if head[:2] != b"\xff\xd8":
                return None
            f.seek(2)
            while True:
                marker = f.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None
                size = struct.unpack(">H", f.read(2))[0]
                if 0xC0 <= marker[1] <= 0xCF and marker[1] not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", f.read(5)[1:])
                    return w, h
                f.seek(size - 2, 1)
    except Exception:
        return None


def population_summary(csv_path):
    """All-data metric means plus a per-metadata-column baseline (numeric mean,
    or top value shares) computed from the version's population csv."""
    if not csv_path or not os.path.isfile(csv_path):
        return {}, {}
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}, {}
    metrics, values, sums, counts, nonnum = {}, {}, {}, {}, set()
    for col in rows[0].keys():
        if col == "sample_id" or col.startswith("metadata_is_none"):
            continue
        (metrics if col.startswith("metrics.") else values)[col] = {}
    for r in rows:
        for col in metrics:
            try:
                v = float(r.get(col) or "")
            except ValueError:
                continue
            sums[col] = sums.get(col, 0.0) + v
            counts[col] = counts.get(col, 0) + 1
        for col, vc in values.items():
            v = r.get(col, "")
            if col not in nonnum:
                try:
                    float(v)
                    sums[col] = sums.get(col, 0.0) + float(v)
                    counts[col] = counts.get(col, 0) + 1
                except ValueError:
                    nonnum.add(col)
            if len(vc) < 5000:
                vc[v] = vc.get(v, 0) + 1
    metric_means = {col: round(sums[col] / counts[col], 4) for col in metrics if counts.get(col)}
    total = len(rows)
    metadata = {}
    for col, vc in values.items():
        if col not in nonnum and counts.get(col) and len(vc) > 12:
            metadata[col] = round(sums[col] / counts[col], 4)
        else:
            top = sorted(vc.items(), key=lambda kv: -kv[1])[:12]
            metadata[col] = {v: round(c / total, 4) for v, c in top}
    return metric_means, metadata


def _rel(export_dir, path):
    # manifests from leap 0.0.163 hold absolute paths; later ones are relative to the export dir
    if not path:
        return None
    return os.path.relpath(path, export_dir) if os.path.isabs(path) else path


def cmd_digest(args):
    out_dir = os.path.abspath(args.dir)
    manifest_path = os.path.join(out_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        print(f"{out_dir} has no manifest.json; run the tl_export_analysis tool into it first",
              file=sys.stderr)
        raise SystemExit(5)
    manifest = json.load(open(manifest_path))
    export_dir = manifest.get("dir") or out_dir
    insights = manifest.get("insights") or []
    if not insights:
        print("the export has no insights: generate insights in the UI "
              "(Population Exploration -> Insights) or pick another version", file=sys.stderr)
        raise SystemExit(5)

    digests, order = {}, []
    for ins in insights:
        summary = ins.get("summary") or {}
        d = {
            "index": ins.get("index"),
            "cid": ins.get("cid"),
            "status": ins.get("status"),
            "description": ins.get("description"),
            "type": ins.get("type"),
            "name": ins.get("name"),
            "dir": _rel(export_dir, ins.get("dir")),
            "insightType": dict(ins.get("engine") or {}, type=ins.get("type")),
            "files": {k: _rel(export_dir, ins.get(src)) for k, src in
                      (("csv", "samplesCsv"), ("cluster", "clusterJson"),
                       ("top_panel", "topPanelJson"), ("fixing_csv", "fixingCsv"))
                      if ins.get(src)},
            "deep_link": ins.get("link"),
            "samples": {},
            "errors": [],
            "subinsights": [],
            "summary": summary,
        }
        if ins.get("createTestLink"):
            d["add_test_link"] = ins["createTestLink"]
        if summary:
            d["population"] = {"samples": summary.get("groupSize"), "csv_rows": summary.get("csvRows")}
        for smp in ins.get("samples") or []:
            entry = {"rank": smp.get("rank"), "files": [_rel(export_dir, f) for f in smp.get("files") or []]}
            if not entry["files"]:
                entry["missing_visualization"] = True
            d["samples"][smp["id"]] = entry
        for kind, rel in list(d["files"].items()) + [(f"sample {i}", f) for i, e in d["samples"].items() for f in e["files"]]:
            if not os.path.exists(os.path.join(out_dir, rel)):
                d["errors"].append(f"{kind}: {rel} is listed in manifest.json but missing; re-run tl_export_analysis")
        csv_path = d["files"].get("csv") and os.path.join(out_dir, d["files"]["csv"])
        if csv_path and os.path.isfile(csv_path):
            with open(csv_path, newline="") as f:
                reader = csv.DictReader(f)
                d["csv_columns"] = reader.fieldnames or []
                d["_ids"] = {r.get("sample_id") for r in root_rows(list(reader)) if r.get("sample_id")}
            if d["type"] != "low_performance" and d["files"].get("cluster"):
                members = cluster_members(os.path.join(out_dir, d["files"]["cluster"]))
                if members:
                    d["_ids"] &= members
        sizes = [wh for entry in d["samples"].values() for wh in
                 (image_size(os.path.join(out_dir, f)) for f in entry["files"]
                  if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))) if wh]
        if sizes:
            d["asset_resolution"] = {"max_width": max(w for w, _ in sizes),
                                     "max_height": max(h for _, h in sizes),
                                     "images": len(sizes)}
        digests[ins.get("index")] = d
        order.append((ins.get("parentIndex") or 0, d))

    parents = []
    for parent_index, d in order:
        parent = digests.get(parent_index)
        if parent and parent is not d:
            parent["subinsights"].append(d)
        else:
            parents.append(d)
    for a in parents:
        shared = [{"insight": b["index"], "shared": len(a.get("_ids", set()) & b.get("_ids", set())),
                   "of_this": round(len(a.get("_ids", set()) & b.get("_ids", set())) / len(a["_ids"]), 3)}
                  for b in parents if b is not a and a.get("_ids") and (a["_ids"] & b.get("_ids", set()))]
        if shared:
            a["overlaps"] = sorted(shared, key=lambda x: -x["shared"])
    for d in digests.values():
        d.pop("_ids", None)

    pop_rel = _rel(export_dir, manifest.get("populationCsv"))
    pop_metrics, pop_metadata = population_summary(pop_rel and os.path.join(out_dir, pop_rel))
    integration = None
    if manifest.get("integrationDir"):
        integration = {"dir": _rel(export_dir, manifest["integrationDir"]), "entry_file": manifest.get("entryFile")}
    all_samples = [s for d in digests.values() for s in d["samples"].values()]
    result = {
        "projectId": manifest.get("projectId"),
        "versionId": manifest.get("versionId"),
        "version": manifest.get("version"),
        "links": {"insights_panel": manifest.get("insightsPanelLink") or (parents[0]["deep_link"] if parents else None)},
        "api_base": manifest.get("apiBase"),
        "population_metrics": pop_metrics,
        "population_metadata": pop_metadata,
        "prediction_labels": manifest.get("classLabels") or {},
        "visualizers": [{"name": v.get("name"), "type": v.get("type"), "arg_names": v.get("argNames")}
                        for v in manifest.get("visualizers") or []],
        "integration": integration,
        "skipped": manifest.get("skipped") or [],
        "notes": manifest.get("notes") or [],
        "insights": parents,
        "counts": {
            "total": len(digests),
            "parents": len(parents),
            "subinsights": len(digests) - len(parents),
            "samples_with_visualizations": sum(1 for s in all_samples if not s.get("missing_visualization")),
            "samples_missing_visualizations": sum(1 for s in all_samples if s.get("missing_visualization")),
        },
    }
    out_path = os.path.join(out_dir, "insights.json")
    open(out_path, "w").write(json.dumps(result, indent=2, default=str))
    for line in result["skipped"]:
        print(f"warning: {line}", file=sys.stderr)
    print(out_path)


def column_stats(rows, col):
    vals = []
    for r in rows:
        v = r.get(col)
        if v in (None, ""):
            continue
        try:
            vals.append(float(v))
        except ValueError:
            vals = None
            break
    entry = {}
    if vals:
        entry.update(mean=round(sum(vals) / len(vals), 4),
                     min=round(min(vals), 4), max=round(max(vals), 4))
    if vals is None or len(set(vals)) <= 12:
        counts = {}
        for r in rows:
            v = r.get(col, "")
            counts[v] = counts.get(v, 0) + 1
        entry["distinct"] = len(counts)
        entry["top"] = [{"value": v, "count": c, "share": round(c / len(rows), 3)}
                        for v, c in sorted(counts.items(), key=lambda kv: -kv[1])[:10]]
    return entry


def cluster_members(path):
    """Sample ids of an insight's own members (cluster.json samples_index), or None."""
    try:
        index = json.load(open(path)).get("samples_index") or {}
    except (OSError, ValueError, AttributeError):
        return None
    return {f"{state}_{i}" for state, idx in index.items() for i in idx} or None


def root_rows(rows):
    """A failure mode's csv from an engine before root-only csvs also holds latent
    neighbours, marked is_low_perf_root_member=False; keep the root members."""
    root = [r for r in rows if str(r.get("is_low_perf_root_member")).lower() == "true"]
    return root or rows


def walk_insights(insights):
    for d in insights:
        yield d
        yield from walk_insights(d.get("subinsights") or [])


def cmd_summarize(args):
    digest = json.load(open(os.path.join(args.dir, "insights.json")))
    pop_metrics = digest.get("population_metrics") or {}
    pop_meta = digest.get("population_metadata") or {}
    out = []
    for d in walk_insights(digest.get("insights") or []):
        csv_path = os.path.join(args.dir, d["dir"], "samples.csv")
        if not os.path.isfile(csv_path):
            continue
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        members = (None if d.get("type") == "low_performance"
                   else cluster_members(os.path.join(args.dir, d["dir"], "cluster.json")))
        own = [r for r in rows if r.get("sample_id") in members] if members else []
        group = own or root_rows(rows)
        split = {}
        for r in group:
            state = (r.get("sample_id") or "").rsplit("_", 1)[0] or "unknown"
            split[state] = split.get(state, 0) + 1
        metrics, metadata = {}, {}
        for col in rows[0].keys():
            if col in ("sample_id", "is_low_perf_root_member"):
                continue
            entry = column_stats(group, col)
            if not entry:
                continue
            if col.startswith("metrics."):
                if "mean" in entry:
                    m = {"group_mean": entry["mean"]}
                    if col in pop_metrics:
                        m["all_data_mean"] = round(pop_metrics[col], 4)
                    metrics[col] = m
                continue
            base = pop_meta.get(col)
            if isinstance(base, dict) and entry.get("top"):
                for t in entry["top"]:
                    if t["value"] in base:
                        t["all_data_share"] = base[t["value"]]
            elif isinstance(base, (int, float)) and "mean" in entry:
                entry["all_data_mean"] = base
            metadata[col] = entry
        out.append({"insight": d.get("index"), "type": d.get("type"),
                    "parent_index": next(
                        (p.get("index") for p in walk_insights(digest["insights"])
                         if d in (p.get("subinsights") or [])), None),
                    "csv_rows": len(rows), "group_rows": len(group),
                    "split": split, "metrics": metrics, "metadata": metadata})
    print(json.dumps(out, indent=1))


def render_graph(data, dest, plt):
    body = data.get("body") or []
    if not body:
        return False
    series = list(zip(*body)) if isinstance(body[0], (list, tuple)) else [body]
    x_range = data.get("x_range")
    xs = (list(_frange(x_range[0], x_range[1], len(series[0])))
          if x_range and len(x_range) == 2 else range(len(series[0])))
    fig, ax = plt.subplots(figsize=(6, 3))
    legend = data.get("legend") or []
    for i, s in enumerate(series):
        ax.plot(xs, s, label=legend[i] if i < len(legend) else None)
    if legend:
        ax.legend(fontsize=7)
    ax.set_xlabel(data.get("x_label") or "")
    ax.set_ylabel(data.get("y_label") or "")
    fig.tight_layout()
    fig.savefig(dest, dpi=100)
    plt.close(fig)
    return True


def _frange(start, stop, n):
    step = (stop - start) / max(n - 1, 1)
    return (start + i * step for i in range(n))


def render_hbar(data, dest, plt):
    body = data.get("body") or []
    labels = data.get("labels") or [str(i) for i in range(len(body))]
    if not body:
        return False
    fig, ax = plt.subplots(figsize=(6, max(2, 0.3 * len(body))))
    ys = range(len(body))
    ax.barh(ys, body, height=0.4, label="prediction")
    gt = data.get("gt")
    if gt:
        ax.barh([y + 0.4 for y in ys], gt, height=0.4, label="ground truth")
        ax.legend(fontsize=7)
    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=7)
    fig.tight_layout()
    fig.savefig(dest, dpi=100)
    plt.close(fig)
    return True


def render_bboxes(data, root, Image, ImageDraw):
    img_path = os.path.join(root, "assets", "data.jpg")
    if not os.path.isfile(img_path):
        img_path = os.path.join(root, "assets", "data.png")
    boxes = data.get("bounding_box") or []
    if not boxes or not os.path.isfile(img_path):
        return False
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for b in boxes:
        bw, bh = b["width"] * w, b["height"] * h
        cx, cy = b["x"] * w, b["y"] * h
        draw.rectangle([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2],
                       outline="#2a78d6", width=2)
    img.save(os.path.join(root, "boxes.jpg"), quality=88)
    return True


def cmd_render_charts(args):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        plt = None
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        Image = ImageDraw = None
    rendered, plt_skipped, pil_skipped = 0, 0, 0
    for root, _dirs, files in os.walk(args.dir):
        if "payload.json" not in files:
            continue
        try:
            payload = json.load(open(os.path.join(root, "payload.json")))
        except Exception:
            continue
        data = payload.get("data") or {}
        kind = data.get("type")
        try:
            if kind in ("graph", "hbar"):
                dest = os.path.join(root, "chart.png")
                if os.path.exists(dest):
                    continue
                if not plt:
                    plt_skipped += 1
                elif (render_graph if kind == "graph" else render_hbar)(data, dest, plt):
                    rendered += 1
            elif kind == "bbox_image":
                if os.path.exists(os.path.join(root, "boxes.jpg")):
                    continue
                if not Image:
                    pil_skipped += 1
                elif render_bboxes(data, root, Image, ImageDraw):
                    rendered += 1
        except Exception as e:
            print(f"render failed for {root}: {e}", file=sys.stderr)
    thumbs = 0
    if Image:
        for root, _dirs, files in os.walk(args.dir):
            for f in files:
                if (not f.lower().endswith((".jpg", ".jpeg", ".png"))
                        or f.endswith(".thumb.jpg")):
                    continue
                dest = os.path.join(root, f + ".thumb.jpg")
                if os.path.exists(dest):
                    continue
                try:
                    img = Image.open(os.path.join(root, f))
                    if max(img.size) <= 640:
                        continue
                    img = img.convert("RGB")
                    img.thumbnail((640, 640))
                    img.save(dest, "JPEG", quality=80)
                    thumbs += 1
                except Exception as e:
                    print(f"thumbnail failed for {root}/{f}: {e}", file=sys.stderr)
    print(f"rendered {rendered}, thumbnails {thumbs} "
          f"(skipped {plt_skipped} charts for missing matplotlib, "
          f"{pil_skipped} bbox overlays for missing PIL)")
    if pil_skipped:
        print(f"PIL missing: {pil_skipped} bbox overlays (and all thumbnails) "
              f"not rendered, those samples have no boxes.jpg", file=sys.stderr)
    if plt_skipped:
        raise SystemExit(6)


REPORT_CSS = """\
:root {
  color-scheme: dark;
  --page-bg: #1c1c1f; --card: #27272a; --chip: #323236;
  --ink: #fafafa; --ink-2: #d4d4d8; --muted: #a1a1aa;
  --line: rgba(255,255,255,.08); --line-2: rgba(255,255,255,.14);
  --acc: #3b82f6; --acc-light: #60a5fa; --brand: #06b6d4;
  --st-train: #3987e5; --st-val: #d95926; --st-test: #199e70;
  --st-unl: #c98500; --st-other: #a1a1aa;
}
[data-sev="1"] { --sev-c: #eab308; --sev-bd: rgba(234,179,8,.40); --sev-bg: rgba(234,179,8,.08); }
[data-sev="2"] { --sev-c: #f97316; --sev-bd: rgba(249,115,22,.40); --sev-bg: rgba(249,115,22,.08); }
[data-sev="3"] { --sev-c: #ef4444; --sev-bd: rgba(239,68,68,.40); --sev-bg: rgba(239,68,68,.08); }
[data-kind="failure-mode"] { --ins-a: #06b6d4; }
[data-kind="duplication"] { --ins-a: #f97316; }
[data-kind="out-of-distribution"] { --ins-a: #22c55e; }
[data-kind="data-leakage"] { --ins-a: #8b5cf6; }
[data-kind="domain-gap"] { --ins-a: #eab308; }
[data-kind="mislabeled"] { --ins-a: #f59e0b; }
body { margin: 0; background: var(--page-bg); color: var(--ink);
       font: 15px/1.55 "Nunito Sans", system-ui, -apple-system, sans-serif; }
a { color: var(--acc-light); text-decoration: none; }
a:hover { color: var(--acc); text-decoration: underline; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,.18); border-radius: 5px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,.3); }
header { position: sticky; top: 0; z-index: 30; background: var(--page-bg);
         border-bottom: 1px solid var(--line); }
.hwrap { max-width: 1180px; margin: 0 auto; padding: 18px 28px 14px;
         display: flex; flex-direction: column; gap: 12px; }
.hrow { display: flex; align-items: flex-start; gap: 20px; flex-wrap: wrap; }
.htitle { flex: 1 1 420px; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.htitle .hchips { margin-top: 8px; }
.eyebrow { font-size: 11px; font-weight: 700; letter-spacing: .18em;
           text-transform: uppercase; color: var(--brand); }
h1 { margin: 0; font-size: 26px; font-weight: 900; line-height: 1.15;
     letter-spacing: -.01em; }
.hchips, .chips { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.chip { display: inline-flex; align-items: center; gap: 7px; padding: 4px 11px;
        border: 1px solid var(--line-2); border-radius: 6px; background: var(--chip);
        font-size: 13px; line-height: 18px; white-space: nowrap; color: var(--ink-2); }
.chip b { color: var(--ink); font-weight: 700; font-variant-numeric: tabular-nums; }
.chip.sev { gap: 9px; border-color: var(--sev-bd); background: var(--sev-bg); }
.chip.sev .k { font-size: 11px; font-weight: 700; letter-spacing: .1em;
               text-transform: uppercase; color: var(--muted); }
.chip.sev b { color: var(--sev-c); }
a.button { display: inline-flex; align-items: center; gap: 7px; height: 32px;
     padding: 0 12px; border: 1px solid var(--line-2); border-radius: 6px;
     background: var(--chip); color: #fff; font-size: 12px; font-weight: 700;
     letter-spacing: .08em; text-transform: uppercase; white-space: nowrap;
     transition: color .15s, border-color .15s, background .15s; }
a.button:hover { color: var(--acc-light); border-color: var(--acc-light);
     background: rgba(59,130,246,.12); text-decoration: none; }
a.button svg { width: 16px; height: 16px; fill: none; stroke: currentColor;
     stroke-width: 2; stroke-linecap: round; flex: none; }
main { max-width: 1180px; margin: 0 auto; padding: 26px 28px 96px;
       display: flex; flex-direction: column; gap: 34px; }
.block { display: flex; flex-direction: column; gap: 14px; }
h2 { margin: 0; align-self: flex-start; font-size: 13px; font-weight: 800;
     letter-spacing: .14em; text-transform: uppercase; padding-bottom: 5px;
     border-bottom: 2px solid var(--brand); }
.grouphead { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.grouphead h2 { align-self: auto; }
.count { padding: 2px 10px; border: 1px solid var(--line-2); border-radius: 999px;
     font-size: 12px; font-weight: 700; color: var(--muted);
     font-variant-numeric: tabular-nums; white-space: nowrap; }
.blurb { margin: 0; max-width: 88ch; font-size: 13px; line-height: 1.6;
     color: var(--muted); }
.summary { margin: 0; max-width: 88ch; font-size: 15px; line-height: 1.65;
     color: var(--ink-2); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
     gap: 10px; }
.tile { border: 1px solid var(--line); border-radius: 8px; background: var(--card);
     padding: 14px 16px; }
.tile[data-kind] { border-left: 3px solid var(--ins-a); }
.tile b { display: block; font-size: 30px; font-weight: 900; line-height: 1.1;
     font-variant-numeric: tabular-nums; }
.tile span { display: block; margin-top: 4px; font-size: 11px; font-weight: 700;
     letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }
.tablewrap { border: 1px solid var(--line); border-radius: 8px;
     background: var(--card); overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 11px 12px; vertical-align: baseline; }
th:first-child, td:first-child { padding-left: 16px; }
th:last-child, td:last-child { padding-right: 16px; }
thead th { padding-top: 9px; padding-bottom: 9px; font-size: 11px; font-weight: 700;
     letter-spacing: .1em; text-transform: uppercase; color: var(--muted);
     background: var(--chip); }
td { border-top: 1px solid var(--line); }
tr:hover td { background: var(--chip); }
tr.typerow td, tr.typerow:hover td { font-size: 11px; font-weight: 700;
     letter-spacing: .12em; text-transform: uppercase; color: var(--ink);
     background: color-mix(in srgb, var(--ins-a) 5%, transparent); }
.tick { display: inline-block; width: 3px; height: 13px; border-radius: 2px;
     background: var(--ins-a); vertical-align: -2px; margin-right: 9px; }
td.issue { font-weight: 600; color: var(--ink); }
td.action { color: var(--muted); }
th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.num { color: var(--ink); }
a.rownum { display: inline-block; padding: 2px 9px; border: 1px solid var(--line-2);
     border-radius: 999px; font-size: 12px; font-weight: 700; color: var(--ink);
     font-variant-numeric: tabular-nums; white-space: nowrap; }
a.rownum:hover { border-color: var(--acc-light); text-decoration: none; }
.sevcell { white-space: nowrap; font-weight: 700; font-size: 12px;
     letter-spacing: .06em; color: var(--sev-c, var(--ink-2)); }
.sortnote { margin: -6px 0 12px; font-size: 13px; color: var(--muted); }
.cards { display: flex; flex-direction: column; gap: 16px; }
.card { border: 1px solid var(--line); border-radius: 8px; background: var(--card);
     overflow: hidden; scroll-margin-top: 200px; }
.chead { display: flex; align-items: flex-start; gap: 14px; padding: 13px 16px;
     border-bottom: 2px solid var(--ins-a); flex-wrap: wrap; }
.cidx { flex: none; font-size: 13px; font-weight: 700; color: var(--muted);
     font-variant-numeric: tabular-nums; padding-top: 3px; white-space: nowrap; }
.chead h3 { flex: 1 1 380px; min-width: 0; margin: 0; font-size: 17px;
     font-weight: 700; line-height: 1.35; }
.cbtns { flex: none; display: flex; align-items: center; gap: 8px; }
.card .chips { padding: 13px 16px 0; }
.lede { margin: 14px 16px 0; padding: 11px 14px; border-left: 3px solid var(--ins-a);
     border-radius: 0 6px 6px 0; background: rgba(255,255,255,.03);
     font-size: 15px; font-weight: 600; line-height: 1.5; }
.folds { display: flex; flex-wrap: wrap; gap: 8px; padding: 14px 16px 0; }
.folds details[open] { flex: 1 1 100%; }
summary { list-style: none; cursor: pointer; }
summary::-webkit-details-marker { display: none; }
summary::marker { content: ""; }
.folds summary { display: inline-flex; align-items: center; gap: 7px;
     padding: 5px 12px; border: 1px solid var(--line-2); border-radius: 999px;
     background: var(--chip); font-size: 11px; font-weight: 700;
     letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }
.folds summary::before { content: "\\25B8"; font-size: 14px; line-height: 1; }
.folds details[open] > summary::before { content: "\\25BE"; }
.fold { padding: 14px 2px 4px; display: flex; flex-direction: column; gap: 12px; }
.fold p { margin: 0; max-width: 92ch; font-size: 14px; line-height: 1.65;
     color: var(--ink-2); }
p.rootcause { font-size: 13px; line-height: 1.6; color: var(--muted); }
.rootcause b { color: var(--ins-a); }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
     gap: 22px; padding: 14px 16px; border: 1px solid var(--line);
     border-radius: 6px; background: var(--chip); }
.facts > div { display: flex; flex-direction: column; gap: 8px; }
.flabel { font-size: 11px; font-weight: 700; letter-spacing: .1em;
     text-transform: uppercase; color: var(--muted); }
.splitbar { display: flex; gap: 2px; height: 12px; border-radius: 3px;
     overflow: hidden; background: var(--card); }
.splitbar span { min-width: 3px; }
.st-train { background: var(--st-train); } .st-val { background: var(--st-val); }
.st-test { background: var(--st-test); } .st-unl { background: var(--st-unl); }
.st-other { background: var(--st-other); }
.legend { display: flex; flex-wrap: wrap; gap: 14px; }
.legend > span { display: inline-flex; align-items: center; gap: 6px;
     font-size: 12px; color: var(--muted); white-space: nowrap; }
.legend b { width: 9px; height: 9px; border-radius: 2px;
     background: var(--dot, var(--st-other)); }
.legend .train { --dot: var(--st-train); } .legend .val { --dot: var(--st-val); }
.legend .test { --dot: var(--st-test); } .legend .unl { --dot: var(--st-unl); }
.legend i { font-style: normal; color: var(--ink); font-variant-numeric: tabular-nums; }
.contrast { display: grid; grid-template-columns: 74px 1fr 54px; gap: 5px 10px;
     align-items: center; font-size: 12px; }
.contrast .cmetric { grid-column: 1 / -1; font-size: 11px; font-weight: 700;
     letter-spacing: .1em; text-transform: uppercase; color: var(--muted);
     margin-top: 7px; }
.contrast .cmetric:first-child { margin-top: 0; }
.contrast .lbl { color: var(--ink-2); }
.contrast .val { text-align: right; font-variant-numeric: tabular-nums;
     color: var(--ink); font-weight: 700; }
.contrast .val.all, .contrast .lbl.all { color: var(--muted); font-weight: 400; }
.contrast .track { height: 8px; border-radius: 4px;
     background: rgba(255,255,255,.07); overflow: hidden; }
.contrast .fill { display: block; height: 100%; border-radius: 4px;
     background: var(--ins-a, var(--acc)); }
.contrast .fill.all { background: var(--muted); }
.viewnote { margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
     font-size: 11px; line-height: 1.5; color: var(--muted); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
     gap: 14px; }
.grid.wide { grid-template-columns: repeat(auto-fill, minmax(480px, 1fr)); }
.grid.solo { grid-template-columns: 1fr; }
figure { margin: 0; display: flex; flex-direction: column; gap: 7px; }
figure img { display: block; width: 100%; border-radius: 4px;
     border: 1px solid var(--line); background: var(--chip); box-sizing: border-box; }
.grid.solo > figure > img { width: auto; max-width: 100%; max-height: 62vh; }
.pair { display: flex; gap: 6px; }
.pair > span { position: relative; flex: 1 1 0; min-width: 0; display: block; }
.ptag { position: absolute; top: 6px; left: 6px; padding: 2px 7px;
     border-radius: 3px; background: rgba(9,9,11,.78); font-size: 10px;
     font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
     color: var(--ink-2); }
figcaption { font-size: 12px; line-height: 1.5; color: var(--muted); }
blockquote { border-left: 3px solid var(--line-2); margin: 0; padding: 4px 14px;
     color: var(--ink-2); font-style: italic; }
blockquote .muted { display: block; font-style: normal; margin-top: 6px;
     font-size: 12px; color: var(--muted); }
.donext { margin: 16px; padding: 13px 16px; border: 1px solid rgba(59,130,246,.28);
     border-radius: 6px; background: rgba(59,130,246,.06); display: flex;
     flex-direction: column; gap: 9px; }
.donext h4 { margin: 0; font-size: 11px; font-weight: 700; letter-spacing: .12em;
     text-transform: uppercase; color: var(--acc-light); }
ul.actions { list-style: none; margin: 0; padding: 0; display: flex;
     flex-direction: column; gap: 9px; }
ul.actions li { position: relative; padding-left: 23px; font-size: 14px;
     line-height: 1.55; color: var(--ink-2); }
ul.actions li::before { content: ""; position: absolute; left: 0; top: 5px;
     width: 12px; height: 12px; border: 1.5px solid var(--acc-light);
     border-radius: 3px; }
.btnrow { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 2px; }
a.download { display: inline-flex; align-items: center; height: 32px;
     padding: 0 12px; border-radius: 6px; background: var(--acc); color: #fff;
     font-size: 12px; font-weight: 700; letter-spacing: .06em;
     text-transform: uppercase; white-space: nowrap; }
a.download:hover { background: var(--acc-light); color: #fff;
     text-decoration: none; }
.block.group:not(:has(.card)), #approved[hidden] { display: none; }
#approved h2 { border-bottom-color: #34d399; color: #d1fae5; }
#approved .count { border-color: rgba(52,211,153,.4); color: #6ee7b7; }
.review { flex: 0 1 340px; min-width: 260px; padding: 12px 14px;
     border: 1px solid var(--line); border-radius: 8px; background: var(--card);
     display: flex; flex-direction: column; gap: 8px; }
.review.done { border-color: rgba(52,211,153,.45); background: rgba(16,185,129,.10); }
.rtop { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.rtop .k { font-size: 11px; font-weight: 700; letter-spacing: .12em;
     text-transform: uppercase; color: var(--muted); }
.rtop span:last-child { font-size: 13px; color: var(--ink-2); }
.rtop i { font-style: normal; }
.rtop b { color: #6ee7b7; font-size: 15px; font-variant-numeric: tabular-nums; }
.rbar { height: 8px; border-radius: 4px; background: rgba(255,255,255,.07); overflow: hidden; }
.rbar span { display: block; height: 100%; width: 0; border-radius: 4px;
     background: #34d399; transition: width .5s cubic-bezier(.2,.8,.2,1); }
.rnote { font-size: 12px; line-height: 1.5; color: var(--muted); }
.rfoot { display: flex; align-items: center; justify-content: space-between; gap: 10px;
     padding-top: 10px; border-top: 1px solid var(--line); font-size: 12px; line-height: 1.5; }
.rsaved { display: flex; align-items: flex-start; gap: 8px; color: var(--muted); }
.rsaved b { color: var(--ink-2); }
.rsaved svg { flex: none; margin-top: 2px; stroke: #6ee7b7; }
.rsaved.warn { color: #fbbf24; }
.rsaved.warn svg { display: none; }
.racts { display: flex; flex: none; gap: 6px; }
.racts[hidden], html[data-ro] button.apv { display: none; }
button.share { display: inline-flex; align-items: center; gap: 8px; height: 32px;
     padding: 0 12px; border: 0; border-radius: 6px; background: var(--acc); color: #fff;
     font: inherit; font-size: 12px; font-weight: 800; letter-spacing: .08em;
     text-transform: uppercase; white-space: nowrap; cursor: pointer; transition: background .15s; }
button.share:hover { background: var(--acc-light); }
button.share:focus-visible { outline: 2px solid #93c5fd; outline-offset: 2px; }
.toast { position: fixed; left: 50%; bottom: 24px; z-index: 50; transform: translateX(-50%);
     padding: 10px 16px; border: 1px solid var(--line); border-radius: 8px;
     background: var(--card); color: var(--ink-2); font-size: 13px;
     box-shadow: 0 12px 30px -12px rgba(0,0,0,.6); }
.toast[hidden] { display: none; }
button.apv { display: inline-flex; align-items: center; gap: 7px; height: 32px;
     padding: 0 14px; border: 1px solid rgba(52,211,153,.55); border-radius: 6px;
     background: rgba(16,185,129,.10); color: #6ee7b7; font: inherit;
     font-size: 12px; font-weight: 800; letter-spacing: .08em;
     text-transform: uppercase; white-space: nowrap; cursor: pointer;
     transition: background .18s, color .18s, border-color .18s, box-shadow .18s; }
button.apv:hover { background: rgba(16,185,129,.20); border-color: #34d399;
     box-shadow: 0 0 0 3px rgba(52,211,153,.14); }
button.apv:focus-visible { outline: 2px solid #6ee7b7; outline-offset: 2px; }
button.apv[aria-pressed="true"] { border-color: #10b981; background: #10b981; color: #052e1f; }
button.apv[aria-pressed="true"]:hover { background: #34d399; border-color: #34d399; }
button.apv:disabled { opacity: .6; cursor: progress; }
.review svg, button.apv svg, .apvbadge svg { width: 16px; height: 16px;
     fill: none; stroke: currentColor; stroke-width: 2.5; stroke-linecap: round;
     stroke-linejoin: round; }
.apvbadge { display: none; width: fit-content; align-items: center; gap: 6px;
     margin-bottom: 6px; padding: 2px 9px 2px 7px; border-radius: 999px;
     background: rgba(16,185,129,.16); color: #6ee7b7; font-size: 11px;
     font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
.apvbadge svg { width: 12px; height: 12px; stroke-width: 3; }
.card.approved { border-color: rgba(52,211,153,.42); background: #22302c;
     box-shadow: 0 0 0 1px rgba(52,211,153,.10), 0 14px 34px -22px rgba(16,185,129,.7); }
.card.approved .chead { border-bottom-color: #34d399; background: rgba(16,185,129,.07); }
.card.approved .lede { border-left-color: #34d399; background: rgba(16,185,129,.07); }
.card.approved .apvbadge { display: flex; }
tr.approved a.rownum { border-color: #34d399; color: #6ee7b7; }
.settle { animation: settle 1.1s cubic-bezier(.2,.8,.2,1); }
@keyframes settle {
  0% { transform: translateY(14px); box-shadow: 0 0 0 0 rgba(52,211,153,.55); }
  45% { transform: none; box-shadow: 0 0 0 8px rgba(52,211,153,.10); } }
@media (prefers-reduced-motion: reduce) {
  .settle { animation: none; } .rbar span, button.apv { transition: none; } }
@media (max-width: 700px) { header { position: static; } }
"""

# Review state: every click autosaves in the browser's localStorage, keyed by the
# report id baked in at build time (not by the file's path, so a moved or renamed
# report keeps its approvals). "Share" bakes the current approvals into a
# read-only copy of the page as it was loaded, with only the JSON block replaced.
REVIEW_JS = """\
(function () {
  var SRC = "<!doctype html>\\n" + document.documentElement.outerHTML;
  var stateEl = document.getElementById("tl-review");
  var ORIG = stateEl.textContent;
  var st = JSON.parse(ORIG);
  var RO = !!st.readonly, KEY = "tl-report:" + st.reportId;
  // with sync, each click updates the insight on the Tensorleap server that built the report
  var SYNC = !RO && st.sync ? st.sync : null, busy = {}, syncedAt = null;
  var approved = [], last = null, savedAt = null, canStore = true, local = null;
  if (RO) document.documentElement.setAttribute("data-ro", "");
  else {
    try { local = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { canStore = false; }
  }
  var saved = local ? local.approved : st.approved.slice();
  if (local) savedAt = local.savedAt;
  var shelf = document.getElementById("approved");
  var shelfCards = shelf.querySelector(".cards");
  var cards = {}, home = {}, rows = {};
  document.querySelectorAll("section.card").forEach(function (c) {
    cards[c.id] = c;
    home[c.id] = document.createComment(c.id);
    c.before(home[c.id]);
    c.querySelector("button.apv").addEventListener("click", function () { toggle(c.id); });
    c.addEventListener("animationend", function () { c.classList.remove("settle"); });
  });
  document.querySelectorAll("a.rownum").forEach(function (a) {
    rows[a.getAttribute("href").slice(1)] = a.closest("tr");
  });
  var total = Object.keys(cards).length;
  function q(s) { return document.querySelector(s); }
  function when(iso) {
    var d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString([], {dateStyle: "medium", timeStyle: "short"});
  }
  function setOn(id, on) {
    var i = approved.indexOf(id);
    if (on && i < 0) { approved.unshift(id); shelfCards.prepend(cards[id]); last = id; }
    else if (!on && i >= 0) { approved.splice(i, 1); home[id].after(cards[id]); last = null; }
  }
  function titleOf(id) {
    var h = cards[id].querySelector("h3").cloneNode(true), badge = h.querySelector(".apvbadge");
    if (badge) badge.remove();
    return h.textContent.trim();
  }
  function toggle(id) {
    if (RO || busy[id]) return;
    var want = approved.indexOf(id) < 0, cid = SYNC && SYNC.insights[id];
    setOn(id, want);
    persist();
    render();
    if (!cid) return;
    busy[id] = true;
    render();
    fetch(SYNC.apiBase + "/insights/reportApproval", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({projectId: SYNC.projectId, insightId: cid,
                            action: want ? "approve" : "unapprove", title: titleOf(id)})
    }).then(function (r) {
      if (r.status === 404) throw "elsewhere";
      if (!r.ok) throw "failed";
      return r.json();
    }).then(function (res) {
      // the server's status wins: approving an approved insight or unapproving an archived one changes nothing there
      setOn(id, res.status === "Approved");
      syncedAt = new Date().toISOString();
    }, function (why) {
      setOn(id, !want);
      toast(why === "elsewhere" ? "This report was made on another Tensorleap server. Nothing changed."
                                : "Couldn't reach Tensorleap. Nothing changed.");
    }).then(function () {
      busy[id] = false;
      persist();
      render();
    });
  }
  function persist() {
    var at = new Date().toISOString();
    try {
      localStorage.setItem(KEY, JSON.stringify({approved: approved, savedAt: at}));
      savedAt = at; canStore = true;
    } catch (e) { canStore = false; }
  }
  function apply(ids) {
    approved.slice().forEach(function (id) {
      if (ids.indexOf(id) < 0) { approved.splice(approved.indexOf(id), 1); home[id].after(cards[id]); }
    });
    ids.forEach(function (id) {
      if (cards[id] && approved.indexOf(id) < 0) { approved.push(id); shelfCards.append(cards[id]); }
    });
  }
  function render() {
    var n = approved.length;
    Object.keys(cards).forEach(function (id) {
      var on = approved.indexOf(id) >= 0, c = cards[id];
      c.classList.toggle("approved", on);
      c.classList.toggle("settle", on && id === last);
      var b = c.querySelector("button.apv");
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.disabled = !!busy[id];
      b.lastChild.textContent = on ? "Approved" : "Approve";
      if (rows[id]) rows[id].classList.toggle("approved", on);
    });
    last = null;
    shelf.hidden = n === 0;
    shelf.querySelector(".count").textContent = n;
    q(".review").classList.toggle("done", n === total);
    q(".rtop b").textContent = n;
    q(".rtop .tot").textContent = total;
    q(".rbar").setAttribute("aria-valuemax", total);
    q(".rbar").setAttribute("aria-valuenow", n);
    q(".rbar span").style.width = Math.round(100 * n / (total || 1)) + "%";
    q(".rnote").textContent = RO
      ? "Read-only copy shared from a review; the approvals can't be changed here."
      : n === 0
      ? "Approve the insights you agree with. They move to the top so your team sees what was confirmed."
      : n === total ? "Every insight reviewed."
      : (total - n) + " left to review. Approved insights are pinned at the top.";
    var s = q(".rsaved");
    s.classList.toggle("warn", !RO && !SYNC && !canStore);
    s.querySelector("span").textContent = RO
      ? "Shared " + when(st.savedAt)
      : SYNC ? (syncedAt ? "Saved in Tensorleap, " + when(syncedAt) : "Approvals are saved in Tensorleap as you click.")
      : !canStore ? "This browser isn't keeping your approvals here. Download a copy to keep them."
      : savedAt ? "Saved in this browser, " + when(savedAt)
      : "Your approvals are saved in this browser as you click.";
    q(".racts").hidden = RO;
  }
  function sharedPage() {
    var next = {reportId: st.reportId, readonly: true, approved: approved.slice(),
                savedAt: new Date().toISOString()};
    var json = JSON.stringify(next).replace(/</g, "\\\\u003c");
    return SRC.replace('id="tl-review">' + ORIG, function () { return 'id="tl-review">' + json; });
  }
  var FILE = (q("h1").textContent.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "")
              || "report") + "-review.html";
  function download(page) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([page], {type: "text/html"}));
    a.download = FILE;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 10000);
  }
  var toastEl = q(".toast"), toastT = null;
  function toast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(toastT);
    toastT = setTimeout(function () { toastEl.hidden = true; }, 4000);
  }
  q("button.share").addEventListener("click", function () {
    download(sharedPage());
    toast("Read-only copy downloaded");
  });
  window.addEventListener("storage", function (e) {
    if (RO || e.key !== KEY) return;
    try { var v = JSON.parse(e.newValue || "null"); } catch (x) { return; }
    apply(v ? v.approved : st.approved);
    savedAt = v ? v.savedAt : null;
    render();
  });
  apply(saved);
  render();
})();
"""

SPLIT_CLASS = {"training": "train", "train": "train", "validation": "val",
               "val": "val", "test": "test", "unlabeled": "unl"}
AUDIO_EXTS = (".wav", ".mp3", ".flac", ".ogg", ".m4a")

SEV_WORDS = {"1": "LOW", "2": "MEDIUM", "3": "HIGH"}


def _modality(spec):
    counts = {"visual": 0, "textual": 0, "auditory": 0}
    for g in spec.get("groups", []):
        for c in g.get("cards", []):
            for s in c.get("samples", []):
                if "text" in s:
                    counts["textual"] += 1
                elif any(p.lower().endswith(AUDIO_EXTS)
                         for p in s.get("images", [])):
                    counts["auditory"] += 1
                else:
                    counts["visual"] += 1
    # ponytail: majority vote across all cards so the label is uniform report-wide
    return max(counts, key=counts.get)
TYPE_KIND = {"Failure Mode": "failure-mode",
             "Out of Distribution": "out-of-distribution",
             "Duplication": "duplication", "Data Leakage": "data-leakage",
             "Domain Gap": "domain-gap", "Mislabeled": "mislabeled"}
FONT_FILES = (
    ("nunito-sans-latin-ext.woff2",
     "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, "
     "U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, "
     "U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF"),
    ("nunito-sans-latin.woff2",
     "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, "
     "U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, "
     "U+2212, U+2215, U+FEFF, U+FFFD"),
)
ICON_MAGNIFIER = ('<svg viewBox="0 0 24 24" aria-hidden="true">'
                  '<circle cx="11" cy="11" r="7"></circle>'
                  '<path d="m21 21-4.3-4.3"></path></svg>')
ICON_PLUS = ('<svg viewBox="0 0 24 24" aria-hidden="true">'
             '<path d="M12 5v14M5 12h14"></path></svg>')
ICON_CHECK = ('<svg viewBox="0 0 24 24" aria-hidden="true">'
              '<path d="M5 12.5l4.5 4.5L19 7.5"></path></svg>')
ICON_DOWNLOAD = ('<svg viewBox="0 0 24 24" aria-hidden="true">'
                 '<path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19h14"></path></svg>')


def _font_css():
    faces = []
    here = os.path.dirname(os.path.abspath(__file__))
    for name, unicode_range in FONT_FILES:
        path = os.path.join(here, name)
        if not os.path.isfile(path):
            continue
        b64 = base64.b64encode(open(path, "rb").read()).decode()
        faces.append(
            f"@font-face {{ font-family: 'Nunito Sans'; font-style: normal;"
            f" font-weight: 200 1000; font-display: swap;"
            f" src: url(data:font/woff2;base64,{b64}) format('woff2');"
            f" unicode-range: {unicode_range}; }}")
    return "\n".join(faces)


def _kind(type_name):
    return TYPE_KIND.get(type_name, str(type_name).lower().replace(" ", "-"))


def _idx(card_id):
    m = re.fullmatch(r"insight-(\d+)(?:-sub-(\d+))?", str(card_id))
    if not m:
        return ""
    idx = f"#{m.group(1)}"
    return f"{idx} \u00b7 sub #{m.group(2)}" if m.group(2) else idx


def _fmt(v):
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def _esc(v):
    """report.json strings are plain text (only executive_summary, prose and
    observe are HTML fragments), escape everything else at render time."""
    return html.escape(str(v), quote=True)


def _para(text, cls=""):
    text = (text or "").strip()
    if text.startswith("<"):
        return text
    attr = f' class="{cls}"' if cls else ""
    return f"<p{attr}>{text}</p>"


def _splitbar_html(split):
    total = sum(s["count"] for s in split) or 1
    bar = "".join(
        f'<span class="st-{SPLIT_CLASS.get(s["state"], "other")}"'
        f' style="width:{100 * s["count"] / total:.1f}%"></span>'
        for s in split if s["count"])
    legend = "".join(
        f'<span class="{SPLIT_CLASS.get(s["state"], "")}"><b></b>'
        f'{_esc(s["state"])} <i>{_esc(s["count"])}</i></span>' for s in split)
    return (f'<div><span class="flabel">Split composition</span>'
            f'<div class="splitbar">{bar}</div>'
            f'<div class="legend">{legend}</div></div>')


def _contrast_html(contrast):
    rows = []
    for c in contrast:
        top = max(abs(c["group"]), abs(c["all"])) or 1
        rows.append(f'<span class="cmetric">{_esc(c["metric"])}</span>')
        for cls, label, val in (("", "this group", c["group"]),
                                (" all", "all data", c["all"])):
            rows.append(
                f'<span class="lbl{cls}">{label}</span>'
                f'<span class="track"><span class="fill{cls}"'
                f' style="width:{100 * abs(val) / top:.0f}%"></span></span>'
                f'<span class="val{cls}">{_esc(_fmt(val))}</span>')
    return f'<div><div class="contrast">{"".join(rows)}</div></div>'


def _figure_html(sample, base, missing, pair_labels=None):
    cap = _esc(sample.get("caption", ""))
    if "text" in sample:
        note = f'<span class="muted">{cap}</span>' if cap else ""
        return f'<blockquote>{_esc(sample["text"])}{note}</blockquote>'
    imgs = []
    for src in sample.get("images", []):
        if src.endswith(".thumb.jpg"):
            missing.append(f"{src}: thumbnails are for analysis viewing only, "
                           f"reference the original image")
        elif not os.path.isfile(os.path.join(base, src)):
            missing.append(f"missing image: {src}")
        imgs.append(f'<img src="{_esc(src)}" loading="lazy" alt="">')
    if len(imgs) > 1:
        labels = list(pair_labels or [])
        cells = []
        for i, img in enumerate(imgs):
            tag = (f'<span class="ptag">{_esc(labels[i])}</span>'
                   if i < len(labels) and labels[i] else "")
            cells.append(f"<span>{img}{tag}</span>")
        body = f'<div class="pair">{"".join(cells)}</div>'
    else:
        body = "".join(imgs)
    return f'<figure>{body}<figcaption>{cap}</figcaption></figure>'


def _card_html(card, kind, base, missing, modality):
    sev = int(card.get("severity", 1))
    idx = _idx(card["id"])
    buttons = [f'<button type="button" class="apv" aria-pressed="false">'
               f'{ICON_CHECK}<span>Approve</span></button>']
    at = card.get("add_test")
    if at:
        tip = " ".join(x for x in (at.get("label"), at.get("tip")) if x)
        buttons.append(f'<a class="button" href="{_esc(at["link"])}" '
                       f'target="_blank" rel="noopener" title="{_esc(tip)}">'
                       f'{ICON_PLUS}Test</a>')
    ex = card.get("explore")
    if ex:
        tip = " ".join(x for x in (ex.get("text"), ex.get("detail")) if x)
        buttons.append(f'<a class="button" href="{_esc(ex["link"])}" '
                       f'target="_blank" rel="noopener" title="{_esc(tip)}">'
                       f'{ICON_MAGNIFIER}Analyze insight</a>')
    btns = f'<span class="cbtns">{"".join(buttons)}</span>'
    chips = [f'<span class="chip sev"><span class="k">Severity</span>'
             f'<b>{sev} of 3</b></span>']
    chips += [f'<span class="chip">{_esc(c)}</span>'
              for c in card.get("chips", [])
              if not str(c).lower().startswith("insight #")]
    parts = [f'<section class="card" id="{_esc(card["id"])}" '
             f'data-kind="{kind}" data-sev="{sev}">',
             f'<div class="chead"><span class="cidx">{_esc(idx)}</span>'
             f'<h3><span class="apvbadge">{ICON_CHECK}Approved</span>'
             f'{_esc(card["heading"])}</h3>{btns}</div>',
             f'<div class="chips">{"".join(chips)}</div>',
             f'<p class="lede">{_esc(card["lede"])}</p>']
    info = []
    rc = card.get("root_cause")
    if rc:
        info.append(f'<p class="rootcause"><b>Root cause · {_esc(rc["family"])}:'
                    f'</b> {_esc(rc["caption"])}</p>')
    info.extend(_para(p) for p in card.get("prose", []))
    facts = []
    if card.get("split"):
        facts.append(_splitbar_html(card["split"]))
    if card.get("contrast"):
        facts.append(_contrast_html(card["contrast"]))
    if facts:
        info.append(f'<div class="facts">{"".join(facts)}</div>')
    folds = []
    if info:
        folds.append(f'<details class="info"><summary>Analysis info</summary>'
                     f'<div class="fold">{"".join(info)}</div></details>')
    if card.get("observe"):
        folds.append(f'<details class="visual"><summary>LLM {modality} analysis'
                     f'</summary><div class="fold">{_para(card["observe"])}'
                     f'</div></details>')
    grid = card.get("grid", "default")
    cls = "grid" if grid == "default" else f"grid {grid}"
    samples = card.get("samples", [])
    if samples:
        pl = card.get("pair_labels")
        figs = [_figure_html(s, base, missing, pl) for s in samples]
        inner = []
        if card.get("view_intro"):
            inner.append(f'<p class="viewnote">* {_esc(card["view_intro"])}</p>')
        inner.append(f'<div class="{cls}">{"".join(figs)}</div>')
        folds.append(f'<details class="samples"><summary>Samples '
                     f'({len(figs)})</summary><div class="fold">'
                     f'{"".join(inner)}</div></details>')
    if folds:
        parts.append(f'<div class="folds">{"".join(folds)}</div>')
    if card.get("do_next"):
        items = "".join(f"<li>{_esc(x)}</li>" for x in card["do_next"])
        dl = card.get("download_csv")
        row = ""
        if dl:
            if not os.path.isfile(os.path.join(base, dl["path"])):
                missing.append(f'missing csv: {dl["path"]}')
            name = os.path.basename(dl["path"])
            row = (f'<div class="btnrow"><a class="download" '
                   f'href="{_esc(dl["path"])}" download="{_esc(name)}">'
                   f'{_esc(dl["label"])}</a></div>')
        parts.append(f'<div class="donext"><h4>Do next</h4>'
                     f'<ul class="actions">{items}</ul>{row}</div>')
    parts.append("</section>")
    return "".join(parts)


def _bold_leading_count(text):
    m = re.match(r"(\d[\d,]*)\s(.*)", str(text))
    return f"<b>{_esc(m.group(1))}</b> {_esc(m.group(2))}" if m else _esc(text)


def _header_html(spec):
    chips = []
    if spec.get("evaluated"):
        chips.append(f'<span class="chip">evaluated '
                     f'<b>{_esc(spec["evaluated"])}</b></span>')
    chips.append(f'<span class="chip">{_bold_leading_count(spec["meta"])}</span>')
    return (f'<header><div class="hwrap"><div class="hrow">'
            f'<div class="htitle"><span class="eyebrow">Tensorleap analysis'
            f'</span><h1>{_esc(spec["project"])} \u00b7 '
            f'{_esc(spec["version"])}</h1>'
            f'<div class="hchips">{"".join(chips)}</div></div>'
            f'<a class="button" href="{_esc(spec["insights_link"])}" '
            f'target="_blank" rel="noopener">Open in Tensorleap</a>'
            f'{REVIEW_BOX}</div></div></header>')


REVIEW_BOX = (
    '<div class="review"><div class="rtop"><span class="k">Your review</span>'
    '<span><b>0</b> of <i class="tot">0</i> approved</span></div>'
    '<div class="rbar" role="progressbar" aria-label="Insights approved" '
    'aria-valuemin="0" aria-valuenow="0"><span></span></div>'
    '<div class="rnote"></div>'
    f'<div class="rfoot"><div class="rsaved">{ICON_CHECK}<span></span></div>'
    '<div class="racts"><button type="button" class="share" '
    f'title="Download a read-only copy to share">{ICON_DOWNLOAD}Share</button>'
    '</div></div></div>')


def _tile_html(tile):
    label = str(tile["label"])
    kind = next((_kind(t) for t in TYPE_KIND if t.lower() in label.lower()), "")
    attr = f' data-kind="{kind}"' if kind else ""
    return (f'<div class="tile"{attr}><b>{_esc(tile["value"])}</b>'
            f'<span>{_esc(label)}</span></div>')


def _review_state(spec, base):
    """The page's review state. Each card is matched to its platform insight in
    insights.json beside report.json: cards start approved when the insight is,
    and with the server's API address and the insight ids ("sync") Approve updates
    the insight on the server (insights/reportApproval). Without them (an older
    server's export) approvals stay in the reader's browser."""
    state = {"reportId": uuid.uuid4().hex, "readonly": False, "approved": [], "savedAt": ""}
    try:
        digest = json.load(open(os.path.join(base, "insights.json")))
    except (OSError, ValueError):
        return state
    by_index = {}
    stack = list(digest.get("insights") or [])
    while stack:
        d = stack.pop()
        by_index[d.get("index")] = d
        stack.extend(d.get("subinsights") or [])
    ids = {}
    for group in spec.get("groups", []):
        for card in group.get("cards", []):
            m = re.fullmatch(r"insight-(\d+)(?:-sub-(\d+))?", str(card.get("id")))
            d = m and by_index.get(int(m.group(2) or m.group(1)))
            if not d:
                continue
            if d.get("status") == "Approved":
                state["approved"].append(card["id"])
            if d.get("cid"):
                ids[card["id"]] = d["cid"]
    if ids and digest.get("api_base") and digest.get("projectId"):
        state["sync"] = {"apiBase": digest["api_base"], "projectId": digest["projectId"], "insights": ids}
    return state


def _report_html(spec, base, missing):
    title = _esc(f'Tensorleap analysis · {spec["project"]} / {spec["version"]}')
    parts = [_header_html(spec), "<main>"]
    tiles = "".join(_tile_html(t) for t in spec.get("tiles", []))
    if tiles:
        parts.append(f'<div class="tiles">{tiles}</div>')
    parts.append(f'<section class="block"><h2>Executive summary</h2>'
                 f'{_para(spec["executive_summary"], "summary")}</section>')
    rows = ["<thead><tr><th>Insight #</th><th>Issue</th><th>Severity</th>"
            '<th class="num">Samples</th><th>First action</th></tr></thead>']
    for group in spec.get("overview", []):
        rows.append(f'<tr class="typerow" data-kind="{_kind(group["type"])}">'
                    f'<td colspan="5"><span class="tick"></span>'
                    f'{_esc(group["type"])}</td></tr>')
        for r in group["rows"]:
            sev = str(r["severity"]).strip()[:1]
            sev_attr = f' data-sev="{sev}"' if sev in "123" else ""
            word = SEV_WORDS.get(sev, _esc(r["severity"]))
            rows.append(f'<tr><td><a class="rownum" href="#{_esc(r["anchor"])}">'
                        f'{_esc(r["num"])}</a></td>'
                        f'<td class="issue">{_esc(r["issue"])}</td>'
                        f'<td class="sevcell"{sev_attr}>{word}</td>'
                        f'<td class="num">{_esc(r["samples"])}</td>'
                        f'<td class="action">{_esc(r["first_action"])}</td></tr>')
    parts.append(f'<section class="block"><h2>Findings</h2>'
                 f'<p class="sortnote">Within each insight type, ordered by '
                 f'severity, then by the number of affected samples.</p>'
                 f'<div class="tablewrap"><table>{"".join(rows)}</table>'
                 f'</div></section>')
    parts.append('<section class="block" id="approved" hidden>'
                 '<div class="grouphead"><h2>Approved</h2>'
                 '<span class="count">0</span></div>'
                 '<p class="blurb">Findings the reviewer agreed with, pinned '
                 'above the rest. Approve or undo from each card header.</p>'
                 '<div class="cards"></div></section>')
    modality = _modality(spec)
    for group in spec.get("groups", []):
        kind = _kind(group["type"])
        cards = "".join(_card_html(c, kind, base, missing, modality)
                        for c in group["cards"])
        parts.append(f'<section class="block group">'
                     f'<div class="grouphead"><h2>{_esc(group["type"])}</h2>'
                     f'<span class="count">{_esc(group["count"])}</span></div>'
                     f'<p class="blurb">{_esc(group["meaning"])}</p>'
                     f'<div class="cards">{cards}</div></section>')
    parts.append("</main>")
    state = _review_state(spec, base)
    parts.append('<div class="toast" role="status" hidden></div>')
    parts.append(f'<script type="application/json" id="tl-review">{json.dumps(state)}</script>')
    parts.append(f"<script>\n{REVIEW_JS}</script>")
    body = "\n".join(parts)
    return (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{title}</title>\n<style>\n{_font_css()}\n{REPORT_CSS}'
            f'</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n')


def _strip_tags(text):
    import re
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()


def _report_txt(spec):
    head = " · ".join(x for x in (
        f'evaluated {spec["evaluated"]}' if spec.get("evaluated") else "",
        _strip_tags(spec["meta"])) if x)
    lines = [f'Tensorleap analysis: {spec["project"]} / {spec["version"]}',
             head, ""]
    lines += [f'[tile] {t["value"]}: {t["label"]}' for t in spec.get("tiles", [])]
    lines += ["", "Executive summary",
              _strip_tags(spec["executive_summary"]), "", "Overview table"]
    for group in spec.get("overview", []):
        lines.append(f'-- {group["type"]}')
        lines += [f'{r["num"]} | {_strip_tags(r["issue"])} | sev {r["severity"]} | '
                  f'{r["samples"]} samples | {_strip_tags(r["first_action"])}'
                  for r in group["rows"]]
    modality = _modality(spec)
    for group in spec.get("groups", []):
        lines += ["", f'== {group["type"]} ({group["count"]}): '
                      f'{_strip_tags(group["meaning"])}']
        for c in group["cards"]:
            samples = c.get("samples", [])
            lines += ["", f'### {_strip_tags(c["heading"])}  [#{c["id"]}]',
                      "chips: " + " · ".join(
                          [f'Severity {c.get("severity", 1)} of 3']
                          + [_strip_tags(x) for x in c.get("chips", [])])]
            rc = c.get("root_cause")
            if rc:
                lines.append(f'Root cause · {rc["family"]}: {_strip_tags(rc["caption"])}')
            ex = c.get("explore")
            if ex:
                lines.append(f'[header button] Analyze insight '
                             f'(tooltip: {_strip_tags(ex.get("text", ""))} '
                             f'{_strip_tags(ex.get("detail", ""))})')
            lines.append(f'Bottom line: {_strip_tags(c["lede"])}')
            lines += [_strip_tags(p) for p in c.get("prose", [])]
            if c.get("split"):
                lines.append("split: " + ", ".join(
                    f'{s["state"]} {s["count"]}' for s in c["split"]))
            for x in c.get("contrast", []):
                lines.append(f'{_strip_tags(x["metric"])}: this group '
                             f'{_fmt(x["group"])} vs all data {_fmt(x["all"])}')
            if c.get("view_intro"):
                lines.append(f'view: {_strip_tags(c["view_intro"])}')
            caps = [_strip_tags(s.get("caption", "")) for s in samples]
            if caps:
                lines.append(f'samples (collapsed "Samples ({len(caps)})" fold, '
                             f'all shown on open): ' + " | ".join(caps))
            if c.get("observe"):
                lines.append(f'LLM {modality} analysis: '
                             f'{_strip_tags(c["observe"])}')
            lines += [f'Do next: {_strip_tags(x)}' for x in c.get("do_next", [])]
            if c.get("download_csv"):
                lines.append(f'Download button: {c["download_csv"]["label"]} '
                             f'({c["download_csv"]["path"]})')
            if c.get("add_test"):
                lines.append(f'[header button] Test, {c["add_test"]["label"]} '
                             f'(opens Tensorleap, user confirms; '
                             f'{c["add_test"].get("tip", "")})')
    return "\n".join(lines) + "\n"


def _no_dashes(v):
    """House style: no em-dashes anywhere in the rendered report."""
    if isinstance(v, str):
        return v.replace(" \u2014 ", ", ").replace("\u2014", "-")
    if isinstance(v, list):
        return [_no_dashes(x) for x in v]
    if isinstance(v, dict):
        return {k: _no_dashes(x) for k, x in v.items()}
    return v


def cmd_build_report(args):
    spec_path = os.path.join(args.dir, "report.json")
    try:
        spec = _no_dashes(json.load(open(spec_path)))
    except Exception as e:
        print(f"cannot read {spec_path}: {e}", file=sys.stderr)
        raise SystemExit(8)
    missing = []
    try:
        html = _report_html(spec, args.dir, missing)
        txt = _report_txt(spec)
    except (KeyError, TypeError, AttributeError) as e:
        print(f"report.json invalid: missing/bad field {e}", file=sys.stderr)
        raise SystemExit(8)
    html_path = os.path.join(args.dir, "report.html")
    open(html_path, "w", encoding="utf-8").write(html)
    txt_path = os.path.join(args.dir, "report.txt")
    open(txt_path, "w", encoding="utf-8").write(txt)
    print(f"{html_path}\n{txt_path}")
    if missing:
        for msg in missing:
            print(msg, file=sys.stderr)
        raise SystemExit(8)


def cmd_inline_html(args):
    import mimetypes
    import re
    try:
        from PIL import Image
    except ImportError:
        Image = None
    base = os.path.dirname(os.path.abspath(args.file))
    doc = open(args.file, encoding="utf-8").read()
    missing = []

    def encode(path, mime):
        data = open(path, "rb").read()
        if not Image or not mime.startswith("image/") or len(data) <= 50_000:
            return mime, data
        img = Image.open(io.BytesIO(data))
        if img.mode in ("RGBA", "LA", "P"):
            return mime, data
        # trim flat letterbox padding (uniform corner-colour bands) when it
        # is at least 8% of the frame; pairs of one frame share the same box
        from PIL import ImageChops
        corner = img.getpixel((0, 0))
        bbox = ImageChops.difference(
            img.convert("RGB"), Image.new("RGB", img.size, corner)
        ).point(lambda v: 255 if v > 12 else 0).getbbox()
        if bbox:
            w, h = img.size
            trimmed = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if 0.4 * w * h <= trimmed <= 0.92 * w * h:
                img = img.crop(bbox)
        if max(img.size) > 900:
            img.thumbnail((900, 900))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        if buf.tell() < len(data):
            return "image/jpeg", buf.getvalue()
        return mime, data

    root = os.path.realpath(base)

    def inside(path):
        real = os.path.realpath(path)
        return os.path.commonpath([root, real]) == root and os.path.isfile(real)

    def repl(m):
        src = html.unescape(m.group(2))
        if src.startswith(("data:", "http://", "https://")):
            return m.group(0)
        path = os.path.join(base, urllib.request.url2pathname(src))
        if not inside(path):
            missing.append(src)
            return m.group(0)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        mime, data = encode(path, mime)
        b64 = base64.b64encode(data).decode()
        return f"{m.group(1)}data:{mime};base64,{b64}{m.group(3)}"

    doc = re.sub(r'(src=")([^"]+)(")', repl, doc)
    # ponytail: 5 MB cap on embedded downloads; beyond it the file stays
    # beside report.html as a relative link (signed URLs expire in 1 h, so
    # a remote link is never an option)
    def repl_dl(m):
        path = os.path.join(base, urllib.request.url2pathname(html.unescape(m.group(2))))
        if not inside(path) or os.path.getsize(path) > 5_000_000:
            return m.group(0)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        b64 = base64.b64encode(open(path, "rb").read()).decode()
        return f"{m.group(1)}data:{mime};base64,{b64}{m.group(3)}"
    doc = re.sub(r'(<a class="download" href=")([^"]+)(")', repl_dl, doc)
    open(args.file, "w", encoding="utf-8").write(doc)
    size_mb = os.path.getsize(args.file) / 1e6
    print(f"{args.file}: {size_mb:.1f} MB, {len(missing)} unresolved src paths")
    if missing:
        for src in missing:
            print(f"unresolved: {src}", file=sys.stderr)
        raise SystemExit(7)


def main():
    parser = argparse.ArgumentParser(prog="tl_api.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    dg = sub.add_parser("digest")
    dg.add_argument("dir")
    rc = sub.add_parser("render-charts")
    rc.add_argument("dir")
    sm = sub.add_parser("summarize")
    sm.add_argument("dir")
    br = sub.add_parser("build-report")
    br.add_argument("dir")
    ih = sub.add_parser("inline-html")
    ih.add_argument("file")
    args = parser.parse_args()
    stage, code = {"digest": "started", "build-report": "finished"}.get(args.cmd), 1
    try:
        {"digest": cmd_digest, "render-charts": cmd_render_charts,
         "summarize": cmd_summarize, "build-report": cmd_build_report,
         "inline-html": cmd_inline_html}[args.cmd](args)
        code = 0
    except SystemExit as e:
        code = e.code or 0
        raise
    finally:
        if stage:
            skill_run("tensorleap-analysis", stage, exit_code=code)


if __name__ == "__main__":
    main()
