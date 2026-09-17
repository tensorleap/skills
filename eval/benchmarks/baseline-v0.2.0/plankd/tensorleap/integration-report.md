# Integration report — PlanKD / InterFuser

Issues hit while authoring the Tensorleap integration, with the signal, cause and resolution.

## 1. `import cv2` killed with exit 137 (resolved)
- Signal: `poetry run python -c "import cv2"` exited with code 137 (SIGKILL), no traceback, also outside the sandbox.
- Where: `interfuser/timm/data/heatmap_utils.py` and `det_utils.py` import cv2, so the repo dataset classes could not be imported.
- Cause: `codesign --verify` on `.venv/lib/python3.10/site-packages/cv2/cv2.abi3.so` reported
  "invalid signature (code or signature have been modified)"; macOS kills such binaries on load.
- Fix: `poetry run pip install --force-reinstall --no-deps opencv-python==4.11.0.86` (same version). Import works afterwards.

## 2. `CarlaMVDetDataset` never sets `rgb_center_transform` (worked around)
- `__getitem__` reads `self.rgb_center_transform`, but `__init__` does not define it; in the repo it is attached by
  `create_carla_loader`. The integration attaches the same eval-mode transforms in `tensorleap/preprocess.py`.

## 3. Model contract
- ONNX has 7 inputs (rgb / rgb_left / rgb_right / lidar [3,224,224], rgb_center [3,128,128], measurements [7],
  target_point [2]) and 6 outputs (traffic [400,7], waypoints [10,2], junction / traffic_light_state / stop_sign [2],
  traffic_feature [400,128]). Classification heads are raw logits (nn.Linear); softmax is applied in metrics/visualizers.
- `traffic_feature` has no semantic labels; declared with generic labels f0..f127 so the prediction-type count matches.

## 4. Open notes
- The loss is the per-sample weighted waypoint L1 of `interfuser/train.py::WaypointL1Loss`; traffic and classification
  heads are exposed as metrics rather than folded into the loss.
- Bench2Drive GT waypoints are derived from future frames of the same scenario (repo logic); frames at the end of a
  scenario have fewer valid waypoints (masked with the repo's 10000 sentinel).
