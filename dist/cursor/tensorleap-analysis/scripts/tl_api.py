#!/usr/bin/env python3
"""tensorleap-analysis report plumbing.

Data comes from the Tensorleap MCP server (`leap mcp`): tl_export_analysis
writes a version's analysis to a directory with a manifest.json. This script
prepares that directory for the analysis and checks the report written from it:

  tl_api.py digest DIR              # manifest.json -> insights.json (the digest the skill reads)
  tl_api.py summarize DIR           # per-insight composition stats from samples.csv
  tl_api.py render-charts DIR       # chart.png / boxes.jpg for non-image payloads, thumbnails
  tl_api.py check-report DIR        # check DIR/report.json before it is published, write report.txt
  tl_api.py open-report LINK        # open the published report in the user's browser, or say how to reach it

Exit codes:
  0  ok
  2  bad arguments
  5  DIR has no manifest.json or no insights
  6  matplotlib unavailable (render-charts only, fall back to html tables)
  8  check-report: report.json invalid, or it shows a sample the export does not hold (listed on stderr)
"""
import argparse
import csv
import getpass
import html
import ipaddress
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import urllib.parse

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
        if ins.get("addCommentLink"):
            d["add_comment_link"] = ins["addCommentLink"]
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


REPORT_VERSION = 1
MAX_REPORT_BYTES = 1024 * 1024
MODALITIES = ("visual", "textual", "auditory")
LAYOUTS = ("default", "wide", "solo")
# The platform's name for what a group holds; it picks the group's colour in the UI.
KINDS = ("low_performance", "out_of_distribution", "duplication",
         "data_leakage", "domain_gap", "mislabeled_samples")
SEV_WORDS = {1: "LOW", 2: "MEDIUM", 3: "HIGH"}
MISLABELING = "Mislabeling"
FAMILIES = ("Data gap", "Label quality", MISLABELING, "Split problem", "Model behavior")
FINDING_ID = re.compile(r"insight-(\d{1,6})(?:-sub-(\d{1,6}))?")
# The reader has never opened the platform, so its vocabulary stays out of the text.
PLATFORM_WORDS = re.compile(r"tensorleap|\binsights?\b|\bfailure modes?\b", re.I)
PROSE_TAGS = ("em", "strong", "code", "a")
TAG = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b[^>]*>")
MISLABEL_CHIP = re.compile(r"\W*(coherent(ly)?\s+)?mis+[-\s]?label+(ed|ing)\W*", re.I)
MISLABEL_FAMILY = re.compile(r"\s*mis+[-\s]?label+(ed|ing)\s*", re.I)

TEXT, NUMBER, LIST, OBJECT = "text", "a number", "a list", "an object"
_IS = {TEXT: lambda v: isinstance(v, str),
       NUMBER: lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
       LIST: lambda v: isinstance(v, list),
       OBJECT: lambda v: isinstance(v, dict)}


def _idx(finding_id):
    m = FINDING_ID.fullmatch(str(finding_id))
    if not m:
        return ""
    return f"#{m.group(1)} · sub #{m.group(2)}" if m.group(2) else f"#{m.group(1)}"


def _fmt(v):
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def _strip_tags(text):
    return html.unescape(re.sub(r"<[^>]+>", "", str(text or ""))).strip()


def _no_dashes(v):
    """House style: no em-dashes anywhere in the report."""
    if isinstance(v, str):
        return v.replace(" — ", ", ").replace("—", "-")
    if isinstance(v, list):
        return [_no_dashes(x) for x in v]
    if isinstance(v, dict):
        # a sample's quoted text is the data's own words, left as written
        return {k: x if k == "text" else _no_dashes(x) for k, x in v.items()}
    return v


def _fields(obj, where, problems, required, optional=None):
    """Names each missing or mistyped field of obj. A field this script does not
    know is left alone: the server keeps it and the UI ignores it."""
    if not isinstance(obj, dict):
        problems.append(f"{where} must be an object")
        return False
    ok = True
    for name, kind in {**required, **(optional or {})}.items():
        if name not in obj:
            if name in required:
                problems.append(f"{where}: {name} is missing")
                ok = False
        elif not _IS[kind](obj[name]):
            problems.append(f"{where}: {name} must be {kind}")
            ok = False
        elif kind == TEXT and name in required and not obj[name].strip():
            problems.append(f"{where}: {name} is empty")
            ok = False
    return ok


def _export_index(export_dir):
    """What check-report needs to know of the export: each group's parent, the
    views downloaded per group and sample (True when one of its payloads
    rendered, False when every one is a failed render), and the view names that
    read as identifiers."""
    manifest = json.load(open(os.path.join(export_dir, "manifest.json")))
    parents, views = {}, {}
    for ins in manifest.get("insights") or []:
        parents[ins.get("index")] = ins.get("parentIndex") or None
        for smp in ins.get("samples") or []:
            sample = views.setdefault(ins.get("index"), {}).setdefault(str(smp.get("id")), {})
            for path in smp.get("files") or []:
                # .../samples/<sample>/<data type>/<view>/payload.json
                parts = path.replace(os.sep, "/").split("/samples/", 1)[-1].split("/")
                if len(parts) != 4 or parts[3] != "payload.json":
                    continue
                try:
                    rendered = not json.load(open(os.path.join(export_dir, path))).get("has_error")
                except (OSError, ValueError):
                    rendered = True
                sample[parts[2]] = sample.get(parts[2], False) or rendered
    names = {str(v.get("name")) for v in manifest.get("visualizers") or []}
    return parents, views, {n for n in names if "_" in n}


def _report_problems(spec, parents, views, visualizer_names):
    problems, seen, texts = [], set(), []

    def say(where, value, prose=False):
        if isinstance(value, str):
            texts.append((where, value, prose))

    def kind_of(obj, where):
        if "kind" in obj and obj["kind"] not in KINDS:
            problems.append(f'{where}: kind must be one of {", ".join(KINDS)}')

    if not _fields(spec, "report.json", problems,
                   {"schemaVersion": NUMBER, "project": TEXT, "version": TEXT, "modality": TEXT,
                    "tiles": LIST, "summary": TEXT, "groups": LIST},
                   {"evaluated": TEXT, "coverage": TEXT}):
        return problems
    if spec["schemaVersion"] != REPORT_VERSION:
        problems.append(f"schemaVersion must be {REPORT_VERSION}")
    if spec["modality"] not in MODALITIES:
        problems.append(f'modality must be one of {", ".join(MODALITIES)}')
    # every finding a link may point to, also one that is refused below
    declared = {f["id"] for g in spec["groups"] if isinstance(g, dict) and isinstance(g.get("findings"), list)
                for f in g["findings"] if isinstance(f, dict) and isinstance(f.get("id"), str)}
    say("summary", spec["summary"], prose=True)
    say("coverage", spec.get("coverage"))
    for i, tile in enumerate(spec["tiles"], 1):
        if _fields(tile, f"tile {i}", problems, {"value": TEXT, "label": TEXT}, {"kind": TEXT}):
            kind_of(tile, f"tile {i}")
            say(f"tile {i}", tile["label"])

    for g, group in enumerate(spec["groups"], 1):
        where = f"group {g}"
        if not _fields(group, where, problems, {"title": TEXT, "findings": LIST},
                       {"kind": TEXT, "count": TEXT, "meaning": TEXT}):
            continue
        kind_of(group, where)
        say(where, group["title"])
        say(where, group.get("meaning"))
        ranks = [f["severity"] for f in group["findings"]
                 if isinstance(f, dict) and _IS[NUMBER](f.get("severity"))]
        if ranks != sorted(ranks, reverse=True):
            problems.append(f"{where}: its findings must be ordered by severity, highest first")
        for f in group["findings"]:
            where = f'finding {f.get("id")}' if isinstance(f, dict) else "a finding"
            if not _fields(f, where, problems,
                           {"id": TEXT, "title": TEXT, "chips": LIST, "bottomLine": TEXT,
                            "analysis": LIST, "split": LIST, "contrast": LIST, "doNext": LIST},
                           {"severity": NUMBER, "shortTitle": TEXT, "sampleCount": TEXT,
                            "firstAction": TEXT, "rootCause": OBJECT, "observations": TEXT,
                            "samples": OBJECT, "test": OBJECT}):
                continue
            own = None
            match = FINDING_ID.fullmatch(f["id"])
            if not match:
                problems.append(f"{where}: id must be insight-<number> or insight-<number>-sub-<number>")
            elif f["id"] in seen:
                problems.append(f"{where}: this id appears more than once")
            else:
                group_no, sub_no = int(match.group(1)), match.group(2) and int(match.group(2))
                own = sub_no or group_no
                if own not in parents or parents[own] != (group_no if sub_no else None):
                    own = None
                    problems.append(f"{where}: the export has no " + (
                        f"sub-group {sub_no} under group {group_no}" if sub_no else f"top-level group {group_no}")
                        + "; the id names the group the finding is about, by its number in the digest")
            seen.add(f["id"])
            if "severity" in f and f["severity"] not in (1, 2, 3):
                problems.append(f"{where}: severity is 1, 2 or 3")
            for name in ("title", "shortTitle", "firstAction", "bottomLine"):
                say(where, f.get(name))
            say(where, f.get("observations"), prose=True)
            for name, prose in (("chips", False), ("analysis", True), ("doNext", False)):
                if not all(isinstance(x, str) for x in f[name]):
                    problems.append(f"{where}: {name} must hold text only")
                for x in f[name]:
                    say(where, x, prose)
            for part in f["split"]:
                _fields(part, f"{where} split", problems, {"state": TEXT, "count": NUMBER})
            for row in f["contrast"]:
                if _fields(row, f"{where} contrast", problems, {"metric": TEXT, "group": NUMBER, "all": NUMBER}):
                    say(where, row["metric"])
            if "test" in f and _fields(f["test"], f"{where} test", problems, {"hint": TEXT}):
                say(where, f["test"]["hint"])

            for chip in f["chips"]:
                if isinstance(chip, str) and MISLABEL_CHIP.fullmatch(chip):
                    problems.append(f'{where}: remove the hand-written "{chip}" chip; the report shows a '
                                    f'Mislabeled chip by itself when the root cause family is {MISLABELING}')
            rc = f.get("rootCause")
            if rc is not None and _fields(rc, f"{where} rootCause", problems, {"family": TEXT, "caption": TEXT}):
                say(where, rc["caption"])
                families = [part.strip() for part in rc["family"].split(",")]
                if any(MISLABEL_FAMILY.fullmatch(part) for part in families):
                    if rc["family"] != MISLABELING:
                        problems.append(f'{where}: root cause family "{rc["family"]}" must be written exactly as '
                                        f'"{MISLABELING}", alone (never with a second family)')
                    elif group.get("kind") != "low_performance":
                        problems.append(f"{where}: root cause {MISLABELING} is only for a group of kind "
                                        f"low_performance that is just coherent mislabelling")
                elif len(set(families)) != len(families) or len(families) > 2 or any(
                        part not in FAMILIES for part in families):
                    problems.append(f'{where}: root cause family "{rc["family"]}" must be one of '
                                    f'{", ".join(FAMILIES)} (or two of them, comma-separated)')

            samples = f.get("samples")
            if samples is None or not _fields(samples, f"{where} samples", problems,
                                              {"layout": TEXT, "viewLabels": LIST, "items": LIST}, {"intro": TEXT}):
                continue
            if samples["layout"] not in LAYOUTS:
                problems.append(f'{where}: samples layout must be one of {", ".join(LAYOUTS)}')
            say(where, samples.get("intro"))
            # a card shows its own group's samples; a top-level one may also show its sub-groups'
            mine = [g for g in views if own is not None and (g == own or parents.get(g) == own)]
            pair = False
            for item in samples["items"]:
                if not _fields(item, f"{where} sample", problems, {"caption": TEXT, "visualizers": LIST},
                               {"sampleId": TEXT, "text": TEXT}):
                    continue
                say(where, item["caption"])
                say(where, item.get("text"))
                sample_id, shown = item.get("sampleId"), item["visualizers"]
                if not sample_id or not 1 <= len(shown) <= 2:
                    if "text" not in item:
                        problems.append(f'{where}: the sample "{item["caption"][:50]}" must be a text, or a '
                                        f'sampleId with one or two visualizers')
                    continue
                pair = pair or len(shown) == 2
                if own is None:
                    continue
                stored = {}
                for g in mine:
                    for name, rendered in views[g].get(sample_id, {}).items():
                        stored[name] = stored.get(name, False) or rendered
                if not any(sample_id in views[g] for g in mine) and any(
                        sample_id in by_sample for by_sample in views.values()):
                    problems.append(f"{where}: sample {sample_id} was exported for another group; "
                                    f"a card shows samples of its own group")
                    continue
                for view in shown:
                    if view not in stored:
                        problems.append(f'{where}: the export has no "{view}" view of sample {sample_id}; '
                                        f'use a sample and a view that the export downloaded')
                    elif not stored[view]:
                        problems.append(f'{where}: the "{view}" view of sample {sample_id} did not render '
                                        f'(its stored payload is an error); show another view')
            if pair and not (len(samples["viewLabels"]) == 2 and all(isinstance(x, str) for x in samples["viewLabels"])):
                problems.append(f"{where}: viewLabels must name the two views of a pair, in order")
            for label in samples["viewLabels"]:
                say(where, label)

    for where, text, prose in texts:
        tags = {m.group(1).lower() for m in TAG.finditer(text)}
        if not prose and tags:
            problems.append(f'{where}: "{text[:50]}" must be plain text; markup shows as it is written')
        elif tags - set(PROSE_TAGS):
            problems.append(f'{where}: only {", ".join(PROSE_TAGS)} markup is shown, remove '
                            f'{", ".join(sorted(tags - set(PROSE_TAGS)))}')
        for anchor in re.findall(r'href="#([^"]*)"', text):
            if anchor not in declared:
                problems.append(f'{where}: a link points to "#{anchor}", which is not a finding in this report')
        plain = _strip_tags(text)
        word = PLATFORM_WORDS.search(plain)
        if word:
            problems.append(f'{where}: "{word.group(0)}" is the platform\'s vocabulary; say it in the '
                            f'reader\'s words ("{plain[:50]}")')
        for name in visualizer_names:
            if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w.])", plain):
                problems.append(f'{where}: "{name}" is a name from the integration code; say what the view shows instead')
    if len(json.dumps(spec, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_REPORT_BYTES:
        problems.append("the report is larger than 1 MB; shorten the text or show fewer samples")
    return problems


def _report_txt(spec):
    """The report as its reader meets it in the UI, top to bottom, without the layout."""
    lines = ["Model analysis", f'{spec["project"]} · {spec["version"]}',
             " · ".join(x for x in (f'evaluated {spec["evaluated"]}' if spec.get("evaluated") else "",
                                         spec.get("coverage", "")) if x), ""]
    lines += [f'[tile] {t["value"]}: {t["label"]}' for t in spec["tiles"]]
    lines += ["", "Executive summary", _strip_tags(spec["summary"]), "", "Findings"]
    for group in spec["groups"]:
        lines.append(f'-- {group["title"]}')
        lines += [" | ".join((_idx(f["id"]), f.get("shortTitle") or f["title"],
                              SEV_WORDS.get(f.get("severity"), ""), f.get("sampleCount", ""),
                              f.get("firstAction", ""))) for f in group["findings"]]
    for group in spec["groups"]:
        lines += ["", "== " + group["title"]
                  + (f' ({group["count"]})' if group.get("count") else "")
                  + (f': {group["meaning"]}' if group.get("meaning") else "")]
        for f in group["findings"]:
            rc = f.get("rootCause")
            chips = ([f'Severity {f["severity"]} of 3'] if f.get("severity") else []) + f["chips"]
            if rc and rc["family"] == MISLABELING:
                chips.append("Mislabeled")
            lines += ["", f'### {f["title"]}  [{_idx(f["id"])}]', "chips: " + " · ".join(chips),
                      f'Bottom line: {f["bottomLine"]}']
            if rc:
                lines.append(f'Root cause · {rc["family"]}: {rc["caption"]}')
            lines += [_strip_tags(p) for p in f["analysis"]]
            if f["split"]:
                lines.append("split: " + ", ".join(f'{s["state"]} {s["count"]}' for s in f["split"]))
            lines += [f'{x["metric"]}: this group {_fmt(x["group"])} vs all data {_fmt(x["all"])}'
                      for x in f["contrast"]]
            if f.get("observations"):
                lines.append(f'LLM {spec["modality"]} analysis: {_strip_tags(f["observations"])}')
            samples = f.get("samples")
            if samples:
                if samples.get("intro"):
                    lines.append(f'view: {samples["intro"]}')
                if samples["viewLabels"]:
                    lines.append("views: " + " | ".join(samples["viewLabels"]))
                lines.append(f'samples (collapsed "Samples ({len(samples["items"])})" fold, all shown on open): '
                             + " | ".join(s["caption"] for s in samples["items"]))
            lines += [f"Do next: {x}" for x in f["doNext"]]
            if f.get("test"):
                lines.append(f'[button] Add the regression test ({f["test"]["hint"]})')
    return "\n".join(lines) + "\n"


def cmd_check_report(args):
    export_dir = os.path.abspath(args.dir)
    if not os.path.isfile(os.path.join(export_dir, "manifest.json")):
        print(f"{export_dir} has no manifest.json; run the tl_export_analysis tool into it first",
              file=sys.stderr)
        raise SystemExit(5)
    path = os.path.join(export_dir, "report.json")
    try:
        spec = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print(f"cannot read {path}: {e}", file=sys.stderr)
        raise SystemExit(8)
    if _no_dashes(spec) != spec:
        # report.json is what gets published, so the house style is applied to the file itself
        spec = _no_dashes(spec)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("report.json: em-dashes rewritten")
    problems = _report_problems(spec, *_export_index(export_dir))
    if problems:
        for msg in problems:
            print(f"report.json invalid: {msg}", file=sys.stderr)
        raise SystemExit(8)
    txt_path = os.path.join(export_dir, "report.txt")
    open(txt_path, "w", encoding="utf-8").write(_report_txt(spec))
    print(txt_path)


def _desktop_opener(env, platform):
    """The command that opens a link in this machine's browser, or (None, why not).
    An SSH session, or Linux without a display, means the user's browser is on
    another machine."""
    if env.get("SSH_CONNECTION") or env.get("SSH_TTY"):
        return None, "SSH session"
    if platform == "darwin":
        return "open", ""
    if platform.startswith("linux"):
        if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
            return "xdg-open", ""
        return None, "no display"
    return None, f"no known way to open a browser on {platform}"


def _or_placeholder(lookup, placeholder):
    """lookup() or, when it fails (no login name in a container, say), the placeholder."""
    try:
        return lookup() or placeholder
    except Exception:
        return placeholder


def _ssh_server_address(ssh_connection):
    """The server address from SSH_CONNECTION, or "<server>" when it is loopback: under AWS
    SSM or another local port forward that address means nothing on the user's machine."""
    addr = ssh_connection[2]
    try:
        if ipaddress.ip_address(addr.split("%")[0]).is_loopback:
            return "<server>"
    except ValueError:
        pass
    return addr


def cmd_open_report(args):
    link = urllib.parse.urlsplit(args.link)
    if link.scheme not in ("http", "https") or not link.hostname:
        print(f"not a link: {args.link}", file=sys.stderr)
        raise SystemExit(2)
    opener, why = _desktop_opener(os.environ, sys.platform)
    lines = []
    if opener:
        exe = shutil.which(opener)
        if not exe:
            opener, why = None, f"{opener} not found"
        else:
            try:
                proc = subprocess.Popen([exe, args.link], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if proc.wait(timeout=10):
                    opener, why = None, f"{opener} exited {proc.returncode}"
            except OSError as e:
                opener, why = None, f"{opener} failed: {e}"
            except subprocess.TimeoutExpired:
                # some xdg-open setups wait for the browser to close; it is left running
                why = f"{opener} still running after 10 s"
    if opener:
        lines.append(f"opened: {args.link}" + (f" ({why})" if why else ""))
    else:
        lines += [f"not opened: {why}", f"report: {args.link}"]
        # a link to this machine's own server only works on the user's machine through a tunnel
        if why in ("SSH session", "no display") and link.hostname in ("localhost", "127.0.0.1"):
            ssh = (os.environ.get("SSH_CONNECTION") or "").split()
            host = f"{_or_placeholder(getpass.getuser, '<user>')}@" + (
                _ssh_server_address(ssh) if len(ssh) > 2 else _or_placeholder(socket.gethostname, "<host>"))
            port = link.port or (443 if link.scheme == "https" else 80)
            lines.append(f"tunnel: ssh -N -L {port}:localhost:{port} {host}")
    print("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(prog="tl_api.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("digest", "render-charts", "summarize", "check-report"):
        sub.add_parser(name).add_argument("dir")
    sub.add_parser("open-report").add_argument("link")
    args = parser.parse_args()
    stage, code = {"digest": "started", "check-report": "finished"}.get(args.cmd), 1
    try:
        {"digest": cmd_digest, "render-charts": cmd_render_charts,
         "summarize": cmd_summarize, "check-report": cmd_check_report,
         "open-report": cmd_open_report}[args.cmd](args)
        code = 0
    except SystemExit as e:
        code = e.code or 0
        raise
    finally:
        if stage:
            skill_run("tensorleap-analysis", stage, exit_code=code)


if __name__ == "__main__":
    main()
