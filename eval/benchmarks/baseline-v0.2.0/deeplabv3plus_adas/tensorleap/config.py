import os
from typing import Any, Dict

import yaml

TL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TL_DIR)


def load_config() -> Dict[str, Any]:
    with open(os.path.join(TL_DIR, "project_config.yaml"), "r") as f:
        return yaml.safe_load(f)


CONFIG = load_config()
DATA_ROOT = CONFIG["data_root"]


def data_path(rel_path: str) -> str:
    return os.path.join(DATA_ROOT, rel_path)


def repo_path(rel_path: str) -> str:
    return os.path.join(REPO_ROOT, rel_path)
