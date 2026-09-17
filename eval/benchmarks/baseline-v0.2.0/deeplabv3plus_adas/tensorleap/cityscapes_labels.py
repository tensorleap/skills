# Cityscapes label table, mirrored from domain_gap/data/cs_data.py (Cityscapes.classes) so the
# integration does not import that module's google-cloud-storage dependency at runtime.
from typing import List, Tuple

import numpy as np

# (name, id, train_id, color)
_CLASSES: List[Tuple[str, int, int, Tuple[int, int, int]]] = [
    ("unlabeled", 0, 19, (0, 0, 0)),
    ("ego vehicle", 1, 19, (0, 0, 0)),
    ("rectification border", 2, 19, (0, 0, 0)),
    ("out of roi", 3, 19, (0, 0, 0)),
    ("static", 4, 19, (0, 0, 0)),
    ("dynamic", 5, 19, (111, 74, 0)),
    ("ground", 6, 19, (81, 0, 81)),
    ("road", 7, 0, (128, 64, 128)),
    ("sidewalk", 8, 1, (244, 35, 232)),
    ("parking", 9, 19, (250, 170, 160)),
    ("rail track", 10, 19, (230, 150, 140)),
    ("building", 11, 2, (70, 70, 70)),
    ("wall", 12, 3, (102, 102, 156)),
    ("fence", 13, 4, (190, 153, 153)),
    ("guard rail", 14, 19, (180, 165, 180)),
    ("bridge", 15, 19, (150, 100, 100)),
    ("tunnel", 16, 19, (150, 120, 90)),
    ("pole", 17, 5, (153, 153, 153)),
    ("polegroup", 18, 19, (153, 153, 153)),
    ("traffic light", 19, 6, (250, 170, 30)),
    ("traffic sign", 20, 7, (220, 220, 0)),
    ("vegetation", 21, 8, (107, 142, 35)),
    ("terrain", 22, 9, (152, 251, 152)),
    ("sky", 23, 10, (70, 130, 180)),
    ("person", 24, 11, (220, 20, 60)),
    ("rider", 25, 12, (255, 0, 0)),
    ("car", 26, 13, (0, 0, 142)),
    ("truck", 27, 14, (0, 0, 70)),
    ("bus", 28, 15, (0, 60, 100)),
    ("caravan", 29, 19, (0, 0, 90)),
    ("trailer", 30, 19, (0, 0, 110)),
    ("train", 31, 16, (0, 80, 100)),
    ("motorcycle", 32, 17, (0, 0, 230)),
    ("bicycle", 33, 18, (119, 11, 32)),
    ("license plate", -1, 19, (0, 0, 142)),
]

NUM_CLASSES = 19
IGNORE_TRAIN_ID = 19

# The 19 evaluated classes, ordered by train_id (matches CATEGORIES in cs_data.py).
CATEGORIES: List[str] = [name for name, _id, train_id, _c in _CLASSES if train_id < NUM_CLASSES]
MASK_LABELS: List[str] = CATEGORIES + ["ignore"]

# label id (0..33; 255 / -1 -> ignore) -> train id (0..18, 19 = ignore)
ID_TO_TRAIN_ID = np.full(256, IGNORE_TRAIN_ID, dtype=np.uint8)
for _name, _id, _train_id, _color in _CLASSES:
    if _id >= 0:
        ID_TO_TRAIN_ID[_id] = _train_id

TRAIN_ID_TO_COLOR = np.zeros((NUM_CLASSES + 1, 3), dtype=np.uint8)
for _name, _id, _train_id, _color in _CLASSES:
    if _train_id < NUM_CLASSES:
        TRAIN_ID_TO_COLOR[_train_id] = _color


def encode_label_ids(label_ids: np.ndarray) -> np.ndarray:
    return ID_TO_TRAIN_ID[label_ids.astype(np.uint8)]
