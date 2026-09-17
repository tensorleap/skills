import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_preprocess

from config import (CONFIG, DEFAULT_SURFACE, MAX_SEQ_LEN, SURFACE_CODES, SURFACE_NORM_FACTOR,
                    SURFACE_TARGET_LEN)

COLS = CONFIG["columns"]
CURRENT_COL = COLS["current_trace"]
DEFORMATION_COL = COLS["deformation_trace"]
SURFACE_COL = COLS["surface_type"]
SHEAR_COL = COLS["shear_value"]
EQUIPMENT_COL = COLS["equipment_id"]

# same alias resolution as WirebondDataset / UNIQUE_COLUMNS_MAPPING (unlabeled parquets use lower-case names)
_ALIASES = {canonical: opts for canonical, opts in CONFIG["UNIQUE_COLUMNS_MAPPING"].items()}
_META_COLUMNS = [COLS["package"], COLS["recipe"], COLS["bau_no"], COLS["lot_id"], COLS["timestamp"],
                 COLS["shear_sequence_no"], COLS["wire_bond"], "ShearGrade", "wire_number", "bond_number"]


def _apply_aliases(df: pd.DataFrame) -> pd.DataFrame:
    for canonical, options in _ALIASES.items():
        present = [c for c in options if c in df.columns]
        if canonical not in df.columns and present:
            df = df.rename(columns={present[0]: canonical})
            present = present[1:]
        for c in present:
            if c in df.columns and c != canonical:
                df[canonical] = df[canonical].where(df[canonical].notna(), df[c])
        df = df.drop(columns=[c for c in present if c != canonical], errors="ignore")
    return df


def _derive_wire_bond(df: pd.DataFrame) -> pd.DataFrame:
    wb_col = COLS["wire_bond"]
    if wb_col in df.columns:
        parsed = df[wb_col].astype(str).str.extract(r"(?i)w(\d+)_b(\d+)", expand=True)
        for col, part in (("wire_number", 0), ("bond_number", 1)):
            derived = pd.to_numeric(parsed[part], errors="coerce")
            if col in df.columns:
                native = pd.to_numeric(df[col], errors="coerce")
                df[col] = native.where(native.notna(), derived)
            else:
                df[col] = derived
    return df


def _equipment_ids(series: pd.Series) -> np.ndarray:
    ids = series.astype("string")
    ids = ids.where(ids.isna(), ids.str.extract(r"(\d+)(?:-|$)", expand=False))
    return ids.astype("float32").to_numpy(dtype=np.float32, na_value=np.nan)


def _shuffle_split(unique_ids: np.ndarray, test_size: float, random_state: int):
    # numpy replica of sklearn.model_selection.train_test_split(shuffle=True) used by WirebondDataset
    n = len(unique_ids)
    n_test = int(np.ceil(test_size * n))
    perm = np.random.RandomState(random_state).permutation(n)
    return unique_ids[perm[n_test:]], unique_ids[perm[:n_test]]


def _zscore(seq: np.ndarray) -> np.ndarray:
    seq = np.asarray(seq, dtype=np.float64)
    if len(seq) == 0:
        return seq
    mean, std = seq.mean(), seq.std(ddof=1) if len(seq) > 1 else 0.0
    return (seq - mean) / std if std > 0 else seq - mean


def _interpolate(seq: np.ndarray, target_length: int) -> np.ndarray:
    if len(seq) > 1:
        return np.interp(np.linspace(1 / target_length, 1, num=target_length),
                         np.linspace(1 / len(seq), 1, num=len(seq)), seq)
    return np.repeat(seq, target_length)


def _target_length(surface: str, seq_len: int, labeled: bool) -> int:
    if not labeled and seq_len > 99:
        return 101
    return SURFACE_TARGET_LEN.get(surface, MAX_SEQ_LEN)


def _pad_truncate(seq: np.ndarray, max_len: int) -> np.ndarray:
    seq = np.asarray(seq, dtype=np.float32)
    if len(seq) < max_len:
        return np.pad(seq, (0, max_len - len(seq)), "constant")
    return seq[:max_len]


def _fix_deformation_curve(curve) -> np.ndarray:
    curve = np.array(curve, dtype=np.float32)
    if len(curve) > 0 and curve[-1] == 0:
        non_zero = np.where(curve != 0)[0]
        if len(non_zero) > 0:
            curve[-1] = curve[non_zero[-1]]
    return curve


def _encode_traces(df: pd.DataFrame, labeled: bool) -> Dict[str, np.ndarray]:
    surfaces = df[SURFACE_COL].tolist()
    raw_cur = [np.asarray(x, dtype=np.float64) for x in df[CURRENT_COL].tolist()]
    raw_def = [np.asarray(x, dtype=np.float64) for x in df[DEFORMATION_COL].tolist()]
    fixed_flag = np.zeros(len(df), dtype=bool)
    if not labeled:
        fixed_flag = np.array([len(d) > 0 and d[-1] == 0 and np.any(d != 0) for d in raw_def])
        raw_def = [_fix_deformation_curve(d).astype(np.float64) for d in raw_def]
    cur, defo, cur_len, def_len = [], [], [], []
    for s, c, d in zip(surfaces, raw_cur, raw_def):
        cur.append(_pad_truncate(_interpolate(_zscore(c), _target_length(s, len(c), labeled)), MAX_SEQ_LEN))
        defo.append(_pad_truncate(_interpolate(_zscore(d), _target_length(s, len(d), labeled)), MAX_SEQ_LEN))
        cur_len.append(len(c))
        def_len.append(len(d))
    return {
        "current_trace": np.stack(cur).astype(np.float32),
        "deformation_trace": np.stack(defo).astype(np.float32),
        "current_raw_len": np.asarray(cur_len, dtype=np.int64),
        "deformation_raw_len": np.asarray(def_len, dtype=np.int64),
        "deformation_trailing_zero_fixed": fixed_flag,
        "raw_current": np.array(raw_cur, dtype=object),
        "raw_deformation": np.array(raw_def, dtype=object),
    }


def _load_labeled() -> pd.DataFrame:
    path = CONFIG["data"]["data_path"]
    df = pd.read_parquet(path)
    df["unique_id"] = df.index.astype(str) + "_" + Path(path).stem
    df["source_file"] = os.path.basename(path)
    df = _apply_aliases(df)
    if SURFACE_COL not in df.columns:
        df[SURFACE_COL] = DEFAULT_SURFACE
    df = _derive_wire_bond(df)
    df = df.dropna(subset=[SHEAR_COL])
    df = df[(df[SHEAR_COL] <= CONFIG["data"]["outlier_max"]) & (df[SHEAR_COL] >= CONFIG["data"]["outlier_min"])]
    df = df[df[SHEAR_COL] != 0]
    return df


def _load_unlabeled() -> pd.DataFrame:
    ucfg = CONFIG["data"]["data_unlabeled"]
    folder = ucfg["folder_name"]
    files = ucfg["files_to_use"]
    all_names = sorted(os.listdir(folder))
    if isinstance(files, list):
        names = [os.path.basename(f) for f in files]
    elif isinstance(files, int):
        names = all_names[:files]
    else:
        names = all_names
    chunks = []
    for name in names:
        chunk = pd.read_parquet(os.path.join(folder, name))
        chunk["unique_id"] = chunk.index.astype(str) + "_" + name
        chunk["source_file"] = name
        chunk = _apply_aliases(chunk)
        if SHEAR_COL in chunk.columns:
            chunk = chunk.dropna(subset=[SHEAR_COL])
            chunk = chunk[(chunk[SHEAR_COL] <= CONFIG["data"]["outlier_max"]) & (chunk[SHEAR_COL] != 0)]
        chunks.append(chunk)
    df = pd.concat(chunks, ignore_index=True)
    start = int(ucfg["start_index"])
    num = ucfg["num_samples"]
    num = len(df) if num == "all" else int(num)
    df = df.iloc[start:start + num]
    if SURFACE_COL not in df.columns:
        df[SURFACE_COL] = DEFAULT_SURFACE
    df[SURFACE_COL] = df[SURFACE_COL].fillna(DEFAULT_SURFACE)
    return _derive_wire_bond(df)


def _sidecar(group: str, split_key: str) -> Optional[np.ndarray]:
    lso = CONFIG["latent_space_and_outputs"]
    path = os.path.join(lso["folder"], lso.get("file_prefix", "") + lso[group][f"{split_key}_filename"])
    return np.load(path) if os.path.exists(path) else None


def _build_split_data(df: pd.DataFrame, labeled: bool, split_key: str, limit: Optional[int]) -> Dict:
    df = df.reset_index(drop=True)
    n_total = len(df)
    traces = _encode_traces(df, labeled)
    surface_codes = df[SURFACE_COL].map(SURFACE_CODES).fillna(SURFACE_CODES[DEFAULT_SURFACE]).astype(np.int64).to_numpy()
    norm_factor = np.array([SURFACE_NORM_FACTOR.get(int(c), SURFACE_NORM_FACTOR[2]) for c in surface_codes], dtype=np.float32)
    if labeled:
        shear = df[SHEAR_COL].to_numpy(dtype=np.float32)
    else:
        shear = np.full(n_total, np.nan, dtype=np.float32)
    data = {
        "labeled": labeled,
        "sample_ids": df["unique_id"].astype(str).tolist(),
        "surface_code": surface_codes,
        "surface_name": df[SURFACE_COL].astype(str).to_numpy(),
        "norm_factor": norm_factor,
        "shear_value": shear,
        "shear_value_normed": (shear / norm_factor).astype(np.float32),
        "equipment_id": _equipment_ids(df[EQUIPMENT_COL]) if EQUIPMENT_COL in df.columns else np.full(n_total, np.nan, np.float32),
        "source_file": df["source_file"].astype(str).to_numpy(),
    }
    data.update(traces)
    for col in _META_COLUMNS:
        data[col] = df[col].to_numpy() if col in df.columns else np.full(n_total, None, dtype=object)
    for group in ("latent_space", "discriminator_output", "reconstruction_output"):
        arr = _sidecar(group, split_key)
        data[group] = arr if arr is not None and len(arr) == n_total else None
    if limit:
        keep = min(int(limit), n_total)
        data = {k: (v[:keep] if isinstance(v, (np.ndarray, list)) and len(v) == n_total else v) for k, v in data.items()}
    data["index"] = {sid: i for i, sid in enumerate(data["sample_ids"])}
    return data


@tensorleap_preprocess()
def preprocess() -> List[PreprocessResponse]:
    limit = CONFIG["data"].get("sample_limit_per_split")
    limit = int(limit) if limit else None
    split_cfg = CONFIG["split"]

    labeled = _load_labeled()
    eq_ids = _equipment_ids(labeled[EQUIPMENT_COL])
    labeled = labeled.assign(_eq=eq_ids)
    train_eq, val_eq = _shuffle_split(np.sort(labeled["_eq"].unique()), split_cfg["test_size"], split_cfg["random_state"])
    train_df = labeled[labeled["_eq"].isin(train_eq)]
    val_df = labeled[labeled["_eq"].isin(val_eq)]

    train_data = _build_split_data(train_df, True, "train", limit)
    val_data = _build_split_data(val_df, True, "val", limit)
    responses = [
        PreprocessResponse(sample_ids=train_data["sample_ids"], data=train_data, state=DataStateType.training),
        PreprocessResponse(sample_ids=val_data["sample_ids"], data=val_data, state=DataStateType.validation),
    ]
    if CONFIG["data"]["data_unlabeled"]["use_unlabeled"]:
        unlabeled_data = _build_split_data(_load_unlabeled(), False, "unlabeled", limit)
        responses.append(PreprocessResponse(sample_ids=unlabeled_data["sample_ids"], data=unlabeled_data,
                                            state=DataStateType.unlabeled))
    return responses
