import os

import numpy as np
from PIL import Image
from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_input_encoder, tensorleap_gt_encoder

from domain_gap.data.cs_data import Cityscapes
from domain_gap.utils.config import CONFIG as REPO_CONFIG
from preprocess import TL_CONFIG

IMAGE_WIDTH, IMAGE_HEIGHT = int(REPO_CONFIG["IMAGE_SIZE"][0]), int(REPO_CONFIG["IMAGE_SIZE"][1])
IMAGE_MEAN = REPO_CONFIG["IMAGE_MEAN"].astype(np.float32)
IMAGE_STD = REPO_CONFIG["IMAGE_STD"].astype(np.float32)
IGNORE_LABEL = int(TL_CONFIG["ignore_label"])
MAX_RAW_ID = len(Cityscapes.id_to_train_id) - 1


def record(sample_id: str, preprocess: PreprocessResponse) -> dict:
    return preprocess.data["records"][sample_id]


def abs_path(rel_path: str, preprocess: PreprocessResponse) -> str:
    return os.path.join(preprocess.data["data_root"], rel_path)


def load_rgb_uint8(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    img = Image.open(abs_path(record(sample_id, preprocess)["image_path"], preprocess)).convert("RGB")
    img = img.resize((IMAGE_WIDTH, IMAGE_HEIGHT), Image.BILINEAR)
    return np.asarray(img, dtype=np.uint8)


def load_train_id_mask(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    rec = record(sample_id, preprocess)
    mask = Image.open(abs_path(rec["gt_path"], preprocess)).convert("L")
    mask = mask.resize((IMAGE_WIDTH, IMAGE_HEIGHT), Image.NEAREST)
    raw_ids = np.clip(np.asarray(mask, dtype=np.int64), 0, MAX_RAW_ID)
    train_ids = Cityscapes.id_to_train_id[raw_ids]
    return train_ids.astype(np.uint8)


@tensorleap_input_encoder(name="normalized_image", channel_dim=-1)
def image_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    img = load_rgb_uint8(sample_id, preprocess).astype(np.float32) / 255.0
    return ((img - IMAGE_MEAN) / IMAGE_STD).astype(np.float32)


@tensorleap_gt_encoder(name="segmentation_mask")
def mask_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    rec = record(sample_id, preprocess)
    if not rec["gt_path"]:
        return np.array([], dtype=np.float32)
    return load_train_id_mask(sample_id, preprocess)[..., None].astype(np.float32)
