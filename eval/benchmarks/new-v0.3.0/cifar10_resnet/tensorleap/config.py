import os

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)

with open(os.path.join(_HERE, "project_config.yaml"), "r") as f:
    CONFIG = yaml.safe_load(f)

LABELS = list(CONFIG["labels"])
DATA_ROOT = CONFIG["data_root"]
CIFAR_DIR = os.path.join(DATA_ROOT, CONFIG["cifar_dirname"])
MODEL_PATH = os.path.join(REPO_ROOT, CONFIG["model_path"])
