import os
from typing import Optional

import cv2
import numpy as np

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DatasetMetadataType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_metadata

from encoders import IMAGE_SIZE, letterbox_geometry
from preprocess import CLASS_NAMES, CONFIG

SMALL_OBJECT_AREA = float(CONFIG["small_object_area"])
BORDER_MARGIN = 0.005  # box edge within 0.5% of the image edge -> likely truncated


def _record(sample_id: str, preprocess: PreprocessResponse) -> dict:
    return preprocess.data["records"][sample_id]


def _box_pixels(record: dict) -> np.ndarray:
    # labels (cls, cx, cy, w, h normalized) -> (n, 2) widths/heights in original pixels
    labels = record["labels"]
    return np.stack([labels[:, 3] * record["width"], labels[:, 4] * record["height"]], 1) if len(labels) else np.zeros((0, 2))


@tensorleap_metadata(
    "image",
    {
        "width": DatasetMetadataType.int,
        "height": DatasetMetadataType.int,
        "letterbox_scale": DatasetMetadataType.float,
        "mean_brightness": DatasetMetadataType.float,
    },
)
def image_metadata(sample_id: str, preprocess: PreprocessResponse) -> dict:
    record = _record(sample_id, preprocess)
    ratio, _, _ = letterbox_geometry(record["width"], record["height"])
    gray = cv2.imread(record["image_path"], cv2.IMREAD_GRAYSCALE)
    return {
        "width": record["width"],
        "height": record["height"],
        "letterbox_scale": float(ratio),
        "mean_brightness": float(gray.mean()),
    }


@tensorleap_metadata("sequence_id", DatasetMetadataType.string)
def sequence_metadata(sample_id: str, preprocess: PreprocessResponse) -> str:
    # VisDrone file names are <sequence>_<frame>_d_<index>; the sequence is the capture/scene.
    return os.path.basename(_record(sample_id, preprocess)["image_path"]).split("_")[0]


@tensorleap_metadata(
    "gt",
    {
        "num_boxes": DatasetMetadataType.int,
        "num_classes_present": DatasetMetadataType.int,
        "boxes_per_megapixel": DatasetMetadataType.float,
        "dominant_class": DatasetMetadataType.string,
    },
)
def gt_count_metadata(sample_id: str, preprocess: PreprocessResponse) -> dict:
    record = _record(sample_id, preprocess)
    labels = record["labels"]
    classes = labels[:, 0].astype(int)
    megapixels = record["width"] * record["height"] / 1e6
    return {
        "num_boxes": int(len(labels)),
        "num_classes_present": int(len(np.unique(classes))),
        "boxes_per_megapixel": float(len(labels) / megapixels),
        "dominant_class": CLASS_NAMES[int(np.bincount(classes).argmax())] if len(classes) else None,
    }


@tensorleap_metadata(
    "gt_size",
    {
        "mean_box_area_px": DatasetMetadataType.float,
        "min_box_area_px": DatasetMetadataType.float,
        "small_object_fraction": DatasetMetadataType.float,
        "mean_box_side_model_px": DatasetMetadataType.float,
    },
)
def gt_size_metadata(sample_id: str, preprocess: PreprocessResponse) -> dict:
    record = _record(sample_id, preprocess)
    wh = _box_pixels(record)
    if len(wh) == 0:
        return {"mean_box_area_px": None, "min_box_area_px": None, "small_object_fraction": None, "mean_box_side_model_px": None}
    areas = wh.prod(1)
    ratio, _, _ = letterbox_geometry(record["width"], record["height"])
    return {
        "mean_box_area_px": float(areas.mean()),
        "min_box_area_px": float(areas.min()),
        "small_object_fraction": float((areas < SMALL_OBJECT_AREA).mean()),
        "mean_box_side_model_px": float(np.sqrt(areas).mean() * ratio),
    }


@tensorleap_metadata("gt_border_fraction", DatasetMetadataType.float)
def gt_border_metadata(sample_id: str, preprocess: PreprocessResponse) -> Optional[float]:
    labels = _record(sample_id, preprocess)["labels"]
    if len(labels) == 0:
        return None
    x1 = labels[:, 1] - labels[:, 3] / 2
    y1 = labels[:, 2] - labels[:, 4] / 2
    x2 = labels[:, 1] + labels[:, 3] / 2
    y2 = labels[:, 2] + labels[:, 4] / 2
    at_border = (x1 <= BORDER_MARGIN) | (y1 <= BORDER_MARGIN) | (x2 >= 1 - BORDER_MARGIN) | (y2 >= 1 - BORDER_MARGIN)
    return float(at_border.mean())


@tensorleap_metadata("gt_class_count", {name: DatasetMetadataType.int for name in CLASS_NAMES})
def gt_class_count_metadata(sample_id: str, preprocess: PreprocessResponse) -> dict:
    classes = _record(sample_id, preprocess)["labels"][:, 0].astype(int)
    counts = np.bincount(classes, minlength=len(CLASS_NAMES))
    return {name: int(counts[i]) for i, name in enumerate(CLASS_NAMES)}
