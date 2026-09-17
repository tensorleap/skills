import os
from typing import Any, Dict

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "project_config.yaml")

with open(CONFIG_PATH, "r") as _f:
    CONFIG: Dict[str, Any] = yaml.safe_load(_f)

SURFACE_CODES = {name: spec["code"] for name, spec in CONFIG["surface_types"].items()}
SURFACE_NAMES = {spec["code"]: name for name, spec in CONFIG["surface_types"].items()}
SURFACE_NORM_FACTOR = {spec["code"]: float(spec["normalization_factor"]) for spec in CONFIG["surface_types"].values()}
SURFACE_TARGET_LEN = {name: int(spec["target_sequence_length"]) for name, spec in CONFIG["surface_types"].items()}
MAX_SEQ_LEN = int(CONFIG["data"]["max_seq_len"])
DEFAULT_SURFACE = "DCB"


def resolve_path(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)
