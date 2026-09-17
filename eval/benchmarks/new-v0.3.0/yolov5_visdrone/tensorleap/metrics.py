import os

import numpy as np

from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_loss

from preprocess import CONFIG, DATA_ROOT

LOSS_WEIGHTS_PATH = os.path.join(DATA_ROOT, CONFIG["loss_weights_file"])

_COMPUTE_LOSS = None


def _get_compute_loss():
    # Legacy YOLOv5 loss: ComputeLoss reads hyp / anchors / strides from the trained checkpoint.
    global _COMPUTE_LOSS
    if _COMPUTE_LOSS is None:
        import torch
        from utils.loss import ComputeLoss

        ckpt = torch.load(LOSS_WEIGHTS_PATH, map_location="cpu")
        model = ckpt["model"].float().eval()
        _COMPUTE_LOSS = ComputeLoss(model)
    return _COMPUTE_LOSS


def gt_to_targets(boxes: np.ndarray, image_index: int = 0):
    # boxes: (max_boxes, 5) = cx, cy, w, h, cls (normalized; padding rows have cls = -1)
    import torch

    valid = boxes[:, 4] >= 0
    real = boxes[valid]
    targets = torch.zeros((len(real), 6), dtype=torch.float32)
    targets[:, 0] = image_index
    targets[:, 1] = torch.from_numpy(real[:, 4])
    targets[:, 2:6] = torch.from_numpy(real[:, :4])
    return targets


@tensorleap_custom_loss(name="yolov5_loss")
def yolov5_loss(raw_p3: np.ndarray, raw_p4: np.ndarray, raw_p5: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    import torch

    compute_loss = _get_compute_loss()
    batch_size = raw_p3.shape[0]
    losses = np.zeros((batch_size,), dtype=np.float32)
    for b in range(batch_size):
        preds = [torch.from_numpy(np.ascontiguousarray(level[b : b + 1])).float() for level in (raw_p3, raw_p4, raw_p5)]
        total, _ = compute_loss(preds, gt_to_targets(boxes[b]))
        losses[b] = float(total.item())
    return losses


from code_loader.contract.enums import MetricDirection
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_metric

from visualizers import decode_detections

IMAGE_SIZE = int(CONFIG["image_size"])
MATCH_IOU = 0.5


def _gt_xyxy(boxes: np.ndarray):
    real = boxes[boxes[:, 4] >= 0]
    xyxy = np.empty((len(real), 4), dtype=np.float32)
    xyxy[:, 0] = (real[:, 0] - real[:, 2] / 2) * IMAGE_SIZE
    xyxy[:, 1] = (real[:, 1] - real[:, 3] / 2) * IMAGE_SIZE
    xyxy[:, 2] = (real[:, 0] + real[:, 2] / 2) * IMAGE_SIZE
    xyxy[:, 3] = (real[:, 1] + real[:, 3] / 2) * IMAGE_SIZE
    return xyxy, real[:, 4].astype(int)


def _iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    lt = np.maximum(a[:, None, :2], b[None, :, :2])
    rb = np.minimum(a[:, None, 2:], b[None, :, 2:])
    inter = np.clip(rb - lt, 0, None).prod(-1)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / (area_a[:, None] + area_b[None, :] - inter + 1e-9)


def match_sample(detections: np.ndarray, boxes: np.ndarray) -> dict:
    kept = decode_detections(detections)  # sorted by confidence, (n, 6)
    gt_xyxy, gt_cls = _gt_xyxy(boxes)
    n_pred, n_gt = len(kept), len(gt_xyxy)
    matched_ious = []
    if n_pred and n_gt:
        ious = _iou_matrix(kept[:, :4], gt_xyxy)
        ious[kept[:, 5].astype(int)[:, None] != gt_cls[None, :]] = 0.0
        taken = np.zeros(n_gt, dtype=bool)
        for p in range(n_pred):
            row = np.where(taken, 0.0, ious[p])
            g = int(row.argmax())
            if row[g] >= MATCH_IOU:
                taken[g] = True
                matched_ious.append(float(row[g]))
    tp = len(matched_ious)
    return {
        "precision": tp / n_pred if n_pred else np.nan,
        "recall": tp / n_gt if n_gt else np.nan,
        "f1": (2 * tp / (n_pred + n_gt)) if (n_pred + n_gt) else np.nan,
        "mean_matched_iou": float(np.mean(matched_ious)) if matched_ious else np.nan,
        "num_predictions": float(n_pred),
        "num_false_positives": float(n_pred - tp),
        "num_missed_gt": float(n_gt - tp),
    }


METRIC_DIRECTIONS = {
    "precision": MetricDirection.Upward,
    "recall": MetricDirection.Upward,
    "f1": MetricDirection.Upward,
    "mean_matched_iou": MetricDirection.Upward,
    "num_predictions": MetricDirection.Upward,
    "num_false_positives": MetricDirection.Downward,
    "num_missed_gt": MetricDirection.Downward,
}
METRIC_INSIGHTS = {k: k not in ("num_predictions",) for k in METRIC_DIRECTIONS}


@tensorleap_custom_metric(name="detection", direction=METRIC_DIRECTIONS, compute_insights=METRIC_INSIGHTS)
def detection_metrics(detections: np.ndarray, boxes: np.ndarray) -> dict:
    per_sample = [match_sample(detections[b], boxes[b]) for b in range(detections.shape[0])]
    return {key: np.array([s[key] for s in per_sample], dtype=np.float32) for key in METRIC_DIRECTIONS}
