import numpy as np
from code_loader.contract.enums import MetricDirection
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_loss, tensorleap_custom_metric

from preprocess import TL_CONFIG

IGNORE_LABEL = int(TL_CONFIG["ignore_label"])
EPS = 1e-7


def _as_batch(x: np.ndarray, sample_ndim: int) -> np.ndarray:
    return x if x.ndim == sample_ndim + 1 else x[None]


def _log_softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=-1, keepdims=True)
    return shifted - np.log(np.exp(shifted).sum(axis=-1, keepdims=True))


def _valid_and_labels(gt_sample: np.ndarray):
    labels = gt_sample[..., 0].astype(np.int64)
    valid = labels != IGNORE_LABEL
    return labels, valid


@tensorleap_custom_loss(name="pixel_cross_entropy")
def pixel_cross_entropy(segmentation_mask: np.ndarray, prediction: np.ndarray) -> np.ndarray:
    gt = _as_batch(segmentation_mask, 3)
    logits = _as_batch(prediction, 3)
    losses = np.zeros(logits.shape[0], dtype=np.float32)
    for b in range(logits.shape[0]):
        labels, valid = _valid_and_labels(gt[b])
        if not valid.any():
            losses[b] = 0.0
            continue
        log_probs = _log_softmax(logits[b][valid].astype(np.float32))
        picked = np.take_along_axis(log_probs, labels[valid][:, None], axis=-1)[:, 0]
        losses[b] = float(-picked.mean())
    return losses


def _softmax(logits: np.ndarray) -> np.ndarray:
    return np.exp(_log_softmax(logits))


@tensorleap_custom_metric(name="pixel_accuracy", direction=MetricDirection.Upward)
def pixel_accuracy(segmentation_mask: np.ndarray, prediction: np.ndarray) -> np.ndarray:
    gt = _as_batch(segmentation_mask, 3)
    logits = _as_batch(prediction, 3)
    out = np.full(logits.shape[0], np.nan, dtype=np.float32)
    for b in range(logits.shape[0]):
        labels, valid = _valid_and_labels(gt[b])
        if not valid.any():
            continue
        pred_labels = logits[b].argmax(axis=-1)
        out[b] = float((pred_labels[valid] == labels[valid]).mean())
    return out


@tensorleap_custom_metric(name="mean_iou", direction=MetricDirection.Upward)
def mean_iou(segmentation_mask: np.ndarray, prediction: np.ndarray) -> np.ndarray:
    gt = _as_batch(segmentation_mask, 3)
    logits = _as_batch(prediction, 3)
    n_classes = logits.shape[-1]
    out = np.full(logits.shape[0], np.nan, dtype=np.float32)
    for b in range(logits.shape[0]):
        labels, valid = _valid_and_labels(gt[b])
        if not valid.any():
            continue
        pred_labels = logits[b].argmax(axis=-1)[valid]
        true_labels = labels[valid]
        ious = []
        for c in np.unique(np.concatenate([true_labels, pred_labels])):
            if c >= n_classes:
                continue
            inter = np.count_nonzero((pred_labels == c) & (true_labels == c))
            union = np.count_nonzero((pred_labels == c) | (true_labels == c))
            if union > 0:
                ious.append(inter / union)
        if ious:
            out[b] = float(np.mean(ious))
    return out


@tensorleap_custom_metric(name="mean_max_confidence", direction=MetricDirection.Upward)
def mean_max_confidence(prediction: np.ndarray) -> np.ndarray:
    logits = _as_batch(prediction, 3)
    out = np.zeros(logits.shape[0], dtype=np.float32)
    for b in range(logits.shape[0]):
        probs = _softmax(logits[b].astype(np.float32))
        out[b] = float(probs.max(axis=-1).mean())
    return out
