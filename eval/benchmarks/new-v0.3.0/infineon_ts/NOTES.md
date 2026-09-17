# NOTES — Tensorleap integration for Infineon wire-bond shear regression (ONNX)

Session log (not committed).

## Setup
- Invoked skill `integration:tensorleap-integration-creation` (Skill tool) as first action.
- Preflight gate: PASS — CLI `leap` (shim), local server http://localhost:4589, data volumes
  `/Users/orram/data` and `/Users/orram/Tensorleap/data`, authenticated as or.ram@tensorleap.ai.
- Python env: repo's poetry venv (`.venv`, Python 3.10.14). `poetry add code-loader` failed
  (poetry's resolver tried to rebuild scipy 1.6.1 from sdist); pyproject/lock left untouched and
  code-loader 1.0.207 installed with `poetry run pip install code-loader` instead.
- Data delivery: local server + data already on a configured dataset volume
  (`/Users/orram/Tensorleap/data/eval/infineon_ts/ts`) -> reuse in place, no copy.
  Emptiness gate: labeled parquet 1060 rows x 41 cols; unlabeled/ has 2 parquets (30319 + 100 rows);
  derived/ has the 9 latent/discriminator/reconstruction .npy sidecars (train 821, val 235, unlabeled 2000).
- Model contract (onnxruntime): inputs `current_trace` (batch,94) f32, `deformation_trace` (batch,94) f32;
  output `vst_prediction` (batch,) f32 = surface-normalized shear value.
- project_config.yaml edited: data.data_path / data.data_unlabeled.folder_name -> staged volume paths,
  `data.sample_limit_per_split: 250`, `latent_space_and_outputs.folder` + `file_prefix` for the sidecars.

## Authoring (run loop after every stage; all clean)
1. Skeleton + preprocess -> table shows preprocess ✅; splits training 250 / validation 235 / unlabeled 250.
2. Numpy port verified against `WirebondDataset` reference tensors: identical ids, traces, normalized GT, surfaces, eq ids.
3. Input encoders + GT + load_model + minimal integration_test -> first `Successful!`.
4. Custom loss `mse_normed` + 5 metrics -> "All mandatory parts have been successfully set".
5. 7 metadata groups + 4 visualizers -> "All parts have been successfully set"; 3 train + 3 val + 1 unlabeled samples pass.
6. `tl_check.py`: isValid True, no generalError, all 35 handlers passed.
7. Fixed unlabeled wire_number/bond_number overwrite (see tensorleap/integration-report.md issue 3).

## Deploy
- Project created: `infineon-ts-wirebond-shear` (id 6aaaadce40b289f1d18c1ccf), set in leap.yaml.
- Before push: `leap run list -t Push` showed no in-flight Push run.
- Push command (background, log in push.log):
  `leap push -m model/enhanced_trial_19_full_model_complete.onnx -n enhanced_trial_19_v1 -b 32 --eval`

### Push attempt 1 (17:55) — FAILED on the wrong backend
- Project `infineon-ts-wirebond-shear` id 6aaaadce40b289f1d18c1ccf, Push run 6aaaadef40b289f1d18c1cda -> FAILED:
  `The repository with name 'engine-generic' does not exist in the registry with id '633875197750'`.
- Diagnosis: between ~17:53 and ~17:59 localhost:4589 was answered by a different Tensorleap backend, not the
  local k3d server. Evidence: (a) run/project ids created then carry process signature `40b289f1d18c1c…` while
  every k3d job (pods `push-…2f329df70cd36…`) carries `2f329df70cd36…`; (b) the k3d node-server log shows zero CLI
  requests 17:53–17:59 and no trace of the push id; (c) ECR account 633875197750 is not the local registry
  (tensorleap-registry:5000, which does hold `engine-generic:1.6.74-36fb1a2c-py310/py38/py312`);
  (d) after 18:00 `leap run list` / `leap projects list` no longer show that project or run.
  Treated as an environment condition (transient port-forward / other backend), not an integration error.
- Side fixes kept from the investigation: `tensorleap/__pycache__/**` excluded in leap.yaml (pyc files were bundled).
  pythonVersion briefly set to py38, reverted to py310 (venv is 3.10; the py310 base image exists locally and
  other py310 pushes succeeded on this server today). Log kept as push_attempt1_wrong_server.log.

### Push attempt 2 (18:0x) — local k3d server
- Project recreated on the local server: `infineon-ts-wirebond-shear` id 6aaaafe02f329df70cd36ff9.
- No in-flight Push before pushing. Command (background, log in push.log):
  `leap push -m model/enhanced_trial_19_full_model_complete.onnx -n enhanced_trial_19_v1 -b 32 --eval`
- Push run id: 6aaaafff2f329df70cd37004 (QUEUED at 18:04). The local `leap push` CLI process was killed by the harness
  (host low on memory) at ~18:05, so its `--eval` step will NOT fire automatically when the push finishes.
- 20:33 status: still QUEUED behind another project's Evaluate 6aaaaf732f329df70cd36ff3 (STARTED 18:02, running
  150+ min, progressing slowly under throttling). Host: 47G used / 85M unused / 20G compressed; k3d node memory
  requests at 85%; mongodb/keycloak/ingress readiness probes timing out. Two background watchers were also killed by
  the harness for memory. Treated as an environment problem (per working agreement) — stopped without re-pushing.

## Resume steps (when the cluster frees up)
1. `leap run list -t Push | grep 6aaaafff` — wait for FINISHED (if FAILED: `leap run logs 6aaaafff2f329df70cd37004`).
2. No Evaluate will exist for this version; trigger it with a full re-evaluation overwrite from the repo root:
   `leap push -o enhanced_trial_19_v1 -u metric -b 32 < /dev/null > push2.log 2>&1`
3. `leap run list -t Evaluate` -> note EVAL_ID, watch until FINISHED/FAILED; on failure read `leap run logs <EVAL_ID>`.
4. Cap: `data.sample_limit_per_split: 250` in project_config.yaml (set to null/0 for the full dataset).
