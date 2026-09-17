import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tensorleap"))

from code_loader.contract.datasetclasses import PreprocessResponse, PredictionTypeHandler
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_load_model, tensorleap_integration_test

from config import LABELS, MODEL_PATH
from preprocess import preprocess
from encoders import image_input, class_gt
from metrics import categorical_crossentropy, accuracy, gt_class_probability, confidence
from visualizers import image_visualizer, prediction_bar, gt_bar
from metadata import label_metadata, superclass_metadata, source_batch_metadata, image_metadata, sharpness_metadata

prediction_types = [PredictionTypeHandler(name="classes", labels=LABELS, channel_dim=-1)]


@tensorleap_load_model(prediction_types)
def load_model():
    import tensorflow as tf
    return tf.keras.models.load_model(MODEL_PATH, compile=False)


@tensorleap_integration_test()
def integration_test(sample_id: str, preprocess: PreprocessResponse):
    x = image_input(sample_id, preprocess)
    gt = class_gt(sample_id, preprocess)
    model = load_model()
    pred = model(x)
    loss = categorical_crossentropy(pred, gt)
    acc = accuracy(pred, gt)
    gcp = gt_class_probability(pred, gt)
    conf = confidence(pred)
    label_metadata(sample_id, preprocess)
    superclass_metadata(sample_id, preprocess)
    source_batch_metadata(sample_id, preprocess)
    image_metadata(sample_id, preprocess)
    sharpness_metadata(sample_id, preprocess)
    image_visualizer(x)
    prediction_bar(pred, gt)
    gt_bar(gt)


if __name__ == "__main__":
    subsets = preprocess()
    train = next(s for s in subsets if s.state == DataStateType.training)
    x = image_input(train.sample_ids[0], train)
    gt = class_gt(train.sample_ids[0], train)
    print("input", x.shape, x.dtype, "gt", gt.shape, gt.dtype, gt)
    print(label_metadata(train.sample_ids[0], train), superclass_metadata(train.sample_ids[0], train),
          source_batch_metadata(train.sample_ids[0], train), image_metadata(train.sample_ids[0], train),
          sharpness_metadata(train.sample_ids[0], train))
    for subset in subsets:
        for sample_id in subset.sample_ids[:3]:
            integration_test(sample_id, subset)
