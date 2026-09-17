import numpy as np

from code_loader.contract.datasetclasses import SamplePreprocessResponse
from code_loader.contract.enums import MetricDirection
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_loss, tensorleap_custom_metric

from preprocess import SURFACE_CODE_TO_FACTOR, row_index


def _flatten_pred(prediction: np.ndarray) -> np.ndarray:
    return np.asarray(prediction, dtype=np.float32).reshape(prediction.shape[0], -1)[:, 0]


def _flatten_gt(gt: np.ndarray) -> np.ndarray:
    return np.asarray(gt, dtype=np.float32).reshape(gt.shape[0], -1)[:, 0]


@tensorleap_custom_loss(name="mse_normed")
def mse_loss(vst_prediction: np.ndarray, shear_value_normed: np.ndarray) -> np.ndarray:
    return ((_flatten_pred(vst_prediction) - _flatten_gt(shear_value_normed)) ** 2).astype(np.float32)


def _factors(spr: SamplePreprocessResponse, batch_size: int) -> np.ndarray:
    sample_ids = np.asarray(spr.sample_ids).reshape(-1)
    if sample_ids.size == 1 and batch_size > 1:
        sample_ids = np.repeat(sample_ids, batch_size)
    surface = spr.preprocess_response.data["surface"]
    return np.array([SURFACE_CODE_TO_FACTOR[int(surface[row_index(str(sid))])] for sid in sample_ids], dtype=np.float32)


@tensorleap_custom_metric(name="abs_error_normed", direction=MetricDirection.Downward)
def abs_error_normed(vst_prediction: np.ndarray, shear_value_normed: np.ndarray) -> np.ndarray:
    return np.abs(_flatten_pred(vst_prediction) - _flatten_gt(shear_value_normed)).astype(np.float32)


@tensorleap_custom_metric(name="abs_error_shear_force", direction=MetricDirection.Downward)
def abs_error_shear_force(vst_prediction: np.ndarray, shear_value_normed: np.ndarray, spr: SamplePreprocessResponse) -> np.ndarray:
    error = _flatten_pred(vst_prediction) - _flatten_gt(shear_value_normed)
    return (np.abs(error) * _factors(spr, error.shape[0])).astype(np.float32)


@tensorleap_custom_metric(name="relative_error_pct", direction=MetricDirection.Downward)
def relative_error_pct(vst_prediction: np.ndarray, shear_value_normed: np.ndarray) -> np.ndarray:
    gt = _flatten_gt(shear_value_normed)
    return (100.0 * np.abs(_flatten_pred(vst_prediction) - gt) / np.maximum(np.abs(gt), 1e-6)).astype(np.float32)
