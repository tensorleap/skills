"""Synthetic Tensorleap integration used by the tl_perf tests.

Driven entirely by environment variables so each test can plant a runtime pathology:
  SYNTH_DATA_DIR     directory of <split>_<idx>.npy float32 vectors (written by the tests)
  SYNTH_MODEL_PATH   ONNX model: input "x" [batch, 8] -> output [batch, 4]
  SYNTH_N            samples per state (default 16)
  SYNTH_BREAK        "1" makes the input encoder raise (an invalid integration)
  SYNTH_DECODE_MS    extra cost of decoding one sample file
  SYNTH_REDUNDANT    "1": metadata re-invokes the input encoder (a second decode per sample)
  SYNTH_SCAN_SIZE    >0: metadata does a linear list scan of this size per sample (O(n^2) total)
  SYNTH_NOISE        "1": the input encoder adds unseeded noise (nondeterministic output)
  SYNTH_POST_LOG     file: every real (uncached) run of the prediction post-processing appends
                     "<pid>" — shared by the metric and the visualizer through an lru_cache
  SYNTH_POST_MS      cost of that post-processing
  SYNTH_ALTER        "1": change one metadata value (a behavior change, for compare tests)
  SYNTH_CACHE        "1": the lossless fix — decode through a small shared lru_cache
"""
import functools
import os
import time

import numpy as np
import onnxruntime as ort
from code_loader.contract.datasetclasses import PredictionTypeHandler, PreprocessResponse
from code_loader.contract.enums import DataStateType, LeapDataType
from code_loader.contract.visualizer_classes import LeapHorizontalBar
from code_loader.inner_leap_binder.leapbinder_decorators import (
    tensorleap_custom_loss, tensorleap_custom_metric, tensorleap_custom_visualizer,
    tensorleap_gt_encoder, tensorleap_input_encoder, tensorleap_integration_test,
    tensorleap_load_model, tensorleap_metadata, tensorleap_preprocess)

LABELS = ["a", "b", "c", "d"]


def _env_int(name, default=0):
    return int(os.environ.get(name, str(default)) or default)


@tensorleap_preprocess()
def preprocess():
    n = _env_int("SYNTH_N", 16)
    return [
        PreprocessResponse(length=n, data={"split": "train"}, state=DataStateType.training),
        PreprocessResponse(length=n, data={"split": "val"}, state=DataStateType.validation),
    ]


def _burn(ms):
    """Deterministic CPU work (millisecond sleeps are unreliable under OS timer coalescing)."""
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        pass


def _decode(split, idx):
    path = os.path.join(os.environ["SYNTH_DATA_DIR"], "%s_%d.npy" % (split, idx))
    x = np.load(path).astype(np.float32)
    _burn(_env_int("SYNTH_DECODE_MS"))
    return x


@functools.lru_cache(maxsize=4)
def _decode_cached(split, idx):
    return _decode(split, idx)


def _load(idx, preprocess_response):
    """SYNTH_CACHE=1 is the lossless fix: one small cache shared by the encoder and the
    metadata of a sample (they run in the same process for the same sample)."""
    split = preprocess_response.data["split"]
    if os.environ.get("SYNTH_CACHE") == "1":
        return _decode_cached(split, int(idx)).copy()
    return _decode(split, int(idx))


@tensorleap_input_encoder("x")
def input_encoder(idx, preprocess_response):
    if os.environ.get("SYNTH_BREAK") == "1":
        raise RuntimeError("synthetic integration deliberately broken")
    x = _load(idx, preprocess_response)
    if os.environ.get("SYNTH_NOISE") == "1":
        x = (x + np.random.normal(0, 0.01, x.shape)).astype(np.float32)
    return x


@tensorleap_gt_encoder("label")
def gt_encoder(idx, preprocess_response):
    y = np.zeros(len(LABELS), dtype=np.float32)
    y[int(idx) % len(LABELS)] = 1.0
    return y


@tensorleap_metadata("mean_value")
def meta_mean(idx, preprocess_response):
    if os.environ.get("SYNTH_REDUNDANT") == "1":
        x = input_encoder(idx, preprocess_response)   # re-runs another component's decode
    else:
        x = _load(idx, preprocess_response)
    return float(np.mean(x))


@tensorleap_metadata("scan_position")
def meta_scan(idx, preprocess_response):
    size = _env_int("SYNTH_SCAN_SIZE")
    shift = 1 if os.environ.get("SYNTH_ALTER") == "1" else 0   # a behavior change
    if size <= 0:
        return shift
    ids = list(range(size))
    return ids.index(size - 1 - (int(idx) % 2)) + shift


@functools.lru_cache(maxsize=256)
def _postprocess(key):
    log = os.environ.get("SYNTH_POST_LOG")
    if log:
        with open(log, "a") as fh:
            fh.write("%d\n" % os.getpid())
    _burn(_env_int("SYNTH_POST_MS"))
    return float(np.frombuffer(key, dtype=np.float32).max())


def _post(row):
    return _postprocess(np.ascontiguousarray(row, dtype=np.float32).tobytes())


@tensorleap_custom_metric("confidence")
def confidence(prediction):
    return np.array([_post(row) for row in prediction], dtype=np.float32)


@tensorleap_custom_loss("mse")
def mse(prediction, ground_truth):
    return ((prediction - ground_truth) ** 2).mean(axis=1)


@tensorleap_custom_visualizer("bar", LeapDataType.HorizontalBar)
def bar(prediction):
    row = prediction[0]
    _post(row)
    return LeapHorizontalBar(body=row.astype(np.float32), labels=LABELS)


@tensorleap_load_model([PredictionTypeHandler("classes", LABELS)])
def load_model():
    return ort.InferenceSession(os.environ["SYNTH_MODEL_PATH"], providers=["CPUExecutionProvider"])


@tensorleap_integration_test()
def integration_test(idx, preprocess_response):
    x = input_encoder(idx, preprocess_response)
    gt = gt_encoder(idx, preprocess_response)
    model = load_model()
    prediction = model.run(None, {"x": x})[0]
    confidence(prediction)
    mse(prediction, gt)
    bar(prediction)
    meta_mean(idx, preprocess_response)
    meta_scan(idx, preprocess_response)


if __name__ == "__main__":
    responses = preprocess()
    integration_test(0, responses[0])
