from typing import Any, Dict, Optional

import numpy as np
from PIL import Image
from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DatasetMetadataType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_metadata

from domain_gap.data.cs_data import CATEGORIES
from encoders import record, abs_path, load_train_id_mask, IGNORE_LABEL

STATS_STRIDE = 4


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    value = float(value)
    if not np.isfinite(value):
        return None
    return value


@tensorleap_metadata("source", {
    "dataset": DatasetMetadataType.string,
    "city": DatasetMetadataType.string,
    "subset_folder": DatasetMetadataType.string,
    "has_gt": DatasetMetadataType.boolean,
    "selection_reason": DatasetMetadataType.string,
    "kitti_drive": DatasetMetadataType.string,
    "manifest_vegetation_percent": DatasetMetadataType.float,
})
def source_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, Any]:
    rec = record(sample_id, preprocess)
    return {
        "dataset": rec["dataset"],
        "city": rec["city"],
        "subset_folder": rec["subset_name"],
        "has_gt": bool(rec["gt_path"]),
        "selection_reason": rec["selection_reason"],
        "kitti_drive": rec["drive"],
        "manifest_vegetation_percent": _float_or_none(rec["vegetation_percent"]),
    }


@tensorleap_metadata("vehicle", {
    "gps_heading": DatasetMetadataType.float,
    "gps_latitude": DatasetMetadataType.float,
    "gps_longitude": DatasetMetadataType.float,
    "outside_temperature": DatasetMetadataType.float,
    "speed": DatasetMetadataType.float,
    "yaw_rate": DatasetMetadataType.float,
})
def vehicle_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, Optional[float]]:
    vehicle = record(sample_id, preprocess)["vehicle"] or {}
    return {
        "gps_heading": _float_or_none(vehicle.get("gpsHeading")),
        "gps_latitude": _float_or_none(vehicle.get("gpsLatitude")),
        "gps_longitude": _float_or_none(vehicle.get("gpsLongitude")),
        "outside_temperature": _float_or_none(vehicle.get("outsideTemperature")),
        "speed": _float_or_none(vehicle.get("speed")),
        "yaw_rate": _float_or_none(vehicle.get("yawRate")),
    }


@tensorleap_metadata("image", {
    "original_width": DatasetMetadataType.int,
    "original_height": DatasetMetadataType.int,
    "aspect_ratio": DatasetMetadataType.float,
    "brightness": DatasetMetadataType.float,
    "contrast": DatasetMetadataType.float,
    "mean_red": DatasetMetadataType.float,
    "mean_green": DatasetMetadataType.float,
    "mean_blue": DatasetMetadataType.float,
    "saturation": DatasetMetadataType.float,
})
def image_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, Any]:
    img = Image.open(abs_path(record(sample_id, preprocess)["image_path"], preprocess)).convert("RGB")
    width, height = img.size
    rgb = np.asarray(img, dtype=np.float32)[::STATS_STRIDE, ::STATS_STRIDE]
    gray = rgb.mean(axis=-1)
    channel_means = rgb.reshape(-1, 3).mean(axis=0)
    saturation = (rgb.max(axis=-1) - rgb.min(axis=-1)) / (rgb.max(axis=-1) + 1e-6)
    return {
        "original_width": int(width),
        "original_height": int(height),
        "aspect_ratio": float(width / height),
        "brightness": float(gray.mean()),
        "contrast": float(gray.std()),
        "mean_red": float(channel_means[0]),
        "mean_green": float(channel_means[1]),
        "mean_blue": float(channel_means[2]),
        "saturation": float(saturation.mean()),
    }


GT_KEYS = {f"fraction_{name.replace(' ', '_')}": DatasetMetadataType.float for name in CATEGORIES}
GT_KEYS.update({
    "labeled_fraction": DatasetMetadataType.float,
    "num_classes_present": DatasetMetadataType.int,
    "dominant_class": DatasetMetadataType.string,
    "small_object_fraction": DatasetMetadataType.float,
})
SMALL_OBJECT_CLASSES = ["pole", "traffic light", "traffic sign", "person", "rider", "motorcycle", "bicycle"]


@tensorleap_metadata("gt", GT_KEYS)
def gt_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, Any]:
    result: Dict[str, Any] = {key: None for key in GT_KEYS}
    rec = record(sample_id, preprocess)
    if not rec["gt_path"]:
        return result
    labels = load_train_id_mask(sample_id, preprocess)
    counts = np.bincount(labels.ravel(), minlength=IGNORE_LABEL + 1)
    total = labels.size
    valid = counts[:len(CATEGORIES)]
    fractions = valid / total
    for name, frac in zip(CATEGORIES, fractions):
        result[f"fraction_{name.replace(' ', '_')}"] = float(frac)
    result["labeled_fraction"] = float(valid.sum() / total)
    result["num_classes_present"] = int((valid > 0).sum())
    result["dominant_class"] = str(CATEGORIES[int(valid.argmax())]) if valid.sum() > 0 else None
    result["small_object_fraction"] = float(sum(fractions[CATEGORIES.index(c)] for c in SMALL_OBJECT_CLASSES))
    return result
