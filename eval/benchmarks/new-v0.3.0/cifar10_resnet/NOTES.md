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
