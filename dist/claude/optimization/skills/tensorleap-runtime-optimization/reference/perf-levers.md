# Levers you control from the integration

Everything here is changed in the integration's own code or in `leap push` flags, and is
**lossless when applied correctly** — `tl_perf compare` must say so before a change is kept.
Anything that changes outputs belongs in `perf-lossy-options.md` instead.

| Lever | Use when | How | Verify / watch out |
|---|---|---|---|
| **Shared per-sample cache** | several components decode/transform the same input (catalog A) | one pure function keyed on the source path/id, `functools.lru_cache(maxsize≈components)`; callers `.copy()` before mutating | the cached step must be deterministic; never size it for the dataset; visualizers can't use it |
| **Grouped preprocess** | data files hold many samples and reads thrash (catalog B) | `PreprocessResponse(sample_ids=[[...file A...], [...file B...]])`, encoders accept a list of ids (code-loader ≥ 1.0.196) | incompatible with element instances, simulations, instance custom latent spaces; per-row equality check |
| **Batch size** | always — pick it from data, not habit | `tl_perf floor` (throughput knee) + `tl_perf fit` (memory) → `leap push -b <N>` | batch-size support of every metric/loss (`fit` checks it); a fixed model batch dimension overrides `-b` |
| **Vectorized metrics/loss** | metric or loss cost scales with rows (catalog D, G) | numpy over the batch axis; no per-row Python loops | one value per sample; `compare` equivalent |
| **Cheap preprocess** | startup is large (catalog C, E) | keep preprocess to listing samples + light tables; manifests instead of opening files | runs again in every worker process |
| **Compact inputs** | inputs are images normalized in the encoder | return `uint8 / 255` in `[0, 1]`, normalize inside the model | changing where normalization happens changes the model: only if you control the model and outputs stay identical |
| **Disk cache for parents** | instance-level samples re-decode their parent | cache decoded parents on the data volume keyed by parent id | cache location must be on the data volume; bound its size |
| **Bounded caches** | memory grows during generation (catalog F) | `lru_cache(maxsize=…)`; drop large intermediates | memory is multiplied by the number of worker processes |
| **CPU-oriented dataset code** | torch/TF used in metrics/visualizers "for speed" | numpy on CPU; avoid per-call framework setup | dataset code runs on CPU on the platform |
| **Casts at the boundary** | torch/numpy ops in metrics/loss reject half precision | `.astype(np.float32)` on arrays passed in | no numeric change beyond restoring float32 |
| **Dependency upgrade** | a library bug/slowness is fixed upstream (catalog U) | bump the pin (e.g. code-loader) | `compare` — upgrades can change outputs; then it is a reported behavior change |
| **Remove debug work** | prints/statistics on large arrays in hot paths (catalog H) | delete them | none |
| **Crop before heavy ops** | volumetric ops over sparse foreground (catalog G) | bounding box + margin, scatter back | margin must cover the op's reach, `compare` bit-identical |

## Things you do **not** control (report them instead)

How Tensorleap schedules, batches and scales work internally, storage and transfer inside
the platform, and server resources are outside the integration. When the
evidence points there, add a **Recommended Tensorleap action** to the report, phrased by the
need ("evaluation appears producer-bound: generation costs 30× inference per sample"),
with the numbers — not by guessing at internal settings.
