# Integration report — yolov5s / VisDrone2019-DET

Running log of issues hit while authoring the Tensorleap integration and how each was resolved.

## Setup
- Preflight: local server, volume `/Users/orram/Tensorleap/data` covers the staged dataset root. No copy needed.
- `data/VisDrone.yaml` `path` pointed at the staged 48-image subset (checked-in value was developer-local).
- `poetry add code-loader@latest` installed 1.0.207 and downgraded numpy to 1.26.4 / opencv-python to 4.11 to satisfy code-loader's constraints.
- The repo's `utils/loss.py` pulls in pandas/torchvision/matplotlib via `utils.general`; the YOLOv5 loss was ported into `tensorleap/yolo_loss.py` (torch only) instead of bundling `utils/**`.

## Loss
- Ported `ComputeLoss` (`tensorleap/yolo_loss.py`) verified against `utils.loss.ComputeLoss` on real heads: identical (0.681443 both) when called directly.
- Calling through `@tensorleap_custom_loss` gives ~1% higher values (0.6885). Cause: the decorator's `_simulate_engine_tensor_dtype` downcasts inputs to float16 to mimic the engine. Expected, not a bug.
- Discrimination check: loss on matching GT 0.35–0.69 vs 2.0–2.7 with GT from a different image.

## Deploy
- `leap projects create yolov5-visdrone` -> id 6aa933262f329df70cd36e60. Appending `projectId` to leap.yaml produced a duplicate-key error, and the next `leap projects info` call rewrote leap.yaml to a blank template (`projectId: "" / secretId: "" / entryFile: ""`). Restored the manifest by hand with `projectId` at the top. Lesson: a malformed leap.yaml gets overwritten by the CLI.
- A foreign `leap push` process (pid 69800, started 11:36, cwd `integration`) from another working tree was running; left untouched.
- Push 6aa9336c2f329df70cd36e6b FINISHED; platform build steps all passed. `torch>=2.1` resolved to torch 2.14.0 on the aarch64 image and pulled CUDA/triton wheels (~8 min install). Consider pinning a CPU-only wheel if build time matters.
- Evaluate 6aa936582f329df70cd36e79 started (batch 8).
