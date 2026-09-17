"""YOLOv5 detection loss (ported from utils/loss.py ComputeLoss, torch-only, no repo imports)."""
import math
from typing import Dict, List, Sequence, Tuple

import torch
import torch.nn as nn


def smooth_bce(eps: float = 0.1) -> Tuple[float, float]:
    return 1.0 - 0.5 * eps, 0.5 * eps


def bbox_ciou(box1: torch.Tensor, box2: torch.Tensor, eps: float = 1e-7) -> torch.Tensor:
    (x1, y1, w1, h1), (x2, y2, w2, h2) = box1.chunk(4, 1), box2.chunk(4, 1)
    w1_, h1_, w2_, h2_ = w1 / 2, h1 / 2, w2 / 2, h2 / 2
    b1_x1, b1_x2, b1_y1, b1_y2 = x1 - w1_, x1 + w1_, y1 - h1_, y1 + h1_
    b2_x1, b2_x2, b2_y1, b2_y2 = x2 - w2_, x2 + w2_, y2 - h2_, y2 + h2_
    inter = (torch.min(b1_x2, b2_x2) - torch.max(b1_x1, b2_x1)).clamp(0) * (
        torch.min(b1_y2, b2_y2) - torch.max(b1_y1, b2_y1)
    ).clamp(0)
    union = w1 * h1 + w2 * h2 - inter + eps
    iou = inter / union
    cw = torch.max(b1_x2, b2_x2) - torch.min(b1_x1, b2_x1)
    ch = torch.max(b1_y2, b2_y2) - torch.min(b1_y1, b2_y1)
    c2 = cw ** 2 + ch ** 2 + eps
    rho2 = ((b2_x1 + b2_x2 - b1_x1 - b1_x2) ** 2 + (b2_y1 + b2_y2 - b1_y1 - b1_y2) ** 2) / 4
    v = (4 / math.pi ** 2) * torch.pow(torch.atan(w2 / (h2 + eps)) - torch.atan(w1 / (h1 + eps)), 2)
    with torch.no_grad():
        alpha = v / (v - iou + (1 + eps))
    return iou - (rho2 / c2 + v * alpha)


class YoloV5Loss:
    def __init__(self, hyp: Dict[str, float], anchors_px: Sequence[Sequence[Sequence[float]]], strides: Sequence[float], nc: int):
        self.hyp = hyp
        self.nc = nc
        self.nl = len(strides)
        self.na = len(anchors_px[0])
        strides_t = torch.tensor(strides, dtype=torch.float32).view(-1, 1, 1)
        self.anchors = torch.tensor(anchors_px, dtype=torch.float32) / strides_t  # grid units, like Detect.anchors
        self.balance = {3: [4.0, 1.0, 0.4]}.get(self.nl, [4.0, 1.0, 0.25, 0.06, 0.02])
        self.cp, self.cn = smooth_bce(hyp.get("label_smoothing", 0.0))
        self.bce_cls = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([hyp["cls_pw"]]))
        self.bce_obj = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([hyp["obj_pw"]]))
        if hyp.get("fl_gamma", 0.0) > 0:
            raise NotImplementedError("focal loss not ported; checkpoint hyp has fl_gamma=0")

    def __call__(self, p: List[torch.Tensor], targets: torch.Tensor) -> torch.Tensor:
        lcls = torch.zeros(1)
        lbox = torch.zeros(1)
        lobj = torch.zeros(1)
        tcls, tbox, indices, anchors = self.build_targets(p, targets)
        for i, pi in enumerate(p):
            b, a, gj, gi = indices[i]
            tobj = torch.zeros(pi.shape[:4], dtype=pi.dtype)
            n = b.shape[0]
            if n:
                pxy, pwh, _, pcls = pi[b, a, gj, gi].split((2, 2, 1, self.nc), 1)
                pxy = pxy.sigmoid() * 2 - 0.5
                pwh = (pwh.sigmoid() * 2) ** 2 * anchors[i]
                pbox = torch.cat((pxy, pwh), 1)
                iou = bbox_ciou(pbox, tbox[i]).squeeze()
                lbox += (1.0 - iou).mean()
                iou = iou.detach().clamp(0).type(tobj.dtype)
                tobj[b, a, gj, gi] = iou
                if self.nc > 1:
                    t = torch.full_like(pcls, self.cn)
                    t[range(n), tcls[i]] = self.cp
                    lcls += self.bce_cls(pcls, t)
            lobj += self.bce_obj(pi[..., 4], tobj) * self.balance[i]
        lbox *= self.hyp["box"]
        lobj *= self.hyp["obj"]
        lcls *= self.hyp["cls"]
        bs = p[0].shape[0]
        return (lbox + lobj + lcls) * bs

    def build_targets(self, p: List[torch.Tensor], targets: torch.Tensor):
        na, nt = self.na, targets.shape[0]
        tcls, tbox, indices, anch = [], [], [], []
        gain = torch.ones(7)
        ai = torch.arange(na).float().view(na, 1).repeat(1, nt)
        targets = torch.cat((targets.repeat(na, 1, 1), ai[..., None]), 2)
        g = 0.5
        off = torch.tensor([[0, 0], [1, 0], [0, 1], [-1, 0], [0, -1]]).float() * g
        for i in range(self.nl):
            anchors, shape = self.anchors[i], p[i].shape
            gain[2:6] = torch.tensor(shape)[[3, 2, 3, 2]]
            t = targets * gain
            if nt:
                r = t[..., 4:6] / anchors[:, None]
                j = torch.max(r, 1 / r).max(2)[0] < self.hyp["anchor_t"]
                t = t[j]
                gxy = t[:, 2:4]
                gxi = gain[[2, 3]] - gxy
                j, k = ((gxy % 1 < g) & (gxy > 1)).T
                l, m = ((gxi % 1 < g) & (gxi > 1)).T
                j = torch.stack((torch.ones_like(j), j, k, l, m))
                t = t.repeat((5, 1, 1))[j]
                offsets = (torch.zeros_like(gxy)[None] + off[:, None])[j]
            else:
                t = targets[0]
                offsets = 0
            bc, gxy, gwh, a = t.chunk(4, 1)
            a, (b, c) = a.long().view(-1), bc.long().T
            gij = (gxy - offsets).long()
            gi, gj = gij.T
            indices.append((b, a, gj.clamp_(0, shape[2] - 1), gi.clamp_(0, shape[3] - 1)))
            tbox.append(torch.cat((gxy - gij, gwh), 1))
            anch.append(anchors[a])
            tcls.append(c)
        return tcls, tbox, indices, anch
