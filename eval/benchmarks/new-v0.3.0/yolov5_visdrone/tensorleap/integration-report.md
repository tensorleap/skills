# Integration report — yolov5 / VisDrone

Issues hit while authoring, with signal, cause, resolution.

- **torch import broken in the shipped venv** (`A module that was compiled using NumPy 1.x
  cannot be run in NumPy 2.0.2`). Cause: poetry.lock resolved numpy 2.0.2 with torch 2.1.0.
  Resolution: `poetry add code-loader` re-resolved numpy to 1.26.4; torch imports cleanly.
- **`leap projects create <name>` wiped leap.yaml** (replaced with `projectId: ""`, `entryFile: ""`,
  `include: []`). Resolution: rewrote the manifest by hand with the new projectId. Keep a copy before
  running `leap projects create` in an existing workspace.
- **Decorated metric differs from the raw function by one detection** (85 vs 86 predictions on one
  sample). Cause: `tensorleap_custom_metric` runs `_simulate_engine_tensor_dtype`, downcasting inputs
  to mimic the engine, which flips a borderline-confidence box through NMS. Expected behavior, no fix.
- **poetry.lock shipped numpy 2.0.2 with torch 2.1.0**: torch could not initialize NumPy. Resolved as a
  side effect of `poetry add code-loader` (numpy -> 1.26.4). requirements.txt pins `numpy~=1.26.0`.
- Open: platform build must resolve torch/torchvision/onnxruntime wheels for Linux aarch64 with py310;
  verified only that `~=` pins are used. Watch the Push run log if the build fails.
