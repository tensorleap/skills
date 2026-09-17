import numpy as np
from code_loader.contract.datasetclasses import SamplePreprocessResponse
from code_loader.contract.enums import LeapDataType
from code_loader.contract.visualizer_classes import LeapGraph, LeapHorizontalBar
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_visualizer


def _unbatch_trace(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if x.ndim > 1:
        x = x[0]
    return x.reshape(-1)


def _sample_row(spr: SamplePreprocessResponse):
    sid = spr.sample_ids
    if isinstance(sid, np.ndarray):
        sid = sid.reshape(-1)[0]
    data = spr.preprocess_response.data
    return data, data["index"][str(sid)]


@tensorleap_custom_visualizer("current_trace_graph", LeapDataType.Graph)
def current_trace_graph(current_trace: np.ndarray) -> LeapGraph:
    x = _unbatch_trace(current_trace)
    return LeapGraph(data=x.reshape(-1, 1), x_label="sample", y_label="current (z-score)", legend=["current"])


@tensorleap_custom_visualizer("deformation_trace_graph", LeapDataType.Graph)
def deformation_trace_graph(deformation_trace: np.ndarray) -> LeapGraph:
    x = _unbatch_trace(deformation_trace)
    return LeapGraph(data=x.reshape(-1, 1), x_label="sample", y_label="deformation (z-score)", legend=["deformation"])


@tensorleap_custom_visualizer("deformation_vs_reconstruction", LeapDataType.Graph)
def deformation_vs_reconstruction(deformation_trace: np.ndarray, spr: SamplePreprocessResponse) -> LeapGraph:
    x = _unbatch_trace(deformation_trace)
    data, i = _sample_row(spr)
    if data["reconstruction_output"] is None:
        return LeapGraph(data=x.reshape(-1, 1), x_label="sample", y_label="deformation (z-score)", legend=["deformation"])
    recon = np.asarray(data["reconstruction_output"][i], dtype=np.float32).reshape(-1)
    return LeapGraph(data=np.stack([x, recon], axis=1).astype(np.float32), x_label="sample",
                     y_label="deformation (z-score)", legend=["deformation", "autoencoder reconstruction"])


@tensorleap_custom_visualizer("shear_prediction_bar", LeapDataType.HorizontalBar)
def shear_prediction_bar(vst_prediction: np.ndarray, spr: SamplePreprocessResponse) -> LeapHorizontalBar:
    pred = float(np.asarray(vst_prediction, dtype=np.float32).reshape(-1)[0])
    data, i = _sample_row(spr)
    factor = float(data["norm_factor"][i])
    body = np.array([pred * factor], dtype=np.float32)
    gt_value = float(data["shear_value"][i])
    gt = np.array([gt_value], dtype=np.float32) if data["labeled"] and np.isfinite(gt_value) else None
    return LeapHorizontalBar(body=body, labels=["shear_value"], gt=gt)
