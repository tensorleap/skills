import numpy as np
from PIL import Image
from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.inner_leap_binder.leapbinder_decorators import (
    tensorleap_gt_encoder,
    tensorleap_input_encoder,
)

from cityscapes_labels import encode_label_ids
from config import CONFIG, data_path

IMAGE_W, IMAGE_H = CONFIG["image_size"]
IMAGE_MEAN = np.array(CONFIG["image_mean"], dtype=np.float32)
IMAGE_STD = np.array(CONFIG["image_std"], dtype=np.float32)


def get_record(sample_id: str, preprocess: PreprocessResponse) -> dict:
    return preprocess.data["records"][sample_id]


def load_rgb(rel_path: str) -> np.ndarray:
    image = Image.open(data_path(rel_path)).convert("RGB")
    if image.size != (IMAGE_W, IMAGE_H):
        image = image.resize((IMAGE_W, IMAGE_H), Image.BILINEAR)
    return np.asarray(image, dtype=np.uint8)


def load_train_id_mask(rel_path: str) -> np.ndarray:
    label_ids = Image.open(data_path(rel_path))
    if label_ids.size != (IMAGE_W, IMAGE_H):
        label_ids = label_ids.resize((IMAGE_W, IMAGE_H), Image.NEAREST)
    return encode_label_ids(np.asarray(label_ids))


def normalize(rgb_uint8: np.ndarray) -> np.ndarray:
    return ((rgb_uint8.astype(np.float32) / 255.0 - IMAGE_MEAN) / IMAGE_STD).astype(np.float32)


def denormalize(normalized: np.ndarray) -> np.ndarray:
    rgb = (normalized * IMAGE_STD + IMAGE_MEAN) * 255.0
    return np.clip(rgb, 0, 255).astype(np.uint8)


@tensorleap_input_encoder(name="normalized_image", channel_dim=-1)
def image_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    record = get_record(sample_id, preprocess)
    return normalize(load_rgb(record["image_path"]))


@tensorleap_gt_encoder(name="segmentation_mask")
def mask_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    record = get_record(sample_id, preprocess)
    if not record["gt_path"]:
        return np.array([], dtype=np.float32)
    return load_train_id_mask(record["gt_path"]).astype(np.float32)
