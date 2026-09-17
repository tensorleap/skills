import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "tensorleap"))

from code_loader.contract.datasetclasses import PredictionTypeHandler, PreprocessResponse
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_integration_test, tensorleap_load_model

from preprocess import preprocess
from preprocess import CLASS_NAMES
from encoders import image_input, boxes_gt
from metrics import yolov5_loss, detection_metrics
from visualizers import image_visualizer, gt_boxes_visualizer, pred_boxes_visualizer
from metadata import (
    image_metadata,
    sequence_metadata,
    gt_count_metadata,
    gt_size_metadata,
    gt_border_metadata,
    gt_class_count_metadata,
)

MODEL_PATH = os.path.join(REPO_ROOT, "weights", "yolov5s-visdrone.onnx")
OUTPUT_CHANNELS = ["x", "y", "w", "h", "objectness"] + list(CLASS_NAMES)

prediction_types = [
    PredictionTypeHandler(name="detections", labels=OUTPUT_CHANNELS, channel_dim=-1),
    PredictionTypeHandler(name="raw_p3_stride8", labels=OUTPUT_CHANNELS, channel_dim=-1),
    PredictionTypeHandler(name="raw_p4_stride16", labels=OUTPUT_CHANNELS, channel_dim=-1),
    PredictionTypeHandler(name="raw_p5_stride32", labels=OUTPUT_CHANNELS, channel_dim=-1),
]


@tensorleap_load_model(prediction_types)
def load_model():
    import onnxruntime as ort

    return ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])


@tensorleap_integration_test()
def integration_test(sample_id: str, preprocess: PreprocessResponse):
    x = image_input(sample_id, preprocess)
    gt = boxes_gt(sample_id, preprocess)
    model = load_model()
    outputs = model.run(None, {model.get_inputs()[0].name: x})
    detections = outputs[0]
    raw_p3 = outputs[1]
    raw_p4 = outputs[2]
    raw_p5 = outputs[3]
    loss = yolov5_loss(raw_p3, raw_p4, raw_p5, gt)
    image_visualizer(x)
    gt_boxes_visualizer(x, gt)
    pred_boxes_visualizer(x, detections)
    detection_metrics(detections, gt)
    image_metadata(sample_id, preprocess)
    sequence_metadata(sample_id, preprocess)
    gt_count_metadata(sample_id, preprocess)
    gt_size_metadata(sample_id, preprocess)
    gt_border_metadata(sample_id, preprocess)
    gt_class_count_metadata(sample_id, preprocess)


if __name__ == "__main__":
    subsets = preprocess()
    for subset in subsets:
        for sample_id in subset.sample_ids[:3]:
            integration_test(sample_id, subset)
        print(f"{subset.state.value}: {len(subset.sample_ids)} samples, checked {min(3, len(subset.sample_ids))}")
