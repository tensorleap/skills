import json
from typing import Any, Dict, List

import numpy as np
from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import (
    tensorleap_preprocess,
    tensorleap_unlabeled_preprocess,
)

from config import CONFIG, repo_path


def _load_labeled_recipe(split: str) -> List[Dict[str, Any]]:
    with open(repo_path(f"{CONFIG['labeled_splits_dir']}/{split}.json"), "r") as f:
        recipe = json.load(f)
    records = []
    for i in range(recipe["real_size"]):
        records.append({
            "image_path": recipe["image_path"][i],
            "gt_path": recipe["gt_path"][i],
            "gt_image_path": recipe["gt_image_path"][i],
            "metadata_path": recipe["metadata"][i],
            "file_name": recipe["file_names"][i],
            "city": recipe["cities"][i],
            "dataset": recipe["dataset"][i],
            "subset": recipe["subset_name"],
            "labeled": True,
        })
    return records


def _load_unlabeled_recipe() -> List[Dict[str, Any]]:
    with open(repo_path(CONFIG["unlabeled_manifest"]), "r") as f:
        manifest = json.load(f)
    records = []
    for entry in manifest:
        records.append({
            "image_path": entry["path"],
            "gt_path": "",
            "gt_image_path": "",
            "metadata_path": "",
            "file_name": entry["filename"],
            "city": entry["city"],
            "dataset": entry["dataset"],
            "subset": "unlabeled",
            "labeled": False,
            "vegetation_percent": entry.get("vegetation_percent"),
            "selection_reason": entry.get("selection_reason", ""),
            "drive": entry.get("drive", ""),
        })
    return records


def _cap(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    limit = CONFIG.get("sample_limit_per_split") or 0
    if limit <= 0 or len(records) <= limit:
        return records
    order = np.random.RandomState(CONFIG["seed"]).permutation(len(records))[:limit]
    return [records[i] for i in sorted(order)]


def _response(records: List[Dict[str, Any]], prefix: str, state: DataStateType) -> PreprocessResponse:
    # The csv-derived recipes repeat some images, so the row index is the unique id.
    sample_ids = [f"{prefix}_{i:04d}" for i in range(len(records))]
    data = {"records": dict(zip(sample_ids, records))}
    return PreprocessResponse(sample_ids=sample_ids, data=data, state=state)


@tensorleap_preprocess()
def preprocess() -> List[PreprocessResponse]:
    train = _response(_cap(_load_labeled_recipe("train")), "train", DataStateType.training)
    val = _response(_cap(_load_labeled_recipe("val")), "val", DataStateType.validation)
    return [train, val]


@tensorleap_unlabeled_preprocess()
def unlabeled_preprocess() -> PreprocessResponse:
    return _response(_cap(_load_unlabeled_recipe()), "unlabeled", DataStateType.unlabeled)
