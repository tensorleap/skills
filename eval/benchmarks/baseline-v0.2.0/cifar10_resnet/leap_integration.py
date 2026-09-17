import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tensorleap"))

from code_loader.contract.datasetclasses import PreprocessResponse, PredictionTypeHandler
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import (
    tensorleap_load_model,
    tensorleap_integration_test,
)

from config import LABELS
from preprocess import preprocess
from encoders import image_input, class_gt
from metrics import categorical_crossentropy, accuracy, gt_confidence
from metadata import sample_metadata, image_stats
from visualizers import image_visualizer, gt_visualizer, prediction_visualizer

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model", "resnet.h5")

prediction_types = [PredictionTypeHandler(name="classes", labels=LABELS, channel_dim=-1)]


@tensorleap_load_model(prediction_types)
def load_model():
    import tensorflow as tf

    return tf.keras.models.load_model(MODEL_PATH)


@tensorleap_integration_test()
def integration_test(sample_id: str, preprocess: PreprocessResponse):
    x = image_input(sample_id, preprocess)
    gt = class_gt(sample_id, preprocess)
    model = load_model()
    pred = model(x)
    loss = categorical_crossentropy(pred, gt)
    acc = accuracy(pred, gt)
    conf = gt_confidence(pred, gt)
    meta = sample_metadata(sample_id, preprocess)
    stats = image_stats(sample_id, preprocess)
    image_visualizer(x)
    gt_visualizer(gt)
    prediction_visualizer(pred)


if __name__ == "__main__":
    subsets = preprocess()
    train = next(s for s in subsets if s.state == DataStateType.training)
    print(sample_metadata(train.sample_ids[0], train), image_stats(train.sample_ids[0], train))
    for subset in subsets:
        for sample_id in subset.sample_ids[:3]:
            integration_test(sample_id, subset)
