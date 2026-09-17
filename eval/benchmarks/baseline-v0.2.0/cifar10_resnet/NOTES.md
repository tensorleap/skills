# NOTES — cifar10_resnet pre-integration fixture

Session log (2026-09-15). Not committed.

## Setup
- Preflight OK: local server http://localhost:4589, data volume /Users/orram/data, authenticated as or.ram@tensorleap.ai. leap CLI v0.0.161 (shim at skills/eval/.shim/leap).
- Poetry env (.venv, Python 3.10.14). Added: code-loader>=1.0.142 (resolved 1.0.207), pyyaml, tensorflow-macos~=2.11.0 (darwin) / tensorflow~=2.11.0 (linux). `poetry add tensorflow~=2.11.0` without a platform marker fails on macOS arm64 (no wheel) — hence the markers.
- Data delivery row 2: CIFAR-10 python batches already on the volume at /Users/orram/data/cifar-10-batches-py — reused, not copied.
- Model: model/resnet.h5 — input `image` (224,224,3) float32, output (10,) softmax probabilities (Dense softmax -> Permute). Existing repo encoder upsamples 32x32 by 7x (scipy zoom) and divides by 255.
- User asked for a CAPPED evaluation: `sample_limit_per_split: 250` in tensorleap/project_config.yaml (balanced per split; remove/None for the full dataset).

## Authoring log
- Step 1-2: tensorleap/{config.py,project_config.yaml,preprocess.py} + leap_integration.py skeleton. preprocess loads the 5 python batches from data_root, splits 80/20 with train_test_split(random_state=42) as the original repo did, then caps each split to sample_limit_per_split (250/250). Run: preprocess row green.
- Step 4-6: tensorleap/encoders.py (input `image` 224x224x3 float32 via 7x scipy zoom /255, channel_dim=-1; GT `classes` one-hot float32), load_model (Keras h5, prediction_types classes/10 labels), minimal integration_test -> first `Successful!`.
- Step 9: tensorleap/metrics.py (custom loss categorical_crossentropy on softmax probs, metrics accuracy + gt_confidence, both Upward), tensorleap/metadata.py (sample: label/label_index/filename/source_index; image_stats: brightness/contrast/RGB means), tensorleap/visualizers.py (LeapImage of input, LeapHorizontalBar for GT and prediction). integration_test run over 3 training + 3 validation samples: 6x `Successful!`, all 9 exit-table rows green, no default-use warnings.
- tl_check.py (check_dataset): isValid True, all handlers passed, trainingLength 250 / validationLength 250.
- leap.yaml: entryFile leap_integration.py, py310, include leap.yaml/leap_integration.py/requirements.txt/tensorleap/**; excludes model/, images/, cifar10_resnet/, .venv/. requirements.txt built from runtime imports: numpy, scipy, scikit-learn, pyyaml, tensorflow~=2.11.0.
- No issues hit during authoring.

## Deploy
- `leap projects create cifar10-resnet` -> projectId 6aa926eb2f329df70cd36e30 (written to leap.yaml).
- Reconciled `leap run list -t Push`: no in-flight Push. A local `leap push` process (pid 69800) exists but belongs to another project (DeepLabV3Plus-ADAS, user's interactive shell) — left untouched.
- Push started 2026-09-15 14:08 local: `leap push -m model/resnet.h5 -n v1-cap250 -b 32 --eval --yes` (local pid 10041, log push.log). Push run id: 6aa927292f329df70cd36e3b.
- Push 6aa927292f329df70cd36e3b FINISHED (all stages green: Build Dependencies, Parsing Dataset, Data Loader Preparation, Parsing Model, Convert, Build Model, Run Model Inference, Testing Loss/Visualizers/Metrics). Model version name: v1-cap250, batch 32.
- Evaluate job started 2026-09-15 14:11 local: run id 6aa927d02f329df70cd36e47 (STARTED). Watching with a background poll every 5 min until terminal.
- Note for a future re-push: the bundle included tensorleap/__pycache__/*.pyc — harmless, but `tensorleap/__pycache__/**` could be added to leap.yaml exclude.
