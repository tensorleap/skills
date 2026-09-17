# Skill-eval report — cifar10_resnet

- **Result:** ✅ PASS   (success = a FINISHED evaluate)
- **Push:** ✅ FINISHED — 6aaa9e382f329df70cd36f3a
- **Evaluate job:** 6aaa9ec92f329df70cd36f46
- **Got stuck (where):** —
- **Skill invoked:** 1 `Skill`-tool call(s) to `tensorleap-integration-creation` across 71 turns
- **Model:** claude-fable-5-1
- **Skill source:** --plugin-dir /Users/orram/Tensorleap/skills-metadata-taxonomy/dist/claude/integration
- **Provenance:** skill `75d18af34363` (git 3dcecda) · fixture `c22bcf19612e` · prompt `e741eabc3e51` · CC 2.1.273 · effort high

## Metrics

| metric | value |
|--------|------:|
| wall clock | 12m16s |
| turns | 71 |
| output tokens | 52.8k |
| input tokens | 2.0k |
| cache read | 6.7M |
| cache write | 225.8k |
| total tokens | 7.0M |
| **est. cost (USD)** | **$18.27** |

_Cost uses assumed rates (per 1M tok): in $15.0, out $75.0, cache-read $1.5, cache-write $18.75. Override with REPORT_*_RATE env vars. `out_tokens`/`turns` are the comparable work metrics; `total_tokens` is cache-inflated._

## Main problems

From `NOTES.md`:

```
# Integration notes — cifar10_resnet (pre)

Date: 2026-09-16

## Setup
- Skill `integration:tensorleap-integration-creation` invoked first.
- Preflight OK: local server v1.6.74 at localhost:4589, data volume `/Users/orram/data`, authenticated.
- Env: poetry (.venv, Python 3.10.14). Installed `code-loader` 1.0.207 and `tensorflow-macos==2.11.0` via `poetry add`.
- Model contract (`model/resnet.h5`): input `image` (224,224,3) float32; one output (10,) softmax probabilities.
- Data: CIFAR-10 python batches already present on the data volume at `/Users/orram/data/cifar-10-batches-py`
  (reused; preprocess fetches via keras `get_file` into the data root only if absent).
- Cap requested by user: `sample_limit_per_split: 250` in `tensorleap/project_config.yaml`.

## Log
- Authored `leap_integration.py` (root), `tensorleap/{config,preprocess,encoders,metrics,metadata,visualizers}.py`,
  `tensorleap/project_config.yaml`, `leap.yaml`, `requirements.txt`. Run loop clean at every stage; final table all ✅.
- `tl_check.py`: isValid True, 250/250 train/val, input image (224,224,3), output classes (10).
- Created project `cifar10-resnet-pre` (id 6aaa9e0e2f329df70cd36f2f) and set `projectId` in `leap.yaml`.
- Push: `leap push -m model/resnet.h5 --type H5_TF2 -n resnet-v1 -b 32 --eval --yes` (background; log in scratchpad push.log).
- Push run id: 6aaa9e382f329df70cd36f3a (INITIALIZING at 16:48).
- Push FINISHED (all build steps ✔). Evaluate job id: 6aaa9ec92f329df70cd36f46 (STARTED 16:51, batch 32).
```

## Transcript

`/Users/orram/.claude/projects/-Users-orram-Tensorleap-skills-eval--fixtures-cifar10-resnet-pre/ae54290a-5006-4395-aaca-0906993efeb8.jsonl`
