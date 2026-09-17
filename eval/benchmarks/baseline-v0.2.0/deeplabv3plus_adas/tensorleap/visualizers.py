import numpy as np
from code_loader.contract.enums import LeapDataType
from code_loader.contract.visualizer_classes import LeapImage, LeapImageMask
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_visualizer

from cityscapes_labels import IGNORE_TRAIN_ID, MASK_LABELS
from config import CONFIG
from encoders import denormalize

STEP = int(CONFIG["viz_downscale"])


def _unbatch(x: np.ndarray, sample_ndim: int) -> np.ndarray:
    return x[0] if x.ndim == sample_ndim + 1 else x


def _display_image(normalized_image: np.ndarray) -> np.ndarray:
    return denormalize(_unbatch(normalized_image, 3))[::STEP, ::STEP]


@tensorleap_custom_visualizer(name="image", visualizer_type=LeapDataType.Image)
def image_visualizer(normalized_image: np.ndarray) -> LeapImage:
    return LeapImage(_display_image(normalized_image))


@tensorleap_custom_visualizer(name="gt_mask", visualizer_type=LeapDataType.ImageMask)
def gt_mask_visualizer(normalized_image: np.ndarray, gt: np.ndarray) -> LeapImageMask:
    image = _display_image(normalized_image)
    if gt.size == 0:
        mask = np.full(image.shape[:2], IGNORE_TRAIN_ID, dtype=np.uint8)
    else:
        mask = np.rint(_unbatch(gt, 2)[::STEP, ::STEP]).astype(np.uint8)
    return LeapImageMask(mask=mask, image=image, labels=MASK_LABELS)


@tensorleap_custom_visualizer(name="prediction_mask", visualizer_type=LeapDataType.ImageMask)
def prediction_mask_visualizer(normalized_image: np.ndarray, prediction: np.ndarray) -> LeapImageMask:
    image = _display_image(normalized_image)
    mask = _unbatch(prediction, 3)[::STEP, ::STEP].argmax(axis=-1).astype(np.uint8)
    return LeapImageMask(mask=mask, image=image, labels=MASK_LABELS)
