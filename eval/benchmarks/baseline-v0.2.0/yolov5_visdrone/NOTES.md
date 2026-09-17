# NOTES — Tensorleap integration for yolov5 / VisDrone (pre fixture)

Not committed. Log of what was done, in order.

- Invoked `integration:tensorleap-integration-creation` skill first.
- Preflight gate: PASS (local server http://localhost:4589, volume /Users/orram/data, authenticated).
  `leap server info` also lists `/Users/orram/Tensorleap/data` as a dataset volume, so the staged
  subset at `/Users/orram/Tensorleap/data/eval/yolov5_visdrone/VisDrone` is already on a volume
  (data-delivery row 2: reuse, no copy).
- Env: repo's poetry venv (`.venv`, Python 3.10.14). `poetry add code-loader@latest` -> 1.0.207
  (numpy downgraded 2.0.2 -> 1.26.4, opencv 5.0 -> 4.11 by the resolver).
- Set `path` in `data/VisDrone.yaml` to the staged root (was a developer-local path).
- Model contract (weights/yolov5s-visdrone.onnx): input `images` float32 [B,3,H,W]; outputs
  `output1_permuted` [B,25200,15] decoded (xywh px, obj, 10 cls, sigmoided), `output2..4_permuted`
  raw heads [B,3,80/40/20,80/40/20,15] (pre-sigmoid).
- Loss constants (hyp, anchors, strides) read from yolov5s-visdrone.pt and stored in
  tensorleap/project_config.yaml, so the platform loss does not need torch.load of the .pt.
- Authoring loop (run after every edit, all green): preprocess (3 splits x 16, cap 250/split via
  `sample_limit_per_split`) -> input encoder `image` (3,640,640 CHW float32 letterbox) -> load_model
  (ONNX, 4 prediction types) -> integration_test -> GT `boxes` (500,5 padded, cls -1) -> custom loss
  `yolov5_loss` (torch port of ComputeLoss on raw heads; matches repo ComputeLoss exactly) -> metadata
  (image_*, gt_*) -> visualizers (image, gt_boxes, predicted_boxes) -> metric `detection`
  (precision/recall/f1/mean_matched_iou/FP/missed @IoU0.5).
- `tl_check.py`: isValid true, all payloads passed, 16/16/16 samples.
- Project: `leap projects create yolov5-visdrone` -> projectId 6aa933262f329df70cd36e60.
- Push: `leap push -m weights/yolov5s-visdrone.onnx -n v1-cap250 -b 8 --eval` (background, push.log).
- Push run id: 6aa9336c2f329df70cd36e6b -> FINISHED (all platform steps ✔: Parsing Dataset, Parsing Model,
  Build Model, Run Model Inference, Testing Loss, Testing Visualizers, Testing Metrics). Build installed
  torch 2.14.0 (aarch64 wheel pulls CUDA/triton extras — heavy but OK), onnxruntime 1.23.2, numpy 1.26.4.
- Evaluate job id: 6aa936582f329df70cd36e79 -> STARTED 15:13 (batch 8, cap 250/split => 16/16/16 samples).
  Background watcher polls `leap run list -t Evaluate` every 60s until terminal.
