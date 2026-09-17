import json
import os
from typing import Any, Dict, List

import yaml
from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_preprocess

from domain_gap.utils.config import CONFIG as REPO_CONFIG

TL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TL_DIR)

with open(os.path.join(TL_DIR, "project_config.yaml"), "r") as f:
    TL_CONFIG: Dict[str, Any] = yaml.safe_load(f)

DATA_ROOT = REPO_CONFIG["LOCAL_BASE_PATH"]

STATES = {
    "training": DataStateType.training,
    "validation": DataStateType.validation,
    "unlabeled": DataStateType.unlabeled,
}


def _split_file(state_name: str) -> str:
    return os.path.join(REPO_ROOT, TL_CONFIG["splits"][state_name])


def _records_from_recipe(state_name: str) -> Dict[str, Dict[str, Any]]:
    with open(_split_file(state_name), "r") as f:
        recipe = json.load(f)
    records: Dict[str, Dict[str, Any]] = {}
    for i in range(recipe["real_size"]):
        image_path = recipe["image_path"][i]
        if image_path in records:
            continue
        records[image_path] = {
            "image_path": image_path,
            "gt_path": recipe["gt_path"][i],
            "metadata_path": recipe["metadata"][i],
            "file_name": recipe["file_names"][i],
            "city": recipe["cities"][i],
            "dataset": recipe["dataset"][i],
            "subset_name": recipe["subset_name"],
            "vegetation_percent": None,
            "selection_reason": None,
            "drive": None,
        }
    return records


def _records_from_manifest(state_name: str) -> Dict[str, Dict[str, Any]]:
    with open(_split_file(state_name), "r") as f:
        manifest = json.load(f)
    records: Dict[str, Dict[str, Any]] = {}
    for entry in manifest:
        image_path = entry["path"]
        if image_path in records:
            continue
        records[image_path] = {
            "image_path": image_path,
            "gt_path": "",
            "metadata_path": "",
            "file_name": entry["filename"],
            "city": entry["city"],
            "dataset": entry["dataset"],
            "subset_name": entry.get("split") or "raw",
            "vegetation_percent": entry.get("vegetation_percent"),
            "selection_reason": entry.get("selection_reason"),
            "drive": entry.get("drive"),
        }
    return records


def _attach_vehicle_metadata(records: Dict[str, Dict[str, Any]]) -> None:
    for rec in records.values():
        rec["vehicle"] = None
        if not rec["metadata_path"]:
            continue
        path = os.path.join(DATA_ROOT, rec["metadata_path"])
        if not os.path.exists(path):
            continue
        with open(path, "r") as f:
            rec["vehicle"] = json.load(f)


def _capped(records: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    limit = TL_CONFIG.get("sample_limit_per_split")
    if not limit:
        return records
    ids = list(records.keys())[:limit]
    return {k: records[k] for k in ids}


def _response(state_name: str) -> PreprocessResponse:
    if state_name == "unlabeled":
        records = _records_from_manifest(state_name)
    else:
        records = _records_from_recipe(state_name)
    records = _capped(records)
    _attach_vehicle_metadata(records)
    return PreprocessResponse(
        sample_ids=list(records.keys()),
        data={"records": records, "data_root": DATA_ROOT},
        state=STATES[state_name],
    )


@tensorleap_preprocess()
def preprocess() -> List[PreprocessResponse]:
    return [_response("training"), _response("validation"), _response("unlabeled")]
