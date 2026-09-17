import os
from typing import Dict, List

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_preprocess

from tl_config import CONFIG, DATA_ROOT, DATASET

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp")


def _list_split(split_key: str) -> Dict[str, Dict[str, str]]:
    images_dir = os.path.join(DATA_ROOT, DATASET[split_key])
    labels_dir = os.path.join(os.path.dirname(images_dir), CONFIG["labels_dirname"])
    records = {}
    for fname in sorted(os.listdir(images_dir)):
        stem, ext = os.path.splitext(fname)
        if ext.lower() not in IMAGE_EXTS:
            continue
        records[stem] = {
            "image_path": os.path.join(images_dir, fname),
            "label_path": os.path.join(labels_dir, stem + ".txt"),
        }
    return records


@tensorleap_preprocess()
def preprocess() -> List[PreprocessResponse]:
    limit = CONFIG.get("sample_limit_per_split")
    responses = []
    for state_name, split_key in CONFIG["splits"].items():
        records = _list_split(split_key)
        sample_ids = sorted(records.keys())
        if limit:
            sample_ids = sample_ids[: int(limit)]
        responses.append(
            PreprocessResponse(
                sample_ids=sample_ids,
                data={"records": records, "split": split_key},
                state=DataStateType[state_name],
            )
        )
    return responses
