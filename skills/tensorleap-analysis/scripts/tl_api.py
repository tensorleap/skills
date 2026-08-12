#!/usr/bin/env python3
"""Tensorleap node-server client for the tensorleap-analysis skill.

Auth and base URL come from the leap CLI config (~/.config/tensorleap/config.yaml,
override path with TENSORLEAP_CONFIG): auth.api_url + auth.api_key. The api_key
may be absent on --disable-auth installs; requests are then sent without a header.
Stdlib only — no pip dependencies (render-charts alone needs matplotlib and says
so via its exit code).

Usage:
  tl_api.py whoami
  tl_api.py list-versions [--project NAME_OR_ID]
  tl_api.py fetch --project ID --version ID --out DIR [--top-k 10]
                  [--rank-by COLUMN] [--asc]
  tl_api.py render-charts DIR
  tl_api.py inline-html FILE.html   # embed <img src> files as data URIs, in place

Exit codes:
  0  ok
  2  bad arguments / no matching project
  3  not authenticated (missing config, or server rejected the key)
  4  server unreachable or returned an unexpected error
  5  version has no insights
  6  matplotlib unavailable (render-charts only — fall back to html tables)
  7  inline-html: some src paths did not resolve (listed on stderr)
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

API = {"url": None, "key": None}
SUB_TOP_K = 3
CANDIDATE_FACTOR = 4


def read_config():
    cfg = os.environ.get("TENSORLEAP_CONFIG") or os.path.expanduser(
        "~/.config/tensorleap/config.yaml")
    try:
        lines = open(cfg).read().splitlines()
    except Exception as exc:
        print(f"cannot read {cfg}: {exc} — is the CLI logged in? (leap auth login)",
              file=sys.stderr)
        raise SystemExit(3)
    url = key = None
    top = None
    for line in lines:
        if line[:1] not in (" ", "\t", "", "#"):
            top = line.split(":", 1)[0].strip()
        elif top == "auth":
            k, _, v = line.strip().partition(":")
            if k == "api_url" and v.strip():
                url = v.strip().strip("'\"")
            elif k == "api_key" and v.strip():
                key = v.strip().strip("'\"")
    if not url:
        print(f"no auth.api_url in {cfg} — run: leap auth login", file=sys.stderr)
        raise SystemExit(3)
    url = url.rstrip("/")
    if url.endswith("/api/v2"):
        url = url[:-len("/api/v2")]
    API["url"], API["key"] = url, key


def api(path, body, soft=False):
    req = urllib.request.Request(
        f"{API['url']}/api/v2/{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    if API["key"]:
        req.add_header("Authorization", f"Bearer {API['key']}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        if e.code in (401, 403):
            print(f"auth rejected by {API['url']} ({e.code}): {detail}", file=sys.stderr)
            raise SystemExit(3)
        if soft:
            return None
        print(f"POST /api/v2/{path} -> {e.code}: {detail}", file=sys.stderr)
        raise SystemExit(4)
    except urllib.error.URLError as e:
        print(f"cannot reach {API['url']}: {e.reason}", file=sys.stderr)
        raise SystemExit(4)


def fetch_url(url):
    with urllib.request.urlopen(url, timeout=300) as resp:
        return resp.read()


def download_blob(file_name, soft=False):
    resp = api("versions/getDownloadSignedUrl", {"fileName": file_name}, soft=soft)
    if resp is None:
        return None
    try:
        return fetch_url(resp["url"])
    except Exception as e:
        if soft:
            return None
        print(f"download failed for {file_name}: {e}", file=sys.stderr)
        raise SystemExit(4)


def hash_sample_index(raw):
    digest = hashlib.sha256(str(raw).encode()).digest()[:16]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def is_evaluated(version):
    res = version.get("resources") or {}
    return bool(res.get("inference_artifact_id") and res.get("es_metrics_index"))


def cmd_whoami(_args):
    me = api("auth/whoAmI", {})
    local = me.get("local") or {}
    print(json.dumps({
        "api_url": API["url"],
        "email": local.get("email"),
        "name": local.get("name"),
        "teamId": local.get("teamId"),
        "role": me.get("role"),
    }, indent=2))


def cmd_list_versions(args):
    resp = api("projects/getProjects", {})
    projects = resp.get("projects") or resp.get("data") or []
    if not args.project:
        print(json.dumps([{"cid": p.get("cid"), "name": p.get("name")}
                          for p in projects], indent=2))
        return
    needle = args.project.lower()
    matches = [p for p in projects
               if p.get("cid") == args.project or (p.get("name") or "").lower() == needle]
    if not matches:
        matches = [p for p in projects if needle in (p.get("name") or "").lower()]
    if len(matches) != 1:
        names = [p.get("name") for p in matches] or [p.get("name") for p in projects]
        print(f"project {args.project!r} matched {len(matches)} of: {names}",
              file=sys.stderr)
        raise SystemExit(2)
    project = matches[0]
    versions = api("versions/getProjectSlimVersions",
                   {"projectId": project["cid"]}).get("versions", [])
    out = []
    for v in versions:
        if not is_evaluated(v):
            continue
        res = v.get("resources") or {}
        out.append({
            "versionId": v.get("cid"),
            "name": v.get("notes"),
            "serialNumber": v.get("serialNumber"),
            "createdAt": v.get("createdAt"),
            "hasInsightsArtifacts": bool((res.get("vis_resources") or {}).get("insights_revision") is not None),
        })
    print(json.dumps({"projectId": project["cid"], "projectName": project.get("name"),
                      "evaluatedVersions": out}, indent=2))


def sample_ids_from_csv(csv_bytes, rank_by, ascending, k):
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode(errors="replace"))))
    if not rows or "sample_id" not in rows[0]:
        return None, rows[0].keys() if rows else []
    if not rank_by:
        rank_by = next((c for c in rows[0]
                        if c.startswith("metrics.")
                        and ("loss" in c.lower() or "entropy" in c.lower())), None)
    if rank_by and rank_by in rows[0]:
        def keyf(r):
            try:
                return float(r[rank_by])
            except (TypeError, ValueError):
                return float("-inf")
        rows.sort(key=keyf, reverse=not ascending)
    return [r["sample_id"] for r in rows[:k]], list(rows[0].keys())


def sample_ids_from_cluster(cluster_json, k):
    ids = []
    for state, indices in (cluster_json.get("samples_index") or {}).items():
        ids.extend(f"{state}_{i}" for i in indices)
    return ids[:k]


def fetch_insight_files(insight, project_id, out_dir, k, rank_by, ascending, digest):
    itype = insight.get("insightType") or {}
    idir = os.path.join(out_dir, digest["dir"])
    os.makedirs(idir, exist_ok=True)
    sample_ids = None

    csv_path = itype.get("csv_path")
    if csv_path:
        blob = download_blob(f"projects/{project_id}/{csv_path}", soft=True)
        if blob is not None:
            if csv_path.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                    inner = next((n for n in zf.namelist() if n.endswith(".csv")), None)
                    blob = zf.read(inner) if inner else b""
            local_csv = os.path.join(idir, "samples.csv")
            open(local_csv, "wb").write(blob)
            digest["files"]["csv"] = local_csv
            sample_ids, columns = sample_ids_from_csv(blob, rank_by, ascending, k)
            digest["csv_columns"] = list(columns)
            if sample_ids is None:
                digest["errors"].append("csv has no sample_id column")

    if sample_ids is None and itype.get("blob_path"):
        blob = download_blob(f"projects/{project_id}/{itype['blob_path']}", soft=True)
        if blob is not None:
            try:
                sample_ids = sample_ids_from_cluster(json.loads(blob), k)
            except Exception as e:
                digest["errors"].append(f"cluster blob unreadable: {e}")

    top_panel = itype.get("top_panel_path")
    if top_panel:
        blob = download_blob(f"projects/{project_id}/{top_panel}", soft=True)
        if blob is not None:
            local_tp = os.path.join(idir, "top_panel.json")
            open(local_tp, "wb").write(blob)
            digest["files"]["top_panel"] = local_tp
            try:
                summary = (json.loads(blob).get("summary") or {})
                digest["top_panel_summary"] = {"title": summary.get("title"),
                                               "sentence": summary.get("sentence")}
            except Exception:
                pass

    digest["top_samples"] = sample_ids or []
    if not sample_ids:
        digest["errors"].append("no sample ids resolved (csv/cluster blob missing)")


def list_sample_paths(prefix, hashed_id):
    resp = api("visualizations/getSampleVisualizationsPath",
               {"scatterSampleVisualizationsPrefix": prefix,
                "sampleId": hashed_id, "fileNameMatch": ""}, soft=True)
    if resp and resp.get("paths"):
        return resp["paths"]
    paths = []
    for match in ("payload.json", ".jpg", ".png", ".mp4", ".wav"):
        resp = api("visualizations/getSampleVisualizationsPath",
                   {"scatterSampleVisualizationsPrefix": prefix,
                    "sampleId": hashed_id, "fileNameMatch": match}, soft=True)
        if resp:
            paths.extend(resp.get("paths", []))
    return paths


def cmd_fetch(args):
    insights = api("insights/getInsights",
                   {"projectId": args.project, "versionId": args.version}).get("insights", [])
    if not insights:
        print(f"version {args.version} has no insights", file=sys.stderr)
        raise SystemExit(5)
    os.makedirs(args.out, exist_ok=True)

    digests = {}
    for ins in insights:
        itype = ins.get("insightType") or {}
        itype.pop("min_hash", None)
        d = {
            "cid": ins.get("cid"),
            "id": itype.get("id_"),
            "parent_id": itype.get("parent_id"),
            "index": ins.get("index"),
            "status": ins.get("status"),
            "description": ins.get("description"),
            "type": itype.get("type"),
            "dir": f"insight_{ins.get('index')}_{itype.get('type')}",
            "insightType": itype,
            "files": {},
            "errors": [],
            "subinsights": [],
        }
        digests[itype.get("id_") or ins.get("cid")] = d

    parents, orphans = [], []
    for d in digests.values():
        parent = digests.get(d["parent_id"]) if d["parent_id"] else None
        if parent:
            d["dir"] = f"{parent['dir']}/sub_{d['index']}_{d['type']}"
            parent["subinsights"].append(d)
        elif d["parent_id"]:
            orphans.append(d)
        else:
            parents.append(d)

    def fetch_files(d):
        k = args.top_k if not d["parent_id"] else min(SUB_TOP_K, args.top_k)
        d["top_k"] = k
        fetch_insight_files({"insightType": d["insightType"]}, args.project,
                            args.out, k * CANDIDATE_FACTOR, args.rank_by,
                            args.asc, d)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch_files, digests.values()))

    id_map = {}
    for d in digests.values():
        for raw in d["top_samples"]:
            state, _, idx = raw.partition("_")
            id_map.setdefault(raw, f"{state}_{hash_sample_index(idx)}")

    available, prefix = set(), None
    if id_map:
        resp = api("visualizations/getScatterSampleVisualizations",
                   {"projectId": args.project, "versionId": args.version,
                    "sampleIds": sorted(set(id_map.values()))}, soft=True)
        if resp:
            available = set(resp.get("samplesIds", []))
            prefix = resp.get("scatterSampleVisualizationsPrefix")

    jobs = []
    for d in digests.values():
        k = d.pop("top_k")
        rendered = [r for r in d["top_samples"] if id_map[r] in available]
        unrendered = [r for r in d["top_samples"] if id_map[r] not in available]
        d["top_samples"] = (rendered + unrendered)[:k] if rendered else unrendered[:k]
        d["samples"] = {}
        for raw in d["top_samples"]:
            hashed = id_map[raw]
            entry = {"visualization_id": hashed, "files": []}
            d["samples"][raw] = entry
            if hashed not in available or not prefix:
                entry["missing_visualization"] = True
                continue
            jobs.append((d, raw, hashed, entry))

    def choose_paths(paths, hashed):
        keep = []
        has_plain_image = any(
            f"{hashed}/image/" in p and "/assets/" in p for p in paths)
        for p in paths:
            data_type = p.split(f"{hashed}/", 1)[-1].split("/", 1)[0]
            if "/assets/" in p or p.endswith((".mp4", ".wav")):
                if data_type == "image_heatmap" and has_plain_image:
                    continue
                keep.append(p)
            elif p.endswith("payload.json") and data_type not in (
                    "image", "image_heatmap", "video", "video_heatmap"):
                keep.append(p)
        return keep

    def fetch_sample(job):
        d, raw, hashed, entry = job
        for path in choose_paths(list_sample_paths(prefix, hashed), hashed):
            rel = path.split(f"{hashed}/", 1)[-1]
            local = os.path.join(args.out, d["dir"], "samples", raw, rel)
            os.makedirs(os.path.dirname(local), exist_ok=True)
            blob = download_blob(path, soft=True)
            if blob is None:
                entry.setdefault("errors", []).append(f"download failed: {path}")
                continue
            open(local, "wb").write(blob)
            entry["files"].append(local)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch_sample, jobs))
    for d in digests.values():
        for entry in d["samples"].values():
            d["errors"].extend(entry.pop("errors", []))

    for d in digests.values():
        d.pop("top_samples", None)

    result = {
        "projectId": args.project,
        "versionId": args.version,
        "insights": parents + orphans,
        "counts": {
            "total": len(digests),
            "parents": len(parents),
            "subinsights": len(digests) - len(parents) - len(orphans),
            "samples_with_visualizations": sum(
                1 for d in digests.values()
                for s in d["samples"].values() if s["files"]),
            "samples_missing_visualizations": sum(
                1 for d in digests.values()
                for s in d["samples"].values() if not s["files"]),
        },
    }
    out_path = os.path.join(args.out, "insights.json")
    open(out_path, "w").write(json.dumps(result, indent=2, default=str))
    print(out_path)


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


def cmd_render_charts(args):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not importable — render graph/hbar payloads as md tables instead",
              file=sys.stderr)
        raise SystemExit(6)
    rendered = 0
    for root, _dirs, files in os.walk(args.dir):
        if "payload.json" not in files:
            continue
        try:
            payload = json.load(open(os.path.join(root, "payload.json")))
        except Exception:
            continue
        data = payload.get("data") or {}
        dest = os.path.join(root, "chart.png")
        if os.path.exists(dest):
            continue
        try:
            if data.get("type") == "graph" and render_graph(data, dest, plt):
                rendered += 1
            elif data.get("type") == "hbar" and render_hbar(data, dest, plt):
                rendered += 1
        except Exception as e:
            print(f"chart failed for {root}: {e}", file=sys.stderr)
    print(f"rendered {rendered} charts")


def cmd_inline_html(args):
    import mimetypes
    import re
    base = os.path.dirname(os.path.abspath(args.file))
    html = open(args.file, encoding="utf-8").read()
    missing = []

    def repl(m):
        src = m.group(2)
        if src.startswith(("data:", "http://", "https://")):
            return m.group(0)
        path = os.path.join(base, urllib.request.url2pathname(src))
        if not os.path.isfile(path):
            missing.append(src)
            return m.group(0)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        b64 = base64.b64encode(open(path, "rb").read()).decode()
        return f"{m.group(1)}data:{mime};base64,{b64}{m.group(3)}"

    html = re.sub(r'(src=")([^"]+)(")', repl, html)
    open(args.file, "w", encoding="utf-8").write(html)
    size_mb = os.path.getsize(args.file) / 1e6
    print(f"{args.file}: {size_mb:.1f} MB, {len(missing)} unresolved src paths")
    if missing:
        for src in missing:
            print(f"unresolved: {src}", file=sys.stderr)
        raise SystemExit(7)


def main():
    parser = argparse.ArgumentParser(prog="tl_api.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami")
    lv = sub.add_parser("list-versions")
    lv.add_argument("--project")
    fe = sub.add_parser("fetch")
    fe.add_argument("--project", required=True)
    fe.add_argument("--version", required=True)
    fe.add_argument("--out", required=True)
    fe.add_argument("--top-k", type=int, default=10)
    fe.add_argument("--rank-by")
    fe.add_argument("--asc", action="store_true")
    rc = sub.add_parser("render-charts")
    rc.add_argument("dir")
    ih = sub.add_parser("inline-html")
    ih.add_argument("file")
    args = parser.parse_args()
    if args.cmd not in ("render-charts", "inline-html"):
        read_config()
    {"whoami": cmd_whoami, "list-versions": cmd_list_versions,
     "fetch": cmd_fetch, "render-charts": cmd_render_charts,
     "inline-html": cmd_inline_html}[args.cmd](args)


if __name__ == "__main__":
    main()
