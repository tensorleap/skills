import numpy as np
from scipy.ndimage import laplace

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DatasetMetadataType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_metadata

from config import LABELS
from encoders import get_image, get_label

VEHICLES = {"airplane", "automobile", "ship", "truck"}


@tensorleap_metadata("label", DatasetMetadataType.string)
def label_metadata(sample_id: str, preprocess: PreprocessResponse) -> str:
    return LABELS[get_label(sample_id, preprocess)]


@tensorleap_metadata("superclass", DatasetMetadataType.string)
def superclass_metadata(sample_id: str, preprocess: PreprocessResponse) -> str:
    return "vehicle" if LABELS[get_label(sample_id, preprocess)] in VEHICLES else "animal"


@tensorleap_metadata("source_batch", DatasetMetadataType.int)
def source_batch_metadata(sample_id: str, preprocess: PreprocessResponse) -> int:
    return int(preprocess.data["batch_idx"][preprocess.data["pos"][sample_id]])


@tensorleap_metadata("image", {"brightness": DatasetMetadataType.float,
                               "contrast": DatasetMetadataType.float,
                               "colorfulness": DatasetMetadataType.float,
                               "red_mean": DatasetMetadataType.float,
                               "green_mean": DatasetMetadataType.float,
                               "blue_mean": DatasetMetadataType.float})
def image_metadata(sample_id: str, preprocess: PreprocessResponse) -> dict:
    img = get_image(sample_id, preprocess).astype(np.float32)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    gray = 0.299 * r + 0.587 * g + 0.114 * b
    rg, yb = r - g, 0.5 * (r + g) - b
    colorfulness = np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    return {"brightness": float(gray.mean()), "contrast": float(gray.std()),
            "colorfulness": float(colorfulness),
            "red_mean": float(r.mean()), "green_mean": float(g.mean()), "blue_mean": float(b.mean())}


@tensorleap_metadata("sharpness", DatasetMetadataType.float)
def sharpness_metadata(sample_id: str, preprocess: PreprocessResponse) -> float:
    img = get_image(sample_id, preprocess).astype(np.float32)
    gray = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
    return float(laplace(gray).var())
