import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tensorleap"))

from code_loader.contract.datasetclasses import PredictionTypeHandler, PreprocessResponse, SamplePreprocessResponse
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_integration_test, tensorleap_load_model

from config import CONFIG, resolve_path
from encoders import current_trace, deformation_trace, shear_gt
from metadata import (autoencoder_metadata, current_trace_metadata, deformation_trace_metadata, record_time_metadata,
                      sample_metadata, shear_gt_metadata, wire_bond_metadata)
from metrics import (abs_error_normed, abs_error_shear, mse_loss, predicted_shear_value, relative_error,
                     signed_error_shear)
from preprocess import preprocess
from visualizers import (current_trace_graph, deformation_trace_graph, deformation_vs_reconstruction,
                         shear_prediction_bar)

MODEL_PATH = resolve_path(CONFIG["model"]["model_path"])
CURRENT_INPUT, DEFORMATION_INPUT = CONFIG["model"]["input_names"]
OUTPUT_NAME = CONFIG["model"]["output_name"]


@tensorleap_load_model(prediction_types=[PredictionTypeHandler(OUTPUT_NAME, labels=["shear_value_normed"], channel_dim=-1)])
def load_model():
    import onnxruntime as ort
    return ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])


@tensorleap_integration_test()
def integration_test(sample_id: str, preprocess_response: PreprocessResponse):
    cur = current_trace(sample_id, preprocess_response)
    defo = deformation_trace(sample_id, preprocess_response)
    gt = shear_gt(sample_id, preprocess_response)
    model = load_model()
    pred = model.run(None, {CURRENT_INPUT: cur, DEFORMATION_INPUT: defo})[0]
    spr = SamplePreprocessResponse(sample_id, preprocess_response)
    loss = mse_loss(pred, gt)
    abs_error_normed(pred, gt)
    abs_error_shear(pred, gt, spr)
    relative_error(pred, gt)
    predicted_shear_value(pred, spr)
    signed_error_shear(pred, gt, spr)
    current_trace_graph(cur)
    deformation_trace_graph(defo)
    deformation_vs_reconstruction(defo, spr)
    shear_prediction_bar(pred, spr)
    sample_metadata(sample_id, preprocess_response)
    wire_bond_metadata(sample_id, preprocess_response)
    shear_gt_metadata(sample_id, preprocess_response)
    record_time_metadata(sample_id, preprocess_response)
    current_trace_metadata(sample_id, preprocess_response)
    deformation_trace_metadata(sample_id, preprocess_response)
    autoencoder_metadata(sample_id, preprocess_response)


if __name__ == "__main__":
    subsets = preprocess()
    for subset in subsets:
        n = 3 if subset.state in {DataStateType.training, DataStateType.validation} else 1
        for sid in subset.sample_ids[:n]:
            integration_test(sid, subset)
