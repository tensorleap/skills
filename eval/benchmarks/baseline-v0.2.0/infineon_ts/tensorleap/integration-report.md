# Integration report — Infineon wire-bond shear-value regression (ONNX)

Issues encountered while authoring the Tensorleap integration, with cause and resolution.

## Environment
- `poetry add code-loader` failed: the resolver tried to downgrade scipy 1.10.1 -> 1.6.1 (no wheel for py3.10/arm64, source build fails).
  Resolution: `poetry run pip install "code-loader>=1.0.142" pyyaml` into the existing venv; pyproject/poetry.lock left unchanged.
  `pyyaml` was also missing from the venv even though `infineon/config.py` imports it.

## Data / sidecars
- The `.npy` latent-space / discriminator / reconstruction sidecars referenced in `project_config.yaml` were not staged.
  Resolution: `tensorleap/generate_sidecars.py` runs the full PyTorch model (`model/*.pth`, architecture copied from the notebook into
  `tensorleap/vst_model.py`) over each split of `WirebondDataset` and writes the arrays to `latent_space_and_outputs.output_dir`
  (`/Users/orram/Tensorleap/data/eval/infineon_ts/ts/derived`, on the data volume).
  Caveat: `infineon/config.py` prefixes the filenames with a sha of the whole config, so editing `project_config.yaml`
  (including `sample_limit_per_split`) requires re-running the generator.
- Unlabeled split: `merge_chunks` concatenates `W00165844_WB-31.pq` (30k rows, different column names) and `dummy_unlabeled.parquet`,
  then takes the first 2000 rows; all of them come from the first file and have no surface type, so the repo's dataset code maps them to code 0.
  Left as-is (repo data contract), noted here only.

## Authoring signals
- `shear_value_gt warning: Tensorleap will add a batch dimension at axis 0 ... axis 0 is already size 1` — the GT is a single regression
  value returned as shape `(1,)`. This is a real dimension (not a pre-batched output), so it is left as-is; the loss/metrics flatten
  per-sample values with `reshape(batch, -1)[:, 0]` and accept both `(B,)` predictions and `(B, 1)` ground truth.
- The `shear` metadata group emitted a key `shear_value_normed`, identical to the GT encoder name. Renamed the group to `gt_shear`
  (`gt_shear_value`, `gt_shear_value_normed`, `gt_shear_grade`, `gt_shear_norm_shear`) to avoid the collision.
- `leap projects create` overwrote the hand-written `leap.yaml` with an empty template (`projectId: "" ... include: []`);
  the manifest had to be rewritten with the new project id afterwards.

## Design notes
- Model I/O (ONNX): inputs `current_trace`, `deformation_trace` both `(batch, 94)` float32; single output `vst_prediction` `(batch,)` =
  normalized shear value (ShearValue / surface-type factor). Prediction type `vst_prediction` with one label `shear_value_normed`.
- Splits: repo's equipment-based split (training 821 / validation 235 rows) plus 2000 unlabeled rows, capped at
  `tensorleap.sample_limit_per_split` (250) per split -> 250 / 235 / 250.
- Reconstruction sidecar: the decoder reconstructs the *normalized deformation trace* (corr 0.998 on training data), so the
  `reconstruction` metadata/visualizer compare against `deformation_trace`.
- Discriminator sidecar: argmax accuracy on training equipment classes is ~6% (adversarial gradient-reversal training), exposed as
  `discriminator_pred_class / confidence / entropy` metadata only.
- Dependencies: `requirements.txt` at the repo root was rewritten to the integration's runtime imports (numpy, pandas, pyarrow, pyyaml,
  scikit-learn, torch, onnxruntime). `torch` is required because the repo's `WirebondDataset` returns tensors. The original file
  (matplotlib / ruptures / nolds pins) is in git history.
