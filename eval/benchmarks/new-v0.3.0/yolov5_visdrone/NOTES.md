# NOTES — Tensorleap integration for yolov5_visdrone (pre fixture)

Skill: tensorleap-integration-creation (invoked via Skill tool). Date: 2026-09-16.

## Setup
- Preflight: OK (local server http://localhost:4589, volumes `/Users/orram/data` and
  `/Users/orram/Tensorleap/data`, authenticated as or.ram@tensorleap.ai).
- Env: repo poetry env (`.venv`, Python 3.10.14). `poetry add code-loader` -> 1.0.207
  (numpy downgraded 2.0.2 -> 1.26.4, which also fixed the torch 2.1.0 import).
- Data delivery: row 2 (local server, data already on a volume). Staged subset at
  `/Users/orram/Tensorleap/data/eval/yolov5_visdrone/VisDrone` is under the
  `/Users/orram/Tensorleap/data` volume, so it is read in place, no copy.
  `data/VisDrone.yaml` `path` set to that dir (operator instruction).
- Model: `weights/yolov5s-visdrone.onnx` — input `images` [B,3,H,W] float32; outputs:
  decoded [B,25200,15] + 3 raw per-level heads [B,3,H/s,W/s,15] (s=8,16,32).
  Verified raw->decoded consistency numerically. `.pt` in data root has `hyp`, anchors.
- Existing project `yolov5-visdrone` found on server; will use a new `yolov5-visdrone-pre`.

## Log
- Step 1-2: skeleton + `tensorleap/preprocess.py` (train/val/test from data/VisDrone.yaml split dirs,
  labels loaded once, `sample_limit_per_split: 250` balanced cap in project_config.yaml). 16/16/16 samples.
- Step 4/8: `tensorleap/encoders.py` — input `image` (3,640,640) float32 CHW, channel_dim=1, letterboxed
  via the repo's `utils.augmentations.letterbox`; GT `boxes` (500,5) = cx,cy,w,h,cls in the letterboxed
  frame, padding rows cls=-1.
- Step 5-6: `@tensorleap_load_model` (onnxruntime InferenceSession, 4 prediction types) + thin
  integration test -> `Successful!`.
- Custom loss `yolov5_loss` (tensorleap/metrics.py): repo `utils.loss.ComputeLoss` built from the staged
  `yolov5s-visdrone.pt` (hyp/anchors), fed raw per-level heads; per-sample. Probe: correct GT
  0.24-0.60, shuffled GT 0.78-1.71, empty GT 0.10-0.35 -> discriminates.
- Visualizers: `image`, `gt_boxes`, `pred_boxes` (NMS via repo `non_max_suppression`, conf 0.25 / iou 0.45).
  Rendered overlays checked visually: aligned.
- Metric `detection` (dict): precision/recall/f1 @IoU0.5 same-class greedy match, mean_matched_iou,
  num_predictions (no insights), num_false_positives, num_missed_gt. NaN when undefined.
- Metadata: image (w/h/letterbox_scale/mean_brightness), sequence_id, gt counts (num_boxes,
  num_classes_present, boxes_per_megapixel, dominant_class), gt_size (areas, small_object_fraction,
  mean side in model px), gt_border_fraction, gt_class_count (10 classes).
- Harness: 3 samples per split (train/val/test) -> 9x `Successful!`, no default-use warnings.
- `tl_check.py`: isValid True, 28 payloads, none failed.
- requirements.txt: numpy<2, pyyaml, pillow, opencv-python-headless, torch~=2.1, torchvision~=0.16,
  onnxruntime~=1.19 + pandas/matplotlib/seaborn/scipy/requests/tqdm/psutil/setuptools<81 (pulled by
  utils/ + models/ imports of the ComputeLoss loader). leap.yaml includes utils/** and models/**.
- Project: created `yolov5-visdrone-pre` (id 6aaaa3192f329df70cd36f71). NOTE: `leap projects create`
  overwrote leap.yaml with a blank template; rewrote it.
- No in-flight Push runs before pushing.
- Push: `leap push -m weights/yolov5s-visdrone.onnx -n v1-capped250 -b 8 --eval` (background, push.log).
- Push run id: 6aaaa3582f329df70cd36f7c (QUEUED at 17:10; another project's Evaluate 6aaaa2802f329df70cd36f69 was running on the shared local cluster).
