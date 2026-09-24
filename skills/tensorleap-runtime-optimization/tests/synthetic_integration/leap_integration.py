"""Synthetic Tensorleap integration used by the tl_perf tests.

Driven entirely by environment variables so each test can shape its runtime profile:
  SYNTH_DATA_DIR    directory of <split>_<idx>.npy float32 vectors (written by the tests)
  SYNTH_MODEL_PATH  ONNX model: input "x" [batch, 8] -> output [batch, 4]
  SYNTH_N           samples per state (default 16)
  SYNTH_BREAK       "1" makes the input encoder raise (an invalid integration)
"""
import os

import numpy as np
import onnxruntime as ort
from code_loader.contract.datasetclasses import PredictionTypeHandler, PreprocessResponse
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import (
    tensorleap_custom_metric, tensorleap_gt_encoder, tensorleap_input_encoder,
    tensorleap_integration_test, tensorleap_load_model, tensorleap_metadata,
    tensorleap_preprocess)

LABELS = ["a", "b", "c", "d"]


def _n():
    return int(os.environ.get("SYNTH_N", "16"))


@tensorleap_preprocess()
def preprocess():
    return [
        PreprocessResponse(length=_n(), data={"split": "train"}, state=DataStateType.training),
        PreprocessResponse(length=_n(), data={"split": "val"}, state=DataStateType.validation),
    ]


def _load(idx, preprocess_response):
    path = os.path.join(os.environ["SYNTH_DATA_DIR"],
                        "%s_%d.npy" % (preprocess_response.data["split"], int(idx)))
    return np.load(path).astype(np.float32)


@tensorleap_input_encoder("x")
def input_encoder(idx, preprocess_response):
    if os.environ.get("SYNTH_BREAK") == "1":
        raise RuntimeError("synthetic integration deliberately broken")
    return _load(idx, preprocess_response)


@tensorleap_gt_encoder("label")
def gt_encoder(idx, preprocess_response):
    y = np.zeros(len(LABELS), dtype=np.float32)
    y[int(idx) % len(LABELS)] = 1.0
    return y


@tensorleap_metadata("mean_value")
def meta_mean(idx, preprocess_response):
    return float(_load(idx, preprocess_response).mean())


@tensorleap_custom_metric("mse")
def mse(prediction, ground_truth):
    return ((prediction - ground_truth) ** 2).mean(axis=1)


@tensorleap_load_model([PredictionTypeHandler("classes", LABELS)])
def load_model():
    return ort.InferenceSession(os.environ["SYNTH_MODEL_PATH"], providers=["CPUExecutionProvider"])


@tensorleap_integration_test()
def integration_test(idx, preprocess_response):
    x = input_encoder(idx, preprocess_response)
    gt = gt_encoder(idx, preprocess_response)
    model = load_model()
    prediction = model.run(None, {"x": x})[0]
    mse(prediction, gt)
    meta_mean(idx, preprocess_response)


if __name__ == "__main__":
    responses = preprocess()
    integration_test(0, responses[0])
