import os
from typing import List

import numpy as np
from sklearn.model_selection import train_test_split

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_preprocess

from config import CONFIG, CIFAR_DIR, DATA_ROOT


def _ensure_cifar_on_volume() -> str:
    if os.path.isfile(os.path.join(CIFAR_DIR, "data_batch_1")):
        return CIFAR_DIR
    from keras.utils import get_file
    return get_file(fname=CONFIG["cifar_dirname"], origin=CONFIG["cifar_origin"], untar=True,
                    cache_dir=DATA_ROOT, cache_subdir=".")


def _load_train_batches(cifar_dir: str):
    from keras.datasets.cifar import load_batch
    images, labels, batch_idx = [], [], []
    for i in range(1, 6):
        x, y = load_batch(os.path.join(cifar_dir, f"data_batch_{i}"))
        images.append(x)
        labels.append(np.asarray(y, dtype=np.int64))
        batch_idx.append(np.full(len(y), i, dtype=np.int64))
    images = np.concatenate(images).transpose(0, 2, 3, 1)
    return images, np.concatenate(labels), np.concatenate(batch_idx)


@tensorleap_preprocess()
def preprocess() -> List[PreprocessResponse]:
    cifar_dir = _ensure_cifar_on_volume()
    images, labels, batch_idx = _load_train_batches(cifar_dir)
    indices = np.arange(len(labels))
    train_idx, val_idx = train_test_split(indices, test_size=CONFIG["val_fraction"],
                                          random_state=CONFIG["split_seed"])
    limit = CONFIG.get("sample_limit_per_split")
    if limit:
        train_idx = train_idx[:limit]
        val_idx = val_idx[:limit]

    def subset(idx: np.ndarray, state: DataStateType) -> PreprocessResponse:
        return PreprocessResponse(
            sample_ids=[str(i) for i in idx],
            data={"images": images[idx], "labels": labels[idx], "batch_idx": batch_idx[idx],
                  "pos": {str(i): k for k, i in enumerate(idx)}},
            state=state,
        )

    return [subset(train_idx, DataStateType.training), subset(val_idx, DataStateType.validation)]
