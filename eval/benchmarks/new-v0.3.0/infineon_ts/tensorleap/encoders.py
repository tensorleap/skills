import numpy as np
from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_gt_encoder, tensorleap_input_encoder

from config import CONFIG

INPUT_NAMES = CONFIG["model"]["input_names"]  # ["current_trace", "deformation_trace"] — ONNX input order


def row_index(sample_id: str, preprocess: PreprocessResponse) -> int:
    return preprocess.data["index"][sample_id]


@tensorleap_input_encoder(INPUT_NAMES[0], channel_dim=-1)
def current_trace(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    return preprocess.data["current_trace"][row_index(sample_id, preprocess)].astype(np.float32)


@tensorleap_input_encoder(INPUT_NAMES[1], channel_dim=-1)
def deformation_trace(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    return preprocess.data["deformation_trace"][row_index(sample_id, preprocess)].astype(np.float32)


@tensorleap_gt_encoder("shear_value_normed")
def shear_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    if not preprocess.data["labeled"]:
        return np.array([], dtype=np.float32)
    y = preprocess.data["shear_value_normed"][row_index(sample_id, preprocess)]
    return np.array([y], dtype=np.float32)
