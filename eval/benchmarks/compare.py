#!/usr/bin/env python3
"""Before/after metadata & metrics comparison across benchmark arms.

Reads eval/benchmarks/<arm>/<fixture>/{tensorleap/metadata.py, tensorleap/metrics.py,
<fixture>.json} and an optional manual annotation file
eval/benchmarks/<arm>/<fixture>/rubric.json, and writes one Markdown report.

The code-derived axes (concepts, keys, explicit metadata_type, metric names/directions,
turns/tokens/cost/verdict) are parsed, never typed by hand. The judgment axes (which
taxonomy sources were used, how many keys are genuinely sliceable, NaN-policy notes) come
from rubric.json when present and are shown as "—" otherwise.

Usage:
  python3 compare.py --before baseline-v0.2.0 --after new-v0.3.0 [--out COMPARISON.md]
"""
import argparse
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _const_list(node, consts):
    if isinstance(node, (ast.List, ast.Tuple)) and all(isinstance(e, ast.Constant) for e in node.elts):
        return [e.value for e in node.elts]
    if isinstance(node, ast.Name) and isinstance(consts.get(node.id), list):
        return consts[node.id]
    return None


def _dict_keys(node, consts):
    """Keys of a metadata_type expression: dict literal, named constant, or comprehension."""
    if isinstance(node, ast.Dict):
        return [k.value for k in node.keys if isinstance(k, ast.Constant)]
    if isinstance(node, ast.DictComp) and len(node.generators) == 1:
        lst = _const_list(node.generators[0].iter, consts)
        return list(lst) if lst is not None else None
    if isinstance(node, ast.Name):
        c = consts.get(node.id)
        if isinstance(c, list):
            return c
        return None
    return None


def parse_metadata(path):
    """-> list of {name, keys (list|None), n_keys, explicit_type(bool), dynamic(bool)}"""
    if not os.path.exists(path):
        return []
    tree = ast.parse(open(path).read())
    consts = {}
    for node in tree.body:
        # plain `X = ...` and annotated `X: Dict[...] = ...` module-level constants
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, value = node.target.id, node.value
        else:
            continue
        keys = _dict_keys(value, consts)
        if keys is not None:
            consts[target] = keys          # a dict's keys double as a key list for comprehensions
        else:
            lst = _const_list(value, consts)
            if lst is None and isinstance(value, ast.Set) and all(isinstance(e, ast.Constant) for e in value.elts):
                lst = [e.value for e in value.elts]
            if lst is not None:
                consts[target] = lst
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and getattr(dec.func, "id", getattr(dec.func, "attr", "")) == "tensorleap_metadata"):
                continue
            name, mtype = None, None
            if dec.args:
                name = dec.args[0].value if isinstance(dec.args[0], ast.Constant) else None
                if len(dec.args) > 1:
                    mtype = dec.args[1]
            for kw in dec.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    name = kw.value.value
                if kw.arg == "metadata_type":
                    mtype = kw.value
            explicit = mtype is not None and not (isinstance(mtype, ast.Constant) and mtype.value is None)
            keys = _dict_keys(mtype, consts) if explicit else None
            if explicit and keys is None and isinstance(mtype, ast.Attribute):
                keys = [name]  # scalar concept: one key named after the concept
            out.append({
                "name": name or node.name,
                "keys": keys,
                "n_keys": len(keys) if keys is not None else None,
                "explicit_type": explicit,
                "dynamic": keys is None,
            })
    return out


def parse_metrics(path):
    """-> list of {kind: metric|loss, name, direction}"""
    if not os.path.exists(path):
        return []
    tree = ast.parse(open(path).read())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fn = getattr(dec.func, "id", getattr(dec.func, "attr", ""))
            if fn not in ("tensorleap_custom_metric", "tensorleap_custom_loss"):
                continue
            name = node.name
            direction = "—"
            if dec.args and isinstance(dec.args[0], ast.Constant):
                name = dec.args[0].value
            for kw in dec.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    name = kw.value.value
                if kw.arg == "direction":
                    direction = "dict" if isinstance(kw.value, (ast.Dict, ast.DictComp)) else ast.unparse(kw.value).split(".")[-1]
            out.append({"kind": "loss" if fn.endswith("loss") else "metric", "name": name, "direction": direction})
    return out


def load_fixture(arm_dir, fixture):
    d = os.path.join(arm_dir, fixture)
    if not os.path.isdir(d):
        return None
    rep = {}
    rp = os.path.join(d, f"{fixture}.json")
    if os.path.exists(rp):
        rep = json.load(open(rp))
    rubric = {}
    rb = os.path.join(d, "rubric.json")
    if os.path.exists(rb):
        rubric = json.load(open(rb))
    verdict = os.path.join(d, "VERDICT.md")
    return {
        "metadata": parse_metadata(os.path.join(d, "tensorleap", "metadata.py")),
        "metrics": parse_metrics(os.path.join(d, "tensorleap", "metrics.py")),
        "report": rep,
        "rubric": rubric,
        "verdict_override": os.path.exists(verdict),
    }


def fmt_keys(md):
    parts = []
    for m in md:
        if m["keys"] is None:
            parts.append(f"`{m['name']}`{{dynamic}}")
        else:
            parts.append(f"`{m['name']}`{{{', '.join(map(str, m['keys']))}}}")
    return "<br>".join(parts) if parts else "—"


def summarize(fx):
    md = fx["metadata"]
    n_keys = sum(m["n_keys"] or 0 for m in md)
    dyn = any(m["dynamic"] for m in md)
    return {
        "concepts": len(md),
        "keys": f"{n_keys}{'+' if dyn else ''}",
        "explicit": f"{sum(1 for m in md if m['explicit_type'])}/{len(md)}" if md else "—",
        "metrics": ", ".join(f"{m['name']}({m['direction']})" for m in fx["metrics"] if m["kind"] == "metric") or "—",
        "loss": ", ".join(m["name"] for m in fx["metrics"] if m["kind"] == "loss") or "—",
    }


def cell(rubric, key):
    v = rubric.get(key)
    if v is None:
        return "—"
    return ", ".join(v) if isinstance(v, list) else str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--out", default=os.path.join(HERE, "COMPARISON.md"))
    ap.add_argument("--fixtures", default="cifar10_resnet,yolov5_visdrone,infineon_ts,deeplabv3plus_adas,plankd")
    a = ap.parse_args()
    b_dir, a_dir = os.path.join(HERE, a.before), os.path.join(HERE, a.after)
    fixtures = [f for f in a.fixtures.split(",") if f]

    L = []
    L.append(f"# Metadata & metrics: `{a.before}` vs `{a.after}`\n")
    L.append("Code-derived rows are parsed from each run's `tensorleap/metadata.py` / `metrics.py` and the harness "
             "report JSON. Judgment rows (sources, sliceable, NaN notes) come from a per-run `rubric.json` when present.\n")

    # --- summary table
    L.append("## Summary\n")
    L.append("| fixture | arm | verdict | turns | out tok | cost | concepts | keys | explicit type | sources used | sliceable keys |")
    L.append("|---|---|---|---:|---:|---:|---:|---:|---:|---|---|")
    totals = {a.before: [0, 0, 0.0, 0], a.after: [0, 0, 0.0, 0]}
    for f in fixtures:
        for arm, d in ((a.before, b_dir), (a.after, a_dir)):
            fx = load_fixture(d, f)
            if fx is None:
                L.append(f"| {f} | {arm} | *not run* | | | | | | | | |")
                continue
            s, r = summarize(fx), fx["report"]
            verdict = r.get("result", "—") + ("\\*" if fx["verdict_override"] else "")
            L.append(f"| {f} | {arm} | {verdict} | {r.get('turns','—')} | {r.get('out_tokens','—')} | "
                     f"{('$%.0f' % r['cost_usd']) if 'cost_usd' in r else '—'} | {s['concepts']} | {s['keys']} | {s['explicit']} | "
                     f"{cell(fx['rubric'],'sources')} | {cell(fx['rubric'],'sliceable')} |")
            if r.get("result") in ("PASS",) or fx["verdict_override"]:
                totals[arm][3] += 1
            totals[arm][0] += r.get("turns", 0) or 0
            totals[arm][1] += r.get("out_tokens", 0) or 0
            totals[arm][2] += r.get("cost_usd", 0) or 0
    L.append("")
    L.append("\\* verdict overridden by a manual `VERDICT.md` (e.g. eval re-run after an infrastructure failure).\n")
    L.append("| arm | passes | Σ turns | Σ out tok | Σ cost |")
    L.append("|---|---:|---:|---:|---:|")
    for arm, t in totals.items():
        L.append(f"| {arm} | {t[3]}/{len(fixtures)} | {t[0]} | {t[1]} | ${t[2]:.0f} |")
    L.append("")

    # --- per-fixture detail
    L.append("## Per fixture\n")
    for f in fixtures:
        L.append(f"### {f}\n")
        L.append(f"| | `{a.before}` | `{a.after}` |")
        L.append("|---|---|---|")
        fb, fa = load_fixture(b_dir, f), load_fixture(a_dir, f)

        def col(fx, fn):
            return fn(fx) if fx else "*not run*"

        L.append(f"| metadata concepts → keys | {col(fb, lambda x: fmt_keys(x['metadata']))} | {col(fa, lambda x: fmt_keys(x['metadata']))} |")
        L.append(f"| explicit `metadata_type` | {col(fb, lambda x: summarize(x)['explicit'])} | {col(fa, lambda x: summarize(x)['explicit'])} |")
        L.append(f"| loss | {col(fb, lambda x: summarize(x)['loss'])} | {col(fa, lambda x: summarize(x)['loss'])} |")
        L.append(f"| metrics (direction) | {col(fb, lambda x: summarize(x)['metrics'])} | {col(fa, lambda x: summarize(x)['metrics'])} |")
        for key, label in (("sources", "taxonomy sources used"), ("sliceable", "sliceable keys (of total)"),
                           ("nan_policy", "missing-value policy"), ("domain_fields", "domain-knowledge fields"),
                           ("noise", "noise / duplicates / identifiers"), ("friction", "friction")):
            L.append(f"| {label} | {col(fb, lambda x, k=key: cell(x['rubric'], k))} | {col(fa, lambda x, k=key: cell(x['rubric'], k))} |")
        def vtc(x):
            r = x["report"]
            return "%s · %s · $%.0f" % (r.get("result", "—"), r.get("turns", "—"), r.get("cost_usd", 0) or 0)

        L.append(f"| verdict · turns · cost | {col(fb, vtc)} | {col(fa, vtc)} |")
        L.append("")

    open(a.out, "w").write("\n".join(L) + "\n")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    sys.exit(main())
