"""Numpy decoding + NMS for the ONNX model's decoded output [N, 5 + nc] (xywh px, obj, cls...)."""
from typing import Tuple

import numpy as np

from tl_config import CONFIG, IMAGE_SIZE


def xywh_to_xyxy(b: np.ndarray) -> np.ndarray:
    out = np.empty_like(b)
    out[:, 0] = b[:, 0] - b[:, 2] / 2
    out[:, 1] = b[:, 1] - b[:, 3] / 2
    out[:, 2] = b[:, 0] + b[:, 2] / 2
    out[:, 3] = b[:, 1] + b[:, 3] / 2
    return out


def box_iou_xyxy(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    lt = np.maximum(a[:, None, :2], b[None, :, :2])
    rb = np.minimum(a[:, None, 2:], b[None, :, 2:])
    wh = np.clip(rb - lt, 0, None)
    inter = wh[..., 0] * wh[..., 1]
    return inter / (area_a[:, None] + area_b[None, :] - inter + 1e-7)


def nms_xyxy(boxes: np.ndarray, scores: np.ndarray, iou_thres: float) -> np.ndarray:
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        iou = box_iou_xyxy(boxes[i : i + 1], boxes[order[1:]])[0]
        order = order[1:][iou <= iou_thres]
    return np.asarray(keep, dtype=int)


def decode_detections(pred: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """pred [N, 5+nc] -> (boxes xyxy normalized [M,4], scores [M], classes [M]) after class-aware NMS."""
    conf_thres = float(CONFIG["conf_threshold"])
    iou_thres = float(CONFIG["iou_threshold"])
    max_det = int(CONFIG["max_det"])
    pred = pred.astype(np.float32)
    obj = pred[:, 4]
    cls_scores = pred[:, 5:] * obj[:, None]
    classes = cls_scores.argmax(1)
    scores = cls_scores[np.arange(len(pred)), classes]
    keep = scores > conf_thres
    boxes = xywh_to_xyxy(pred[keep, :4]) / IMAGE_SIZE
    scores, classes = scores[keep], classes[keep]
    if len(boxes) == 0:
        return boxes.reshape(0, 4), scores, classes
    offset = classes[:, None].astype(np.float32) * 2.0  # separate classes for class-aware NMS
    idx = nms_xyxy(boxes + offset, scores, iou_thres)[:max_det]
    return boxes[idx], scores[idx], classes[idx]


def gt_to_xyxy(gt: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """[MAX_BOXES,5] padded GT -> (boxes xyxy normalized, classes)."""
    valid = gt[gt[:, 0] >= 0]
    return xywh_to_xyxy(valid[:, 1:5]), valid[:, 0].astype(int)
