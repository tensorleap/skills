import os
import sys
from typing import Dict, List

import numpy as np

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_preprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from infineon.config import CONFIG  # noqa: E402
from infineon.wirebonsdataset_page import WirebondDataset  # noqa: E402

TL_CONFIG = CONFIG.get("tensorleap", {})
SURFACE_CODE_TO_NAME = {v["code"]: k for k, v in CONFIG["surface_types"].items()}
SURFACE_CODE_TO_FACTOR = {v["code"]: v["normalization_factor"] for v in CONFIG["surface_types"].values()}

SPLITS = [
    ("train", DataStateType.training, dict(train=True, state="labeled")),
    ("val", DataStateType.validation, dict(train=False, state="labeled")),
    ("unlabeled", DataStateType.unlabeled, dict(train=True, state="unlabeled")),
]
SIDECAR_GROUPS = ("latent_space", "discriminator_output", "reconstruction_output")


def _load_sidecars(split: str) -> Dict[str, np.ndarray]:
    lso = CONFIG["latent_space_and_outputs"]
    out_dir = lso["output_dir"]
    arrays = {}
    for group in SIDECAR_GROUPS:
        path = os.path.join(out_dir, lso[group][f"{split}_filename"])
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Sidecar {path} is missing. Generate it with `poetry run python tensorleap/generate_sidecars.py` "
                f"(filenames depend on the sha of project_config.yaml, so re-generate after editing the config)."
            )
        arrays[group] = np.load(path)
    return arrays


def _build_split(split: str, state: DataStateType, kw: dict) -> PreprocessResponse:
    ds = WirebondDataset(
        CONFIG["data"]["data_path"],
        max_seq_len=CONFIG["data"]["max_seq_len"],
        test_size=CONFIG["split"]["test_size"],
        random_state=CONFIG["split"]["random_state"],
        **kw,
    )
    sidecars = _load_sidecars(split)
    for group, arr in sidecars.items():
        if len(arr) != len(ds):
            raise ValueError(f"{split} sidecar {group} has {len(arr)} rows but the dataset has {len(ds)}; re-generate sidecars.")
    limit = TL_CONFIG.get("sample_limit_per_split")
    sample_ids = ds.df_idx[:limit] if limit else ds.df_idx
    data = {
        "split": split,
        "current_trace": ds.current_trace.numpy().astype(np.float32),
        "deformation_trace": ds.deformation_trace.numpy().astype(np.float32),
        "raw_current_interp": ds.raw_current_interp.numpy().astype(np.float32),
        "raw_deformation_interp": ds.raw_deformation_interp.numpy().astype(np.float32),
        "shear_value_normed": ds.shear_value.numpy().astype(np.float32),
        "surface": ds.surface.numpy().astype(np.int64),
        "dcb_class": ds.dcb_class.numpy().astype(np.int64),
        "current_lengths": ds.current_lengths.numpy().astype(np.int64),
        "deformation_lengths": ds.deformation_lengths.numpy().astype(np.int64),
        "df": ds.df,
        **sidecars,
    }
    return PreprocessResponse(sample_ids=sample_ids, data=data, state=state)


@tensorleap_preprocess()
def preprocess() -> List[PreprocessResponse]:
    return [_build_split(split, state, kw) for split, state, kw in SPLITS]


def row_index(sample_id: str) -> int:
    return int(sample_id)
