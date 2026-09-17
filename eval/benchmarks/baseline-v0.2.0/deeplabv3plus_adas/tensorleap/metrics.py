from typing import Dict

import numpy as np
from code_loader.contract.enums import MetricDirection
from code_loader.inner_leap_binder.leapbinder_decorators import (
    tensorleap_custom_loss,
    tensorleap_custom_metric,
)

from cityscapes_labels import CATEGORIES, IGNORE_TRAIN_ID, NUM_CLASSES


def _log_softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=-1, keepdims=True)
    return shifted - np.log(np.exp(shifted).sum(axis=-1, keepdims=True))


def _labels(gt: np.ndarray) -> np.ndarray:
    return np.rint(gt).astype(np.int64)


@tensorleap_custom_loss(name="cross_entropy")
def cross_entropy(prediction: np.ndarray, gt: np.ndarray) -> np.ndarray:
    # prediction: (B, H, W, 19) logits; gt: (B, H, W) train ids, 19 = ignore.
    labels = _labels(gt)
    valid = labels != IGNORE_TRAIN_ID
    log_probs = _log_softmax(prediction.astype(np.float32))
    picked = np.take_along_axis(log_probs, np.clip(labels, 0, NUM_CLASSES - 1)[..., None], axis=-1)[..., 0]
    per_pixel = np.where(valid, -picked, 0.0)
    denom = np.maximum(valid.reshape(valid.shape[0], -1).sum(axis=1), 1)
    return (per_pixel.reshape(per_pixel.shape[0], -1).sum(axis=1) / denom).astype(np.float32)


def _confusion_counts(pred_ids: np.ndarray, labels: np.ndarray):
    valid = labels != IGNORE_TRAIN_ID
    gt_hist = np.stack([np.bincount(l[v], minlength=NUM_CLASSES)[:NUM_CLASSES] for l, v in zip(labels, valid)])
    pred_hist = np.stack([np.bincount(p[v], minlength=NUM_CLASSES)[:NUM_CLASSES] for p, v in zip(pred_ids, valid)])
    inter = np.stack([np.bincount(l[v & (p == l)], minlength=NUM_CLASSES)[:NUM_CLASSES]
                      for p, l, v in zip(pred_ids, labels, valid)])
    union = gt_hist + pred_hist - inter
    return inter, union, gt_hist, pred_hist, valid


@tensorleap_custom_metric(name="segmentation", direction={"mean_iou": MetricDirection.Upward,
                                                          "pixel_accuracy": MetricDirection.Upward})
def segmentation_quality(prediction: np.ndarray, gt: np.ndarray) -> Dict[str, np.ndarray]:
    labels = _labels(gt)
    pred_ids = prediction.argmax(axis=-1)
    inter, union, _gt_hist, _pred_hist, valid = _confusion_counts(pred_ids, labels)
    present = union > 0
    iou = np.where(present, inter / np.maximum(union, 1), 0.0)
    mean_iou = iou.sum(axis=1) / np.maximum(present.sum(axis=1), 1)
    correct = ((pred_ids == labels) & valid).reshape(valid.shape[0], -1).sum(axis=1)
    pixel_accuracy = correct / np.maximum(valid.reshape(valid.shape[0], -1).sum(axis=1), 1)
    return {"mean_iou": mean_iou.astype(np.float32), "pixel_accuracy": pixel_accuracy.astype(np.float32)}


@tensorleap_custom_metric(name="iou_class", direction={c: MetricDirection.Upward for c in CATEGORIES})
def per_class_iou(prediction: np.ndarray, gt: np.ndarray) -> Dict[str, np.ndarray]:
    # IoU per class; a class absent from both GT and prediction scores 0 (as in the original project).
    labels = _labels(gt)
    inter, union, _gt_hist, _pred_hist, _valid = _confusion_counts(prediction.argmax(axis=-1), labels)
    iou = inter / np.maximum(union, 1)
    return {c: iou[:, i].astype(np.float32) for i, c in enumerate(CATEGORIES)}


@tensorleap_custom_metric(name="mean_confidence", direction=MetricDirection.Upward)
def mean_confidence(prediction: np.ndarray) -> np.ndarray:
    # Unsupervised quality signal: mean max-softmax probability per image (also defined on unlabeled data).
    log_probs = _log_softmax(prediction.astype(np.float32))
    return np.exp(log_probs.max(axis=-1)).reshape(prediction.shape[0], -1).mean(axis=1).astype(np.float32)
