import json
from typing import Dict, Optional, Union

import numpy as np
from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DatasetMetadataType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_metadata

from cityscapes_labels import CATEGORIES, NUM_CLASSES
from config import CONFIG, data_path
from encoders import get_record, load_rgb, load_train_id_mask

VEHICLE_KEYS = {
    "gps_heading": "gpsHeading",
    "gps_latitude": "gpsLatitude",
    "gps_longitude": "gpsLongitude",
    "outside_temperature": "outsideTemperature",
    "speed": "speed",
    "yaw_rate": "yawRate",
}


@tensorleap_metadata(name="sample", metadata_type={
    "file_name": DatasetMetadataType.string,
    "city": DatasetMetadataType.string,
    "dataset": DatasetMetadataType.string,
    "subset": DatasetMetadataType.string,
    "labeled": DatasetMetadataType.boolean,
    "selection_reason": DatasetMetadataType.string,
    "drive": DatasetMetadataType.string,
    "vegetation_percent": DatasetMetadataType.float,
})
def sample_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, Union[str, bool, float, None]]:
    record = get_record(sample_id, preprocess)
    vegetation = record.get("vegetation_percent")
    return {
        "file_name": record["file_name"],
        "city": record["city"],
        "dataset": record["dataset"],
        "subset": record["subset"],
        "labeled": bool(record["labeled"]),
        "selection_reason": record.get("selection_reason", ""),
        "drive": record.get("drive", ""),
        "vegetation_percent": float(vegetation) if vegetation is not None else None,
    }


@tensorleap_metadata(name="vehicle", metadata_type={k: DatasetMetadataType.float for k in VEHICLE_KEYS})
def vehicle_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, float]:
    # Cityscapes ships per-frame vehicle telemetry; KITTI frames fall back to the config defaults.
    record = get_record(sample_id, preprocess)
    defaults = CONFIG["default_vehicle"]
    if not record["metadata_path"]:
        return {k: float(defaults[k]) for k in VEHICLE_KEYS}
    with open(data_path(record["metadata_path"]), "r") as f:
        vehicle = json.load(f)
    return {k: float(vehicle.get(src, defaults[k])) for k, src in VEHICLE_KEYS.items()}


@tensorleap_metadata(name="image_stats", metadata_type={
    "width": DatasetMetadataType.int,
    "height": DatasetMetadataType.int,
    "mean": DatasetMetadataType.float,
    "std": DatasetMetadataType.float,
    "mean_r": DatasetMetadataType.float,
    "mean_g": DatasetMetadataType.float,
    "mean_b": DatasetMetadataType.float,
    "dark_fraction": DatasetMetadataType.float,
})
def image_stats_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, Union[int, float]]:
    from PIL import Image
    record = get_record(sample_id, preprocess)
    with Image.open(data_path(record["image_path"])) as im:
        width, height = im.size
    rgb = load_rgb(record["image_path"]).astype(np.float32) / 255.0
    channel_means = rgb.reshape(-1, 3).mean(axis=0)
    return {
        "width": int(width),
        "height": int(height),
        "mean": float(rgb.mean()),
        "std": float(rgb.std()),
        "mean_r": float(channel_means[0]),
        "mean_g": float(channel_means[1]),
        "mean_b": float(channel_means[2]),
        "dark_fraction": float((rgb.mean(axis=-1) < 0.2).mean()),
    }


@tensorleap_metadata(name="gt_class_percent", metadata_type={
    **{c: DatasetMetadataType.float for c in CATEGORIES},
    "ignore": DatasetMetadataType.float,
    "num_classes_present": DatasetMetadataType.int,
})
def gt_class_percent_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, Optional[float]]:
    record = get_record(sample_id, preprocess)
    if not record["gt_path"]:
        return {**{c: None for c in CATEGORIES}, "ignore": None, "num_classes_present": None}
    mask = load_train_id_mask(record["gt_path"])
    hist = np.bincount(mask.ravel(), minlength=NUM_CLASSES + 1)[:NUM_CLASSES + 1] / mask.size * 100.0
    result = {c: float(hist[i]) for i, c in enumerate(CATEGORIES)}
    result["ignore"] = float(hist[NUM_CLASSES])
    result["num_classes_present"] = int((hist[:NUM_CLASSES] > 0).sum())
    return result
