# Integration report — DeepLabV3+ Cityscapes -> KITTI domain gap

Task: 19-class Cityscapes semantic segmentation (`CATEGORIES` in `domain_gap/data/cs_data.py`).
Model: Keras `models/DeeplabV3.h5`, pushed as `H5_TF2`. Input `normalized_image` (1024, 2048, 3) float32,
output (1024, 2048, 19) float32 logits.

## Layout
- `leap_integration.py` (root): sys.path setup, `@tensorleap_load_model`, `@tensorleap_integration_test`, `__main__` harness.
- `leap.yaml` (root): manifest; includes `tensorleap/**`, `domain_gap/**/*.py`, the repo config yaml and the split recipes.
- `requirements.txt` (root): runtime deps only (numpy<2, PyYAML, Pillow, google-cloud-storage — the last one only because
  `domain_gap/data/cs_data.py` imports `domain_gap/utils/gcs_utils.py` at module import; no bucket access happens at runtime).
  The original customer file was replaced (original content saved in the session scratchpad).
- `tensorleap/project_config.yaml`: `sample_limit_per_split: 250`, split recipe paths, `ignore_label`, viz settings.
  Data root / IMAGE_SIZE / normalization constants are read from the repo's `domain_gap/utils/project_config.yaml`
  (`LOCAL_BASE_PATH: /Users/orram/Tensorleap/data/domain-gap-2`, `USE_LOCAL: True`).
- `tensorleap/preprocess.py`: training = `splits/original_csv_subset/train.json`, validation = `val.json`,
  unlabeled = `splits/unlabeled_subset/manifest.json`. Sample id = bucket-relative image path. Exact duplicate rows in the
  recipes are collapsed (lossless). Cap applied per split (250 / 85 / 250). Vehicle sidecar JSONs are loaded once here.
- `tensorleap/encoders.py`: `normalized_image` = resize to IMAGE_SIZE (2048x1024 W,H) bilinear, /255, (x-IMAGE_MEAN)/IMAGE_STD.
  `segmentation_mask` GT = labelIds -> Cityscapes train ids (0..18, 19 = void/ignore), nearest resize, shape (1024, 2048, 1).
  Unlabeled samples return an empty float32 array.
- `tensorleap/metrics.py`: loss `pixel_cross_entropy` (softmax CE from logits over non-ignore pixels);
  metrics `pixel_accuracy`, `mean_iou` (over classes present in GT or prediction), `mean_max_confidence`
  (prediction-only, works on unlabeled samples). Undefined values (no valid GT) -> NaN.
- `tensorleap/visualizers.py`: `image`, `gt_mask`, `prediction_mask` (LeapImageMask, 20 labels incl. void),
  `predicted_class_distribution` (HorizontalBar). Downscaled x2 (`viz_downscale`).
- `tensorleap/metadata.py`: `source_*` (dataset, city, subset folder, has_gt, selection reason, kitti drive, manifest
  vegetation %), `vehicle_*` (Cityscapes vehicle JSON: heading, lat, lon, temperature, speed, yaw rate; None for KITTI),
  `image_*` (original size / aspect ratio, brightness, contrast, channel means, saturation), `gt_*` (per-class pixel
  fraction, labeled fraction, #classes, dominant class, small-object fraction; None when no GT).

## Issues log
1. **Model file missing from the checkout.** `models/DeeplabV3.h5` is referenced by the task but `*.h5` is gitignored and
   the file was not in the repo or on the data volume. Resolved by copying the identically named file from
   `/Users/orram/Tensorleap/DeepLabV3Plus-ADAS/models/DeeplabV3.h5` (only the binary; no other code was read).
   sha1 174af78550ea7ec6b56dd965701f4583ddb9c712.
2. **Output is logits, not softmax.** The task text says "per-pixel 19-class softmax", but the h5 output has values in
   about [-10, 11] and per-pixel sums far from 1. Treated as logits; softmax lives in metrics/visualizers.
3. **`gt_image_path` (`*_gtFine_color.png`) not on the volume.** Not needed: colors are derived from label ids via the
   class table in `cs_data.py`.
4. **Duplicate rows in the split recipes** (train 400 rows / 321 unique images; val 100 / 85). Collapsed by image path.
5. **Train/val recipes contain labeled KITTI (`data_semantics`) frames** alongside Cityscapes, and the unlabeled manifest
   contains Cityscapes frames (tubingen/munster/bremen) alongside raw KITTI. Recipes were used as-is (per the task).
6. `google-cloud-storage` added to `requirements.txt` purely to satisfy `cs_data.py`'s transitive import.

## Validation status
- `run_integration.sh`: `Successful!` on 3 training + 3 validation samples (Cityscapes aachen/zurich + KITTI), all rows ✅,
  no default-use warnings.
- `tl_check.py` (`check_dataset`): `isValid: True`, all payloads passed.
- Sanity values (single samples): Cityscapes pixel acc ~0.95 / CE ~0.1; KITTI pixel acc 0.56–0.72 / CE 1.3–1.9.
