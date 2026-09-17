# Skill-eval report — plankd

- **Result:** ✅ PASS   (success = a FINISHED evaluate)
- **Push:** ✅ FINISHED — 6aaa8b362f329df70cd36f0d
- **Evaluate job:** 6aaa8bee2f329df70cd36f1b
- **Got stuck (where):** —
- **Skill invoked:** 1 `Skill`-tool call(s) to `tensorleap-integration-creation` across 95 turns
- **Model:** claude-fable-5-1
- **Skill source:** --plugin-dir /Users/orram/Tensorleap/skills/dist/claude/integration
- **Provenance:** skill `7d891c54e522` (git acff89f-dirty) · fixture `9d1be19d3828` · prompt `manual` · CC 2.1.273 · effort high
- **Harness note:** run.sh was OOM-killed by macOS ~15:20 while tracking; agent completed push+eval unattended; verdict/report reconstructed manually from server state

## Metrics

| metric | value |
|--------|------:|
| wall clock | 20m08s |
| turns | 95 |
| output tokens | 140.5k |
| input tokens | 2.8k |
| cache read | 11.8M |
| cache write | 388.9k |
| total tokens | 12.3M |
| **est. cost (USD)** | **$35.50** |

_Cost uses assumed rates (per 1M tok): in $15.0, out $75.0, cache-read $1.5, cache-write $18.75. Override with REPORT_*_RATE env vars. `out_tokens`/`turns` are the comparable work metrics; `total_tokens` is cache-inflated._

## Main problems

From `NOTES.md`:

```
# Tensorleap integration notes (PlanKD / InterFuser)

Session log for the decorator-style Tensorleap integration of this repo. Not committed.

## Setup
- Skill: tensorleap-integration-creation (preflight OK: local server http://localhost:4589, volumes
  /Users/orram/data and /Users/orram/Tensorleap/data, authenticated as or.ram@tensorleap.ai).
- Env: repo poetry env (.venv, Python 3.10.14). Installed code-loader 1.0.207 into it.
- Data delivery row 2: data already on the mounted volume /Users/orram/Tensorleap/data/plankd-bench
  (carla/ = training via dataset_index.txt, b2d/ = validation scenarios). Nothing copied.
- Model: pre-exported ONNX /Users/orram/Tensorleap/data/eval/plankd/interfuser-26_3M.onnx, used as-is.
- Fixed a broken opencv-python wheel in the venv (import was SIGKILLed: invalid code signature);
  reinstalled the same version 4.11.0.86 with pip. Needed because timm/data/heatmap_utils.py imports cv2.

## Files created
- leap_integration.py, leap.yaml, requirements.txt (repo root)
- tensorleap/project_config.yaml, preprocess.py, encoders.py, metrics.py, metadata.py, visualizers.py,
  integration-report.md
- Cap: sample_limit_per_split: 250 in tensorleap/project_config.yaml (evenly spaced subsample per split;
  set to 0 to evaluate everything). CARLA has 505 frames, Bench2Drive 633.

## Validation
- run_integration.sh: all 9 decorator rows exercised, Successful! on 3 training + 3 validation samples.
- tl_check.py (check_dataset): isValid True, 250/250 samples, 39 payloads all passed.
- Full sweep of all 500 capped samples through every encoder + metadata: 0 failures, 0 non-finite.

## Deploy
- Project: plankd-interfuser (6aaa8af72f329df70cd36f02)
- Push log: /private/tmp/claude-501/-Users-orram-Tensorleap-skills-eval--fixtures-plankd-pre/eddb826c-c0ba-49ed-8635-e4140a1241a1/scratchpad/push.log
- Push run: 6aaa8b362f329df70cd36f0d — FINISHED (all build steps passed: Parsing Dataset, Data Loader
  Preparation, Parsing Model, Convert, Build Model, Run Model Inference, Testing Loss/Visualizers/Metrics).
- Model version name: interfuser-26_3M, eval batch size 8.
- Evaluate job: 6aaa8bee2f329df70cd36f1b — STARTED 2026-09-16 15:30 (watching to a terminal state).
```

## Transcript

`/Users/orram/.claude/projects/-Users-orram-Tensorleap-skills-eval--fixtures-plankd-pre/eddb826c-c0ba-49ed-8635-e4140a1241a1.jsonl`
