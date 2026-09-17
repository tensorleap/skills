import cv2
import numpy as np

from code_loader.contract.enums import LeapDataType
from code_loader.contract.visualizer_classes import LeapHorizontalBar, LeapImage
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_visualizer

from preprocess import CONFIG

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
INVALID_WAYPOINT = 1000.0

# Lidar BEV grid of interfuser/timm/data/carla_dataset.py::lidar_to_histogram_features
_X_METERS_MAX, _Y_METERS_MAX, _PPM = 14, 28, 8
_XBINS = np.linspace(-2 * _X_METERS_MAX, 2 * _X_METERS_MAX + 1, 2 * _X_METERS_MAX * _PPM + 1)
_YBINS = np.linspace(-_Y_METERS_MAX, 0, _Y_METERS_MAX * _PPM + 1)


def _unbatch(x: np.ndarray, sample_ndim: int) -> np.ndarray:
    x = np.asarray(x)
    while x.ndim > sample_ndim:
        x = x[0]
    return x


def _denormalize_chw(x: np.ndarray) -> np.ndarray:
    x = _unbatch(x, 3)
    img = x.transpose(1, 2, 0) * IMAGENET_STD + IMAGENET_MEAN
    return (img * 255.0).clip(0, 255).astype(np.uint8)


@tensorleap_custom_visualizer("rgb_front", LeapDataType.Image)
def rgb_front_vis(rgb: np.ndarray) -> LeapImage:
    return LeapImage(_denormalize_chw(rgb))


@tensorleap_custom_visualizer("rgb_left_view", LeapDataType.Image)
def rgb_left_vis(rgb_left: np.ndarray) -> LeapImage:
    return LeapImage(_denormalize_chw(rgb_left))


@tensorleap_custom_visualizer("rgb_right_view", LeapDataType.Image)
def rgb_right_vis(rgb_right: np.ndarray) -> LeapImage:
    return LeapImage(_denormalize_chw(rgb_right))


@tensorleap_custom_visualizer("rgb_center_crop", LeapDataType.Image)
def rgb_center_vis(rgb_center: np.ndarray) -> LeapImage:
    return LeapImage(_denormalize_chw(rgb_center))


def _lidar_to_hwc(lidar: np.ndarray) -> np.ndarray:
    lidar = _unbatch(lidar, 3)
    # histogram is [channel, x(lateral), y(depth)]; show depth as rows so the ego sits at the bottom
    return (lidar.transpose(2, 1, 0) * 255.0).clip(0, 255).astype(np.uint8)


@tensorleap_custom_visualizer("lidar_bev", LeapDataType.Image)
def lidar_bev_vis(lidar: np.ndarray) -> LeapImage:
    return LeapImage(_lidar_to_hwc(lidar))


def _to_pixel(x: float, y: float):
    col = int(np.clip(np.searchsorted(_XBINS, x) - 1, 0, len(_XBINS) - 2))
    row = int(np.clip(np.searchsorted(_YBINS, y) - 1, 0, len(_YBINS) - 2))
    return col, row


def _draw_path(canvas: np.ndarray, points: np.ndarray, color, radius: int) -> None:
    pts = [_to_pixel(float(p[0]), float(p[1])) for p in points]
    for a, b in zip(pts[:-1], pts[1:]):
        cv2.line(canvas, a, b, color, 1)
    for p in pts:
        cv2.circle(canvas, p, radius, color, -1)


@tensorleap_custom_visualizer("trajectory_bev", LeapDataType.Image)
def trajectory_bev_vis(
    lidar: np.ndarray, waypoints_pred: np.ndarray, waypoints_gt: np.ndarray, target_point: np.ndarray
) -> LeapImage:
    lidar = _unbatch(lidar, 3)
    gray = (lidar[2].T * 255.0).clip(0, 255).astype(np.uint8)
    canvas = np.ascontiguousarray(np.stack([gray, gray, gray], axis=-1))
    gt = _unbatch(waypoints_gt, 2)
    gt = gt[np.all(gt < INVALID_WAYPOINT, axis=-1)]
    pred = _unbatch(waypoints_pred, 2)
    tp = _unbatch(target_point, 1)
    if len(gt):
        _draw_path(canvas, gt, (0, 255, 0), 3)
    _draw_path(canvas, pred, (255, 0, 0), 2)
    cv2.drawMarker(canvas, _to_pixel(float(tp[0]), float(tp[1])), (0, 128, 255), cv2.MARKER_CROSS, 12, 2)
    ego_col, ego_row = _to_pixel(0.0, 0.0)
    cv2.rectangle(canvas, (ego_col - 4, ego_row - 9), (ego_col + 4, ego_row), (255, 255, 0), 1)
    return LeapImage(canvas)


def _grid(prob: np.ndarray, scale: int = 8) -> np.ndarray:
    grid = (np.clip(prob, 0.0, 1.0).reshape(20, 20) * 255.0).astype(np.uint8)
    return np.repeat(np.repeat(grid, scale, axis=0), scale, axis=1)


@tensorleap_custom_visualizer("traffic_occupancy", LeapDataType.Image)
def traffic_occupancy_vis(traffic_pred: np.ndarray, traffic_gt: np.ndarray) -> LeapImage:
    pred = _grid(_unbatch(traffic_pred, 2)[:, 0])
    gt = _grid(_unbatch(traffic_gt, 2)[:, 0])
    # red = predicted occupancy, green = ground-truth occupancy, yellow = agreement
    return LeapImage(np.stack([pred, gt, np.zeros_like(pred)], axis=-1))


def _softmax(x: np.ndarray) -> np.ndarray:
    z = x - x.max()
    e = np.exp(z)
    return (e / e.sum()).astype(np.float32)


def _bar(logits: np.ndarray, gt: np.ndarray, labels) -> LeapHorizontalBar:
    return LeapHorizontalBar(
        body=_softmax(_unbatch(logits, 1)),
        labels=list(labels),
        gt=_unbatch(gt, 1).astype(np.float32),
    )


@tensorleap_custom_visualizer("junction_probs", LeapDataType.HorizontalBar)
def junction_bar(junction_pred: np.ndarray, junction_gt: np.ndarray) -> LeapHorizontalBar:
    return _bar(junction_pred, junction_gt, CONFIG["junction_labels"])


@tensorleap_custom_visualizer("traffic_light_probs", LeapDataType.HorizontalBar)
def traffic_light_bar(traffic_light_pred: np.ndarray, traffic_light_gt: np.ndarray) -> LeapHorizontalBar:
    return _bar(traffic_light_pred, traffic_light_gt, CONFIG["traffic_light_labels"])


@tensorleap_custom_visualizer("stop_sign_probs", LeapDataType.HorizontalBar)
def stop_sign_bar(stop_sign_pred: np.ndarray, stop_sign_gt: np.ndarray) -> LeapHorizontalBar:
    return _bar(stop_sign_pred, stop_sign_gt, CONFIG["stop_sign_labels"])
