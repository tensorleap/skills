import numpy as np
from code_loader.contract.datasetclasses import SamplePreprocessResponse
from code_loader.contract.enums import MetricDirection
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_loss, tensorleap_custom_metric


def _flat(pred: np.ndarray) -> np.ndarray:
    return np.asarray(pred, dtype=np.float32).reshape(len(pred), -1)[:, 0]


def _gt_flat(gt: np.ndarray, batch: int) -> np.ndarray:
    gt = np.asarray(gt, dtype=np.float32).reshape(batch, -1)
    if gt.shape[1] == 0:
        return np.full(batch, np.nan, dtype=np.float32)
    return gt[:, 0]


def _norm_factors(spr: SamplePreprocessResponse, batch: int) -> np.ndarray:
    ids = np.asarray(spr.sample_ids).reshape(-1)
    data = spr.preprocess_response.data
    return np.array([data["norm_factor"][data["index"][str(s)]] for s in ids], dtype=np.float32).reshape(-1)[:batch]


@tensorleap_custom_loss("mse_normed")
def mse_loss(vst_prediction: np.ndarray, shear_value_normed: np.ndarray) -> np.ndarray:
    pred = _flat(vst_prediction)
    gt = _gt_flat(shear_value_normed, len(pred))
    return ((pred - gt) ** 2).astype(np.float32)


@tensorleap_custom_metric("abs_error_normed", direction=MetricDirection.Downward)
def abs_error_normed(vst_prediction: np.ndarray, shear_value_normed: np.ndarray) -> np.ndarray:
    pred = _flat(vst_prediction)
    gt = _gt_flat(shear_value_normed, len(pred))
    return np.abs(pred - gt).astype(np.float32)


@tensorleap_custom_metric("abs_error_shear", direction=MetricDirection.Downward)
def abs_error_shear(vst_prediction: np.ndarray, shear_value_normed: np.ndarray, spr: SamplePreprocessResponse) -> np.ndarray:
    pred = _flat(vst_prediction)
    gt = _gt_flat(shear_value_normed, len(pred))
    return (np.abs(pred - gt) * _norm_factors(spr, len(pred))).astype(np.float32)


@tensorleap_custom_metric("relative_error", direction=MetricDirection.Downward)
def relative_error(vst_prediction: np.ndarray, shear_value_normed: np.ndarray) -> np.ndarray:
    pred = _flat(vst_prediction)
    gt = _gt_flat(shear_value_normed, len(pred))
    return (np.abs(pred - gt) / np.abs(gt)).astype(np.float32)


@tensorleap_custom_metric("predicted_shear_value", direction=MetricDirection.Upward, compute_insights=False)
def predicted_shear_value(vst_prediction: np.ndarray, spr: SamplePreprocessResponse) -> np.ndarray:
    pred = _flat(vst_prediction)
    return (pred * _norm_factors(spr, len(pred))).astype(np.float32)


@tensorleap_custom_metric("signed_error_shear", direction=MetricDirection.Downward, compute_insights=False)
def signed_error_shear(vst_prediction: np.ndarray, shear_value_normed: np.ndarray, spr: SamplePreprocessResponse) -> np.ndarray:
    pred = _flat(vst_prediction)
    gt = _gt_flat(shear_value_normed, len(pred))
    return ((pred - gt) * _norm_factors(spr, len(pred))).astype(np.float32)
