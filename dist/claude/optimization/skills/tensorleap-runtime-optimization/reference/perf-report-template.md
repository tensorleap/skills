# Report: `report.json` → `report.md`

Write `tensorleap/runtime-optimization/report.json`, then run
`python {{scripts_dir}}/tl_perf.py report`. The script validates the JSON (exit 11 when
invalid — fix and re-run) and renders `report.md` and `report.html`, filling every table —
environment, floor, fit, the time and memory breakdowns before/after, component timings and
the whole online part — from the run artifacts itself. Never write the tables by hand.

## Layout

The report answers first and keeps details in collapsed appendices. It has two parts. Memory
wins conflicts with runtime by default, but a part leads with memory only when there is memory
pressure (Part 1: memory status AMBER or RED; Part 2: an out-of-memory kill, a pod near its
memory limit, a server memory warning, or an AMBER/RED status); otherwise it leads with time
and says so.

- **Header** — date, run mode (offline only / + smoke check / + online diagnostics),
  code-loader version, and the priority with its reason.
- **Part 1 — Offline: your integration's code** (always). Summary (your `summary` plus
  generated facts: memory per worker, expected runtime before → after, model floor, changes
  kept) · Where the memory goes · Where the time goes (per block, share of the total, ×
  model floor) · What we changed (one row per change) · Trade-offs taken · Remaining
  bottlenecks, ranked, each with its owner · Decisions for you (`lossy_options`) · Server
  check (smoke) · Not verified · **Appendix 1** (environment, component timings with
  P50–P99 for components ≥ 1 % of their block, each change in detail).
- **Part 2 — Online: bottlenecks on the Tensorleap server** (only for an approved
  diagnostics run; otherwise one line saying it was not run). Rendered entirely from
  `online/analysis.json`: Summary (run, memory, time, primary bottleneck, how it compares
  with Part 1, confidence) · Where the memory goes on the server · Where the time goes
  (phases with their share and what set their pace; post-processing is one row, not analyzed) ·
  Bottlenecks, ranked (root causes) ·
  What to do · Server settings (only the ones that did not fit) · Confidence and coverage ·
  **Appendix 2** (server environment and every setting, engine measurements, engine stages
  ≥ 1 % of their pod's busy time, counters and events, warnings, memory per pod when not shown above, evidence
  log lines referenced as `[E1]`, `[E2]` …).
- A closing line lists the run's files.

## Schema

```json
{
  "title": "Runtime optimization — <integration>",
  "summary": "Two sentences: where memory and time went, what changed, what limits it now.",
  "priority": "memory",
  "priority_reason": "the user: \"memory is fine, it just needs to be fast\" (only with runtime)",
  "environment": {"server": "local k3d", "notes": "optional extra rows"},
  "visualized_samples": 10000,
  "optimizations": [
    {
      "title": "Shared memoized image decode",
      "problem": "The image-statistics metadata re-ran the image encoder's full decode for every sample",
      "change": "One shared, memoized decode used by the encoder and the metadata",
      "evidence": "load_image ran 2.0x per sample; each image was read 2x per sample",
      "equivalence": "bit-identical: 2400 values on 48 samples (tl_perf compare exit 0)",
      "before": "30.0 ms/sample", "after": "18.0 ms/sample", "gain": "-38% expected runtime",
      "memory": "1.20 → 1.21 GB per worker",
      "side_effects": "none measured",
      "commit": "a1b2c3d",
      "kind": "performance",
      "catalog": "A"
    }
  ],
  "tradeoffs": [
    {"change": "float32 preprocess arrays", "memory": "-3.1 GB per worker", "runtime": "+6%",
     "decision": "kept: memory first, within the 15% runtime allowance"},
    {"change": "per-file decode cache", "memory": "+0.9 GB per worker", "runtime": "-12%",
     "decision": "rejected: grew memory"}
  ],
  "remaining_bottleneck": {
    "component": "metadata:image_stats",
    "share": "85% of generation, 75% of expected runtime",
    "owner": "integration",
    "evidence": "15 ms/sample = 10x inference; full-resolution texture statistics",
    "explanation": "per-sample image statistics computed at full resolution"
  },
  "lossy_options": [
    {"option": "...", "gain": "...", "cost": "...", "decision": "declined"}
  ],
  "server_validation": {"mode": "smoke", "authorized_by_user": false,
                        "status": "FINISHED", "job": "<evaluate run id>", "duration": "12 min",
                        "notes": "batch 4 as recommended; no memory errors"},
  "online_comparison": "Offline assumed 1,000 visualized samples; the server visualized 10,000, so visualization, not generation, dominates the run.",
  "memory": {
    "status": "AMBER",
    "reasons": ["AMBER: preprocess[training]['data'] alone holds 1.2 GB in every worker"],
    "remaining_holder": {"target": "preprocess[training]['data']['inputs']",
                         "evidence": "1.05 GB float32 per worker; read by the input encoder",
                         "owner": "integration"},
    "notes": "budget 64 GB (this machine's RAM) — state it when the server's memory is unknown"
  },
  "remaining_integration_issues": ["an input check runs 5x per sample (1 ms) — minor"],
  "tensorleap_actions": [
    {"need": "evaluation is producer-bound: sample generation costs 12x inference",
     "evidence": "profile: generation 18 ms/sample vs floor 1.5 ms/sample",
     "impact": "more worker capacity would shorten evaluation"}
  ],
  "coverage_caveats": [
    "a branch taken only for one data domain never ran on the sampled data; equivalence there is not verified"
  ]
}
```

Required: `title`; `optimizations` (list, may be empty; each needs `problem`, `change`,
`evidence`, `equivalence`); `remaining_bottleneck` (`component`, `evidence`);
`tensorleap_actions` (list, may be empty; each needs `need`).

`priority` (optional): `memory` (the default) or `runtime`. Memory first: the header says
memory wins conflicts; under memory pressure Part 1 puts Where the memory goes before Where
the time goes and ranks the memory holder first, and Part 2 leads with where the memory went.
Defaults to the priority `tl_perf score` ran with. `priority_reason` (optional): the user's words behind a
runtime priority.

`server_validation` is optional (absent = no server validation); when present it needs
`mode` — `smoke` (6A) or `diagnostics` (6B) — and `authorized_by_user` (whether the user
said yes to diagnostics in 6.0). A smoke run is one line in Part 1. `diagnostics` requires
`authorized_by_user: true`; once the run has finished, `tl_perf report` renders **Part 2**
from `online/analysis.json` (written by `tl_perf online analyze`; exit 11 if it is
missing) — never write it by hand. Part 2 covers the evaluation and the visualization; the
platform's post-processing shows as one row of time and is not analyzed. `online_comparison`
(optional): how the online picture differs from Part 1 and why; the report adds the
visualized-sample count it measured when it differs from `visualized_samples`.

Optional per optimization: `title` — a few words for the table (default: the problem's
first sentence); `memory` — its measured memory effect; `kind` — `performance`,
`correctness` (a bug fix: wrong or crashing output), `prerequisite` (e.g. a dependency
upgrade another fix needs) or `memory` (a footprint fix, from the memory loop); and
`catalog` — the bottleneck-catalog class (`A`–`W`, `M1`–`M12`, combined with `/` like
`M3/M7`), or `new` for a problem no class describes. Unknown values are rejected (exit 11).

Optional `tradeoffs`: every conflict between memory and runtime — `change`, `memory`,
`runtime`, `decision` (kept / rejected and why). With memory first and none recorded, the
report says so.

Optional `owner` on `remaining_bottleneck` and `memory.remaining_holder`: `integration`,
`tensorleap` or `irreducible` (exit 11 otherwise).

Optional `memory`: `status` (`GREEN` / `AMBER` / `RED`, from `tl_perf score`), `reasons`,
`remaining_holder` (`target`, `evidence`, `owner`), `notes`. `tl_perf report` adds the
footprint table (peak, imports, preprocess result, preprocess transient, caches and growth,
unattributed) before → after from the profiles.

`tl_perf report` writes `report.md` and `report.html` (the published, self-contained page;
the appendices are collapsed there). Never edit either by hand; change `report.json` and
re-render.

## Writing rules

- **Name the remaining bottleneck by its share of total runtime**, not by whether its own
  number went up or down. When everything else collapses, the untouched component becomes
  the limit — that is expected, and it is the finding.
- **Every optimization cites its evidence and its equivalence result** — the measured
  signal that justified it and the `compare` verdict. "Faster" without "equivalent" is not
  an optimization.
- **Separate what changed in the integration from what Tensorleap should look at**, and
  give every remaining bottleneck its owner. Tensorleap actions describe the need and the
  numbers, never internal settings.
- **Record every memory/runtime conflict** in `tradeoffs`; with memory first, a reader must
  see what runtime was given up and what runtime fix was turned down.
- **State what was not verified**: branches the sample never executed, data the local run
  could not reach, a missing server validation.
- Before/after numbers come from the same machine and the same sample set.
- Calibrate claims to evidence: "the same file is read 2× per sample" (measured) beats
  "probably re-reads" (guess).
