from typing import Dict, Optional

import numpy as np
import pandas as pd

from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DatasetMetadataType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_metadata

from preprocess import CONFIG, SURFACE_CODE_TO_NAME, row_index

FS_HZ = CONFIG["tl_metadata"]["fs"]
CUTOFF_HZ = CONFIG["tl_metadata"]["cutoff_hz"]
LATENT_FEATURE_DIMS = 5  # z[:, :-3] feeds the regressor; z[:, -3:] are the physics params (m, c, k)


def _str(value) -> Optional[str]:
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NA:
        return None
    return str(value)


def _float(value) -> Optional[float]:
    if value is None or value is pd.NA:
        return None
    value = float(value)
    return None if np.isnan(value) else value


def _int(value) -> Optional[int]:
    value = _float(value)
    return None if value is None else int(value)


def _row(sample_id: str, preprocess: PreprocessResponse) -> pd.Series:
    return preprocess.data["df"].iloc[row_index(sample_id)]


def _high_frequency_energy_fraction(trace: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(trace - trace.mean())) ** 2
    freqs = np.fft.rfftfreq(len(trace), d=1.0 / FS_HZ)
    total = spectrum.sum()
    return float(spectrum[freqs > CUTOFF_HZ].sum() / total) if total > 0 else 0.0


@tensorleap_metadata(
    name="sample",
    metadata_type={
        "surface_type": DatasetMetadataType.string,
        "surface_code": DatasetMetadataType.int,
        "equipment": DatasetMetadataType.string,
        "equipment_id": DatasetMetadataType.float,
        "dcb_class": DatasetMetadataType.int,
        "package": DatasetMetadataType.string,
        "lot_id": DatasetMetadataType.string,
        "recipe": DatasetMetadataType.float,
        "wire_number": DatasetMetadataType.int,
        "bond_number": DatasetMetadataType.int,
        "shear_sequence_no": DatasetMetadataType.int,
        "timestamp": DatasetMetadataType.string,
        "unique_id": DatasetMetadataType.string,
        "current_length": DatasetMetadataType.int,
        "deformation_length": DatasetMetadataType.int,
    },
)
def sample_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, object]:
    row = _row(sample_id, preprocess)
    idx = row_index(sample_id)
    surface_code = int(preprocess.data["surface"][idx])
    return {
        "surface_type": SURFACE_CODE_TO_NAME.get(surface_code, "UNKNOWN"),
        "surface_code": surface_code,
        "equipment": _str(row[CONFIG["columns"]["equipment_id"]]),
        "equipment_id": _float(row["equipment_id"]),
        "dcb_class": int(preprocess.data["dcb_class"][idx]),
        "package": _str(row[CONFIG["columns"]["package"]]),
        "lot_id": _str(row[CONFIG["columns"]["lot_id"]]),
        "recipe": _float(row[CONFIG["columns"]["recipe"]]),
        "wire_number": _int(row["wire_number"]),
        "bond_number": _int(row["bond_number"]),
        "shear_sequence_no": _int(row[CONFIG["columns"]["shear_sequence_no"]]),
        "timestamp": _str(row[CONFIG["columns"]["timestamp"]]),
        "unique_id": _str(row["unique_id"]),
        "current_length": int(preprocess.data["current_lengths"][idx]),
        "deformation_length": int(preprocess.data["deformation_lengths"][idx]),
    }


@tensorleap_metadata(
    name="gt_shear",
    metadata_type={
        "value": DatasetMetadataType.float,
        "value_normed": DatasetMetadataType.float,
        "grade": DatasetMetadataType.float,
        "norm_shear": DatasetMetadataType.float,
    },
)
def shear_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, Optional[float]]:
    row = _row(sample_id, preprocess)
    return {
        "value": _float(row[CONFIG["columns"]["shear_value"]]),
        "value_normed": _float(preprocess.data["shear_value_normed"][row_index(sample_id)]),
        "grade": _float(row["ShearGrade"]),
        "norm_shear": _float(row["NormShear"]),
    }


@tensorleap_metadata(
    name="trace",
    metadata_type={
        "current_peak": DatasetMetadataType.float,
        "current_mean": DatasetMetadataType.float,
        "current_final": DatasetMetadataType.float,
        "current_peak_position": DatasetMetadataType.float,
        "deformation_max": DatasetMetadataType.float,
        "deformation_final": DatasetMetadataType.float,
        "deformation_rise": DatasetMetadataType.float,
        "current_hf_energy_fraction": DatasetMetadataType.float,
        "deformation_hf_energy_fraction": DatasetMetadataType.float,
    },
)
def trace_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, float]:
    idx = row_index(sample_id)
    raw_current = preprocess.data["raw_current_interp"][idx]
    raw_deformation = preprocess.data["raw_deformation_interp"][idx]
    current_len = int(preprocess.data["current_lengths"][idx])
    return {
        "current_peak": float(raw_current.max()),
        "current_mean": float(raw_current.mean()),
        "current_final": float(raw_current[-1]),
        "current_peak_position": float(np.argmax(raw_current) / max(min(current_len, len(raw_current)) - 1, 1)),
        "deformation_max": float(raw_deformation.max()),
        "deformation_final": float(raw_deformation[-1]),
        "deformation_rise": float(raw_deformation[-1] - raw_deformation[0]),
        "current_hf_energy_fraction": _high_frequency_energy_fraction(preprocess.data["current_trace"][idx]),
        "deformation_hf_energy_fraction": _high_frequency_energy_fraction(preprocess.data["deformation_trace"][idx]),
    }


@tensorleap_metadata(
    name="latent",
    metadata_type={
        **{f"z_{i}": DatasetMetadataType.float for i in range(LATENT_FEATURE_DIMS)},
        "norm": DatasetMetadataType.float,
        "phys_m": DatasetMetadataType.float,
        "phys_c": DatasetMetadataType.float,
        "phys_k": DatasetMetadataType.float,
        "phys_omega_n": DatasetMetadataType.float,
    },
)
def latent_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, float]:
    z = preprocess.data["latent_space"][row_index(sample_id)].astype(np.float64)
    m, c, k = np.logaddexp(0.0, z[-3:]) + 1e-6  # softplus, as in the model's forward pass
    out = {f"z_{i}": float(z[i]) for i in range(LATENT_FEATURE_DIMS)}
    out.update({
        "norm": float(np.linalg.norm(z)),
        "phys_m": float(m),
        "phys_c": float(c),
        "phys_k": float(k),
        "phys_omega_n": float(np.sqrt(k / m)),
    })
    return out


@tensorleap_metadata(
    name="discriminator",
    metadata_type={
        "pred_class": DatasetMetadataType.int,
        "confidence": DatasetMetadataType.float,
        "entropy": DatasetMetadataType.float,
    },
)
def discriminator_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, float]:
    logits = preprocess.data["discriminator_output"][row_index(sample_id)].astype(np.float64)
    probs = np.exp(logits - logits.max())
    probs /= probs.sum()
    return {
        "pred_class": int(np.argmax(probs)),
        "confidence": float(probs.max()),
        "entropy": float(-(probs * np.log(probs + 1e-12)).sum()),
    }


@tensorleap_metadata(
    name="reconstruction",
    metadata_type={"mse": DatasetMetadataType.float, "max_abs_error": DatasetMetadataType.float},
)
def reconstruction_metadata(sample_id: str, preprocess: PreprocessResponse) -> Dict[str, float]:
    idx = row_index(sample_id)
    error = preprocess.data["reconstruction_output"][idx] - preprocess.data["deformation_trace"][idx]
    return {"mse": float(np.mean(error ** 2)), "max_abs_error": float(np.abs(error).max())}
