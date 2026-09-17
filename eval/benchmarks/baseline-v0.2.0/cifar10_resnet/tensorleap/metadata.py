from typing import Dict, Union

import numpy as np

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DatasetMetadataType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_metadata

from config import LABELS


@tensorleap_metadata(
    name="sample",
    metadata_type={
        "label": DatasetMetadataType.string,
        "label_index": DatasetMetadataType.int,
        "filename": DatasetMetadataType.string,
        "source_index": DatasetMetadataType.int,
    },
)
def sample_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, Union[str, int]]:
    idx = int(sample_id)
    label_index = int(preprocess.data["labels"][idx])
    return {
        "label": LABELS[label_index],
        "label_index": label_index,
        "filename": str(preprocess.data["filenames"][idx]),
        "source_index": int(preprocess.data["source_index"][idx]),
    }


@tensorleap_metadata(
    name="image_stats",
    metadata_type={
        "brightness": DatasetMetadataType.float,
        "contrast": DatasetMetadataType.float,
        "red_mean": DatasetMetadataType.float,
        "green_mean": DatasetMetadataType.float,
        "blue_mean": DatasetMetadataType.float,
    },
)
def image_stats(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, float]:
    image = preprocess.data["images"][int(sample_id)].astype(np.float32)
    return {
        "brightness": float(image.mean()),
        "contrast": float(image.std()),
        "red_mean": float(image[..., 0].mean()),
        "green_mean": float(image[..., 1].mean()),
        "blue_mean": float(image[..., 2].mean()),
    }
