import os
import pickle
from typing import List

import numpy as np
from sklearn.model_selection import train_test_split

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_preprocess

from config import CONFIG


def _load_batch(path: str):
    with open(path, "rb") as f:
        batch = pickle.load(f, encoding="bytes")
    size = CONFIG["image_size"]
    images = batch[b"data"].reshape(-1, 3, size, size).transpose(0, 2, 3, 1)
    labels = np.asarray(batch[b"labels"], dtype=np.int64)
    filenames = [name.decode("utf-8") for name in batch[b"filenames"]]
    return images, labels, filenames


def _load_all():
    images, labels, filenames = [], [], []
    for name in CONFIG["train_batches"]:
        x, y, f = _load_batch(os.path.join(CONFIG["data_root"], name))
        images.append(x)
        labels.append(y)
        filenames.extend(f)
    return np.concatenate(images), np.concatenate(labels), np.asarray(filenames)


def _subset(indices: np.ndarray, images, labels, filenames, state: DataStateType) -> PreprocessResponse:
    limit = CONFIG.get("sample_limit_per_split")
    if limit:
        indices = indices[:limit]
    data = {
        "images": images[indices],
        "labels": labels[indices],
        "filenames": filenames[indices],
        "source_index": indices,
    }
    return PreprocessResponse(sample_ids=[str(i) for i in range(len(indices))], data=data, state=state)


@tensorleap_preprocess()
def preprocess() -> List[PreprocessResponse]:
    images, labels, filenames = _load_all()
    all_indices = np.arange(len(labels))
    train_idx, val_idx = train_test_split(
        all_indices, test_size=CONFIG["validation_fraction"], random_state=CONFIG["split_seed"]
    )
    return [
        _subset(train_idx, images, labels, filenames, DataStateType.training),
        _subset(val_idx, images, labels, filenames, DataStateType.validation),
    ]
