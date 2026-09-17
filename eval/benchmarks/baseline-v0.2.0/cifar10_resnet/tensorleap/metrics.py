import numpy as np

from code_loader.contract.enums import MetricDirection
from code_loader.inner_leap_binder.leapbinder_decorators import (
    tensorleap_custom_loss,
    tensorleap_custom_metric,
)

_EPS = 1e-7


@tensorleap_custom_loss(name="categorical_crossentropy")
def categorical_crossentropy(prediction: np.ndarray, ground_truth: np.ndarray) -> np.ndarray:
    probs = np.clip(prediction, _EPS, 1.0 - _EPS)
    return -np.sum(ground_truth * np.log(probs), axis=-1).astype(np.float32)


@tensorleap_custom_metric(name="accuracy", direction=MetricDirection.Upward)
def accuracy(prediction: np.ndarray, ground_truth: np.ndarray) -> np.ndarray:
    return (np.argmax(prediction, axis=-1) == np.argmax(ground_truth, axis=-1)).astype(np.float32)


@tensorleap_custom_metric(name="gt_confidence", direction=MetricDirection.Upward)
def gt_confidence(prediction: np.ndarray, ground_truth: np.ndarray) -> np.ndarray:
    return np.sum(prediction * ground_truth, axis=-1).astype(np.float32)
