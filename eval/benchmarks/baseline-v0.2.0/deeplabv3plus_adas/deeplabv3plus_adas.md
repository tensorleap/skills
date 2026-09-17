# Skill-eval report — deeplabv3plus_adas

- **Result:** ✅ PASS   (success = a FINISHED evaluate)
- **Push:** ✅ FINISHED — 6aaa6b3b2f329df70cd36ecc
- **Evaluate job:** 6aaa6b842f329df70cd36ed8
- **Got stuck (where):** —
- **Skill invoked:** 1 `Skill`-tool call(s) to `tensorleap-integration-creation` across 91 turns
- **Model:** claude-fable-5-1
- **Skill source:** --plugin-dir /Users/orram/Tensorleap/skills/dist/claude/integration
- **Provenance:** skill `7d891c54e522` (git acff89f) · fixture `f694b2ccf074` · prompt `manual` · CC 2.1.273 · effort high
- **Harness note:** run.sh was OOM-killed by macOS at ~13:05 while tracking; agent completed push+eval unattended; verdict/report reconstructed manually from server state

## Metrics

| metric | value |
|--------|------:|
| wall clock | 119m51s |
| turns | 91 |
| output tokens | 100.9k |
| input tokens | 2.5k |
| cache read | 11.0M |
| cache write | 310.9k |
| total tokens | 11.4M |
| **est. cost (USD)** | **$29.92** |

_Cost uses assumed rates (per 1M tok): in $15.0, out $75.0, cache-read $1.5, cache-write $18.75. Override with REPORT_*_RATE env vars. `out_tokens`/`turns` are the comparable work metrics; `total_tokens` is cache-inflated._

## Main problems

From `NOTES.md`:

```
# Integration notes (DeepLabV3+ Cityscapes -> KITTI domain gap)

Not committed. Log of what was done, in order.

## Setup
- Invoked skill `integration:tensorleap-integration-creation`; preflight OK (local server
  http://localhost:4589, data volumes `/Users/orram/data` and `/Users/orram/Tensorleap/data`,
  authenticated as or.ram@tensorleap.ai).
- Dataset already on the volume at `/Users/orram/Tensorleap/data/domain-gap-2` (row 2: reuse,
  no copy). Verified every image/GT/metadata path in `train.json` (400 rows / 321 unique images),
  `val.json` (100 / 85 unique) and `unlabeled_subset/manifest.json` (289: 114 cityscapes,
  175 kitti_raw) exists on disk. `domain_gap/utils/project_config.yaml` already had
  `LOCAL_BASE_PATH: /Users/orram/Tensorleap/data/domain-gap-2` and `USE_LOCAL: True`; unchanged.
- Env: repo's Poetry venv (Python 3.10.14, TF 2.12.0). `poetry add code-loader Pillow` failed on
  the project's `python >=3.8,<3.11` constraint (pillow 12 needs >=3.10), so installed with
  `poetry run pip install code-loader Pillow` -> code-loader 1.0.207, Pillow 12.3.0
  (pyproject/poetry.lock untouched).
- Model contract (`models/DeeplabV3.h5`): input `normalized_image` (1024, 2048, 3) float32;
  single output (1024, 2048, 19) float32 **raw logits** (final Conv2D is linear, values in
  [-10.8, 11.9]) -> not a softmax despite the task description; softmax/argmax live in the
  loss/metrics/visualizers. batch_input_shape is (1, ...) but the model runs with batch 2.

## Design
- `sample_limit_per_split: 250` in `tensorleap/project_config.yaml`, applied to training,
  validation and unlabeled after a seeded (42) shuffle so each recipe's Cityscapes/KITTI mix is
  kept (train.json lists all Cityscapes rows before KITTI rows, so a plain head-slice would drop
  KITTI). Validation has 100 rows so it is not capped.
- Sample ids are `<subset>_<row index>` because the csv-derived recipes repeat images.
- Label table copied into `tensorleap/cityscapes_labels.py` to avoid importing
  `domain_gap.data.cs_data` (pulls google-cloud-storage) on the platform.

## Local validation
- Run loop stages: preprocess -> encoders + load_model -> loss + integration_test -> metrics,
  metadata, visualizers. Exit table: all 9 rows ✅, no default-use warnings.
- 8 `integration_test` runs pass (Cityscapes + KITTI sample x 2 per split, train + val);
  unlabeled frames (cityscapes + kitti_raw) checked via direct decorated calls.
- `tl_check.py` (`check_dataset()`): isValid True, training 250 / validation 100 / unlabeled 250,
  input (1024, 2048, 3), GT (1024, 2048), 47 metadata handlers passed.
- Value sanity (val): Cityscapes aachen_000152 loss 0.096 / mIoU 0.65 / acc 0.96; KITTI 000158
  loss 1.95 / mIoU 0.26 / acc 0.56; loss on shuffled GT 7.9 / 6.3 (metric discriminates).
- Replaced root `requirements.txt` (was the repo's TF/GCS dev list) with the integration runtime
  deps: numpy, Pillow~=12.3.0, PyYAML~=6.0 (original saved in the session scratchpad).

## Deploy
- Reused no existing project (an older `domain-gap` project exists; left untouched). Created
  project `deeplabv3plus-adas` id `6aaa6b1e2f329df70cd36ec1`, set in `leap.yaml`.
- No in-flight Push runs before pushing.
- Push command (background, log in `push.log`):
  `leap push -m models/DeeplabV3.h5 -n deeplabv3-cs-kitti-v1 -b 1 --type H5_TF2 --eval --yes`
  Batch 1 chosen because the H5 has batch_input_shape (1, 1024, 2048, 3) and outputs are 160 MB
  per sample.
- Push run `6aaa6b3b2f329df70cd36ecc` FINISHED (13:11); all build steps ✔ incl. Testing Loss /
  Visualizers / Metrics. Model version `deeplabv3-cs-kitti-v1`.
- Evaluate run `6aaa6b842f329df70cd36ed8` created by `--eval` (13:12). Background watcher polls
  `leap run list -t Evaluate` every 5 min until terminal.
```

## Transcript

`/Users/orram/.claude/projects/-Users-orram-Tensorleap-skills-eval--fixtures-deeplabv3plus-adas-pre/b7cdb70e-2b31-42f3-ab27-761d218ba766.jsonl`
