from typing import List

import numpy as np
from code_loader.contract.enums import LeapDataType
from code_loader.contract.visualizer_classes import BoundingBox, LeapImage, LeapImageWithBBox
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_visualizer

from detections import decode_detections
from tl_config import CLASS_NAMES


def _unbatch(x: np.ndarray, sample_ndim: int) -> np.ndarray:
    return x[0] if x.ndim == sample_ndim + 1 else x


def _to_display(image: np.ndarray) -> np.ndarray:
    image = _unbatch(image, 3)  # CHW float [0,1]
    return (image.transpose(1, 2, 0) * 255.0).clip(0, 255).astype(np.uint8)


def _gt_bboxes(gt: np.ndarray) -> List[BoundingBox]:
    gt = _unbatch(gt, 2)
    boxes = []
    for row in gt[gt[:, 0] >= 0]:
        boxes.append(BoundingBox(x=float(row[1]), y=float(row[2]), width=float(row[3]), height=float(row[4]),
                                 confidence=1.0, label=CLASS_NAMES[int(row[0])]))
    return boxes


@tensorleap_custom_visualizer(name="image", visualizer_type=LeapDataType.Image)
def image_visualizer(image: np.ndarray) -> LeapImage:
    return LeapImage(_to_display(image))


@tensorleap_custom_visualizer(name="gt_boxes", visualizer_type=LeapDataType.ImageWithBBox)
def gt_boxes_visualizer(image: np.ndarray, boxes: np.ndarray) -> LeapImageWithBBox:
    return LeapImageWithBBox(_to_display(image), _gt_bboxes(boxes))


@tensorleap_custom_visualizer(name="predicted_boxes", visualizer_type=LeapDataType.ImageWithBBox)
def pred_boxes_visualizer(image: np.ndarray, detections: np.ndarray) -> LeapImageWithBBox:
    xyxy, scores, classes = decode_detections(_unbatch(detections, 2))
    bboxes = []
    for (x1, y1, x2, y2), s, c in zip(xyxy, scores, classes):
        bboxes.append(BoundingBox(x=float((x1 + x2) / 2), y=float((y1 + y2) / 2), width=float(x2 - x1),
                                  height=float(y2 - y1), confidence=float(s), label=CLASS_NAMES[int(c)]))
    return LeapImageWithBBox(_to_display(image), bboxes)
