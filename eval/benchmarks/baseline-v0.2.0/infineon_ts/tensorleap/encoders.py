import numpy as np

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_gt_encoder, tensorleap_input_encoder

from preprocess import row_index


@tensorleap_input_encoder(name="current_trace", channel_dim=-1)
def current_trace(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    return preprocess.data["current_trace"][row_index(sample_id)].astype(np.float32)


@tensorleap_input_encoder(name="deformation_trace", channel_dim=-1)
def deformation_trace(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    return preprocess.data["deformation_trace"][row_index(sample_id)].astype(np.float32)


@tensorleap_gt_encoder(name="shear_value_normed")
def shear_value_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    if preprocess.data["split"] == "unlabeled":
        return np.array([], dtype=np.float32)
    value = preprocess.data["shear_value_normed"][row_index(sample_id)]
    return np.array([value], dtype=np.float32)
