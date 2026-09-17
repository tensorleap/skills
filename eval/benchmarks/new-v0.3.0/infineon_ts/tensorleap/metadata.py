import math
from typing import Dict, Optional

import numpy as np
import pandas as pd
from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DatasetMetadataType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_metadata

from config import CONFIG
from encoders import row_index

COLS = CONFIG["columns"]
FS = float(CONFIG["tl_metadata"]["fs"])
CUTOFF_HZ = float(CONFIG["tl_metadata"]["cutoff_hz"])


def _f(v) -> Optional[float]:
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _i(v) -> Optional[int]:
    v = _f(v)
    return None if v is None else int(v)


def _s(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA:
        return None
    return str(v)


def _row(sample_id: str, preprocess: PreprocessResponse):
    return preprocess.data, row_index(sample_id, preprocess)


@tensorleap_metadata("sample", {"surface_type": DatasetMetadataType.string, "equipment_id": DatasetMetadataType.int,
                                "package": DatasetMetadataType.string, "recipe": DatasetMetadataType.int,
                                "bau_no": DatasetMetadataType.string, "source_file": DatasetMetadataType.string,
                                "labeled": DatasetMetadataType.boolean})
def sample_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict:
    d, i = _row(sample_id, preprocess)
    return {"surface_type": _s(d["surface_name"][i]), "equipment_id": _i(d["equipment_id"][i]),
            "package": _s(d[COLS["package"]][i]), "recipe": _i(d[COLS["recipe"]][i]),
            "bau_no": _s(d[COLS["bau_no"]][i]), "source_file": _s(d["source_file"][i]), "labeled": bool(d["labeled"])}


@tensorleap_metadata("wire_bond", {"wire_number": DatasetMetadataType.int, "bond_number": DatasetMetadataType.int,
                                   "shear_sequence_no": DatasetMetadataType.int})
def wire_bond_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict:
    d, i = _row(sample_id, preprocess)
    return {"wire_number": _i(d["wire_number"][i]), "bond_number": _i(d["bond_number"][i]),
            "shear_sequence_no": _i(d[COLS["shear_sequence_no"]][i])}


@tensorleap_metadata("shear_gt", {"shear_value": DatasetMetadataType.float, "shear_value_normed": DatasetMetadataType.float,
                                  "shear_grade": DatasetMetadataType.int})
def shear_gt_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict:
    d, i = _row(sample_id, preprocess)
    return {"shear_value": _f(d["shear_value"][i]), "shear_value_normed": _f(d["shear_value_normed"][i]),
            "shear_grade": _i(d["ShearGrade"][i])}


@tensorleap_metadata("record_time", {"date": DatasetMetadataType.string, "hour": DatasetMetadataType.int})
def record_time_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict:
    d, i = _row(sample_id, preprocess)
    raw = d[COLS["timestamp"]][i]
    ts = pd.to_datetime(raw, errors="coerce") if isinstance(raw, str) else pd.NaT
    if pd.isna(ts):
        return {"date": None, "hour": None}
    return {"date": ts.strftime("%Y-%m-%d"), "hour": int(ts.hour)}


def _hf_energy_ratio(x: np.ndarray) -> Optional[float]:
    x = np.asarray(x, dtype=np.float64)
    if len(x) < 4:
        return None
    power = np.abs(np.fft.rfft(x - x.mean())) ** 2
    freqs = np.fft.rfftfreq(len(x), d=1.0 / FS)
    total = power[freqs > 0].sum()
    return _f(power[freqs > CUTOFF_HZ].sum() / total) if total > 0 else None


@tensorleap_metadata("current_trace", {"raw_len": DatasetMetadataType.int, "raw_mean": DatasetMetadataType.float,
                                       "raw_max": DatasetMetadataType.float, "raw_std": DatasetMetadataType.float,
                                       "hf_energy_ratio": DatasetMetadataType.float})
def current_trace_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict:
    d, i = _row(sample_id, preprocess)
    x = np.asarray(d["raw_current"][i], dtype=np.float64)
    if len(x) == 0:
        return {"raw_len": 0, "raw_mean": None, "raw_max": None, "raw_std": None, "hf_energy_ratio": None}
    return {"raw_len": int(len(x)), "raw_mean": _f(x.mean()), "raw_max": _f(x.max()), "raw_std": _f(x.std()),
            "hf_energy_ratio": _hf_energy_ratio(x)}


@tensorleap_metadata("deformation_trace", {"raw_len": DatasetMetadataType.int, "raw_final": DatasetMetadataType.float,
                                           "raw_range": DatasetMetadataType.float, "raw_max": DatasetMetadataType.float,
                                           "trailing_zero_fixed": DatasetMetadataType.boolean,
                                           "len_diff_vs_current": DatasetMetadataType.int})
def deformation_trace_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict:
    d, i = _row(sample_id, preprocess)
    x = np.asarray(d["raw_deformation"][i], dtype=np.float64)
    if len(x) == 0:
        return {"raw_len": 0, "raw_final": None, "raw_range": None, "raw_max": None,
                "trailing_zero_fixed": bool(d["deformation_trailing_zero_fixed"][i]), "len_diff_vs_current": None}
    return {"raw_len": int(len(x)), "raw_final": _f(x[-1]), "raw_range": _f(x.max() - x.min()), "raw_max": _f(x.max()),
            "trailing_zero_fixed": bool(d["deformation_trailing_zero_fixed"][i]),
            "len_diff_vs_current": int(d["deformation_raw_len"][i] - d["current_raw_len"][i])}


@tensorleap_metadata("autoencoder", {"reconstruction_mse": DatasetMetadataType.float, "latent_norm": DatasetMetadataType.float,
                                     "discriminator_max_prob": DatasetMetadataType.float,
                                     "discriminator_entropy": DatasetMetadataType.float})
def autoencoder_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict:
    d, i = _row(sample_id, preprocess)
    out = {"reconstruction_mse": None, "latent_norm": None, "discriminator_max_prob": None, "discriminator_entropy": None}
    if d["reconstruction_output"] is not None:
        out["reconstruction_mse"] = _f(np.mean((d["reconstruction_output"][i] - d["deformation_trace"][i]) ** 2))
    if d["latent_space"] is not None:
        out["latent_norm"] = _f(np.linalg.norm(d["latent_space"][i]))
    if d["discriminator_output"] is not None:
        logits = np.asarray(d["discriminator_output"][i], dtype=np.float64)
        p = np.exp(logits - logits.max())
        p /= p.sum()
        out["discriminator_max_prob"] = _f(p.max())
        out["discriminator_entropy"] = _f(-(p * np.log(p + 1e-12)).sum())
    return out
