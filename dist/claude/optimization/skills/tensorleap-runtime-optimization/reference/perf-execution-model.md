# How Tensorleap runs your integration — what matters for runtime

Optimizing an integration means optimizing it **the way Tensorleap executes it**, not the
way a single local script runs it. A change that looks faster in a one-process local run
can do nothing on the platform (or cost memory there) if it relies on sharing that the
platform does not provide. This page lists the execution facts the skill relies on and how
`tl_perf` mirrors each one. Every optimization decision should be checkable against it.

## 1. Where each component runs

| Component | Runs … | So … |
|---|---|---|
| preprocess (`@tensorleap_preprocess`) | once in **every worker process** Tensorleap starts | keep it fast; never load or transform the whole dataset there (it is paid again per worker) |
| input encoders, ground-truth encoders, metadata (and custom latent space) | together, **in one process, for the same sample** | a small cache keyed by the source file is shared among them — the lossless fix for "two components decode the same file" |
| the same components, across samples | in a long-lived worker process | a cache can also help across samples, **but** samples are not processed in your preprocess order and consecutive samples may go to different worker processes (§3) |
| custom metrics and custom loss | on **batches** (leading dimension = batch), in one process per batch | vectorize over the batch; a metric must return one value per sample |
| visualizers | **one sample at a time**, later, in a process that may **not** be the one that generated the sample; nothing ran there first | a visualizer must be efficient on its own. Caches filled by encoders, metadata, metrics or loss are **not** available to it |
| model inference | on the model runtime (GPU when available) | everything else — encoders, metadata, metrics, loss, visualizers — runs on **CPU**; optimize that code for CPU, never move it to a GPU |

Caches live **per worker process**. When Tensorleap runs several worker processes, cache
memory is multiplied: keep caches small and bounded (`lru_cache(maxsize=…)`, never
unbounded dicts).

With **instance-level samples** (`@tensorleap_element_instance_preprocess`), each instance
re-runs the parent sample's encoders, possibly in another process. Keep encoders cheap, or
cache decoded parents on disk (an in-memory cache mostly misses across processes).

## 2. Cost model: everything is measured against inference

Score each component by its **mean cost per sample relative to the model's mean inference
time per sample** (at the recommended batch size). Work that is many times more expensive
than inference is what limits end-to-end runtime; work far below it is noise.

- Total runtime is additive (samples × mean cost), so candidates are ranked by **means**;
  P50/P90/P95/P99 are reported to explain tails (a few very slow samples can dominate).
- **Visualizers** run once per sample on a large subset after evaluation, so a slow
  visualizer extends the time until results are available even when it does not slow
  evaluation itself.
- **Preprocess** is a per-worker startup cost, reported apart from per-sample cost.

## 3. Sample order

Samples are **not processed in your preprocess order**, and consecutive samples may be
handled by different worker processes. **Do not rely on processing order for cache hits.**

- If your data lives in files that hold many samples (Parquet shards, HDF5, video, large
  arrays), a per-file cache over a flat list of samples will mostly miss: the next sample a
  worker gets usually lives in a different file.
- The robust fix is a **grouped `PreprocessResponse`** (code-loader ≥ 1.0.196):
  `PreprocessResponse(sample_ids=[[ids in file A], [ids in file B], ...])`. All samples of a
  group are fetched together by one process, so group samples by the file they are read
  from. Encoders then receive a list of ids. Grouping cannot be combined with element
  instances, simulations, or instance-aware custom latent spaces (code-loader enforces this).
- `tl_perf profile` reports both the default order and a **sorted-order what-if**. Prefer
  changes that win in both. A gain that appears only in sorted order depends on processing
  order and is not a delivered improvement.

## 4. Data movement

- Prefer returning inputs as `float32` values in `[0, 1]` that are exact multiples of 1/255
  (e.g. `uint8 / 255`) and doing mean/std normalization **inside the model**; Tensorleap can
  then transfer inputs much more compactly.
- Metric and loss inputs arrive as **float16** on the platform (code-loader ≥ 1.0.202
  simulates this locally). Cast explicitly (`.astype(np.float32)`) before torch/numpy ops
  that lack half-precision support.
- Don't emit `NaN`/`None` as metric values (use a concrete, meaningful float), and don't
  reshape the arrays Tensorleap passes into metric/loss functions (e.g. `np.expand_dims`):
  Tensorleap adds the batch dimension itself.

## 5. What `tl_perf` does to mirror this

| Platform behavior | `tl_perf` |
|---|---|
| encoders + metadata of a sample share one process | the generation worker keeps caches warm within and across samples |
| samples arrive in a non-preprocess order | generation runs in a random order by default; a second worker runs a sorted-order what-if |
| visualizers run elsewhere, with nothing run first | visualizers run in a **fresh process**, fed the sample's tensors directly (no encoder, metadata or metric ran there) |
| metrics/loss run on batches | they run on batches of the recommended batch size, through code-loader |
| only what the integration test wires runs | only metrics, losses and visualizers wired in `@tensorleap_integration_test` are profiled |
| preprocess per worker | each worker's startup (import + preprocess) is timed and reported apart |
| process warm-up | the first sample of each worker is reported apart from the per-sample means |

Two things the local run **cannot** show, and the skill must say so in the report:
behavior on data the sample set did not cover (code paths never executed), and the
platform's own scheduling and scaling — checked by the validation push.

## 6. Local measurement hygiene

- Never measure runtime with `@tensorleap_integration_test`: it re-runs preprocessing and
  validation on every call. `tl_perf` drives code-loader's `LeapLoader` directly.
- `tl_perf` disables code-loader's usage analytics while measuring (a slow network call
  there can stall imports); set `TL_DISABLE_ANALYTICS=1` for your own local timing too.
- Measure on the device you will report: the floor is labeled with the device it was
  measured on (a CPU floor on a laptop is valid for before/after comparisons, not as the
  server's GPU floor).
