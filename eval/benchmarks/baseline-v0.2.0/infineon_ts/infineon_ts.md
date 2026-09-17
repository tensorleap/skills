# Skill-eval report — infineon_ts

- **Result:** ✅ PASS   (success = a FINISHED evaluate)
- **Push:** ✅ FINISHED — 6aaa651f2f329df70cd36ea9
- **Evaluate job:** 6aaa65952f329df70cd36eb7
- **Got stuck (where):** —
- **Skill invoked:** 1 `Skill`-tool call(s) to `tensorleap-integration-creation` across 114 turns
- **Model:** claude-fable-5-1
- **Skill source:** --plugin-dir /Users/orram/Tensorleap/skills/dist/claude/integration
- **Provenance:** skill `7d891c54e522` (git acff89f) · fixture `6cea3961bcd0` · prompt `949ae7626711` · CC 2.1.273 · effort high

## Metrics

| metric | value |
|--------|------:|
| wall clock | 21m37s |
| turns | 114 |
| output tokens | 128.9k |
| input tokens | 3.4k |
| cache read | 14.2M |
| cache write | 373.5k |
| total tokens | 14.8M |
| **est. cost (USD)** | **$38.09** |

_Cost uses assumed rates (per 1M tok): in $15.0, out $75.0, cache-read $1.5, cache-write $18.75. Override with REPORT_*_RATE env vars. `out_tokens`/`turns` are the comparable work metrics; `total_tokens` is cache-inflated._

## Main problems

From `NOTES.md`:

```
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
```

## Transcript

`/Users/orram/.claude/projects/-Users-orram-Tensorleap-skills-eval--fixtures-infineon-ts-pre/70a5d087-d2d7-49c1-bd0e-752a2ef71adb.jsonl`
