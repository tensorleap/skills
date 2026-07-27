#!/usr/bin/env python3
"""Emit a per-run skill-eval report: tokens, cost, result, and main problems.

Called by run.sh at the end of a fixture run:

  report.py --fixture <id> --result PASS|FAIL|STUCK --eval-id <id> \
            --transcript-dir <~/.claude/projects/...> --out reports/<id>.md
            [--notes <path/to/NOTES.md>]

Metrics come from the session transcript jsonl (assistant messages carry
message.usage). `out_tokens` and `turns` are the comparable "work" metrics;
`total_tokens` is dominated by prompt-cache re-reads, so it is a throughput
proxy, not a cost proxy — cost is computed per token *class* below.

Cost rates are ASSUMPTIONS (USD per million tokens) — override via env to match
the model/plan you ran. Defaults approximate Claude Opus list pricing.
"""

import argparse
import glob
import json
import os

RATES = {  # USD per 1M tokens; override with REPORT_*_RATE env vars
    "in":          float(os.environ.get("REPORT_IN_RATE", "15")),
    "out":         float(os.environ.get("REPORT_OUT_RATE", "75")),
    "cache_read":  float(os.environ.get("REPORT_CACHE_READ_RATE", "1.5")),
    "cache_write": float(os.environ.get("REPORT_CACHE_WRITE_RATE", "18.75")),
}


def latest_transcript(transcript_dir):
    if not transcript_dir or not os.path.isdir(transcript_dir):
        return None
    files = glob.glob(os.path.join(transcript_dir, "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def parse_transcript(path):
    """Sum turns + token classes from a session jsonl."""
    m = {"turns": 0, "out": 0, "in": 0, "cache_read": 0, "cache_write": 0}
    for line in open(path, errors="ignore"):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        msg = rec.get("message") or {}
        if rec.get("type") == "assistant" or msg.get("role") == "assistant":
            m["turns"] += 1
            u = msg.get("usage") or {}
            m["out"] += u.get("output_tokens", 0)
            m["in"] += u.get("input_tokens", 0)
            m["cache_read"] += u.get("cache_read_input_tokens", 0)
            m["cache_write"] += u.get("cache_creation_input_tokens", 0)
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


def render(args, m, transcript, notes):
    total = m["in"] + m["out"] + m["cache_read"] + m["cache_write"]
    emoji = {"PASS": "✅", "FAIL": "❌", "STUCK": "⚠️"}.get(args.result, "❔")
    lines = [
        f"# Skill-eval report — {args.fixture}",
        "",
        f"- **Result:** {emoji} {args.result}",
        f"- **Evaluate job:** {args.eval_id or '(none created)'}",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "|--------|------:|",
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


def _selfcheck():
    import tempfile
    rec = {"type": "assistant", "message": {"usage": {
        "output_tokens": 100, "input_tokens": 50,
        "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 10}}}
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(rec) + "\n" + json.dumps(rec) + "\n")
        p = f.name
    m = parse_transcript(p)
    os.unlink(p)
    assert m["turns"] == 2 and m["out"] == 200 and m["cache_read"] == 2000, m
    assert cost_usd(m) > 0
    print("selfcheck ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--fixture")
    ap.add_argument("--result", default="UNKNOWN")
    ap.add_argument("--eval-id", default="")
    ap.add_argument("--transcript-dir", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.selfcheck:
        _selfcheck()
        return 0
    if not args.fixture:
        ap.error("--fixture is required")

    transcript = latest_transcript(args.transcript_dir)
    m = parse_transcript(transcript) if transcript else {
        "turns": 0, "out": 0, "in": 0, "cache_read": 0, "cache_write": 0}
    text = render(args, m, transcript, args.notes)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        open(args.out, "w").write(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
