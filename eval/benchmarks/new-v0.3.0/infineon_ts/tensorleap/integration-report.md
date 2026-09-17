# Integration report — Infineon wire-bond shear regression (ONNX time series)

## Summary
- Model: `model/enhanced_trial_19_full_model_complete.onnx` (uploaded separately, not bundled).
  Inputs `current_trace`, `deformation_trace` — float32 (batch, 94). Output `vst_prediction` — float32 (batch,),
  the shear value normalized by surface type (IGBT/1550, DCB/1300, DIODE & NETZDIODE/1400).
- Data: labeled parquet + `unlabeled/` parquets + `derived/` latent/discriminator/reconstruction `.npy` sidecars, all
  under the configured dataset volume `/Users/orram/Tensorleap/data/eval/infineon_ts/ts` (reused in place, no copy).
- Splits: equipment-based 80/20 split (random_state 42), reproduced with a numpy replica of sklearn's
  `train_test_split`; verified bit-identical to the repo's `WirebondDataset` (train 821 / val 235 / unlabeled 2000).
  `data.sample_limit_per_split: 250` caps each split -> training 250, validation 235 (all), unlabeled 250.
- Components: 2 input encoders, 1 GT encoder (`shear_value_normed`, shape (1,); empty array for unlabeled),
  custom loss `mse_normed`, metrics (abs_error_normed, abs_error_shear, relative_error, predicted_shear_value,
  signed_error_shear), 7 metadata groups (35 keys), 4 visualizers (2 input graphs, deformation vs autoencoder
  reconstruction, prediction horizontal bar with GT).
- Local sanity: corr(pred, gt) 0.90 train / 0.92 val; MAE ~79 / ~66 shear units on the capped splits.

## Layout decisions
- `project_config.yaml` stays at the repo root (the repo's own `infineon/config.py` contract; operator asked for
  edits there). `tensorleap/config.py` loads it without the experiment-name sha prefixing, because the staged
  sidecars are named with the fixed prefix `test_unlabeled_9af990a3_` (added `latent_space_and_outputs.folder`
  and `file_prefix` keys).
- Preprocessing is a numpy/pandas port of `infineon/wirebonsdataset_page.py` (avoids torch + sklearn on the platform).
- Root `requirements.txt` replaced with the lean runtime list (numpy, pandas, pyarrow, PyYAML, onnxruntime). The
  original (torch, ruptures, nolds, scikit-learn, scipy, matplotlib) is preserved in git history.

## Issues log
1. `poetry add code-loader` failed: poetry's resolver tried to build scipy 1.6.1 from sdist
   ("scipy (1.6.1) not supporting PEP 517 builds"). Resolution: left pyproject/lock untouched and ran
   `poetry run pip install code-loader` (1.0.207) into the repo venv.
2. `shear_gt warning: Tensorleap will add a batch dimension at axis 0 ... axis 0 is already size 1` — informational;
   the GT is a genuine 1-channel regression target of shape (1,). Kept.
3. Unlabeled `wire_number`/`bond_number` came back None for every sample: concatenating the two unlabeled parquets
   adds a mostly-empty `Wire_Bond` column (from `dummy_unlabeled.parquet`) and the derivation overwrote the native
   int16 columns of `W00165844_WB-31.pq`. Fixed: derived values only fill missing native values.
   (The repo's `WirebondDataset` has the same overwrite behavior — noted, not changed.)
4. Unlabeled `Timestamp` is the aliased float `Time` column (e.g. 8.734), not a datetime -> `record_time` metadata
   returns None for unlabeled samples (deliberate, not fabricated).
5. Discriminator sidecar argmax does not match the equipment class (6% / 27%) — expected for an adversarial (GRL)
   discriminator; exposed as `discriminator_max_prob` / `discriminator_entropy` metadata rather than a class label.
6. Unlabeled file order: `WirebondDataset.merge_chunks` uses `os.listdir` order; the integration sorts file names
   (same order here: `W00165844_WB-31.pq`, `dummy_unlabeled.parquet`), and the reconstruction sidecars were verified
   aligned (per-sample corr with the deformation trace 0.998 vs 0.95 when shifted by one row).

## Open
- Platform evaluate result: see NOTES.md for the push / eval run ids and terminal state.
