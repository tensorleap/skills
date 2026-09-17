from typing import Dict

import numpy as np

from code_loader.contract.enums import MetricDirection
from code_loader.inner_leap_binder.leapbinder_decorators import (
    tensorleap_custom_loss,
    tensorleap_custom_metric,
)

# Per-horizon weights of interfuser/train.py::WaypointL1Loss
WAYPOINT_WEIGHTS = np.array(
    [
        0.1407441030399059, 0.13352157985305926, 0.12588535273178575, 0.11775496498388233,
        0.10901991343009122, 0.09952110967153563, 0.08901438656870617, 0.07708872007078788,
        0.06294267636589287, 0.04450719328435308,
    ],
    dtype=np.float32,
)
INVALID_WAYPOINT = 1000.0


def _valid_mask(gt: np.ndarray) -> np.ndarray:
    return np.all(gt < INVALID_WAYPOINT, axis=-1)  # [B, 10]


@tensorleap_custom_loss(name="waypoint_l1_loss")
def waypoint_l1_loss(waypoints_pred: np.ndarray, waypoints_gt: np.ndarray) -> np.ndarray:
    valid = _valid_mask(waypoints_gt)
    diff = np.abs(waypoints_pred - np.where(valid[..., None], waypoints_gt, 0.0))
    diff = np.where(valid[..., None], diff, 0.0)
    per_step = diff.mean(axis=-1)  # [B, 10]
    return (per_step * WAYPOINT_WEIGHTS[None, :]).sum(axis=-1).astype(np.float32)


@tensorleap_custom_metric(
    name="waypoint_error",
    direction={"ade": MetricDirection.Downward, "fde": MetricDirection.Downward, "lateral_mae": MetricDirection.Downward},
)
def waypoint_error(waypoints_pred: np.ndarray, waypoints_gt: np.ndarray) -> Dict[str, np.ndarray]:
    valid = _valid_mask(waypoints_gt)  # [B, 10]
    dist = np.linalg.norm(waypoints_pred - np.where(valid[..., None], waypoints_gt, 0.0), axis=-1)
    n_valid = np.maximum(valid.sum(axis=-1), 1)
    ade = (dist * valid).sum(axis=-1) / n_valid
    last_idx = np.where(valid.any(axis=-1), valid.shape[1] - 1 - np.argmax(valid[:, ::-1], axis=-1), 0)
    fde = dist[np.arange(dist.shape[0]), last_idx] * valid.any(axis=-1)
    lateral = np.abs(waypoints_pred[..., 0] - np.where(valid, waypoints_gt[..., 0], 0.0))
    lateral_mae = (lateral * valid).sum(axis=-1) / n_valid
    return {
        "ade": ade.astype(np.float32),
        "fde": fde.astype(np.float32),
        "lateral_mae": lateral_mae.astype(np.float32),
    }


@tensorleap_custom_metric(
    name="traffic_error",
    direction={
        "prob_l1": MetricDirection.Downward,
        "attr_l1_on_actors": MetricDirection.Downward,
        "occupancy_f1": MetricDirection.Upward,
    },
)
def traffic_error(traffic_pred: np.ndarray, traffic_gt: np.ndarray) -> Dict[str, np.ndarray]:
    gt_prob = traffic_gt[..., 0]
    pred_prob = traffic_pred[..., 0]
    prob_l1 = np.abs(pred_prob - gt_prob).mean(axis=-1)
    pos = gt_prob >= 0.01  # cells occupied by an actor, as in train.py::MVTL1Loss
    attr = np.abs(traffic_pred[..., 1:6] - traffic_gt[..., 1:6]).mean(axis=-1)  # [B, 400]
    attr_l1 = (attr * pos).sum(axis=-1) / np.maximum(pos.sum(axis=-1), 1)
    pred_pos = pred_prob >= 0.5
    tp = (pred_pos & pos).sum(axis=-1)
    denom = pred_pos.sum(axis=-1) + pos.sum(axis=-1)
    f1 = np.where(denom > 0, 2.0 * tp / np.maximum(denom, 1), 1.0)
    return {
        "prob_l1": prob_l1.astype(np.float32),
        "attr_l1_on_actors": attr_l1.astype(np.float32),
        "occupancy_f1": f1.astype(np.float32),
    }


def _softmax(x: np.ndarray) -> np.ndarray:
    z = x - x.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


def _cls_metrics(logits: np.ndarray, gt_one_hot: np.ndarray) -> Dict[str, np.ndarray]:
    probs = _softmax(logits)
    correct = (probs.argmax(axis=-1) == gt_one_hot.argmax(axis=-1)).astype(np.float32)
    ce = -np.log(np.clip((probs * gt_one_hot).sum(axis=-1), 1e-7, 1.0)).astype(np.float32)
    return {"accuracy": correct, "cross_entropy": ce}


@tensorleap_custom_metric(
    name="junction",
    direction={"accuracy": MetricDirection.Upward, "cross_entropy": MetricDirection.Downward},
)
def junction_metrics(junction_pred: np.ndarray, junction_gt: np.ndarray) -> Dict[str, np.ndarray]:
    return _cls_metrics(junction_pred, junction_gt)


@tensorleap_custom_metric(
    name="traffic_light",
    direction={"accuracy": MetricDirection.Upward, "cross_entropy": MetricDirection.Downward},
)
def traffic_light_metrics(traffic_light_pred: np.ndarray, traffic_light_gt: np.ndarray) -> Dict[str, np.ndarray]:
    return _cls_metrics(traffic_light_pred, traffic_light_gt)


@tensorleap_custom_metric(
    name="stop_sign",
    direction={"accuracy": MetricDirection.Upward, "cross_entropy": MetricDirection.Downward},
)
def stop_sign_metrics(stop_sign_pred: np.ndarray, stop_sign_gt: np.ndarray) -> Dict[str, np.ndarray]:
    return _cls_metrics(stop_sign_pred, stop_sign_gt)
