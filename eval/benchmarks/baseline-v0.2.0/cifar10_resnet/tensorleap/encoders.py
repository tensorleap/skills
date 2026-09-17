import numpy as np
from scipy.ndimage import zoom

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.inner_leap_binder.leapbinder_decorators import (
    tensorleap_input_encoder,
    tensorleap_gt_encoder,
)

from config import CONFIG, LABELS


@tensorleap_input_encoder(name="image", channel_dim=-1)
def image_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    image = preprocess.data["images"][int(sample_id)]
    factor = CONFIG["upscale_factor"]
    return (zoom(image, (factor, factor, 1)) / 255.0).astype(np.float32)


@tensorleap_gt_encoder(name="classes")
def class_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    label = int(preprocess.data["labels"][int(sample_id)])
    one_hot = np.zeros(len(LABELS), dtype=np.float32)
    one_hot[label] = 1.0
    return one_hot
