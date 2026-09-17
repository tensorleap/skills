import os
import sys
from typing import Any, Dict, List

import numpy as np
import yaml

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_preprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_INTERFUSER_DIR = os.path.join(_REPO_ROOT, "interfuser")
if _INTERFUSER_DIR not in sys.path:
    sys.path.insert(0, _INTERFUSER_DIR)

with open(os.path.join(_HERE, "project_config.yaml"), "r") as _f:
    CONFIG: Dict[str, Any] = yaml.safe_load(_f)


def _attach_eval_transforms(ds) -> None:
    from timm.data.transforms_carla_factory import create_carla_rgb_transform

    ds.rgb_transform = create_carla_rgb_transform(CONFIG["input_rgb_size"], is_training=False)
    ds.rgb_center_transform = create_carla_rgb_transform(
        CONFIG["rgb_center_size"], is_training=False, scale=None, need_scale=False
    )
    ds.multi_view_transform = create_carla_rgb_transform(CONFIG["input_rgb_size"], is_training=False)
    ds.lidar_transform = None


def build_carla_dataset():
    from timm.data.carla_dataset import CarlaMVDetDataset

    root = os.path.join(CONFIG["data_root"], CONFIG["carla_dir"])
    ds = CarlaMVDetDataset(
        root,
        towns=CONFIG["carla_towns"],
        weathers=CONFIG["carla_weathers"],
        head="det",
        input_rgb_size=CONFIG["input_rgb_size"],
        input_lidar_size=CONFIG["input_lidar_size"],
        with_lidar=True,
        multi_view=True,
        with_waypoints=False,
        augment_prob=0.0,
    )
    _attach_eval_transforms(ds)
    return ds


def build_b2d_dataset():
    from timm.data.bench2drive_dataset import Bench2DriveMVDetDataset

    root = os.path.join(CONFIG["data_root"], CONFIG["b2d_dir"])
    ds = Bench2DriveMVDetDataset(
        root,
        head="det",
        input_rgb_size=CONFIG["input_rgb_size"],
        input_lidar_size=CONFIG["input_lidar_size"],
        with_lidar=True,
        multi_view=True,
        augment_prob=0.0,
    )
    _attach_eval_transforms(ds)
    return ds


def _select_indices(n: int, limit) -> List[int]:
    if not limit or limit >= n:
        return list(range(n))
    return sorted(set(np.linspace(0, n - 1, int(limit)).round().astype(int).tolist()))


@tensorleap_preprocess()
def preprocess() -> List[PreprocessResponse]:
    limit = CONFIG.get("sample_limit_per_split")
    responses = []
    for name, builder, state in (
        ("carla", build_carla_dataset, DataStateType.training),
        ("b2d", build_b2d_dataset, DataStateType.validation),
    ):
        ds = builder()
        indices = _select_indices(len(ds), limit)
        responses.append(
            PreprocessResponse(
                sample_ids=[str(i) for i in indices],
                data={"name": name, "dataset": ds, "cache": {}},
                state=state,
            )
        )
    return responses


def get_item(preprocess: PreprocessResponse, sample_id: str):
    cache = preprocess.data["cache"]
    if cache.get("sample_id") != sample_id:
        cache.clear()
        data, targets, measurements = preprocess.data["dataset"][int(sample_id)]
        cache["sample_id"] = sample_id
        cache["item"] = (data, targets, measurements)
    return cache["item"]
