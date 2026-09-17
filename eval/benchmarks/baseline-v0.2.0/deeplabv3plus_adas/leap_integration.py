import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tensorleap"))

from code_loader.contract.datasetclasses import PredictionTypeHandler, PreprocessResponse
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import (
    tensorleap_integration_test,
    tensorleap_load_model,
)

from cityscapes_labels import CATEGORIES
from config import CONFIG, repo_path
from encoders import image_input, mask_gt
from metadata import gt_class_percent_metadata, image_stats_metadata, sample_metadata, vehicle_metadata
from metrics import cross_entropy, mean_confidence, per_class_iou, segmentation_quality
from preprocess import preprocess, unlabeled_preprocess
from visualizers import gt_mask_visualizer, image_visualizer, prediction_mask_visualizer

PREDICTION_TYPES = [PredictionTypeHandler(name="segmentation_logits", labels=CATEGORIES, channel_dim=-1)]


@tensorleap_load_model(PREDICTION_TYPES)
def load_model():
    import tensorflow as tf
    return tf.keras.models.load_model(repo_path(CONFIG["model_path"]), compile=False)


@tensorleap_integration_test()
def integration_test(sample_id: str, preprocess_response: PreprocessResponse):
    image = image_input(sample_id, preprocess_response)
    gt = mask_gt(sample_id, preprocess_response)
    model = load_model()
    logits = model(image)
    loss = cross_entropy(logits, gt)
    quality = segmentation_quality(logits, gt)
    class_iou = per_class_iou(logits, gt)
    confidence = mean_confidence(logits)
    sample_metadata(sample_id, preprocess_response)
    vehicle_metadata(sample_id, preprocess_response)
    image_stats_metadata(sample_id, preprocess_response)
    gt_class_percent_metadata(sample_id, preprocess_response)
    image_visualizer(image)
    gt_mask_visualizer(image, gt)
    prediction_mask_visualizer(image, logits)


if __name__ == "__main__":
    subsets = preprocess()
    for subset in subsets:
        records = subset.data["records"]
        ids_by_dataset = {}
        for sid in subset.sample_ids:
            ids_by_dataset.setdefault(records[sid]["dataset"], []).append(sid)
        chosen = [ids for ids in ids_by_dataset.values()]
        for sample_id in [ids[0] for ids in chosen] + [ids[1] for ids in chosen]:
            integration_test(sample_id, subset)
            print(subset.state.value, sample_id, records[sample_id]["dataset"])
    unlabeled = unlabeled_preprocess()
    for sample_id in (unlabeled.sample_ids[0], unlabeled.sample_ids[-1]):
        record = unlabeled.data["records"][sample_id]
        image = image_input(sample_id, unlabeled)
        gt = mask_gt(sample_id, unlabeled)
        for fn in (sample_metadata, vehicle_metadata, image_stats_metadata, gt_class_percent_metadata):
            fn(sample_id, unlabeled)
        image_visualizer(image)
        gt_mask_visualizer(image, gt)
        print("unlabeled", sample_id, record["dataset"], image.shape, gt.shape)
