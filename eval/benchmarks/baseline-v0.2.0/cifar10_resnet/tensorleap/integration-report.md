# Integration report — cifar10_resnet

Date: 2026-09-15. Server: local (http://localhost:4589), data volume /Users/orram/data.

## Summary
- Data: CIFAR-10 python batches already present on the data volume (`/Users/orram/data/cifar-10-batches-py`), reused in place. `preprocess` loads data_batch_1..5, splits 80/20 with `random_state=42` (same as the original repo), and caps each split to `sample_limit_per_split` from `project_config.yaml`.
- Model: `model/resnet.h5`, input `image` (224, 224, 3) float32, output 10 softmax probabilities. Uploaded separately via `leap push -m`, not bundled.
- Components: input encoder `image` (7x upscale + /255), GT encoder `classes` (one-hot), custom loss `categorical_crossentropy`, metrics `accuracy` and `gt_confidence`, metadata `sample_*` and `image_stats_*`, visualizers for the input image and the GT / prediction class bars.
- Local validation: integration_test over 3 training + 3 validation samples, all exit-table rows exercised, no default-use warnings; `check_dataset()` isValid True.

## Sample cap
`sample_limit_per_split: 250` is set in `tensorleap/project_config.yaml` at the user's request (250 training + 250 validation). Remove the key or set it to `null` to evaluate the full 40k/10k split.

## Issues encountered
- `poetry add tensorflow~=2.11.0` has no macOS arm64 wheel; resolved with platform markers (`tensorflow-macos` on darwin, `tensorflow` on linux). `requirements.txt` ships plain `tensorflow~=2.11.0` for the platform's Linux build.
- No integration errors during authoring.

## Open items
- Evaluate job outcome tracked in NOTES.md (repo root).
