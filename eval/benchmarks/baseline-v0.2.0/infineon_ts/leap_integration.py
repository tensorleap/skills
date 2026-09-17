import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "tensorleap"))

from code_loader.contract.datasetclasses import PredictionTypeHandler, PreprocessResponse, SamplePreprocessResponse  # noqa: E402
from code_loader.contract.enums import DataStateType  # noqa: E402
from code_loader.inner_leap_binder.leapbinder_decorators import (  # noqa: E402
    tensorleap_integration_test,
    tensorleap_load_model,
)

from preprocess import CONFIG, preprocess  # noqa: E402
from encoders import current_trace, deformation_trace, shear_value_gt  # noqa: E402
from metrics import abs_error_normed, abs_error_shear_force, mse_loss, relative_error_pct  # noqa: E402
from visualizers import (  # noqa: E402
    deformation_reconstruction_visualizer,
    input_traces_visualizer,
    raw_current_visualizer,
    raw_deformation_visualizer,
    shear_prediction_visualizer,
)
from metadata import (  # noqa: E402
    discriminator_metadata,
    latent_metadata,
    reconstruction_metadata,
    sample_metadata,
    shear_metadata,
    trace_metadata,
)

PREDICTION_TYPES = [PredictionTypeHandler(name="vst_prediction", labels=["shear_value_normed"], channel_dim=-1)]


@tensorleap_load_model(PREDICTION_TYPES)
def load_model():
    import onnxruntime as ort

    return ort.InferenceSession(os.path.join(ROOT, CONFIG["model"]["model_path"]), providers=["CPUExecutionProvider"])


@tensorleap_integration_test()
def integration_test(sample_id: str, preprocess_response: PreprocessResponse):
    current = current_trace(sample_id, preprocess_response)
    deformation = deformation_trace(sample_id, preprocess_response)
    gt = shear_value_gt(sample_id, preprocess_response)
    spr = SamplePreprocessResponse(sample_id, preprocess_response)
    model = load_model()
    inputs = model.get_inputs()
    prediction = model.run(None, {inputs[0].name: current, inputs[1].name: deformation})[0]
    loss = mse_loss(prediction, gt)
    abs_error_normed(prediction, gt)
    abs_error_shear_force(prediction, gt, spr)
    relative_error_pct(prediction, gt)
    input_traces_visualizer(current, deformation)
    raw_current_visualizer(spr)
    raw_deformation_visualizer(spr)
    deformation_reconstruction_visualizer(deformation, spr)
    shear_prediction_visualizer(prediction, gt, spr)
    for metadata_fn in (sample_metadata, shear_metadata, trace_metadata, latent_metadata, discriminator_metadata, reconstruction_metadata):
        metadata_fn(sample_id, preprocess_response)


if __name__ == "__main__":
    subsets = preprocess()
    n_samples = CONFIG["tensorleap"]["local_test_samples_per_split"]
    for subset in subsets:
        if subset.state not in {DataStateType.training, DataStateType.validation}:
            continue
        for sample_id in subset.sample_ids[:n_samples]:
            integration_test(sample_id, subset)
        print(f"{subset.state.name}: {n_samples} samples passed")
