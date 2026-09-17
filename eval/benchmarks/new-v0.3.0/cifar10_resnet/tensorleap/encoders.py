import numpy as np
from scipy.ndimage import zoom

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_input_encoder, tensorleap_gt_encoder

from config import CONFIG, LABELS


def get_image(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    return preprocess.data["images"][preprocess.data["pos"][sample_id]]


def get_label(sample_id: str, preprocess: PreprocessResponse) -> int:
    return int(preprocess.data["labels"][preprocess.data["pos"][sample_id]])


@tensorleap_input_encoder(name="image", channel_dim=-1)
def image_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    f = CONFIG["upscale_factor"]
    resized = zoom(get_image(sample_id, preprocess), (f, f, 1)) / 255.0
    return resized.astype(np.float32)


@tensorleap_gt_encoder(name="classes")
def class_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    one_hot = np.zeros(len(LABELS), dtype=np.float32)
    one_hot[get_label(sample_id, preprocess)] = 1.0
    return one_hot
