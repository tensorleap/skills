import os
from typing import Any, Dict

import numpy as np

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DatasetMetadataType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_metadata

from preprocess import get_item

_INVALID_WAYPOINT = 1000.0

SAMPLE_METADATA_TYPES: Dict[str, DatasetMetadataType] = {
    "dataset": DatasetMetadataType.string,
    "route": DatasetMetadataType.string,
    "town": DatasetMetadataType.int,
    "weather_id": DatasetMetadataType.int,
    "frame_idx": DatasetMetadataType.int,
    "route_idx": DatasetMetadataType.int,
    "speed": DatasetMetadataType.float,
    "steer": DatasetMetadataType.float,
    "throttle": DatasetMetadataType.float,
    "brake": DatasetMetadataType.float,
    "command": DatasetMetadataType.int,
    "is_junction": DatasetMetadataType.boolean,
    "light_state": DatasetMetadataType.int,
    "stop_sign": DatasetMetadataType.boolean,
    "vehicle_present": DatasetMetadataType.boolean,
    "bike_present": DatasetMetadataType.boolean,
    "pedestrian_present": DatasetMetadataType.boolean,
    "actor_count": DatasetMetadataType.int,
    "occupied_traffic_cells": DatasetMetadataType.int,
    "num_valid_waypoints": DatasetMetadataType.int,
    "gt_path_length": DatasetMetadataType.float,
    "gt_final_lateral_offset": DatasetMetadataType.float,
    "target_point_distance": DatasetMetadataType.float,
    "lidar_occupancy": DatasetMetadataType.float,
    "rgb_mean_brightness": DatasetMetadataType.float,
}


def _f(x) -> float:
    return float(np.asarray(x).reshape(-1)[0])


@tensorleap_metadata(name="sample", metadata_type=SAMPLE_METADATA_TYPES)
def sample_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, Any]:
    data, targets, meas = get_item(preprocess, sample_id)
    dataset = preprocess.data["dataset"]
    route_dir, frame_id = dataset.route_frames[int(sample_id)]

    wps = np.asarray(targets[1], dtype=np.float32)
    valid = np.all(wps < _INVALID_WAYPOINT, axis=-1)
    valid_wps = wps[valid]
    path_length = float(np.linalg.norm(np.diff(valid_wps, axis=0), axis=-1).sum()) if len(valid_wps) > 1 else 0.0
    final_lateral = float(valid_wps[-1, 0]) if len(valid_wps) else 0.0

    traffic = np.asarray(targets[4], dtype=np.float32)
    lidar = np.asarray(data["lidar"], dtype=np.float32)
    rgb2 = np.asarray(data["rgb2"], dtype=np.float32)
    target_point = np.asarray(data["target_point"], dtype=np.float32)

    return {
        "dataset": str(preprocess.data["name"]),
        "route": os.path.basename(os.path.normpath(route_dir)),
        "town": int(meas.get("town", -1)),
        "weather_id": int(meas.get("weather_id", -1)),
        "frame_idx": int(frame_id),
        "route_idx": int(data["route_idx"]),
        "speed": _f(meas["speed"]),
        "steer": _f(meas["steer"]),
        "throttle": _f(meas["throttle"]),
        "brake": _f(meas["brake"]),
        "command": int(data["command"]),
        "is_junction": bool(targets[2]),
        "light_state": int(data["light_state"]),
        "stop_sign": bool(targets[6]),
        "vehicle_present": bool(data["is_vehicle_present"]),
        "bike_present": bool(data["is_bike_present"]),
        "pedestrian_present": bool(data["is_pedestrian_present"]),
        "actor_count": int(data["actor_cnt"]),
        "occupied_traffic_cells": int((traffic[:, 0] >= 0.01).sum()),
        "num_valid_waypoints": int(valid.sum()),
        "gt_path_length": path_length,
        "gt_final_lateral_offset": final_lateral,
        "target_point_distance": float(np.linalg.norm(target_point)),
        "lidar_occupancy": float((lidar[2] > 0).mean()),
        "rgb_mean_brightness": float(rgb2.mean()),
    }
