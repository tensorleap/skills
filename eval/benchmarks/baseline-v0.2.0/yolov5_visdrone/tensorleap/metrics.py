import numpy as np
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_loss

from tl_config import CONFIG, NUM_CLASSES

_LOSS = None


def _loss_fn():
    global _LOSS
    if _LOSS is None:
        import torch  # noqa: F401  (imported lazily; heavy)

        from yolo_loss import YoloV5Loss

        cfg = CONFIG["loss"]
        _LOSS = YoloV5Loss(hyp=cfg, anchors_px=cfg["anchors_px"], strides=cfg["strides"], nc=NUM_CLASSES)
    return _LOSS


def gt_to_targets(gt_sample: np.ndarray) -> np.ndarray:
    """[MAX_BOXES,5] (cls,x,y,w,h; cls<0 = padding) -> [n,6] (img_idx, cls, x, y, w, h)."""
    valid = gt_sample[gt_sample[:, 0] >= 0]
    return np.concatenate([np.zeros((len(valid), 1), dtype=np.float32), valid], axis=1)


@tensorleap_custom_loss(name="yolov5_loss")
def yolov5_loss(boxes: np.ndarray, head_p3: np.ndarray, head_p4: np.ndarray, head_p5: np.ndarray) -> np.ndarray:
    import torch

    loss_fn = _loss_fn()
    out = np.zeros(boxes.shape[0], dtype=np.float32)
    for b in range(boxes.shape[0]):
        preds = [torch.from_numpy(np.ascontiguousarray(h[b : b + 1], dtype=np.float32)) for h in (head_p3, head_p4, head_p5)]
        targets = torch.from_numpy(gt_to_targets(boxes[b]))
        with torch.no_grad():
            out[b] = float(loss_fn(preds, targets).item())
    return out


from typing import Dict  # noqa: E402

from code_loader.contract.enums import MetricDirection  # noqa: E402
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_metric  # noqa: E402

from detections import box_iou_xyxy, decode_detections, gt_to_xyxy  # noqa: E402

MATCH_IOU = 0.5
DETECTION_METRIC_DIRECTIONS = {
    "precision": MetricDirection.Upward,
    "recall": MetricDirection.Upward,
    "f1": MetricDirection.Upward,
    "mean_matched_iou": MetricDirection.Upward,
    "num_false_positives": MetricDirection.Downward,
    "num_missed_gt": MetricDirection.Downward,
}


def _match_sample(det: np.ndarray, gt: np.ndarray) -> Dict[str, float]:
    p_xyxy, _, p_cls = decode_detections(det)
    g_xyxy, g_cls = gt_to_xyxy(gt)
    n_p, n_g = len(p_xyxy), len(g_xyxy)
    tp, ious = 0, []
    if n_p and n_g:
        iou = box_iou_xyxy(p_xyxy, g_xyxy)
        iou[p_cls[:, None] != g_cls[None, :]] = 0.0
        used = np.zeros(n_g, dtype=bool)
        for i in iou.max(1).argsort()[::-1]:  # greedy, best predictions first
            j = int(iou[i].argmax())
            if iou[i, j] >= MATCH_IOU and not used[j]:
                used[j] = True
                tp += 1
                ious.append(float(iou[i, j]))
    precision = tp / n_p if n_p else 0.0
    recall = tp / n_g if n_g else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mean_matched_iou": float(np.mean(ious)) if ious else 0.0,
        "num_false_positives": float(n_p - tp),
        "num_missed_gt": float(n_g - tp),
    }


@tensorleap_custom_metric(name="detection", direction=DETECTION_METRIC_DIRECTIONS)
def detection_metrics(detections: np.ndarray, boxes: np.ndarray) -> Dict[str, np.ndarray]:
    per_sample = [_match_sample(detections[b], boxes[b]) for b in range(boxes.shape[0])]
    return {k: np.asarray([s[k] for s in per_sample], dtype=np.float32) for k in DETECTION_METRIC_DIRECTIONS}
