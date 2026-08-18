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
                  [--fast-local] [--cache-dir DIR] [--refresh]

Repeat runs reuse work: blobs are cached under ~/.cache/tensorleap-analysis
(--cache-dir, empty to disable) and sample dirs already present in --out are
kept as-is; --refresh re-downloads everything. --fast-local (localhost
servers only) signs storage URLs locally instead of asking the API for one
signed URL per file — calibrated from a single API-issued URL and verified
byte-for-byte before use, falling back to the API on any doubt.
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
import hmac
import io
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

API = {"url": None, "key": None}
CACHE = {"dir": None, "refresh": False}
SIGN = {"ready": False}
SUB_TOP_K = 6
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
    tmp = f"{path}.part{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def cache_path(file_name):
    if not CACHE["dir"]:
        return None
    return os.path.join(CACHE["dir"],
                        hashlib.sha256(file_name.encode()).hexdigest())


def sigv4(uri, host, stamp, scope, params):
    """Presigned sigv4 signature for a GET of `uri` against `host`."""
    query = "&".join(f"{urllib.parse.quote(k, safe='')}="
                     f"{urllib.parse.quote(v, safe='')}"
                     for k, v in sorted(params.items()))
    canonical = f"GET\n{uri}\n{query}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD"
    to_sign = (f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n"
               f"{hashlib.sha256(canonical.encode()).hexdigest()}")
    key = f"AWS4{SIGN['secret']}".encode()
    for part in scope.split("/"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return query, hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()


def storage_key(file_name):
    """Object key for a name that is either already bucket-absolute
    (organizations/...) or relative to this team's prefix."""
    if file_name.startswith(SIGN["team_prefix"].split("/", 1)[0] + "/"):
        return file_name
    return SIGN["team_prefix"] + file_name


def sign_url(file_name):
    """Presign the object URL locally, using the calibration captured from one
    API-issued URL. Returns None when the fast path is unavailable, so callers
    fall back to the API."""
    if not SIGN.get("ready"):
        return None
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    scope = f"{stamp[:8]}/{SIGN['region']}/s3/aws4_request"
    uri = SIGN["bucket_path"] + urllib.parse.quote(storage_key(file_name))
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{SIGN['access']}/{scope}",
        "X-Amz-Date": stamp,
        "X-Amz-Expires": "3600",
        "X-Amz-SignedHeaders": "host",
    }
    query, sig = sigv4(uri, SIGN["signing_host"], stamp, scope, params)
    return f"{SIGN['base']}{uri}?{query}&X-Amz-Signature={sig}"


def list_objects(key_prefix):
    """Every key under `key_prefix` via S3 ListObjectsV2, signed locally.
    One paginated call replaces one listing API call per sample."""
    if not SIGN.get("ready"):
        return None
    import xml.etree.ElementTree as ET
    ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    keys, token = [], None
    while True:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        scope = f"{stamp[:8]}/{SIGN['region']}/s3/aws4_request"
        params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{SIGN['access']}/{scope}",
            "X-Amz-Date": stamp,
            "X-Amz-Expires": "3600",
            "X-Amz-SignedHeaders": "host",
            "list-type": "2",
            "max-keys": "1000",
            "prefix": key_prefix,
        }
        if token:
            params["continuation-token"] = token
        query, sig = sigv4(SIGN["bucket_path"], SIGN["signing_host"],
                           stamp, scope, params)
        url = f"{SIGN['base']}{SIGN['bucket_path']}?{query}&X-Amz-Signature={sig}"
        try:
            root = ET.fromstring(fetch_url(url))
        except Exception as e:
            print(f"fast-local: listing failed ({e}) — using the API path",
                  file=sys.stderr)
            return None
        keys.extend(c.findtext(f"{ns}Key") for c in root.findall(f"{ns}Contents"))
        if root.findtext(f"{ns}IsTruncated") != "true":
            return keys
        token = root.findtext(f"{ns}NextContinuationToken")
        if not token:
            return keys


def download_blob(file_name, soft=False):
    cp = cache_path(file_name)
    if cp and not CACHE["refresh"] and os.path.isfile(cp):
        with open(cp, "rb") as f:
            return f.read()
    data = None
    fast = sign_url(file_name)
    if fast:
        try:
            data = fetch_url(fast)
        except Exception:
            data = None
    if data is None:
        resp = api("versions/getDownloadSignedUrl", {"fileName": file_name}, soft=soft)
        if resp is None:
            return None
        try:
            data = fetch_url(resp["url"])
        except Exception as e:
            if soft:
                return None
            print(f"download failed for {file_name}: {e}", file=sys.stderr)
            raise SystemExit(4)
    if cp and data is not None:
        write_atomic(cp, data)
    return data


def storage_credentials():
    cmd = ["leap", "server", "tools", "kubectl", "get", "secret",
           "minio-secret", "-n", "tensorleap", "-o", "json"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60).stdout
        data = json.loads(out)["data"]
        return (base64.b64decode(data["rootUser"]).decode(),
                base64.b64decode(data["rootPassword"]).decode())
    except Exception:
        return None, None


def signing_host_candidates(public_netloc):
    """Hosts the server might have signed with. The storage service is reached
    through the public origin, but the signature may cover its in-cluster
    endpoint, so candidates come from the cluster's own service list."""
    candidates = [public_netloc]
    cmd = ["leap", "server", "tools", "kubectl", "get", "svc",
           "-n", "tensorleap", "-o", "json"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60).stdout
        for item in json.loads(out).get("items", []):
            name = (item.get("metadata") or {}).get("name", "")
            if "minio" not in name:
                continue
            for port in (item.get("spec") or {}).get("ports", []):
                candidates.append(f"{name}:{port.get('port')}")
    except Exception:
        pass
    return candidates


def enable_fast_local(probe_file_name):
    """Calibrate local signing against one API-issued URL: reproduce ITS
    signature to learn the signing host, then verify byte equality before
    trusting the fast path."""
    if API["url"].split("://", 1)[-1].split(":")[0] not in (
            "localhost", "127.0.0.1", "::1", "0.0.0.0"):
        print("fast-local: server is not local — using the API path",
              file=sys.stderr)
        return False
    resp = api("versions/getDownloadSignedUrl", {"fileName": probe_file_name},
               soft=True)
    if not resp or not resp.get("url"):
        return False
    url = urllib.parse.urlparse(resp["url"])
    params = dict(urllib.parse.parse_qsl(url.query))
    target = params.pop("X-Amz-Signature", None)
    cred = (params.get("X-Amz-Credential") or "").split("/")
    quoted = urllib.parse.quote(probe_file_name)
    if not target or len(cred) < 5 or not url.path.endswith(quoted):
        return False
    access, secret = storage_credentials()
    if not secret or access != cred[0]:
        print("fast-local: storage credentials unavailable — using the API path",
              file=sys.stderr)
        return False
    prefix = url.path[:-len(quoted)]
    bucket_path = "/" + prefix.strip("/").split("/", 1)[0] + "/"
    SIGN.update({"base": f"{url.scheme}://{url.netloc}",
                 "bucket_path": bucket_path,
                 "team_prefix": prefix[len(bucket_path):],
                 "region": cred[2], "access": access, "secret": secret})
    scope = "/".join(cred[1:])
    signing_host = next(
        (host for host in signing_host_candidates(url.netloc)
         if sigv4(url.path, host, params["X-Amz-Date"], scope, params)[1] == target),
        None)
    if not signing_host:
        print("fast-local: could not reproduce the server's signature — "
              "using the API path", file=sys.stderr)
        return False
    SIGN.update({"signing_host": signing_host, "ready": True})
    try:
        expected = fetch_url(resp["url"])
        got = fetch_url(sign_url(probe_file_name))
    except Exception:
        got = expected = None
    if got is None or got != expected:
        SIGN["ready"] = False
        print("fast-local: local signing did not verify — using the API path",
              file=sys.stderr)
        return False
    print(f"fast-local: signing objects locally (host {signing_host})",
          file=sys.stderr)
    return True


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
            with open(local_csv, "wb") as f:
                f.write(blob)
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
            with open(local_tp, "wb") as f:
                f.write(blob)
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
    listed = SIGN.get("listing")
    if listed is not None:
        return listed.get(hashed_id, [])
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


def ui_base_url():
    url = API["url"]
    scheme, _, rest = url.partition("://")
    host, slash, path = rest.partition("/")
    if host.startswith("api.") and host.split(":")[0].endswith("tensorleap.ai"):
        host = host[4:]
    return f"{scheme}://{host}{slash}{path}".rstrip("/")


def version_meta(project_id, version_id):
    resp = api("versions/getProjectSlimVersions", {"projectId": project_id}, soft=True)
    versions = (resp or {}).get("versions") or []
    return next((v for v in versions if v.get("cid") == version_id), None) or {}


def population_metrics(project_id, version):
    csv_path = (version.get("resources") or {}).get("csv_blob_path")
    if not csv_path:
        return {}
    blob = download_blob(f"projects/{project_id}/{csv_path}", soft=True)
    if blob is None:
        return {}
    if csv_path.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            inner = next((n for n in zf.namelist() if n.endswith(".csv")), None)
            blob = zf.read(inner) if inner else b""
    sums, counts = {}, {}
    for row in csv.DictReader(io.StringIO(blob.decode(errors="replace"))):
        for col, val in row.items():
            if not col.startswith("metrics."):
                continue
            try:
                sums[col] = sums.get(col, 0.0) + float(val)
                counts[col] = counts.get(col, 0) + 1
            except (TypeError, ValueError):
                pass
    return {col: sums[col] / counts[col] for col in sums if counts.get(col)}


def code_snapshot(project_id, version):
    snap_id = version.get("codeSnapshotId")
    if not snap_id:
        return {}
    resp = api("versions/getCodeSnapshot",
               {"projectId": project_id, "codeSnapshotId": snap_id}, soft=True)
    return (resp or {}).get("codeSnapshot") or {}


def extract_integration_code(project_id, snapshot, out_dir):
    blob_name = snapshot.get("blobName")
    if not blob_name:
        return None
    blob = download_blob(f"projects/{project_id}/{blob_name}", soft=True)
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
    return {"dir": dest, "entry_file": snapshot.get("codeEntryFile")}


def mint_version_link(project_id, version_id, dashboard_id):
    state = {"dashboards": {dashboard_id: {
        "selectedVersions": [{"id": version_id, "isVisibile": True}],
    }}}
    resp = api("projectstate/upsertState",
               {"projectId": project_id, "state": json.dumps(state)}, soft=True)
    if not resp or not resp.get("digest"):
        return None
    return (f"{ui_base_url()}/project/{project_id}/dashboard/panel/insights"
            f"?dashboard={dashboard_id}&state={resp['digest']}")


def cmd_fetch(args):
    CACHE["dir"] = args.cache_dir or None
    CACHE["refresh"] = args.refresh
    if CACHE["dir"]:
        os.makedirs(CACHE["dir"], exist_ok=True)
    insights = api("insights/getInsights",
                   {"projectId": args.project, "versionId": args.version}).get("insights", [])
    if not insights:
        print(f"version {args.version} has no insights", file=sys.stderr)
        raise SystemExit(5)
    os.makedirs(args.out, exist_ok=True)

    panel_link = (f"{ui_base_url()}/project/{args.project}"
                  "/dashboard/panel/insights")
    dash_resp = api("dashboards/getProjectDashboards",
                    {"projectId": args.project}, soft=True) or {}
    dashboards = (dash_resp.get("dashboards") or dash_resp.get("data") or [])
    dashboard_id = (dashboards[0] or {}).get("cid") if dashboards else None

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

    if args.fast_local:
        probe = next((f'projects/{args.project}/{x["insightType"][field]}'
                      for x in digests.values()
                      for field in ("csv_path", "blob_path")
                      if x["insightType"].get(field)), None)
        if probe:
            enable_fast_local(probe)

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
        if prefix and SIGN.get("ready"):
            keys = list_objects(storage_key(prefix))
            if keys:
                grouped = {}
                root = storage_key(prefix)
                for key in keys:
                    sample_id = key[len(root):].split("/", 1)[0]
                    grouped.setdefault(sample_id, []).append(key)
                SIGN["listing"] = grouped
                available |= set(grouped) & set(id_map.values())
                print(f"fast-local: listed {len(keys)} stored files for "
                      f"{len(grouped)} samples in one call", file=sys.stderr)

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
        try:
            for path in choose_paths(list_sample_paths(prefix, hashed), hashed):
                rel = path.split(f"{hashed}/", 1)[-1]
                local = os.path.join(args.out, d["dir"], "samples", raw, rel)
                os.makedirs(os.path.dirname(local), exist_ok=True)
                blob = download_blob(path, soft=True)
                if blob is None:
                    entry.setdefault("errors", []).append(f"download failed: {path}")
                    continue
                with open(local, "wb") as f:
                    f.write(blob)
                entry["files"].append(local)
        except (OSError, SystemExit) as e:
            entry.setdefault("errors", []).append(f"sample fetch failed: {raw}: {e}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch_sample, jobs))
    for d in digests.values():
        for entry in d["samples"].values():
            d["errors"].extend(entry.pop("errors", []))

    for d in digests.values():
        d.pop("top_samples", None)

    version = version_meta(args.project, args.version)
    snapshot = code_snapshot(args.project, version)
    setup = ((snapshot.get("parseResult") or {}).get("setup")) or {}
    version_link = (dashboard_id and mint_version_link(
        args.project, args.version, dashboard_id)) or panel_link
    for d in parents:
        d["deep_link"] = version_link

    result = {
        "projectId": args.project,
        "versionId": args.version,
        "links": {"insights_panel": version_link},
        "population_metrics": population_metrics(args.project, version),
        "prediction_labels": {p.get("name"): p["labels"]
                              for p in setup.get("prediction_types") or []
                              if p.get("labels")},
        "visualizers": [{"name": v.get("name"), "type": v.get("type"),
                         "arg_names": v.get("arg_names")}
                        for v in setup.get("visualizers") or []],
        "integration": extract_integration_code(args.project, snapshot, args.out),
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
    rendered, lib_skipped = 0, 0
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
                    lib_skipped += 1
                elif (render_graph if kind == "graph" else render_hbar)(data, dest, plt):
                    rendered += 1
            elif kind == "bbox_image":
                if os.path.exists(os.path.join(root, "boxes.jpg")):
                    continue
                if not Image:
                    lib_skipped += 1
                elif render_bboxes(data, root, Image, ImageDraw):
                    rendered += 1
        except Exception as e:
            print(f"render failed for {root}: {e}", file=sys.stderr)
    print(f"rendered {rendered} (skipped {lib_skipped} for missing matplotlib/PIL)")
    if lib_skipped and not rendered:
        raise SystemExit(6)


def cmd_inline_html(args):
    import mimetypes
    import re
    try:
        from PIL import Image
    except ImportError:
        Image = None
    base = os.path.dirname(os.path.abspath(args.file))
    html = open(args.file, encoding="utf-8").read()
    missing = []

    def encode(path, mime):
        data = open(path, "rb").read()
        if not Image or not mime.startswith("image/") or len(data) <= 50_000:
            return mime, data
        img = Image.open(io.BytesIO(data))
        if img.mode in ("RGBA", "LA", "P"):
            return mime, data
        if max(img.size) > 900:
            img.thumbnail((900, 900))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        if buf.tell() < len(data):
            return "image/jpeg", buf.getvalue()
        return mime, data

    def repl(m):
        src = m.group(2)
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
    fe.add_argument("--top-k", type=int, default=24)
    fe.add_argument("--rank-by")
    fe.add_argument("--asc", action="store_true")
    fe.add_argument("--fast-local", action="store_true",
                    help="sign storage URLs locally (localhost servers only) "
                         "instead of one API call per file")
    fe.add_argument("--cache-dir",
                    default=os.path.expanduser("~/.cache/tensorleap-analysis"),
                    help="blob cache; empty string disables")
    fe.add_argument("--refresh", action="store_true",
                    help="re-download everything, ignoring cache and existing files")
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
