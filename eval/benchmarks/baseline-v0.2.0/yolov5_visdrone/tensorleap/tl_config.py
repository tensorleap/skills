import os

import yaml

TL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TL_DIR)

with open(os.path.join(TL_DIR, "project_config.yaml"), "r") as f:
    CONFIG = yaml.safe_load(f)

with open(os.path.join(REPO_ROOT, CONFIG["dataset_yaml"]), "r") as f:
    DATASET = yaml.safe_load(f)

DATA_ROOT = CONFIG["data_root"]
CLASS_NAMES = list(DATASET["names"])
NUM_CLASSES = int(DATASET["nc"])
IMAGE_SIZE = int(CONFIG["image_size"])
MAX_BOXES = int(CONFIG["max_boxes"])
