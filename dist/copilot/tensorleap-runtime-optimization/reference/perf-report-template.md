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
      "commit": "a1b2c3d"
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
  "server_validation": {"status": "FINISHED", "job": "<evaluate run id>", "duration": "12 min",
                        "notes": "batch 4 as recommended; no memory errors"},
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
