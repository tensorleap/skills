import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tensorleap"))

from code_loader.contract.datasetclasses import PredictionTypeHandler, PreprocessResponse  # noqa: E402
from code_loader.contract.enums import DataStateType  # noqa: E402
from code_loader.inner_leap_binder.leapbinder_decorators import (  # noqa: E402
    tensorleap_integration_test,
    tensorleap_load_model,
)

from preprocess import CONFIG, preprocess  # noqa: E402
from encoders import (  # noqa: E402
    junction_gt,
    lidar_input,
    measurements_input,
    rgb_center_input,
    rgb_input,
    rgb_left_input,
    rgb_right_input,
    stop_sign_gt,
    target_point_input,
    traffic_gt,
    traffic_light_gt,
    waypoints_gt,
)
from metadata import sample_metadata  # noqa: E402
from metrics import (  # noqa: E402
    junction_metrics,
    stop_sign_metrics,
    traffic_error,
    traffic_light_metrics,
    waypoint_error,
    waypoint_l1_loss,
)
from visualizers import (  # noqa: E402
    junction_bar,
    lidar_bev_vis,
    rgb_center_vis,
    rgb_front_vis,
    rgb_left_vis,
    rgb_right_vis,
    stop_sign_bar,
    traffic_light_bar,
    traffic_occupancy_vis,
    trajectory_bev_vis,
)

PREDICTION_TYPES = [
    PredictionTypeHandler(name="traffic", labels=list(CONFIG["traffic_channel_labels"]), channel_dim=-1),
    PredictionTypeHandler(name="waypoints", labels=["x", "y"], channel_dim=-1),
    PredictionTypeHandler(name="junction", labels=list(CONFIG["junction_labels"]), channel_dim=-1),
    PredictionTypeHandler(name="traffic_light_state", labels=list(CONFIG["traffic_light_labels"]), channel_dim=-1),
    PredictionTypeHandler(name="stop_sign", labels=list(CONFIG["stop_sign_labels"]), channel_dim=-1),
    PredictionTypeHandler(name="traffic_feature", labels=[f"f{i}" for i in range(128)], channel_dim=-1),
]


@tensorleap_load_model(PREDICTION_TYPES)
def load_model():
    import onnxruntime as ort

    return ort.InferenceSession(CONFIG["model_path"], providers=["CPUExecutionProvider"])


@tensorleap_integration_test()
def integration_test(sample_id: str, preprocess: PreprocessResponse):
    rgb = rgb_input(sample_id, preprocess)
    rgb_left = rgb_left_input(sample_id, preprocess)
    rgb_right = rgb_right_input(sample_id, preprocess)
    rgb_center = rgb_center_input(sample_id, preprocess)
    lidar = lidar_input(sample_id, preprocess)
    measurements = measurements_input(sample_id, preprocess)
    target_point = target_point_input(sample_id, preprocess)
    model = load_model()
    outputs = model.run(
        None,
        {
            "rgb": rgb,
            "rgb_left": rgb_left,
            "rgb_right": rgb_right,
            "rgb_center": rgb_center,
            "lidar": lidar,
            "measurements": measurements,
            "target_point": target_point,
        },
    )
    traffic_pred = outputs[0]
    waypoints_pred = outputs[1]
    junction_pred = outputs[2]
    traffic_light_pred = outputs[3]
    stop_sign_pred = outputs[4]

    wp_gt = waypoints_gt(sample_id, preprocess)
    tr_gt = traffic_gt(sample_id, preprocess)
    jn_gt = junction_gt(sample_id, preprocess)
    tl_gt = traffic_light_gt(sample_id, preprocess)
    ss_gt = stop_sign_gt(sample_id, preprocess)

    loss = waypoint_l1_loss(waypoints_pred, wp_gt)
    waypoint_error(waypoints_pred, wp_gt)
    traffic_error(traffic_pred, tr_gt)
    junction_metrics(junction_pred, jn_gt)
    traffic_light_metrics(traffic_light_pred, tl_gt)
    stop_sign_metrics(stop_sign_pred, ss_gt)
    sample_metadata(sample_id, preprocess)
    rgb_front_vis(rgb)
    rgb_left_vis(rgb_left)
    rgb_right_vis(rgb_right)
    rgb_center_vis(rgb_center)
    lidar_bev_vis(lidar)
    trajectory_bev_vis(lidar, waypoints_pred, wp_gt, target_point)
    traffic_occupancy_vis(traffic_pred, tr_gt)
    junction_bar(junction_pred, jn_gt)
    traffic_light_bar(traffic_light_pred, tl_gt)
    stop_sign_bar(stop_sign_pred, ss_gt)


if __name__ == "__main__":
    subsets = preprocess()
    for subset in subsets:
        if subset.state not in {DataStateType.training, DataStateType.validation}:
            continue
        for sample_id in subset.sample_ids[:3]:
            print("integration_test", subset.state.value, sample_id)
            integration_test(sample_id, subset)
