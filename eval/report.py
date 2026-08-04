#!/usr/bin/env python3
"""Emit skill-eval reports — always to files, never just to stdout.

Two modes:

1. Per fixture (run.sh, at the end of one fixture) — writes `reports/<id>.md`
   for humans plus a `reports/<id>.json` sidecar the roll-up reads, so the
   roll-up never has to scrape markdown:

     report.py --fixture <id> --result PASS|FAIL|STUCK \
               --eval-id <id> --push-id <id> --push-status FINISHED \
               --transcript-dir <~/.claude/projects/...> \
               --out reports/<id>.md [--notes path/to/NOTES.md] \
               [--skill-source '...'] [--note '...']

2. Roll-up (run_all.sh, after the corpus) — writes `reports/REPORT_V<n>.md` in
   the CREATE_REPORT structure, plus `REPORT_V<n>.json` so the *next* run can
   compute its deltas section:

     report.py --aggregate [--version N] [--fixtures id:VERDICT,id:VERDICT,...]

Metrics come from the session transcript jsonl (assistant messages carry
message.usage). `out_tokens` and `turns` are the comparable "work" metrics;
`total_tokens` is dominated by prompt-cache re-reads, so it is a throughput
proxy, not a cost proxy — cost is computed per token *class* below.

Cost rates are ASSUMPTIONS (USD per million tokens) — override via env to match
the model/plan you ran. Defaults approximate Claude Opus list pricing.

Push and Evaluate are reported as SEPARATE columns on purpose: a broken
integration can reach a FINISHED push and still fail its evaluate. Only a
FINISHED evaluate counts as success.
"""

import argparse
import datetime
import glob
import json
import os
import re
import statistics

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# The skill under test. Detected by the agent's `Skill` tool call in the
# transcript; a graded run whose transcript has zero such calls exercised raw
# Claude instead of the skill, and its verdict is VOID.
# NOT via an `attributionSkill` field — no CLI version we run writes one, so
# that check voided every graded run (ner_roberta V9: skill demonstrably ran).
SKILL_NAME = "tensorleap-integration-creation"

RATES = {  # USD per 1M tokens; override with REPORT_*_RATE env vars
    "in":          float(os.environ.get("REPORT_IN_RATE", "15")),
    "out":         float(os.environ.get("REPORT_OUT_RATE", "75")),
    "cache_read":  float(os.environ.get("REPORT_CACHE_READ_RATE", "1.5")),
    "cache_write": float(os.environ.get("REPORT_CACHE_WRITE_RATE", "18.75")),
}

# Verdicts run_all.sh can report for a fixture whose agent never ran, so there is
# no sidecar and nothing to grade. Kept out of the pass ratio.
UNGRADED = {
    "PREP-FAIL":    "prepare.sh failed — fixture never built",
    "VERIFY-FAIL":  "verify.sh failed — fixture not blind, agent not run",
    "PREREQ-FAIL":  "required data prerequisite missing — agent not run",
    "SKILL-UNUSED": "agent ran but the skill under test was never used — verdict void",
    "RUN-ERROR":    "harness error (not a skill verdict)",
}


def latest_transcript(transcript_dir, since=0):
    """Newest session jsonl modified at/after `since` (epoch seconds).

    The bound matters: a fixture's project dir accumulates one transcript per
    attempt, and "newest overall" silently attributes a PREVIOUS attempt's
    session — turns, tokens, cost — to a run whose own session left none.
    """
    if not transcript_dir or not os.path.isdir(transcript_dir):
        return None
    files = [p for p in glob.glob(os.path.join(transcript_dir, "*.jsonl"))
             if os.path.getmtime(p) >= since]
    return max(files, key=os.path.getmtime) if files else None


EMPTY_METRICS = {"turns": 0, "skill_turns": 0, "out": 0, "in": 0,
                 "cache_read": 0, "cache_write": 0, "model": "",
                 "cc_version": "", "effort": ""}


def parse_transcript(path):
    """Sum turns + token classes from a session jsonl."""
    m = dict(EMPTY_METRICS)
    for line in open(path, errors="ignore"):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        msg = rec.get("message") or {}
        if rec.get("type") == "assistant" or msg.get("role") == "assistant":
            m["turns"] += 1
            # Run conditions stamped on every assistant record — provenance for
            # comparing runs (a CC upgrade or effort change explains a delta).
            m["cc_version"] = rec.get("version") or m["cc_version"]
            m["effort"] = rec.get("effort") or m["effort"]
            for c in msg.get("content") or []:
                if (isinstance(c, dict) and c.get("type") == "tool_use"
                        and c.get("name") == "Skill"
                        and SKILL_NAME in str((c.get("input") or {}).get("skill", ""))):
                    m["skill_turns"] += 1
            u = msg.get("usage") or {}
            m["out"] += u.get("output_tokens", 0)
            m["in"] += u.get("input_tokens", 0)
            m["cache_read"] += u.get("cache_read_input_tokens", 0)
            m["cache_write"] += u.get("cache_creation_input_tokens", 0)
            m["model"] = msg.get("model") or m["model"]
    return m


def cost_usd(m):
    return (m["in"] * RATES["in"] + m["out"] * RATES["out"]
            + m["cache_read"] * RATES["cache_read"]
            + m["cache_write"] * RATES["cache_write"]) / 1_000_000


def human(n):
    for unit, div in (("M", 1_000_000), ("k", 1_000)):
        if n >= div:
            return f"{n/div:.1f}{unit}"
    return str(n)


def stuck_where(result, push_status, eval_id):
    """Where the run died, in the CREATE_REPORT sense — phase, not cause."""
    pushed = push_status == "FINISHED"
    if result == "PASS":
        return "—"
    if result == "FAIL":
        if eval_id:
            return f"eval {eval_id}: FAILED"
        return f"push: {push_status or 'no push created'}"
    if eval_id:                       # STUCK with a job = server side never settled
        return f"eval {eval_id}: never reached a terminal state"
    if pushed:
        return "push FINISHED but no Evaluate was ever created"
    return f"authoring/push: no Evaluate created (push: {push_status or 'none created'})"


# --------------------------------------------------------------------------- #
# Per-fixture report
# --------------------------------------------------------------------------- #
def render(args, m, transcript, notes, where, void):
    total = m["in"] + m["out"] + m["cache_read"] + m["cache_write"]
    emoji = {"PASS": "✅", "FAIL": "❌", "STUCK": "⚠️"}.get(args.result, "❔")
    tick = "✅" if args.push_status == "FINISHED" else "❌"
    lines = [
        f"# Skill-eval report — {args.fixture}",
        "",
        f"- **Result:** {emoji} {args.result}   (success = a FINISHED evaluate)",
    ]
    if void:
        lines.append(f"- **⚠️ VERDICT VOID:** {void}")
    lines += [
        f"- **Push:** {tick} {args.push_status or '(none created)'}"
        f"{' — ' + args.push_id if args.push_id else ''}",
        f"- **Evaluate job:** {args.eval_id or '(none created)'}",
        f"- **Got stuck (where):** {where}",
    ]
    if m["turns"]:
        lines.append(f"- **Skill invoked:** {m['skill_turns']} `Skill`-tool call(s) "
                     f"to `{SKILL_NAME}` across {m['turns']} turns")
    if m["model"]:
        lines.append(f"- **Model:** {m['model']}")
    if args.skill_source:
        lines.append(f"- **Skill source:** {args.skill_source}")
    prov = " · ".join(x for x in (
        (f"skill `{args.skill_fingerprint}`"
         + (f" (git {args.skill_git})" if args.skill_git else ""))
        if args.skill_fingerprint else "",
        f"fixture `{args.fixture_sha}`" if args.fixture_sha else "",
        f"prompt `{args.prompt_sha}`" if args.prompt_sha else "",
        f"CC {m['cc_version']}" if m["cc_version"] else "",
        f"effort {m['effort']}" if m["effort"] else "") if x)
    if prov:
        lines.append(f"- **Provenance:** {prov}")
    if args.note:
        lines.append(f"- **Harness note:** {args.note}")
    lines += [
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "|--------|------:|",
    ]
    if args.duration:
        lines.append(f"| wall clock | {args.duration // 60}m{args.duration % 60:02d}s |")
    lines += [
        f"| turns | {m['turns']} |",
        f"| output tokens | {human(m['out'])} |",
        f"| input tokens | {human(m['in'])} |",
        f"| cache read | {human(m['cache_read'])} |",
        f"| cache write | {human(m['cache_write'])} |",
        f"| total tokens | {human(total)} |",
        f"| **est. cost (USD)** | **${cost_usd(m):.2f}** |",
        "",
        "_Cost uses assumed rates (per 1M tok): "
        f"in ${RATES['in']}, out ${RATES['out']}, "
        f"cache-read ${RATES['cache_read']}, cache-write ${RATES['cache_write']}. "
        "Override with REPORT_*_RATE env vars. `out_tokens`/`turns` are the "
        "comparable work metrics; `total_tokens` is cache-inflated._",
        "",
        "## Main problems",
        "",
    ]
    if notes and os.path.isfile(notes):
        lines += [f"From `{os.path.basename(notes)}`:", "", "```",
                  open(notes, errors="ignore").read().strip(), "```", ""]
    else:
        lines += ["_No NOTES.md captured — inspect the transcript below._", ""]
    lines += ["## Transcript", "",
              f"`{transcript}`" if transcript else "_transcript not found_", ""]
    return "\n".join(lines)


def sidecar(args, m, transcript, where, out, void):
    total = m["in"] + m["out"] + m["cache_read"] + m["cache_write"]
    return {
        "fixture": args.fixture,
        "result": args.result,
        "pushed": args.push_status == "FINISHED",
        "push_id": args.push_id,
        "push_status": args.push_status,
        "eval_id": args.eval_id,
        "stuck_where": where,
        **({"void_reason": void} if void else {}),
        "turns": m["turns"],
        "skill_turns": m["skill_turns"],
        "out_tokens": m["out"],
        "in_tokens": m["in"],
        "cache_read": m["cache_read"],
        "cache_write": m["cache_write"],
        "total_tokens": total,
        "cost_usd": round(cost_usd(m), 2),
        "duration_s": args.duration,
        "model": m["model"],
        "cc_version": m["cc_version"],
        "effort": m["effort"],
        "skill_fingerprint": args.skill_fingerprint,
        "skill_git": args.skill_git,
        "fixture_sha": args.fixture_sha,
        "prompt_sha": args.prompt_sha,
        "skill_source": args.skill_source,
        "note": args.note,
        "transcript": transcript or "",
        "report": out,
        "started_at": (datetime.datetime.fromtimestamp(args.started_at)
                       .isoformat(timespec="minutes") if args.started_at else ""),
        "finished_at": datetime.datetime.now().isoformat(timespec="minutes"),
    }


# --------------------------------------------------------------------------- #
# Roll-up (REPORT_V<n>.md)
# --------------------------------------------------------------------------- #
def _versions(reports_dir):
    out = []
    for p in glob.glob(os.path.join(reports_dir, "REPORT_V*.json")):
        mo = re.match(r"REPORT_V(\d+)\.json$", os.path.basename(p))
        if mo:
            out.append((int(mo.group(1)), p))
    return sorted(out)


def _rows(reports_dir, verdicts):
    """One row per fixture: its sidecar (if the agent ran) + run_all's verdict.

    The json sidecar is the only input — the roll-up never scrapes markdown. A
    fixture with no sidecar is reported as ungraded rather than reconstructed.
    """
    ids = list(verdicts) or sorted(
        os.path.basename(p)[:-5]
        for p in glob.glob(os.path.join(reports_dir, "*.json"))
        if not os.path.basename(p).startswith("REPORT_V"))
    rows = []
    for fid in ids:
        try:
            data = json.load(open(os.path.join(reports_dir, fid + ".json")))
        except (OSError, ValueError):
            data = None
        harness = verdicts.get(fid, "")
        # The agent never ran this time, so any report present is from an earlier
        # run and says nothing about this one — grade it as not evaluated.
        if harness in UNGRADED:
            data = None
        # A voided sidecar (skill never used) is platform-true but says nothing
        # about the skill — keep it out of the graded set even when this roll-up
        # runs without run_all's verdicts (bare `report.py --aggregate`).
        if data and data.get("void_reason"):
            harness, data = "SKILL-UNUSED", None
        rows.append({"fixture": fid, "harness": harness, "data": data})
    return rows


def aggregate(args):
    reports_dir = args.reports_dir
    verdicts = dict(p.split(":", 1) for p in args.fixtures.split(",") if ":" in p)
    rows = _rows(reports_dir, verdicts)
    if not rows:
        return None, "no per-fixture reports found in %s — nothing to roll up" % reports_dir

    seen = _versions(reports_dir)
    version = args.version or (seen[-1][0] + 1 if seen else 1)
    earlier = [p for n, p in seen if n < version]
    prev = json.load(open(earlier[-1])) if earlier else None

    graded = [r for r in rows if r["data"]]

    def facet(key):
        return ", ".join(sorted({(r["data"].get(key) or "") for r in graded} - {""}))
    dates = sorted(r["data"].get("finished_at", "") for r in graded if r["data"].get("finished_at"))
    passes = [r for r in graded if r["data"]["result"] == "PASS"]
    pushed = [r for r in graded if r["data"]["pushed"]]

    L = [f"# Skill-eval run report — V{version}", ""]
    L += [
        f"- **Corpus:** {len(rows)} fixture(s) selected, {len(graded)} actually graded"
        f"{' (' + str(len(rows) - len(graded)) + ' never ran — see below)' if len(rows) != len(graded) else ''}",
        f"- **Dates:** {dates[0]} → {dates[-1]}" if dates else "- **Dates:** (unknown)",
        "- **Harness:** `eval/run_all.sh` → `prepare.sh` → `verify.sh` → `run.sh` → `report.py`",
        f"- **Skill source:** {facet('skill_source') or '(not recorded)'}",
        f"- **Skill fingerprint:** {facet('skill_fingerprint') or '(not recorded)'}"
        f"{'  (git ' + facet('skill_git') + ')' if facet('skill_git') else ''}",
        f"- **Model / CC / effort:** {facet('model') or '?'} / "
        f"{facet('cc_version') or '?'} / {facet('effort') or '?'}",
        f"- **Outcome:** {len(passes)}/{len(graded)} reached a completed evaluate"
        f" ({len(pushed)}/{len(graded)} reached a FINISHED push).",
        "",
        "## Overall success metrics",
        "",
        "| repo | pushed | eval | got stuck (where) | turns | out_tok | total_tok | est. cost |",
        "|------|--------|------|-------------------|------:|--------:|----------:|----------:|",
    ]
    for r in rows:
        d = r["data"]
        if not d:
            why = UNGRADED.get(r["harness"], r["harness"] or "no report written")
            L.append(f"| {r['fixture']} | – | – | _{r['harness'] or 'n/a'}: {why}_ | – | – | – | – |")
            continue
        L.append("| {} | {} | {} | {} | {} | {} | {} | ${:.2f} |".format(
            r["fixture"],
            "✅" if d["pushed"] else "❌",
            "✅" if d["result"] == "PASS" else "❌",
            d.get("stuck_where") or "—",
            d["turns"], human(d["out_tokens"]), human(d["total_tokens"]),
            d.get("cost_usd", 0.0)))

    med = lambda key: statistics.median([r["data"][key] for r in graded]) if graded else 0
    L += [
        "",
        f"**{len(pushed)}/{len(graded)} pushed, {len(passes)}/{len(graded)} completed eval; "
        f"median turns={med('turns'):.0f}, median out_tok={human(int(med('out_tokens')))}, "
        f"total est. cost=${sum(r['data'].get('cost_usd', 0) for r in graded):.2f}.**",
        "",
        "_Success = a FINISHED evaluate. A FINISHED push alone is not success — a "
        "broken integration can push and then fail eval. `total_tok` is "
        "cache-inflated (prompt-cache re-reads), so it is a throughput proxy, not "
        "a cost proxy; `turns` and `out_tok` are the metrics comparable between runs._",
    ]
    carried = [r["fixture"] for r in rows if r["harness"] == "SKIPPED"]
    if carried:
        L += ["", f"_Carried over from an earlier run (report already existed, not "
                  f"re-run): {', '.join(carried)}._"]

    # --- Shared problems ---------------------------------------------------- #
    # Grouped by the PHASE the harness observed, which is all it can know. The
    # thematic grouping and the causes come from the NOTES.md in each per-fixture
    # report — synthesized by a human/agent, never invented here.
    L += ["", "## Shared problems", ""]
    themes = {}
    for r in graded:
        d = r["data"]
        if d["result"] == "PASS":
            continue
        key = ("evaluate FAILED" if "FAILED" in d["stuck_where"]
               else "evaluate never reached a terminal state" if d["eval_id"]
               else "never created an Evaluate (died authoring or pushing)")
        themes.setdefault(key, []).append(r["fixture"])
    for r in rows:
        if not r["data"]:
            themes.setdefault("never ran (harness/environment gap, not a skill verdict)",
                              []).append(r["fixture"])
    if themes:
        for key, ids in themes.items():
            blocking = "BLOCKING" if "never ran" not in key else "ENVIRONMENT"
            L.append(f"- **{key}** — {blocking} — hit: {', '.join(ids)}.")
        L += ["",
              "<!-- REVIEW: the above is the harness's mechanical grouping by failure "
              "phase. Read each fixture's `reports/<id>.md` (it inlines the agent's "
              "NOTES.md) and rewrite these as themed problems with causes, tagging "
              "each BLOCKED vs self-corrected friction. Self-corrected friction is "
              "invisible to the harness — only the transcript/NOTES show it. -->",
              "",
              "Per-fixture detail (each inlines that run's NOTES.md):", ""]
        L += [f"- `reports/{r['fixture']}.md`" for r in rows if r["data"]]
    else:
        L.append("_None — every graded fixture reached a completed evaluate._")

    # --- What worked -------------------------------------------------------- #
    L += ["", "## What worked", ""]
    if passes:
        for r in passes:
            d = r["data"]
            L.append(f"- **{r['fixture']}** — push + evaluate FINISHED in "
                     f"{d['turns']} turns / {human(d['out_tokens'])} out_tok "
                     f"(${d.get('cost_usd', 0):.2f}).")
    else:
        L.append("_No fixture reached a completed evaluate._")

    # --- Deltas vs previous run --------------------------------------------- #
    if prev:
        # `changed` names which run conditions differ from the previous run —
        # the first thing to check before crediting/blaming the skill for a
        # delta. `x?` = the previous run predates recording of x.
        PROV_KEYS = (("skill_fingerprint", "skill"), ("fixture_sha", "fixture"),
                     ("prompt_sha", "prompt"), ("model", "model"),
                     ("effort", "effort"), ("cc_version", "cc"))
        L += ["", f"## Deltas vs V{prev.get('version', '?')}", "",
              "| repo | prev | now | Δturns | Δout_tok | changed |",
              "|------|------|-----|-------:|---------:|---------|"]
        old = {f["fixture"]: f for f in prev.get("fixtures", []) if f.get("data")}
        for r in graded:
            o = old.get(r["fixture"])
            if not o:
                L.append(f"| {r['fixture']} | _new_ | {r['data']['result']} | – | – | – |")
                continue
            od, nd = o["data"], r["data"]
            flags = []
            for key, label in PROV_KEYS:
                o_val, n_val = od.get(key) or "", nd.get(key) or ""
                if not o_val and not n_val:
                    continue
                if not o_val:
                    flags.append(label + "?")
                elif o_val != n_val:
                    flags.append(label)
            L.append("| {} | {} | {} | {:+d} | {:+d} | {} |".format(
                r["fixture"], od["result"], nd["result"],
                nd["turns"] - od["turns"], nd["out_tokens"] - od["out_tokens"],
                ", ".join(flags) or "—"))
        gone = [f for f in old if f not in {r["fixture"] for r in graded}]
        if gone:
            L.append(f"\n_Not in this run: {', '.join(gone)}._")
        L += ["",
              "<!-- REVIEW: state WHY each result changed. Start from the `changed` "
              "column (skill/fixture/prompt/model/effort/cc differ from the previous "
              "run); a delta with `—` and a changed result is a flake or a "
              "non-determinism worth naming. -->"]

    out_md = args.out or os.path.join(reports_dir, f"REPORT_V{version}.md")
    text = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(out_md)), exist_ok=True)
    open(out_md, "w").write(text)
    json.dump({"version": version, "generated_at": datetime.datetime.now().isoformat(timespec="minutes"),
               "passed": len(passes), "pushed": len(pushed), "graded": len(graded),
               "fixtures": rows},
              open(os.path.splitext(out_md)[0] + ".json", "w"), indent=1)
    return out_md, None


# --------------------------------------------------------------------------- #
def _selfcheck():
    import tempfile
    rec = {"type": "assistant", "version": "2.1.220", "effort": "xhigh",
           "message": {"model": "claude-opus-5", "usage": {
               "output_tokens": 100, "input_tokens": 50,
               "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 10}}}
    # Shaped like a real transcript record: the skill shows up as a Skill tool_use.
    rec_skill = json.loads(json.dumps(rec))
    rec_skill["message"]["content"] = [
        {"type": "tool_use", "name": "Skill", "input": {"skill": SKILL_NAME}}]
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "session.jsonl")
        open(p, "w").write(json.dumps(rec) + "\n" + json.dumps(rec_skill) + "\n")
        m = parse_transcript(p)
        assert m["turns"] == 2 and m["out"] == 200 and m["cache_read"] == 2000, m
        assert m["skill_turns"] == 1, m
        assert m["model"] == "claude-opus-5", m
        assert m["cc_version"] == "2.1.220" and m["effort"] == "xhigh", m
        assert cost_usd(m) > 0
        # started-at bound: a transcript older than the run must not be selected.
        assert latest_transcript(td, since=os.path.getmtime(p) + 3600) is None
        assert latest_transcript(td, since=0) == p

    assert stuck_where("PASS", "FINISHED", "e1") == "—"
    assert "FAILED" in stuck_where("FAIL", "FINISHED", "e1")
    assert "no Evaluate was ever created" in stuck_where("STUCK", "FINISHED", "")
    assert "none created" in stuck_where("STUCK", "", "")

    # Roll-up: two graded fixtures (one pass, one eval-fail) + one that never ran
    # + one whose PASS is void (skill never used) and must stay out of the ratio.
    with tempfile.TemporaryDirectory() as d:
        def put(fid, result, pushed, turns, out, ev, void="", **prov):
            json.dump({"fixture": fid, "result": result, "pushed": pushed,
                       "push_id": "p", "push_status": "FINISHED" if pushed else "",
                       "eval_id": ev, "stuck_where": stuck_where(result, "FINISHED" if pushed else "", ev),
                       **({"void_reason": void} if void else {}), **prov,
                       "turns": turns, "out_tokens": out, "total_tokens": out * 100,
                       "cost_usd": 1.5, "model": "claude-opus-5",
                       "skill_source": "--plugin-dir dist", "finished_at": "2026-07-29T12:00"},
                      open(os.path.join(d, fid + ".json"), "w"))
        put("good", "PASS", True, 90, 50_000, "e1")
        put("bad", "FAIL", True, 200, 90_000, "e2", skill_fingerprint="aaa111")
        put("ghost", "PASS", True, 20, 9_000, "e9", void="no turns under the skill")
        a = argparse.Namespace(reports_dir=d, fixtures="good:PASS,bad:FAIL,broken:PREP-FAIL,ghost:PASS",
                               version=None, out="")
        path, err = aggregate(a)
        assert err is None and os.path.isfile(path) and path.endswith("REPORT_V1.md"), (path, err)
        txt = open(path).read()
        assert "2/2 pushed, 1/2 completed eval" in txt, txt   # ghost excluded
        assert "median turns=145" in txt, txt
        assert "SKILL-UNUSED" in txt, txt
        assert "PREP-FAIL" in txt and "## What worked" in txt and "## Shared problems" in txt
        # V2 must pick up V1 as its baseline and diff it — and the `changed`
        # column must flag the differing skill fingerprint (and only that).
        put("bad", "PASS", True, 150, 70_000, "e2", skill_fingerprint="bbb222")
        path2, err2 = aggregate(argparse.Namespace(
            reports_dir=d, fixtures="good:PASS,bad:PASS", version=None, out=""))
        assert err2 is None and path2.endswith("REPORT_V2.md"), path2
        txt2 = open(path2).read()
        assert "## Deltas vs V1" in txt2 and "-50" in txt2, txt2
        assert "| skill |" in txt2, txt2          # bad: fingerprint aaa111 -> bbb222
        assert "| good | PASS | PASS | +0 | +0 | — |" in txt2, txt2
    print("selfcheck ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--aggregate", action="store_true",
                    help="roll the per-fixture sidecars up into reports/REPORT_V<n>.md")
    ap.add_argument("--version", type=int,
                    help="roll-up version number (default: next unused)")
    ap.add_argument("--fixtures", default="",
                    help="roll-up: id:VERDICT,... from run_all.sh — sets the row order and "
                         "includes fixtures whose agent never ran")
    ap.add_argument("--reports-dir", default=os.path.join(SCRIPT_DIR, "reports"))
    ap.add_argument("--fixture")
    ap.add_argument("--result", default="UNKNOWN")
    ap.add_argument("--eval-id", default="")
    ap.add_argument("--push-id", default="")
    ap.add_argument("--push-status", default="",
                    help="terminal state of the Push this run created (FINISHED/FAILED/…)")
    ap.add_argument("--transcript-dir", default="")
    ap.add_argument("--started-at", type=int, default=0,
                    help="epoch seconds; only transcripts modified at/after this "
                         "belong to this run (guards against inheriting a previous "
                         "attempt's session)")
    ap.add_argument("--notes", default="")
    ap.add_argument("--duration", type=int, default=0,
                    help="wall-clock seconds for the whole fixture run")
    ap.add_argument("--skill-fingerprint", default="",
                    help="content hash of the skill files under test")
    ap.add_argument("--skill-git", default="",
                    help="git describe of this repo when testing a local build")
    ap.add_argument("--fixture-sha", default="",
                    help="hash of the fixture's manifest entry (definition provenance)")
    ap.add_argument("--prompt-sha", default="",
                    help="hash of the operator prompt handed to the agent")
    ap.add_argument("--skill-source", default="",
                    help="which copy of the skill was under test (plugin vs --plugin-dir)")
    ap.add_argument("--note", default="",
                    help="harness-side caveat about the run (e.g. agent released early)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.selfcheck:
        _selfcheck()
        return 0
    if args.aggregate:
        path, err = aggregate(args)
        if err:
            print(err)
            return 1
        print(f"wrote {path}")
        return 0
    if not args.fixture:
        ap.error("--fixture is required")

    transcript = latest_transcript(args.transcript_dir, args.started_at)
    m = parse_transcript(transcript) if transcript else dict(EMPTY_METRICS)
    # A graded verdict with zero skill-attributed turns exercised raw Claude,
    # not the skill — the verdict is real platform state but grades nothing.
    void = ""
    if args.result in ("PASS", "FAIL") and m["skill_turns"] == 0:
        void = (f"the {SKILL_NAME} skill was never invoked — "
                + ("no transcript found for this run" if not transcript
                   else f"{m['turns']} turns in {transcript}, none calling it")
                + " — this verdict does not grade the skill.")
    where = stuck_where(args.result, args.push_status, args.eval_id)
    out = args.out or os.path.join(args.reports_dir, f"{args.fixture}.md")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, "w").write(render(args, m, transcript, args.notes, where, void))
    json.dump(sidecar(args, m, transcript, where, out, void),
              open(os.path.splitext(out)[0] + ".json", "w"), indent=1)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
