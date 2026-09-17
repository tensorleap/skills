import numpy as np

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.inner_leap_binder.leapbinder_decorators import (
    tensorleap_gt_encoder,
    tensorleap_input_encoder,
)

from preprocess import get_item


def _to_np(x) -> np.ndarray:
    if hasattr(x, "numpy"):
        x = x.numpy()
    return np.ascontiguousarray(np.asarray(x, dtype=np.float32))


@tensorleap_input_encoder(name="rgb", channel_dim=1, model_input_index=0)
def rgb_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    data, _, _ = get_item(preprocess, sample_id)
    return _to_np(data["rgb"])


@tensorleap_input_encoder(name="rgb_left", channel_dim=1, model_input_index=1)
def rgb_left_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    data, _, _ = get_item(preprocess, sample_id)
    return _to_np(data["rgb_left"])


@tensorleap_input_encoder(name="rgb_right", channel_dim=1, model_input_index=2)
def rgb_right_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    data, _, _ = get_item(preprocess, sample_id)
    return _to_np(data["rgb_right"])


@tensorleap_input_encoder(name="rgb_center", channel_dim=1, model_input_index=3)
def rgb_center_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    data, _, _ = get_item(preprocess, sample_id)
    return _to_np(data["rgb_center"])


@tensorleap_input_encoder(name="lidar", channel_dim=1, model_input_index=4)
def lidar_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    data, _, _ = get_item(preprocess, sample_id)
    return _to_np(data["lidar"])


@tensorleap_input_encoder(name="measurements", channel_dim=-1, model_input_index=5)
def measurements_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    data, _, _ = get_item(preprocess, sample_id)
    return _to_np(data["measurements"])


@tensorleap_input_encoder(name="target_point", channel_dim=-1, model_input_index=6)
def target_point_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    data, _, _ = get_item(preprocess, sample_id)
    return _to_np(data["target_point"])


@tensorleap_gt_encoder(name="waypoints_gt")
def waypoints_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    _, targets, _ = get_item(preprocess, sample_id)
    return _to_np(targets[1])


@tensorleap_gt_encoder(name="traffic_gt")
def traffic_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    _, targets, _ = get_item(preprocess, sample_id)
    return _to_np(targets[4])


def _one_hot(index: int, n: int = 2) -> np.ndarray:
    out = np.zeros((n,), dtype=np.float32)
    out[int(index)] = 1.0
    return out


@tensorleap_gt_encoder(name="junction_gt")
def junction_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    _, targets, _ = get_item(preprocess, sample_id)
    return _one_hot(targets[2])


@tensorleap_gt_encoder(name="traffic_light_gt")
def traffic_light_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    _, targets, _ = get_item(preprocess, sample_id)
    return _one_hot(targets[3])


@tensorleap_gt_encoder(name="stop_sign_gt")
def stop_sign_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    _, targets, _ = get_item(preprocess, sample_id)
    return _one_hot(targets[6])
