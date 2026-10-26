#!/usr/bin/env python3
"""Write the fixed member ids of one insight from a tensorleap-analysis export.

Reads DIR/insights.json (the digest), finds the insight by its index, keeps
the group's own members (root members for a failure mode, cluster members for
the other types) and writes one row per member: sample_id, split, instance,
insight. Instance rows (element-instance mode) resolve to their parent sample
and keep the instance name, so the training side can select images and the
scoring side can match instances.

Usage:
  python3 members.py DIR --insight N [--out FILE]

Exit: 0 ok · 2 bad args · 5 insight / csv not found.
"""

import argparse
import csv
import json
import os
import sys


def walk(insights):
    for d in insights:
        yield d
        yield from walk(d.get("subinsights") or [])


def cluster_members(path):
    try:
        index = json.load(open(path)).get("samples_index") or {}
    except (OSError, ValueError, AttributeError):
        return None
    return {f"{state}_{i}" for state, idx in index.items() for i in idx} or None


def root_rows(rows):
    root = [r for r in rows if str(r.get("is_low_perf_root_member")).lower() == "true"]
    return root or rows


def member_rows(export_dir, insight):
    csv_rel = (insight.get("files") or {}).get("csv")
    if not csv_rel:
        return None
    path = os.path.join(export_dir, csv_rel)
    if not os.path.isfile(path):
        return None
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if insight.get("type") == "low_performance":
        return root_rows(rows)
    cluster = (insight.get("files") or {}).get("cluster")
    members = cluster_members(os.path.join(export_dir, cluster)) if cluster else None
    return [r for r in rows if r.get("sample_id") in members] if members else rows


def to_member(row, insight_index):
    sample = row.get("sample_id") or ""
    instance = ""
    if row.get("builtin_instance_metadata_is_instance") == "1":
        sample = row.get("builtin_instance_metadata_original_sample_id") or sample
        instance = row.get("builtin_instance_metadata_instance_name") or ""
    split = sample.rsplit("_", 1)[0] if "_" in sample else "unknown"
    return {"sample_id": sample, "split": split, "instance": instance, "insight": insight_index}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dir", help="tensorleap-analysis/<version> export directory")
    ap.add_argument("--insight", type=int, required=True, help="insight index as shown in insights.json")
    ap.add_argument("--out", help="output csv (default: stdout)")
    args = ap.parse_args(argv[1:])

    digest_path = os.path.join(args.dir, "insights.json")
    if not os.path.isfile(digest_path):
        print(f"no insights.json in {args.dir}; run tl_api.py digest first", file=sys.stderr)
        return 2
    digest = json.load(open(digest_path))
    insight = next((d for d in walk(digest.get("insights") or []) if d.get("index") == args.insight), None)
    if insight is None:
        print(f"insight {args.insight} not found in {digest_path}", file=sys.stderr)
        return 5
    rows = member_rows(args.dir, insight)
    if rows is None:
        print(f"insight {args.insight} has no samples.csv in the export", file=sys.stderr)
        return 5

    members = [to_member(r, args.insight) for r in rows]
    seen, unique = set(), []
    for m in members:
        key = (m["sample_id"], m["instance"])
        if key not in seen:
            seen.add(key)
            unique.append(m)

    out = open(args.out, "w", newline="") if args.out else sys.stdout
    writer = csv.DictWriter(out, fieldnames=["sample_id", "split", "instance", "insight"])
    writer.writeheader()
    writer.writerows(unique)
    if args.out:
        out.close()

    by_split = {}
    for m in unique:
        by_split[m["split"]] = by_split.get(m["split"], 0) + 1
    n_instances = sum(1 for m in unique if m["instance"])
    samples = len({m["sample_id"] for m in unique})
    level = f"{n_instances} instances in {samples} samples" if n_instances else f"{samples} samples"
    print(f"insight {args.insight} ({insight.get('type')}): {level}; "
          + ", ".join(f"{k} {v}" for k, v in sorted(by_split.items())), file=sys.stderr)
    if not any(k.startswith("val") for k in by_split):
        print("warning: no validation members; the failure mode cannot be measured "
              "without a re-split (ask the user)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
