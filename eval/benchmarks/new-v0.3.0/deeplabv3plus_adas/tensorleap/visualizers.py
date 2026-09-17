import numpy as np
from code_loader.contract.enums import LeapDataType
from code_loader.contract.visualizer_classes import LeapImage, LeapImageMask, LeapHorizontalBar
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_visualizer

from domain_gap.data.cs_data import CATEGORIES
from encoders import IMAGE_MEAN, IMAGE_STD, IGNORE_LABEL
from preprocess import TL_CONFIG

DOWNSCALE = int(TL_CONFIG.get("viz_downscale", 1))
MASK_LABELS = list(CATEGORIES) + ["void"]


def _unbatch(x: np.ndarray, sample_ndim: int) -> np.ndarray:
    return x[0] if x.ndim == sample_ndim + 1 else x


def _downscale(x: np.ndarray) -> np.ndarray:
    if DOWNSCALE <= 1:
        return x
    return x[::DOWNSCALE, ::DOWNSCALE]


def _denormalize(normalized_image: np.ndarray) -> np.ndarray:
    img = _unbatch(normalized_image, 3)
    img = (img * IMAGE_STD + IMAGE_MEAN) * 255.0
    return _downscale(np.clip(img, 0, 255).astype(np.uint8))


def _gt_labels(segmentation_mask: np.ndarray) -> np.ndarray:
    mask = _unbatch(segmentation_mask, 3)[..., 0]
    return _downscale(mask.astype(np.uint8))


def _pred_labels(prediction: np.ndarray) -> np.ndarray:
    logits = _unbatch(prediction, 3)
    return _downscale(logits.argmax(axis=-1).astype(np.uint8))


@tensorleap_custom_visualizer(name="image", visualizer_type=LeapDataType.Image)
def image_visualizer(normalized_image: np.ndarray) -> LeapImage:
    return LeapImage(data=_denormalize(normalized_image))


@tensorleap_custom_visualizer(name="gt_mask", visualizer_type=LeapDataType.ImageMask)
def gt_mask_visualizer(normalized_image: np.ndarray, segmentation_mask: np.ndarray) -> LeapImageMask:
    return LeapImageMask(mask=_gt_labels(segmentation_mask), image=_denormalize(normalized_image), labels=MASK_LABELS)


@tensorleap_custom_visualizer(name="prediction_mask", visualizer_type=LeapDataType.ImageMask)
def prediction_mask_visualizer(normalized_image: np.ndarray, prediction: np.ndarray) -> LeapImageMask:
    return LeapImageMask(mask=_pred_labels(prediction), image=_denormalize(normalized_image), labels=MASK_LABELS)


@tensorleap_custom_visualizer(name="predicted_class_distribution", visualizer_type=LeapDataType.HorizontalBar)
def predicted_class_distribution(prediction: np.ndarray) -> LeapHorizontalBar:
    pred = _unbatch(prediction, 3).argmax(axis=-1)
    counts = np.bincount(pred.ravel(), minlength=len(CATEGORIES))[: len(CATEGORIES)]
    fractions = (counts / max(pred.size, 1)).astype(np.float32)
    return LeapHorizontalBar(body=fractions, labels=list(CATEGORIES))
