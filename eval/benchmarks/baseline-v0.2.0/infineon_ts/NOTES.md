# Tensorleap integration notes (infineon_ts)

Session log; not committed.

## Setup
- Preflight gate: OK (local server http://localhost:4589, data volumes `/Users/orram/data` and `/Users/orram/Tensorleap/data`, authenticated as or.ram@tensorleap.ai).
- Env: repo's poetry venv (`.venv`, Python 3.10.14). `poetry add code-loader` failed (resolver downgraded scipy to 1.6.1 which does not build), so `code-loader==1.0.207` and `pyyaml` were installed with `poetry run pip install` (pyproject/lock left untouched).
- Data: staged under `/Users/orram/Tensorleap/data/eval/infineon_ts/ts`, which is on the `/Users/orram/Tensorleap/data` data volume -> reused in place (no copy).
  `project_config.yaml` `data.data_path` / `data.data_unlabeled.folder_name` now point there.
- Sidecars: `.npy` latent/discriminator/reconstruction files were missing; generated with `tensorleap/generate_sidecars.py` from the full `.pth` model
  into `/Users/orram/Tensorleap/data/eval/infineon_ts/ts/derived` (new key `latent_space_and_outputs.output_dir`).
- Cap: `tensorleap.sample_limit_per_split: 250` in `project_config.yaml` (user requested a capped eval).

## Push / eval
(filled in below)
- Project: `infineon-wirebond-vst` (projectId 6aaa64e52f329df70cd36e9e) created with `leap projects create`; note the CLI overwrote leap.yaml with a template, rewritten afterwards.
- Local validation: run loop all 9 rows green (3 samples x training + validation), `tl_check.py` isValid=True, 48 payloads passed, no default-use warnings.
- Push command (background, log in push.log):
  `leap push -m model/enhanced_trial_19_full_model_complete.onnx --type ONNX -n v1-capped-250 -b 64 --eval --yes`
