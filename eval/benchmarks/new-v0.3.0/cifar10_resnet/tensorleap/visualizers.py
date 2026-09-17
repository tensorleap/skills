import numpy as np

from code_loader.contract.enums import LeapDataType
from code_loader.contract.visualizer_classes import LeapImage, LeapHorizontalBar
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_visualizer

from config import LABELS


def _unbatch(x: np.ndarray, sample_ndim: int) -> np.ndarray:
    return x[0] if x.ndim == sample_ndim + 1 else x


@tensorleap_custom_visualizer("image_visualizer", LeapDataType.Image)
def image_visualizer(image: np.ndarray) -> LeapImage:
    img = _unbatch(image, 3)
    return LeapImage((img * 255.0).clip(0, 255).astype(np.uint8))


@tensorleap_custom_visualizer("prediction_bar", LeapDataType.HorizontalBar)
def prediction_bar(prediction: np.ndarray, ground_truth: np.ndarray) -> LeapHorizontalBar:
    return LeapHorizontalBar(body=_unbatch(prediction, 1).astype(np.float32), labels=LABELS,
                             gt=_unbatch(ground_truth, 1).astype(np.float32))


@tensorleap_custom_visualizer("gt_bar", LeapDataType.HorizontalBar)
def gt_bar(ground_truth: np.ndarray) -> LeapHorizontalBar:
    return LeapHorizontalBar(body=_unbatch(ground_truth, 1).astype(np.float32), labels=LABELS)
