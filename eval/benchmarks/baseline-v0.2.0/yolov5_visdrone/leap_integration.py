import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tensorleap"))

from code_loader.contract.datasetclasses import PredictionTypeHandler, PreprocessResponse  # noqa: E402
from code_loader.contract.enums import DataStateType  # noqa: E402
from code_loader.inner_leap_binder.leapbinder_decorators import (  # noqa: E402
    tensorleap_integration_test,
    tensorleap_load_model,
)

from encoders import boxes_gt, image_input  # noqa: E402
from metadata import gt_metadata, image_metadata  # noqa: E402
from metrics import detection_metrics, yolov5_loss  # noqa: E402
from preprocess import preprocess  # noqa: E402
from tl_config import CLASS_NAMES, CONFIG, REPO_ROOT  # noqa: E402
from visualizers import gt_boxes_visualizer, image_visualizer, pred_boxes_visualizer  # noqa: E402

DET_LABELS = ["x", "y", "w", "h", "objectness"] + CLASS_NAMES

prediction_types = [
    PredictionTypeHandler(name="detections", labels=DET_LABELS, channel_dim=-1),
    PredictionTypeHandler(name="head_p3_stride8", labels=DET_LABELS, channel_dim=-1),
    PredictionTypeHandler(name="head_p4_stride16", labels=DET_LABELS, channel_dim=-1),
    PredictionTypeHandler(name="head_p5_stride32", labels=DET_LABELS, channel_dim=-1),
]


@tensorleap_load_model(prediction_types)
def load_model():
    import onnxruntime as ort

    return ort.InferenceSession(os.path.join(REPO_ROOT, CONFIG["model_path"]), providers=["CPUExecutionProvider"])


@tensorleap_integration_test()
def integration_test(sample_id: str, preprocess: PreprocessResponse):
    image = image_input(sample_id, preprocess)
    model = load_model()
    outputs = model.run(None, {model.get_inputs()[0].name: image})
    detections = outputs[0]
    gt = boxes_gt(sample_id, preprocess)
    loss = yolov5_loss(gt, outputs[1], outputs[2], outputs[3])
    image_metadata(sample_id, preprocess)
    gt_metadata(sample_id, preprocess)
    image_visualizer(image)
    gt_boxes_visualizer(image, gt)
    pred_boxes_visualizer(image, detections)
    detection_metrics(detections, gt)


if __name__ == "__main__":
    subsets = preprocess()
    for subset in subsets:
        for sid in subset.sample_ids[:3]:
            integration_test(sid, subset)
        print(f"{subset.state.value}: {len(subset.sample_ids)} samples, first 3 passed integration_test")
