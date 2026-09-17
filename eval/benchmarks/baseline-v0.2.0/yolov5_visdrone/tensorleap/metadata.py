from typing import Dict, Union

import numpy as np
from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DatasetMetadataType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_metadata

from encoders import load_yolo_labels, read_image_bgr, record_for
from tl_config import CLASS_NAMES

SMALL_OBJECT_PX = 32
IMAGE_META_TYPES = {
    "width": DatasetMetadataType.int,
    "height": DatasetMetadataType.int,
    "aspect_ratio": DatasetMetadataType.float,
    "mean_brightness": DatasetMetadataType.float,
    "resolution": DatasetMetadataType.string,
}
GT_META_TYPES = {
    "num_boxes": DatasetMetadataType.int,
    "num_classes_present": DatasetMetadataType.int,
    "small_object_fraction": DatasetMetadataType.float,
    "mean_box_area_px": DatasetMetadataType.float,
    "dominant_class": DatasetMetadataType.string,
    **{f"count_{name}": DatasetMetadataType.int for name in CLASS_NAMES},
}


@tensorleap_metadata(name="image", metadata_type=IMAGE_META_TYPES)
def image_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, Union[int, float, str]]:
    img = read_image_bgr(record_for(sample_id, preprocess)["image_path"])
    h, w = img.shape[:2]
    return {
        "width": int(w),
        "height": int(h),
        "aspect_ratio": float(w / h),
        "mean_brightness": float(img.mean()),
        "resolution": f"{w}x{h}",
    }


@tensorleap_metadata(name="gt", metadata_type=GT_META_TYPES)
def gt_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, Union[int, float, str]]:
    record = record_for(sample_id, preprocess)
    h, w = read_image_bgr(record["image_path"]).shape[:2]
    labels = load_yolo_labels(record["label_path"])
    classes = labels[:, 0].astype(int)
    counts = np.bincount(classes, minlength=len(CLASS_NAMES)) if len(labels) else np.zeros(len(CLASS_NAMES), dtype=int)
    areas_px = labels[:, 3] * w * labels[:, 4] * h
    out: Dict[str, Union[int, float, str]] = {
        "num_boxes": int(len(labels)),
        "num_classes_present": int((counts > 0).sum()),
        "small_object_fraction": float((areas_px < SMALL_OBJECT_PX ** 2).mean()) if len(labels) else 0.0,
        "mean_box_area_px": float(areas_px.mean()) if len(labels) else 0.0,
        "dominant_class": CLASS_NAMES[int(counts.argmax())] if len(labels) else "none",
    }
    for name, count in zip(CLASS_NAMES, counts):
        out[f"count_{name}"] = int(count)
    return out
