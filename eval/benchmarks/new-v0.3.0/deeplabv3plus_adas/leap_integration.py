import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tensorleap"))

import numpy as np
from code_loader.contract.datasetclasses import PreprocessResponse, PredictionTypeHandler
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_load_model, tensorleap_integration_test

from domain_gap.data.cs_data import CATEGORIES
from preprocess import preprocess
from encoders import image_input, mask_gt
from metrics import pixel_cross_entropy, pixel_accuracy, mean_iou, mean_max_confidence
from metadata import source_metadata, vehicle_metadata, image_metadata, gt_metadata
from visualizers import image_visualizer, gt_mask_visualizer, prediction_mask_visualizer, predicted_class_distribution

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(REPO_ROOT, "models", "DeeplabV3.h5")

prediction_types = [PredictionTypeHandler(name="segmentation_logits", labels=list(CATEGORIES), channel_dim=-1)]


@tensorleap_load_model(prediction_types)
def load_model():
    import tensorflow as tf
    return tf.keras.models.load_model(MODEL_PATH, compile=False)


@tensorleap_integration_test()
def integration_test(sample_id: str, preprocess_response: PreprocessResponse):
    image = image_input(sample_id, preprocess_response)
    gt = mask_gt(sample_id, preprocess_response)
    model = load_model()
    prediction = model(image)
    loss = pixel_cross_entropy(gt, prediction)
    acc = pixel_accuracy(gt, prediction)
    miou = mean_iou(gt, prediction)
    conf = mean_max_confidence(prediction)
    image_visualizer(image)
    gt_mask_visualizer(image, gt)
    prediction_mask_visualizer(image, prediction)
    predicted_class_distribution(prediction)
    source_metadata(sample_id, preprocess_response)
    vehicle_metadata(sample_id, preprocess_response)
    image_metadata(sample_id, preprocess_response)
    gt_metadata(sample_id, preprocess_response)


if __name__ == "__main__":
    subsets = preprocess()
    for subset in subsets:
        if subset.state not in {DataStateType.training, DataStateType.validation}:
            continue
        kitti_ids = [i for i in subset.sample_ids if i.startswith("KITTI")]
        for sample_id in subset.sample_ids[:2] + kitti_ids[:1]:
            print(subset.state.name, sample_id)
            integration_test(sample_id, subset)
