# Integration report — cifar10_resnet

## Summary
- Model: `model/resnet.h5` (Keras, TF 2.11). Input `image` (224,224,3) float32; single output (10,) **softmax probabilities**.
- Data: CIFAR-10 python batches on the local data volume (`/Users/orram/data/cifar-10-batches-py`), reused as-is.
  `preprocess` downloads via keras `get_file` into the config-driven data root only when the folder is absent.
- Split: 80/20 of the 50k training images with `train_test_split(random_state=42)` (mirrors the repo's `preprocess_func`).
- Cap: `sample_limit_per_split: 250` (balanced per split) in `tensorleap/project_config.yaml`, set at the user's request.
  Set it to `null` / `0` to evaluate the full dataset.
- Input encoder reuses the repo's `zoom(image, (7, 7, 1)) / 255` upscale (32 -> 224). Sanity check: 80% accuracy on 40 validation samples.
- Loss: categorical cross-entropy on the softmax output (clipped, per-sample 1D).
- Metrics: accuracy, gt_class_probability (Upward), confidence (max prob, Upward, insights off).
- Metadata: label, superclass (animal/vehicle), source_batch (CIFAR data_batch 1-5), image stats (brightness, contrast,
  colorfulness, per-channel means), sharpness (Laplacian variance).
- Visualizers: image (uint8), prediction_bar (probabilities with GT overlay), gt_bar.

## Issues encountered
- None blocking. Local run loop was clean at every stage (`Successful!` on 3 train + 3 validation samples,
  `check_dataset()` `isValid: True`, no default-use warnings).
- `requirements.txt` at the repo root was replaced with the integration's runtime list (tensorflow~=2.11.0, numpy~=1.24.0,
  scipy~=1.10.0, scikit-learn~=1.3.0, pyyaml~=6.0); the original pinned `tensorflow-macos` which has no Linux/aarch64 wheel.
- Platform-only risk to watch on the Evaluate job: tensorflow 2.11 wheel availability for py310 on the aarch64 base image.
