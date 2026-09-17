import os
from typing import Dict, Tuple

import cv2
import numpy as np
from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_gt_encoder, tensorleap_input_encoder

from tl_config import IMAGE_SIZE, MAX_BOXES


def record_for(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, str]:
    return preprocess.data["records"][sample_id]


def read_image_bgr(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return img


def letterbox_geometry(h0: int, w0: int, size: int = IMAGE_SIZE) -> Tuple[float, float, float]:
    r = min(size / h0, size / w0)
    new_w, new_h = int(round(w0 * r)), int(round(h0 * r))
    pad_w = (size - new_w) / 2.0
    pad_h = (size - new_h) / 2.0
    return r, pad_w, pad_h


def letterbox(img: np.ndarray, size: int = IMAGE_SIZE) -> np.ndarray:
    h0, w0 = img.shape[:2]
    r, pad_w, pad_h = letterbox_geometry(h0, w0, size)
    resized = cv2.resize(img, (int(round(w0 * r)), int(round(h0 * r))), interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(pad_h - 0.1)), int(round(pad_h + 0.1))
    left, right = int(round(pad_w - 0.1)), int(round(pad_w + 0.1))
    return cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))


@tensorleap_input_encoder(name="image", channel_dim=1)
def image_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    img = letterbox(read_image_bgr(record_for(sample_id, preprocess)["image_path"]))
    img = img[:, :, ::-1].transpose(2, 0, 1)  # BGR->RGB, HWC->CHW
    return np.ascontiguousarray(img, dtype=np.float32) / 255.0


def load_yolo_labels(label_path: str) -> np.ndarray:
    rows = []
    if os.path.exists(label_path):
        with open(label_path, "r") as f:
            for line in f.read().strip().splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    rows.append([float(v) for v in parts[:5]])
    return np.asarray(rows, dtype=np.float32).reshape(-1, 5)


def labels_to_letterbox(labels: np.ndarray, h0: int, w0: int, size: int = IMAGE_SIZE) -> np.ndarray:
    r, pad_w, pad_h = letterbox_geometry(h0, w0, size)
    out = labels.copy()
    out[:, 1] = (labels[:, 1] * w0 * r + pad_w) / size
    out[:, 2] = (labels[:, 2] * h0 * r + pad_h) / size
    out[:, 3] = labels[:, 3] * w0 * r / size
    out[:, 4] = labels[:, 4] * h0 * r / size
    return out


@tensorleap_gt_encoder(name="boxes")
def boxes_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    record = record_for(sample_id, preprocess)
    h0, w0 = read_image_bgr(record["image_path"]).shape[:2]
    labels = labels_to_letterbox(load_yolo_labels(record["label_path"]), h0, w0)[:MAX_BOXES]
    gt = np.zeros((MAX_BOXES, 5), dtype=np.float32)
    gt[:, 0] = -1.0
    gt[: len(labels)] = labels
    return gt
