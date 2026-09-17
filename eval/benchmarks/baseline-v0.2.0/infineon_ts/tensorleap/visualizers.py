import numpy as np

from code_loader.contract.datasetclasses import SamplePreprocessResponse
from code_loader.contract.enums import LeapDataType
from code_loader.contract.visualizer_classes import LeapGraph, LeapHorizontalBar
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_visualizer

from preprocess import SURFACE_CODE_TO_FACTOR, row_index


def _unbatch(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return x[0] if x.ndim == 2 else x


def _sample_index(spr: SamplePreprocessResponse) -> int:
    sid = spr.sample_ids
    if isinstance(sid, np.ndarray):
        sid = sid.reshape(-1)[0]
    return row_index(str(sid))


@tensorleap_custom_visualizer(name="input_traces", visualizer_type=LeapDataType.Graph)
def input_traces_visualizer(current_trace: np.ndarray, deformation_trace: np.ndarray) -> LeapGraph:
    data = np.stack([_unbatch(current_trace), _unbatch(deformation_trace)], axis=1).astype(np.float32)
    return LeapGraph(data=data, x_label="resampled step", y_label="z-score", legend=["current", "deformation"])


@tensorleap_custom_visualizer(name="raw_current", visualizer_type=LeapDataType.Graph)
def raw_current_visualizer(spr: SamplePreprocessResponse) -> LeapGraph:
    trace = spr.preprocess_response.data["raw_current_interp"][_sample_index(spr)]
    return LeapGraph(data=trace.reshape(-1, 1).astype(np.float32), x_label="resampled step", y_label="current", legend=["current"])


@tensorleap_custom_visualizer(name="raw_deformation", visualizer_type=LeapDataType.Graph)
def raw_deformation_visualizer(spr: SamplePreprocessResponse) -> LeapGraph:
    trace = spr.preprocess_response.data["raw_deformation_interp"][_sample_index(spr)]
    return LeapGraph(data=trace.reshape(-1, 1).astype(np.float32), x_label="resampled step", y_label="deformation", legend=["deformation"])


@tensorleap_custom_visualizer(name="deformation_reconstruction", visualizer_type=LeapDataType.Graph)
def deformation_reconstruction_visualizer(deformation_trace: np.ndarray, spr: SamplePreprocessResponse) -> LeapGraph:
    recon = spr.preprocess_response.data["reconstruction_output"][_sample_index(spr)]
    data = np.stack([_unbatch(deformation_trace), recon], axis=1).astype(np.float32)
    return LeapGraph(data=data, x_label="resampled step", y_label="z-score", legend=["deformation", "reconstruction"])


@tensorleap_custom_visualizer(name="shear_prediction", visualizer_type=LeapDataType.HorizontalBar)
def shear_prediction_visualizer(vst_prediction: np.ndarray, shear_value_normed: np.ndarray, spr: SamplePreprocessResponse) -> LeapHorizontalBar:
    idx = _sample_index(spr)
    factor = float(SURFACE_CODE_TO_FACTOR[int(spr.preprocess_response.data["surface"][idx])])
    pred = float(np.asarray(vst_prediction, dtype=np.float32).reshape(-1)[0]) * factor
    gt_values = np.asarray(shear_value_normed, dtype=np.float32).reshape(-1)
    body = np.array([pred], dtype=np.float32)
    if gt_values.size == 0 or np.isnan(gt_values[0]):
        return LeapHorizontalBar(body=body, labels=["shear_force"])
    gt = np.array([float(gt_values[0]) * factor], dtype=np.float32)
    return LeapHorizontalBar(body=body, labels=["shear_force"], gt=gt)
