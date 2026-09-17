from typing import List

import numpy as np

from code_loader.contract.enums import LeapDataType
from code_loader.contract.responsedataclasses import BoundingBox
from code_loader.contract.visualizer_classes import LeapImage, LeapImageWithBBox
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_visualizer

from preprocess import CLASS_NAMES, CONFIG

IMAGE_SIZE = int(CONFIG["image_size"])
CONF_THRES = float(CONFIG["conf_thres"])
IOU_THRES = float(CONFIG["iou_thres"])
MAX_DET = int(CONFIG["max_det"])


def _unbatch(x: np.ndarray, sample_ndim: int) -> np.ndarray:
    return x[0] if x.ndim == sample_ndim + 1 else x


def input_to_uint8_hwc(image: np.ndarray) -> np.ndarray:
    image = _unbatch(image, 3)  # (3, H, W) float in [0, 1]
    return (image.transpose(1, 2, 0) * 255.0).clip(0, 255).astype(np.uint8)


def decode_detections(detections: np.ndarray) -> np.ndarray:
    # detections: (N, 5 + nc) decoded YOLOv5 output (xywh pixels, obj, class probs) for ONE image.
    # Returns (n, 6) = x1, y1, x2, y2, conf, cls in model-frame pixels after NMS.
    import torch
    from utils.general import non_max_suppression

    pred = torch.from_numpy(np.ascontiguousarray(detections[None])).float()
    kept = non_max_suppression(pred, conf_thres=CONF_THRES, iou_thres=IOU_THRES, max_det=MAX_DET)[0]
    return kept.numpy()


def gt_to_bounding_boxes(boxes: np.ndarray) -> List[BoundingBox]:
    boxes = boxes[boxes[:, 4] >= 0]
    return [
        BoundingBox(x=float(cx), y=float(cy), width=float(w), height=float(h), confidence=1.0, label=CLASS_NAMES[int(c)])
        for cx, cy, w, h, c in boxes
    ]


def detections_to_bounding_boxes(kept: np.ndarray) -> List[BoundingBox]:
    result = []
    for x1, y1, x2, y2, conf, c in kept:
        result.append(
            BoundingBox(
                x=float((x1 + x2) / 2 / IMAGE_SIZE),
                y=float((y1 + y2) / 2 / IMAGE_SIZE),
                width=float((x2 - x1) / IMAGE_SIZE),
                height=float((y2 - y1) / IMAGE_SIZE),
                confidence=float(conf),
                label=CLASS_NAMES[int(c)],
            )
        )
    return result


@tensorleap_custom_visualizer(name="image", visualizer_type=LeapDataType.Image)
def image_visualizer(image: np.ndarray) -> LeapImage:
    return LeapImage(input_to_uint8_hwc(image))


@tensorleap_custom_visualizer(name="gt_boxes", visualizer_type=LeapDataType.ImageWithBBox)
def gt_boxes_visualizer(image: np.ndarray, boxes: np.ndarray) -> LeapImageWithBBox:
    return LeapImageWithBBox(input_to_uint8_hwc(image), gt_to_bounding_boxes(_unbatch(boxes, 2)))


@tensorleap_custom_visualizer(name="pred_boxes", visualizer_type=LeapDataType.ImageWithBBox)
def pred_boxes_visualizer(image: np.ndarray, detections: np.ndarray) -> LeapImageWithBBox:
    kept = decode_detections(_unbatch(detections, 2))
    return LeapImageWithBBox(input_to_uint8_hwc(image), detections_to_bounding_boxes(kept))
