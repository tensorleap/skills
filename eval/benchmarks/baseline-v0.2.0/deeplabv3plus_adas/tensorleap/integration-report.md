# Integration report — DeepLabV3 (Cityscapes -> KITTI domain gap)

Issues hit while authoring, with the signal, cause and resolution.

1. **Model output is logits, not softmax.** Task description said "per-pixel 19-class
   softmax"; inspecting `models/DeeplabV3.h5` shows the last layer is a linear Conv2D (19
   filters) followed by resize/permutes; random-input outputs sum to ~0.44 with range
   [-10.8, 11.9]. Resolution: treat the output as logits; `cross_entropy` applies log-softmax,
   metrics/visualizers use argmax. Prediction type named `segmentation_logits`.
2. **`poetry add code-loader Pillow` fails to resolve** (`pillow (12.3.0) requires Python
   >=3.10` vs project range `>=3.8,<3.11`). Resolution: installed into the same Poetry venv
   with `poetry run pip install code-loader Pillow` (code-loader 1.0.207); pyproject/lock left
   untouched.
3. **Split recipes repeat images** (`train.json`: 400 rows, 321 unique images; `val.json`:
   100 rows, 85 unique). Sample ids must be unique, so ids are `<subset>_<row index>`; every
   recipe row is kept (no curation).
4. **`train.json` orders all Cityscapes rows before KITTI rows.** A head-slice for
   `sample_limit_per_split: 250` would drop every labeled KITTI training frame, so the cap is
   applied after a seeded (42) permutation of each recipe.
5. **Normalization choice.** Config carries ImageNet, Cityscapes and KITTI mean/std. Checked
   pixel accuracy of the model per normalization on one Cityscapes and one KITTI frame:
   ImageNet 0.95 / 0.72, Cityscapes-stats 0.89 / 0.54, KITTI-stats 0.89 / 0.65, none 0.72 /
   0.39. ImageNet (`image_mean` / `image_std`) is used.
6. **`domain_gap.data.cs_data` imports google-cloud-storage at module top.** Importing it on
   the platform would need that dependency and, indirectly, an `AUTH_SECRET`. Resolution: the
   Cityscapes label table is mirrored in `tensorleap/cityscapes_labels.py`.
7. **Unlabeled subset GT.** `@tensorleap_unlabeled_preprocess` frames have no mask; the GT
   encoder returns `np.array([], dtype=np.float32)` for them and `gt_mask` renders an all-ignore
   mask. The integration test body (loss/metrics) is only exercised on labeled subsets; unlabeled
   frames were checked through direct decorated calls (input encoder, metadata, visualizers).

Open items: none at local-validation time. Local results: 8 `integration_test` runs pass
(Cityscapes + KITTI x train + val), `check_dataset()` `isValid: True` (training 250,
validation 100, unlabeled 250).
