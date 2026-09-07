#!/usr/bin/env python3
"""Tensorleap analysis client for the tensorleap-analysis skill.

Talks ONLY to node-server's analysis-export facade (listTargets,
exportAnalysis, getSampleAssets), a deliberate public contract carrying a
contractVersion the skill verifies before trusting the shape. Requires a
Tensorleap server that exposes the analysis-export API; on older servers the
first call fails with a clear message. Artifact URLs arrive pre-signed in
batches, so there are no per-file signing round-trips.

Auth and base URL come from the leap CLI config (~/.config/tensorleap/config.yaml,
override path with TENSORLEAP_CONFIG): auth.api_url + auth.api_key. The api_key
may be absent on --disable-auth installs; requests are then sent without a header.
Stdlib only, no pip dependencies (render-charts alone needs matplotlib and says
so via its exit code).

Usage:
  tl_api.py whoami
  tl_api.py list-versions [--project NAME_OR_ID]
  tl_api.py fetch --project ID --version ID --out DIR [--top-k 10]
                  [--rank-by COLUMN] [--asc]
                  [--cache-dir DIR] [--refresh]

Repeat runs reuse work: blobs are cached under ~/.cache/tensorleap-analysis
(--cache-dir, empty to disable) and sample dirs already present in --out are
kept as-is; --refresh re-downloads everything.
  tl_api.py render-charts DIR
  tl_api.py summarize DIR           # per-insight composition stats from samples.csv
  tl_api.py build-report DIR        # assemble report.html + report.txt from DIR/report.json
  tl_api.py inline-html FILE.html   # embed <img src> files as data URIs, in place

Exit codes:
  0  ok
  2  bad arguments / no matching project
  3  not authenticated (missing config, or server rejected the key)
  4  server unreachable, has no analysis-export API, or returned an unexpected error
  5  version has no insights
  6  matplotlib unavailable (render-charts only, fall back to html tables)
  7  inline-html: some src paths did not resolve (listed on stderr)
  8  build-report: report.json invalid or referenced images missing (listed on stderr)
  9  analysis-export contract mismatch (update the skill or the server, whichever is older)
"""
import argparse
import base64
import csv
import hashlib
import html
import io
import itertools
import json
import os
import re
import struct
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

API = {"url": None, "key": None}
CACHE = {"dir": None, "refresh": False}
# Blob downloads are soft (a missing image must not kill the run), so count
# them: signed URLs point at the storage host, which a port-forward to
# node-server alone does not expose. Then the API works and every blob fails.
BLOBS = {"ok": 0, "failed": 0, "host": None}
# Must match ANALYSIS_EXPORT_CONTRACT_VERSION in node-server's
# src/analysis-export/interfaces.ts.
CONTRACT_VERSION = 1
SUB_TOP_K = 6
CANDIDATE_FACTOR = 4


def read_config():
    cfg = os.environ.get("TENSORLEAP_CONFIG") or os.path.expanduser(
        "~/.config/tensorleap/config.yaml")
    try:
        lines = open(cfg).read().splitlines()
    except Exception as exc:
        print(f"cannot read {cfg}: {exc}, is the CLI logged in? (leap auth login)",
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
        print(f"no auth.api_url in {cfg}, run: leap auth login", file=sys.stderr)
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
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code in (401, 403):
                print(f"auth rejected by {API['url']} ({e.code}): {detail}", file=sys.stderr)
                raise SystemExit(3)
            if e.code == 404 and path.startswith("analysis-export/"):
                print(f"{API['url']} has no analysis-export API, the "
                      "tensorleap-analysis skill requires a newer Tensorleap "
                      "server", file=sys.stderr)
                raise SystemExit(4)
            if soft:
                return None
            print(f"POST /api/v2/{path} -> {e.code}: {detail}", file=sys.stderr)
            raise SystemExit(4)
        except OSError as e:
            if attempt == 1:
                time.sleep(2)
                continue
            reason = getattr(e, "reason", e)
            if soft:
                return None
            print(f"cannot reach {API['url']}: {reason}", file=sys.stderr)
            raise SystemExit(4)


def fetch_url(url):
    with urllib.request.urlopen(url, timeout=300) as resp:
        return resp.read()


def write_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.part{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def cache_path(file_name):
    if not CACHE["dir"]:
        return None
    return os.path.join(CACHE["dir"],
                        hashlib.sha256(file_name.encode()).hexdigest())


def check_contract(resp):
    got = (resp or {}).get("contractVersion")
    if got != CONTRACT_VERSION:
        print(f"analysis-export contract mismatch: server v{got}, skill "
              f"v{CONTRACT_VERSION}, update the older side", file=sys.stderr)
        raise SystemExit(9)
    return resp


def download_url(url, soft=False):
    """Fetch a pre-signed artifact URL, caching by its (stable) path, the
    signature query changes every run, the object path does not."""
    if not url:
        return None
    cp = cache_path(urllib.parse.urlparse(url).path)
    if cp and not CACHE["refresh"] and os.path.isfile(cp):
        with open(cp, "rb") as f:
            return f.read()
    try:
        data = fetch_url(url)
    except Exception as e:
        BLOBS["failed"] += 1
        BLOBS["host"] = urllib.parse.urlparse(url).netloc
        if soft:
            return None
        print(f"download failed for {url.split('?')[0]}: {e}", file=sys.stderr)
        raise SystemExit(4)
    BLOBS["ok"] += 1
    if cp:
        write_atomic(cp, data)
    return data


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


def hash_sample_index(raw):
    digest = hashlib.sha256(str(raw).encode()).digest()[:16]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def cmd_whoami(_args):
    me = check_contract(api("analysis-export/listTargets", {})).get("me") or {}
    print(json.dumps({
        "api_url": API["url"],
        "email": me.get("email"),
        "name": me.get("name"),
        "teamId": me.get("teamId"),
        "role": me.get("role"),
    }, indent=2))


def cmd_list_versions(args):
    resp = check_contract(api("analysis-export/listTargets", {}))
    projects = resp.get("projects") or []
    if not args.project:
        print(json.dumps(projects, indent=2))
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
    versions = check_contract(
        api("analysis-export/listTargets", {"projectId": project["cid"]})
    ).get("versions") or []
    out = [{
        "versionId": v.get("cid"),
        "name": v.get("name"),
        "serialNumber": v.get("serialNumber"),
        "createdAt": v.get("createdAt"),
        "hasInsightsArtifacts": bool(v.get("hasInsights")),
    } for v in versions if v.get("evaluated")]
    print(json.dumps({"projectId": project["cid"], "projectName": project.get("name"),
                      "evaluatedVersions": out}, indent=2))


def unzip_csv(blob, csv_path):
    """Inner csv bytes for a .zip path (pass-through otherwise), or None when
    the zip is corrupt, e.g. a stale/damaged cache entry."""
    if not csv_path.endswith(".zip"):
        return blob
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            inner = next((n for n in zf.namelist() if n.endswith(".csv")), None)
            return zf.read(inner) if inner else b""
    except zipfile.BadZipFile:
        return None


def sample_ids_from_csv(csv_bytes, rank_by, ascending, k):
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode(errors="replace"))))
    if not rows or "sample_id" not in rows[0]:
        return None, (rows[0].keys() if rows else []), rows, {}
    if rank_by and rank_by not in rows[0]:
        print(f"rank column {rank_by!r} is not in this csv, "
              f"using automatic ranking", file=sys.stderr)
        rank_by = None
    if not rank_by:
        rank_by = next((c for c in rows[0]
                        if c.endswith("aggressor_affinity_score")), None)
    if not rank_by:
        rank_by = next((c for c in rows[0]
                        if c.startswith("metrics.")
                        and ("loss" in c.lower() or "entropy" in c.lower())), None)
    # A low_performance csv holds the failing group PLUS its latent neighbourhood,
    # and the neighbours are frequently healthy. Rank only the rows that actually
    # underperform, so the fetched samples belong to the group the report describes.
    ranked = [r for r in rows
              if str(r.get("is_low_perf_root_member")).lower() == "true"] or list(rows)
    ranks = {}
    if rank_by:
        def keyf(r):
            try:
                return float(r[rank_by])
            except (TypeError, ValueError):
                return None
        valued = [r for r in ranked if keyf(r) is not None]
        valued.sort(key=keyf, reverse=not ascending)
        # rows with no rank value go last in either direction
        ranked = valued + [r for r in ranked if keyf(r) is None]
        ranks = {r["sample_id"]: keyf(r) for r in ranked}
    return [r["sample_id"] for r in ranked[:k]], list(rows[0].keys()), rows, ranks


def prefer_rendered(ids, ranks, rendered):
    """Rank order is preserved; only among equally-ranked candidates do
    samples with rendered visualizations come first. Ids with no rank
    (cluster fallback, csv without a rank column) are all tied, so there
    the rendered ones lead outright."""
    out = []
    for _, grp in itertools.groupby(ids, key=lambda i: ranks.get(i)):
        grp = list(grp)
        out += [i for i in grp if i in rendered]
        out += [i for i in grp if i not in rendered]
    return out


def sample_ids_from_cluster(cluster_json, k):
    ids = []
    for state, indices in (cluster_json.get("samples_index") or {}).items():
        ids.extend(f"{state}_{i}" for i in indices)
    return ids[:k]


def fetch_insight_files(insight, out_dir, k, rank_by, ascending, digest):
    itype = insight.get("insightType") or {}
    urls = digest.pop("_urls")
    idir = os.path.join(out_dir, digest["dir"])
    os.makedirs(idir, exist_ok=True)
    sample_ids = None

    if urls.get("csv"):
        blob = download_url(urls["csv"], soft=True)
        if blob is not None:
            blob = unzip_csv(blob, itype.get("csv_path") or "")
            if blob is None:
                digest["errors"].append("csv zip unreadable (corrupt blob or "
                                        "stale cache, retry with --refresh)")
        if blob is not None:
            local_csv = os.path.join(idir, "samples.csv")
            with open(local_csv, "wb") as f:
                f.write(blob)
            digest["files"]["csv"] = local_csv
            sample_ids, columns, rows, digest["_ranks"] = \
                sample_ids_from_csv(blob, rank_by, ascending, k)
            digest["csv_columns"] = list(columns)
            core = sum(1 for r in rows
                       if str(r.get("is_low_perf_root_member")).lower() == "true")
            digest["population"] = {"samples": core or len(rows),
                                    "csv_rows": len(rows)}
            digest["_ids"] = set(r.get("sample_id") for r in rows if r.get("sample_id"))
            if sample_ids is None:
                digest["errors"].append("csv has no sample_id column")

    if sample_ids is None and urls.get("cluster"):
        blob = download_url(urls["cluster"], soft=True)
        if blob is not None:
            try:
                sample_ids = sample_ids_from_cluster(json.loads(blob), k)
            except Exception as e:
                digest["errors"].append(f"cluster blob unreadable: {e}")

    if urls.get("top_panel"):
        blob = download_url(urls["top_panel"], soft=True)
        if blob is not None:
            local_tp = os.path.join(idir, "top_panel.json")
            with open(local_tp, "wb") as f:
                f.write(blob)
            digest["files"]["top_panel"] = local_tp
            try:
                summary = (json.loads(blob).get("summary") or {})
                digest["top_panel_summary"] = {"title": summary.get("title"),
                                               "sentence": summary.get("sentence")}
            except Exception:
                pass

    if urls.get("fixing_csv"):
        blob = download_url(urls["fixing_csv"], soft=True)
        if blob is not None:
            local_fix = os.path.join(idir, "fixing_samples.csv")
            with open(local_fix, "wb") as f:
                f.write(blob)
            digest["files"]["fixing_csv"] = local_fix
        else:
            digest["errors"].append("aggressor_fixing csv download failed")

    digest["top_samples"] = sample_ids or []
    if not sample_ids:
        digest["errors"].append("no sample ids resolved (csv/cluster blob missing)")


def ui_base_url():
    url = API["url"]
    scheme, _, rest = url.partition("://")
    host, slash, path = rest.partition("/")
    if host.startswith("api.") and host.split(":")[0].endswith("tensorleap.ai"):
        host = host[4:]
    return f"{scheme}://{host}{slash}{path}".rstrip("/")


def population_summary(csv_url):
    """All-data metric means plus a per-metadata-column baseline (numeric mean,
    or top value shares) computed from the version's full csv."""
    if not csv_url:
        return {}, {}
    blob = download_url(csv_url, soft=True)
    if blob is not None:
        blob = unzip_csv(blob, urllib.parse.urlparse(csv_url).path)
    if blob is None:
        return {}, {}
    sums, counts, nonnum, values, total = {}, {}, set(), {}, 0
    for row in csv.DictReader(io.StringIO(blob.decode(errors="replace"))):
        total += 1
        for col, val in row.items():
            if col == "sample_id" or val in (None, ""):
                continue
            try:
                sums[col] = sums.get(col, 0.0) + float(val)
                counts[col] = counts.get(col, 0) + 1
            except (TypeError, ValueError):
                nonnum.add(col)
            if not col.startswith("metrics."):
                vc = values.setdefault(col, {})
                if len(vc) < 5000 or val in vc:
                    vc[val] = vc.get(val, 0) + 1
    metrics = {col: sums[col] / counts[col] for col in sums
               if col.startswith("metrics.") and counts.get(col)}
    metadata = {}
    for col, vc in values.items():
        if col not in nonnum and counts.get(col) and len(vc) > 12:
            metadata[col] = round(sums[col] / counts[col], 4)
        else:
            top = sorted(vc.items(), key=lambda kv: -kv[1])[:12]
            metadata[col] = {v: round(c / total, 4) for v, c in top}
    return metrics, metadata


def extract_integration_code(export, out_dir):
    blob = download_url(export.get("integrationCodeUrl"), soft=True)
    if blob is None:
        return None
    dest = os.path.join(out_dir, "integration")
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
            members = [m for m in tar.getmembers()
                       if (m.isfile() or m.isdir())
                       and not m.name.startswith("/")
                       and ".." not in m.name.split("/")]
            tar.extractall(dest, members=members)
    except (tarfile.TarError, OSError) as e:
        print(f"integration code extract failed: {e}", file=sys.stderr)
        return None
    return {"dir": dest, "entry_file": export.get("integrationEntryFile")}


def cmd_fetch(args):
    CACHE["dir"] = args.cache_dir or None
    CACHE["refresh"] = args.refresh
    if CACHE["dir"]:
        os.makedirs(CACHE["dir"], exist_ok=True)
    export = check_contract(api("analysis-export/exportAnalysis",
                                {"projectId": args.project,
                                 "versionId": args.version}))
    insights = export.get("insights") or []
    if not insights:
        print(f"version {args.version} has no insights", file=sys.stderr)
        raise SystemExit(5)
    os.makedirs(args.out, exist_ok=True)

    deep_link = ui_base_url() + export["deepLinkPath"]

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
            "_urls": {"csv": ins.get("csvUrl"),
                      "cluster": ins.get("clusterBlobUrl"),
                      "top_panel": ins.get("topPanelUrl"),
                      "fixing_csv": ins.get("fixingCsvUrl")},
            "_analyze_path": ins.get("analyzeLinkPath"),
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
        fetch_insight_files({"insightType": d["insightType"]}, args.out,
                            k * CANDIDATE_FACTOR, args.rank_by, args.asc, d)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch_files, digests.values()))

    id_map = {}
    for d in digests.values():
        for raw in d["top_samples"]:
            state, _, idx = raw.partition("_")
            id_map.setdefault(raw, f"{state}_{hash_sample_index(idx)}")

    available, files_by_sample = set(), {}
    if id_map:
        resp = api("analysis-export/getSampleAssets",
                   {"projectId": args.project, "versionId": args.version,
                    "sampleIds": sorted(set(id_map.values()))}, soft=True)
        for sample in (resp or {}).get("samples") or []:
            if sample.get("files"):
                available.add(sample["sampleId"])
                files_by_sample[sample["sampleId"]] = sample["files"]

    jobs = []
    for d in digests.values():
        k = d.pop("top_k")
        d["top_samples"] = prefer_rendered(
            d["top_samples"], d.pop("_ranks", {}),
            {r for r in d["top_samples"] if id_map[r] in available})[:k]
        d["samples"] = {}
        for raw in d["top_samples"]:
            hashed = id_map[raw]
            entry = {"visualization_id": hashed, "files": []}
            d["samples"][raw] = entry
            if hashed not in available:
                entry["missing_visualization"] = True
                continue
            jobs.append((d, raw, hashed, entry))

    def choose_paths(paths, hashed):
        keep = []
        for p in paths:
            data_type = p.split(f"{hashed}/", 1)[-1].split("/", 1)[0]
            if "/assets/" in p or p.endswith((".mp4", ".wav")):
                keep.append(p)
            elif p.endswith("payload.json") and data_type not in (
                    "image", "image_heatmap", "video", "video_heatmap"):
                keep.append(p)
        return keep

    def fetch_sample(job):
        d, raw, hashed, entry = job
        sample_dir = os.path.join(args.out, d["dir"], "samples", raw)
        if not args.refresh and os.path.isdir(sample_dir):
            existing = [os.path.join(root, f)
                        for root, _dirs, files in os.walk(sample_dir)
                        for f in files if not f.endswith(".part")]
            if existing:
                entry["files"].extend(sorted(existing))
                return
        url_by_path = {f["path"]: f["url"]
                       for f in files_by_sample.get(hashed, [])}
        try:
            for path in choose_paths(sorted(url_by_path), hashed):
                rel = path.split(f"{hashed}/", 1)[-1]
                local = os.path.join(args.out, d["dir"], "samples", raw, rel)
                os.makedirs(os.path.dirname(local), exist_ok=True)
                blob = download_url(url_by_path[path], soft=True)
                if blob is None:
                    entry.setdefault("errors", []).append(f"download failed: {path}")
                    continue
                with open(local, "wb") as f:
                    f.write(blob)
                entry["files"].append(local)
        except OSError as e:
            entry.setdefault("errors", []).append(f"sample fetch failed: {raw}: {e}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch_sample, jobs))
    for d in digests.values():
        for entry in d["samples"].values():
            d["errors"].extend(entry.pop("errors", []))

    for d in digests.values():
        d.pop("top_samples", None)
        sizes = [wh for entry in d["samples"].values() for wh in
                 (image_size(f) for f in entry["files"]
                  if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif")))
                 if wh]
        if sizes:
            d["asset_resolution"] = {"max_width": max(w for w, _ in sizes),
                                     "max_height": max(h for _, h in sizes),
                                     "images": len(sizes)}

    carded = parents + orphans
    for a in carded:
        shared = []
        for b in carded:
            if b is a:
                continue
            common = (a.get("_ids") or set()) & (b.get("_ids") or set())
            if common:
                shared.append({"insight": b["index"], "shared": len(common),
                               "of_this": round(len(common) / len(a["_ids"]), 3)})
        if shared:
            a["overlaps"] = sorted(shared, key=lambda x: -x["shared"])
    for d in digests.values():
        d.pop("_ids", None)

    for d in digests.values():
        path = d.pop("_analyze_path", None)
        d["deep_link"] = ui_base_url() + path if path else deep_link
        if (d["insightType"].get("automatic_tests") or []) and d.get("cid"):
            sep = "&" if "?" in d["deep_link"] else "?"
            d["add_test_link"] = (f'{d["deep_link"]}{sep}'
                                  f'addTestFromInsight={d["cid"]}')

    pop_metrics, pop_metadata = population_summary(export.get("populationCsvUrl"))
    result = {
        "projectId": args.project,
        "versionId": args.version,
        "links": {"insights_panel": deep_link},
        "population_metrics": pop_metrics,
        "population_metadata": pop_metadata,
        "prediction_labels": export.get("predictionLabels") or {},
        "visualizers": [{"name": v.get("name"), "type": v.get("type"),
                         "arg_names": v.get("argNames")}
                        for v in export.get("visualizers") or []],
        "integration": extract_integration_code(export, args.out),
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
    result["counts"]["blob_downloads_failed"] = BLOBS["failed"]
    out_path = os.path.join(args.out, "insights.json")
    open(out_path, "w").write(json.dumps(result, indent=2, default=str))
    if BLOBS["failed"]:
        print(f"warning: {BLOBS['failed']} of {BLOBS['ok'] + BLOBS['failed']} "
              f"artifact downloads failed (host {BLOBS['host']})", file=sys.stderr)
        if not BLOBS["ok"]:
            print("the API answered but no artifact was reachable: the signed "
                  "URLs point at the server's storage host, which a port-forward "
                  "to node-server alone does not expose. Reach the server through "
                  "its public URL (the one the UI uses) and re-run with --refresh.",
                  file=sys.stderr)
            raise SystemExit(4)
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
        root = [r for r in rows
                if str(r.get("is_low_perf_root_member")).lower() == "true"]
        group = root or rows
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
                    "group_is_root_members": bool(root),
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
     overflow: hidden; scroll-margin-top: 120px; }
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
    buttons = []
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
    btns = f'<span class="cbtns">{"".join(buttons)}</span>' if buttons else ""
    chips = [f'<span class="chip sev"><span class="k">Severity</span>'
             f'<b>{sev} of 3</b></span>']
    chips += [f'<span class="chip">{_esc(c)}</span>'
              for c in card.get("chips", [])
              if not str(c).lower().startswith("insight #")]
    parts = [f'<section class="card" id="{_esc(card["id"])}" '
             f'data-kind="{kind}" data-sev="{sev}">',
             f'<div class="chead"><span class="cidx">{_esc(idx)}</span>'
             f'<h3>{_esc(card["heading"])}</h3>{btns}</div>',
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
            f'{_esc(spec["version"])}</h1></div>'
            f'<a class="button" href="{_esc(spec["insights_link"])}" '
            f'target="_blank" rel="noopener">Open in Tensorleap</a></div>'
            f'<div class="hchips">{"".join(chips)}</div></div></header>')


def _tile_html(tile):
    label = str(tile["label"])
    kind = next((_kind(t) for t in TYPE_KIND if t.lower() in label.lower()), "")
    attr = f' data-kind="{kind}"' if kind else ""
    return (f'<div class="tile"{attr}><b>{_esc(tile["value"])}</b>'
            f'<span>{_esc(label)}</span></div>')


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
    modality = _modality(spec)
    for group in spec.get("groups", []):
        kind = _kind(group["type"])
        cards = "".join(_card_html(c, kind, base, missing, modality)
                        for c in group["cards"])
        parts.append(f'<section class="block">'
                     f'<div class="grouphead"><h2>{_esc(group["type"])}</h2>'
                     f'<span class="count">{_esc(group["count"])}</span></div>'
                     f'<p class="blurb">{_esc(group["meaning"])}</p>'
                     f'<div class="cards">{cards}</div></section>')
    parts.append("</main>")
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

    def repl(m):
        src = html.unescape(m.group(2))
        if src.startswith(("data:", "http://", "https://")):
            return m.group(0)
        path = os.path.join(base, urllib.request.url2pathname(src))
        if not os.path.isfile(path):
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
        if not os.path.isfile(path) or os.path.getsize(path) > 5_000_000:
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
    sub.add_parser("whoami")
    lv = sub.add_parser("list-versions")
    lv.add_argument("--project")
    fe = sub.add_parser("fetch")
    fe.add_argument("--project", required=True)
    fe.add_argument("--version", required=True)
    fe.add_argument("--out", required=True)
    fe.add_argument("--top-k", type=int, default=24)
    fe.add_argument("--rank-by")
    fe.add_argument("--asc", action="store_true")
    fe.add_argument("--cache-dir",
                    default=os.path.expanduser("~/.cache/tensorleap-analysis"),
                    help="blob cache; empty string disables")
    fe.add_argument("--refresh", action="store_true",
                    help="re-download everything, ignoring cache and existing files")
    rc = sub.add_parser("render-charts")
    rc.add_argument("dir")
    sm = sub.add_parser("summarize")
    sm.add_argument("dir")
    br = sub.add_parser("build-report")
    br.add_argument("dir")
    ih = sub.add_parser("inline-html")
    ih.add_argument("file")
    args = parser.parse_args()
    if args.cmd not in ("render-charts", "summarize", "build-report", "inline-html"):
        read_config()
    {"whoami": cmd_whoami, "list-versions": cmd_list_versions,
     "fetch": cmd_fetch, "render-charts": cmd_render_charts,
     "summarize": cmd_summarize, "build-report": cmd_build_report,
     "inline-html": cmd_inline_html}[args.cmd](args)


if __name__ == "__main__":
    main()
