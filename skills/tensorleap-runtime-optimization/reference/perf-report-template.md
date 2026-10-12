# Report: `report.json` → `report.md`

Write `tensorleap/runtime-optimization/report.json`, then run
`python {{scripts_dir}}/tl_perf.py report`. The script validates the JSON (exit 11 when
invalid — fix and re-run) and renders `report.md`, filling the environment, floor, fit and
before/after breakdown tables from the run artifacts itself. Never write the tables by hand.

## Schema

```json
{
  "title": "Runtime optimization — <integration>",
  "summary": "Two sentences: where time went, what changed, what limits it now.",
  "environment": {"server": "local k3d", "notes": "optional extra rows"},
  "visualized_samples": 10000,
  "optimizations": [
    {
      "problem": "The image-statistics metadata re-ran the image encoder's full decode for every sample",
      "change": "One shared, memoized decode used by the encoder and the metadata",
      "evidence": "load_image ran 2.0x per sample; each image was read 2x per sample",
      "equivalence": "bit-identical: 2400 values on 48 samples (tl_perf compare exit 0)",
      "before": "30.0 ms/sample", "after": "18.0 ms/sample", "gain": "-38% expected runtime",
      "side_effects": "none measured (peak memory +1%)",
      "commit": "a1b2c3d",
      "kind": "performance",
      "catalog": "A"
    }
  ],
  "remaining_bottleneck": {
    "component": "metadata:image_stats",
    "share": "85% of generation, 75% of expected runtime",
    "evidence": "15 ms/sample = 10x inference; full-resolution texture statistics",
    "explanation": "per-sample image statistics computed at full resolution"
  },
  "lossy_options": [
    {"option": "...", "gain": "...", "cost": "...", "decision": "declined"}
  ],
  "server_validation": {"mode": "smoke", "authorized_by_user": false,
                        "status": "FINISHED", "job": "<evaluate run id>", "duration": "12 min",
                        "notes": "batch 4 as recommended; no memory errors"},
  "memory": {
    "status": "AMBER",
    "reasons": ["AMBER: preprocess[training]['data'] alone holds 1.2 GB in every worker"],
    "remaining_holder": {"target": "preprocess[training]['data']['inputs']",
                         "evidence": "1.05 GB float32 per worker; read by the input encoder"},
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

`server_validation` is optional (absent = no server validation); when present it needs
`mode` — `smoke` (6A) or `diagnostics` (6B) — and `authorized_by_user` (whether the user
said yes to diagnostics in 6.0). `diagnostics` requires `authorized_by_user: true`; once the
run has finished, `tl_perf report` renders the **Online diagnostics** section from
`online/analysis.json` (written by `tl_perf online analyze`; exit 11 if it is missing) —
never write that section by hand. It covers the evaluation and the visualization; the
platform's post-processing shows as one row of time and is not analyzed. It includes
**Root causes** (per slow phase: what set the pace, where that time went, the part to
change, what is ruled out, how much is explained)
and **Server settings** (what each pod got against what the run used, with a verdict and a
recommendation for automatic or manual settings).

`priority` (optional): `runtime` (default) or `memory` — memory first: the report opens with
it and puts the memory section before the runtime breakdown, and the online diagnostics lead
with where the memory went. Defaults to the priority `tl_perf score` ran with.

`tl_perf report` writes `report.md` and `report.html` — the published page to share (self-
contained) — and appends **How this report was made** (every offline and online step that left
an artifact, with its result) to both. Never edit either by hand; change `report.json` and
re-render.

Optional per optimization: `kind` — `performance`, `correctness` (a bug fix: wrong or
crashing output), `prerequisite` (e.g. a dependency upgrade another fix needs) or `memory`
(a footprint fix, from the memory loop); and `catalog` — the bottleneck-catalog class (`A`–`W`,
`M1`–`M12`, combined with `/` like `M3/M7`), or `new` for a problem no class describes.
Unknown values are rejected (exit 11).

Optional `memory`: `status` (`GREEN` / `AMBER` / `RED`, from `tl_perf score`), `reasons`,
`remaining_holder` (`target`, `evidence`), `notes`. `tl_perf report` adds the footprint
table (peak, imports, preprocess result, preprocess transient, caches and growth,
unattributed) before → after from the profiles itself.

## Writing rules

- **Name the remaining bottleneck by its share of total runtime**, not by whether its own
  number went up or down. When everything else collapses, the untouched component becomes
  the limit — that is expected, and it is the finding.
- **Every optimization cites its evidence and its equivalence result** — the measured
  signal that justified it and the `compare` verdict. "Faster" without "equivalent" is not
  an optimization.
- **Separate what changed in the integration from what Tensorleap should look at.**
  Tensorleap actions describe the need and the numbers, never internal settings.
- **State what was not verified**: branches the sample never executed, data the local run
  could not reach, a missing server validation.
- Before/after numbers come from the same machine and the same sample set.
- Calibrate claims to evidence: "the same file is read 2× per sample" (measured) beats
  "probably re-reads" (guess).
