# Skill-eval report — deeplabv3plus_adas

- **Result:** ✅ PASS   (success = a FINISHED evaluate)
- **Push:** ✅ FINISHED — 6aabeae62f329df70cd370de
- **Evaluate job:** 6aabeb092f329df70cd370e8
- **Got stuck (where):** —
- **Skill invoked:** 1 `Skill`-tool call(s) to `tensorleap-integration-creation` across 93 turns
- **Model:** claude-fable-5-1
- **Skill source:** --plugin-dir /Users/orram/Tensorleap/skills-metadata-taxonomy/dist/claude/integration
- **Provenance:** skill `75d18af34363` (git metadata-taxonomy@8c47707) · fixture `f694b2ccf074` · prompt `manual` · CC 2.1.274 · effort high
- **Harness note:** First eval attempt (6aabc4f8) FAILED on infra: MinIO NoSuchKey on a cluster digest during population display + k8s Insufficient memory scheduling failures, while host had 0.1GB free. Unchanged integration re-pushed unchanged (-o deeplavb3-cs-kitti-v1 -u metric); this eval hit the SAME NoSuchKey warning at ~50min but it was non-fatal (WARNING, not error) and the job continued to FINISHED at 107min. Turns/cost from the original authoring run (93 turns) still apply; this note covers only the eval retry.

## Metrics

| metric | value |
|--------|------:|
| wall clock | 284m13s |
| turns | 93 |
| output tokens | 141.3k |
| input tokens | 2.7k |
| cache read | 12.4M |
| cache write | 420.7k |
| total tokens | 13.0M |
| **est. cost (USD)** | **$37.12** |

_Cost uses assumed rates (per 1M tok): in $15.0, out $75.0, cache-read $1.5, cache-write $18.75. Override with REPORT_*_RATE env vars. `out_tokens`/`turns` are the comparable work metrics; `total_tokens` is cache-inflated._

## Main problems

From `NOTES.md`:

```
# Tensorleap integration notes (DeepLabV3+ Cityscapes -> KITTI domain gap)

Working log for this session. Not committed.

## Setup
- Skill invoked: `integration:tensorleap-integration-creation` (first action).
- Preflight gate: PASS. Local server at http://localhost:4589, data volumes
  `/Users/orram/data` and `/Users/orram/Tensorleap/data`, authenticated as or.ram@tensorleap.ai.
- Dataset: already on the data volume at `/Users/orram/Tensorleap/data/domain-gap-2`
  (Data delivery row 2: local server, data already on volume -> reuse, no copy).
  `domain_gap/utils/project_config.yaml` already had `LOCAL_BASE_PATH` set to that path and `USE_LOCAL: True`.
- Emptiness gate: every `image_path` / `gt_path` / `metadata` referenced by
  `domain_gap/data/splits/original_csv_subset/{train,val}.json` and every `path` in
  `domain_gap/data/splits/unlabeled_subset/manifest.json` exists on the volume.
  `gt_image_path` (`*_gtFine_color.png`) files are NOT on the volume -> the integration derives
  colors from `labelIds` via the class table instead of reading them.
- Python env: repo's own poetry venv (`.venv`, Python 3.10.14, tensorflow-macos 2.12.0, keras 2.12.0,
  numpy 1.23.5). Installed `code-loader==1.0.207` and `Pillow` with `poetry run pip install`
  (pyproject.toml/poetry.lock left untouched).
- Model: `models/DeeplabV3.h5` was NOT present in this checkout (`*.h5` is gitignored, and `git ls-files`
  has no `models/`). The only copy found by filename on this machine is
  `/Users/orram/Tensorleap/DeepLabV3Plus-ADAS/models/DeeplabV3.h5`; copied ONLY that binary into
  `models/DeeplabV3.h5` here (no code from that directory was read). Still gitignored.

## Split composition (from the committed recipes)
- train.json: 400 rows, 321 unique images (290 cityscapes rows: aachen/zurich; 110 kitti data_semantics rows).
- val.json: 100 rows, 85 unique images (40 cityscapes, 60 kitti data_semantics).
- manifest.json (unlabeled): 289 (114 cityscapes tubingen/munster/bremen, 175 kitti_raw Karlsruhe).
- Duplicate rows inside train/val are exact duplicates (same image, GT, city); the integration de-duplicates
  by image path (lossless) so sample ids stay unique.
- No overlap between train / val / manifest.

## Timeline
- 13:36 Model contract inspected: input `normalized_image` (1,1024,2048,3) float32, output (1,1024,2048,19) float32
  RAW LOGITS (values -10..11, per-pixel sums != 1) -> softmax is applied in metrics/visualizers, not in the model.
  Model accepts batch>1 despite the fixed batch_shape of 1 (tested 1/2/4, ~3s/img CPU).
- 13:40 preprocess.py: training=train.json (250 after cap), validation=val.json (85 unique), unlabeled=manifest.json (250 after cap). Run: preprocess row ✅.
- 13:42 encoders.py: `normalized_image` input ((img/255-IMAGE_MEAN)/IMAGE_STD, resized to IMAGE_SIZE 2048x1024 W,H),
  `segmentation_mask` GT = Cityscapes train ids (0..18, 19=ignore) as (H,W,1) float32; unlabeled -> empty array. Run: both rows ✅.
- 13:45 metrics.py custom loss `pixel_cross_entropy` (softmax CE from logits, ignore=19, per-sample mean), load_model (Keras h5),
  minimal integration_test -> `Successful!`, all mandatory rows ✅.
- 13:50 metrics (pixel_accuracy, mean_iou, mean_max_confidence), visualizers (image, gt_mask, prediction_mask,
  predicted_class_distribution), metadata (source_*, vehicle_*, image_*, gt_*) added one group at a time; each run `Successful!`.
- 13:55 Harness expanded to 3 train + 3 val samples (incl. KITTI) -> all `Successful!`, all 9 rows ✅.
  tl_check.py: isValid True; lengths training 250 / validation 85 / unlabeled 250.
- Unlabeled path smoke-tested directly (munster + KITTI raw): GT empty array, gt_* metadata all None, viz uint8 OK.

## Deploy
- Project: `deeplabv3plus-adas-pre` id `6aabc47f2f329df70cd3709b` (created this session; `projectId` set in leap.yaml).
- Push command (from repo root, background, log in session scratchpad `push.log`):
  `leap push -m models/DeeplabV3.h5 -n deeplabv3-cs-kitti-v1 -b 4 --type H5_TF2 --eval --yes`
- Cap in place: `tensorleap/project_config.yaml` -> `sample_limit_per_split: 250` (training 250 / validation 85 / unlabeled 250).
- 13:45 Push run `6aabc4af2f329df70cd370a6` FINISHED (all build steps ✔: deps, dataset parse, model parse/convert/build,
  inference, loss, visualizers, metrics). Model version `deeplabv3-cs-kitti-v1`, batch size 4.
- 13:46 Evaluate job `6aabc4f82f329df70cd370b2` STARTED (tracked by a background watcher, polling every 5 min).
```

## Transcript

`/Users/orram/.claude/projects/-Users-orram-Tensorleap-skills-eval--fixtures-deeplabv3plus-adas-pre/d102cca4-325d-47f2-92af-379e7568ae76.jsonl`
