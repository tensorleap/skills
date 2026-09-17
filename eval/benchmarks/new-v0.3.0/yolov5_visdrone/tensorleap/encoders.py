from typing import Tuple

import cv2
import numpy as np

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_gt_encoder, tensorleap_input_encoder

from preprocess import CONFIG
from utils.augmentations import letterbox

IMAGE_SIZE = int(CONFIG["image_size"])
MAX_BOXES = int(CONFIG["max_boxes"])


def letterbox_geometry(width: int, height: int) -> Tuple[float, float, float]:
    # Same arithmetic as utils.augmentations.letterbox(auto=False, scaleup=True).
    ratio = min(IMAGE_SIZE / height, IMAGE_SIZE / width)
    new_w, new_h = round(width * ratio), round(height * ratio)
    dw, dh = (IMAGE_SIZE - new_w) / 2, (IMAGE_SIZE - new_h) / 2
    return ratio, dw, dh


def load_letterboxed_rgb(image_path: str) -> np.ndarray:
    im0 = cv2.imread(image_path)
    im, _, _ = letterbox(im0, (IMAGE_SIZE, IMAGE_SIZE), auto=False)
    return np.ascontiguousarray(im[:, :, ::-1])


def gt_boxes_letterboxed(record: dict) -> np.ndarray:
    # (cls, cx, cy, w, h) normalized to the original image -> (cx, cy, w, h, cls) normalized
    # to the letterboxed IMAGE_SIZE x IMAGE_SIZE model frame.
    labels = record["labels"]
    ratio, dw, dh = letterbox_geometry(record["width"], record["height"])
    out = np.zeros((len(labels), 5), dtype=np.float32)
    if len(labels):
        out[:, 0] = (labels[:, 1] * record["width"] * ratio + dw) / IMAGE_SIZE
        out[:, 1] = (labels[:, 2] * record["height"] * ratio + dh) / IMAGE_SIZE
        out[:, 2] = labels[:, 3] * record["width"] * ratio / IMAGE_SIZE
        out[:, 3] = labels[:, 4] * record["height"] * ratio / IMAGE_SIZE
        out[:, 4] = labels[:, 0]
    return out


@tensorleap_input_encoder(name="image", channel_dim=1)
def image_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    record = preprocess.data["records"][sample_id]
    rgb = load_letterboxed_rgb(record["image_path"])
    return (rgb.transpose(2, 0, 1).astype(np.float32) / 255.0).astype(np.float32)


@tensorleap_gt_encoder(name="boxes")
def boxes_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    record = preprocess.data["records"][sample_id]
    boxes = gt_boxes_letterboxed(record)[:MAX_BOXES]
    padded = np.zeros((MAX_BOXES, 5), dtype=np.float32)
    padded[:, 4] = -1.0
    padded[: len(boxes)] = boxes
    return padded
