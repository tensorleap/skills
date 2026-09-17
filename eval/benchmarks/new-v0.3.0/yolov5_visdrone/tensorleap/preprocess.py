import os
from typing import Dict, List

import numpy as np
import yaml
from PIL import Image

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_preprocess

TL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TL_DIR)

with open(os.path.join(TL_DIR, "project_config.yaml")) as f:
    CONFIG = yaml.safe_load(f)

with open(os.path.join(REPO_ROOT, CONFIG["dataset_yaml"])) as f:
    DATASET_YAML = yaml.safe_load(f)

CLASS_NAMES: List[str] = list(DATASET_YAML["names"])
DATA_ROOT: str = CONFIG["data_root"]

STATE_BY_NAME = {
    "training": DataStateType.training,
    "validation": DataStateType.validation,
    "test": DataStateType.test,
}


def _label_path(image_path: str) -> str:
    base, _ = os.path.splitext(image_path)
    return base.replace(f"{os.sep}images{os.sep}", f"{os.sep}labels{os.sep}") + ".txt"


def _read_labels(label_path: str) -> np.ndarray:
    if not os.path.exists(label_path):
        return np.zeros((0, 5), dtype=np.float32)
    rows = [l.split() for l in open(label_path).read().strip().splitlines() if l.strip()]
    if not rows:
        return np.zeros((0, 5), dtype=np.float32)
    return np.array(rows, dtype=np.float32).reshape(-1, 5)


def _split_records(split_key: str) -> Dict[str, dict]:
    images_dir = os.path.join(DATA_ROOT, DATASET_YAML[split_key])
    files = sorted(f for f in os.listdir(images_dir) if f.lower().endswith((".jpg", ".jpeg", ".png")))
    records = {}
    for fname in files:
        image_path = os.path.join(images_dir, fname)
        width, height = Image.open(image_path).size
        records[os.path.splitext(fname)[0]] = {
            "image_path": image_path,
            "width": int(width),
            "height": int(height),
            "labels": _read_labels(_label_path(image_path)),
            "split": split_key,
        }
    return records


@tensorleap_preprocess()
def preprocess() -> List[PreprocessResponse]:
    limit = CONFIG.get("sample_limit_per_split")
    responses = []
    for state_name, split_key in CONFIG["splits"].items():
        records = _split_records(split_key)
        sample_ids = sorted(records)
        if limit:
            sample_ids = sample_ids[: int(limit)]
        records = {sid: records[sid] for sid in sample_ids}
        responses.append(
            PreprocessResponse(
                sample_ids=sample_ids,
                data={"records": records, "class_names": CLASS_NAMES, "split": split_key},
                state=STATE_BY_NAME[state_name],
            )
        )
    return responses
