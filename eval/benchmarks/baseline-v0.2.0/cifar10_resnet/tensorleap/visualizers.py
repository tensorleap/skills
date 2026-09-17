import numpy as np

from code_loader.contract.enums import LeapDataType
from code_loader.contract.visualizer_classes import LeapImage, LeapHorizontalBar
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_visualizer

from config import LABELS


@tensorleap_custom_visualizer(name="image_visualizer", visualizer_type=LeapDataType.Image)
def image_visualizer(image: np.ndarray) -> LeapImage:
    if image.ndim == 4:
        image = image[0]
    return LeapImage((np.clip(image, 0.0, 1.0) * 255).astype(np.uint8))


@tensorleap_custom_visualizer(name="gt_visualizer", visualizer_type=LeapDataType.HorizontalBar)
def gt_visualizer(ground_truth: np.ndarray) -> LeapHorizontalBar:
    if ground_truth.ndim == 2:
        ground_truth = ground_truth[0]
    return LeapHorizontalBar(ground_truth.astype(np.float32), LABELS)


@tensorleap_custom_visualizer(name="prediction_visualizer", visualizer_type=LeapDataType.HorizontalBar)
def prediction_visualizer(prediction: np.ndarray) -> LeapHorizontalBar:
    if prediction.ndim == 2:
        prediction = prediction[0]
    return LeapHorizontalBar(prediction.astype(np.float32), LABELS)
