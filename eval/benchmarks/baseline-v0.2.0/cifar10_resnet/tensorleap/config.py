import os
import yaml

_ROOT = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_ROOT, "project_config.yaml"), "r") as f:
    CONFIG = yaml.safe_load(f)

LABELS = CONFIG["labels"]
