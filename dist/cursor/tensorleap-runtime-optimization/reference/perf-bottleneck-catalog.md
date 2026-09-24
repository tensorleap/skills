# Bottleneck catalog

Each class: the **signal** that reveals it in `tl_perf` output, the **static tell** to look
for when reading the code, the usual **cause**, the **lossless fix**, how to **verify**, and
what **not** to do. Classes come from real optimization work on Tensorleap integrations.
Use the static tells in Phase 2 (hypotheses); only a measured signal makes a candidate.

Signals below refer to: `profile.json` (per-handler `mean/sample`, `calls/sample`,
diagnostics `reads` and `repeated_calls`), `score.json` (ratio to inference, evidence),
`fit.json` (memory, batch support), `floor.json`, and `compare.json`.

---

## A. Redundant work across components

- **Signal:** `repeated_calls` shows a component function called more than once per sample,
  "called from" the platform *and* another component; `reads` shows the same file read
  more than once within one sample; a metadata/visualizer handler costs about as much as
  an encoder.
- **Static tell:** a metadata function (or encoder) calling another encoder
  (`img = input_encoder(idx, preprocess)`); several components each opening and decoding
  the same file.
- **Cause:** components of a sample run in the same process but share nothing unless you
  make them.
- **Fix:** hoist the shared expensive step into one pure function keyed on the raw source id
  (file path / sample id) and memoize it with a **small** `functools.lru_cache`
  (`maxsize` ≈ number of components that touch one sample). Callers take `.copy()` before
  mutating the shared array. Components call the shared step, never each other.
- **Verify:** the shared step must be **deterministic** for its key (no randomness, no
  `training=True` augmentations). `compare` bit-identical, reads per sample drop to 1.
- **Don't:** size the cache for the whole dataset (memory × worker processes); rely on it
  from a visualizer (§1 of the execution model).

## B. Storage layout vs processing order (cache thrash)

- **Signal:** `reads_per_sample` ≈ 1 or more in server order but much lower in the
  sorted-order what-if; generation mean differs between the two orders.
- **Static tell:** data split into many files that each hold many samples, read one sample
  at a time behind a small cache.
- **Cause:** samples are not processed in preprocess order, so consecutive samples hit
  different files and every access misses.
- **Fix:** a **grouped `PreprocessResponse`** whose groups follow file boundaries (code-loader
  ≥ 1.0.196); encoders accept a list of ids and loop over a per-sample core function.
  Don't just enlarge the cache.
- **Verify:** per-row `np.array_equal` between grouped and flat encoding; reads per sample
  drop toward files/samples in *both* orders.
- **Don't:** use grouping with element instances, simulations or instance-aware custom
  latent spaces (unsupported).

## C. Opening a data file to read one number

- **Signal:** reads at startup equal to the number of files; `startup` large.
- **Static tell:** `len(load(file))`, `.shape` of a fully loaded file, row counts computed
  by reading whole files in preprocess.
- **Fix:** write a small manifest (row counts, shapes) once, when the data is produced; read
  that instead. Keep file order identical.
- **Verify:** same sample ids in the same order.

## D. Batching problems

- **Signal:** `fit.json` → `batch_support` shows a metric/loss that errors or returns no
  per-sample value on a batch of 2; model throughput in `floor.json` keeps rising with
  batch size but the metric side can't follow.
- **Static tell:** Python loops over rows inside a metric; per-sample model calls
  (`session.run` on `x[None]` in a loop); variable-length ground truth with no padding.
- **Fix:** vectorize metrics over the batch; batch any inference done inside dataset code;
  pad variable-length arrays to a fixed size with a sentinel and have **every** consumer
  strip the sentinel; export ONNX with a dynamic batch axis on every input and output.
  Old code-loader versions (< 1.0.173) have a built-in `categorical_crossentropy` that fails
  for batch sizes other than 1 and 10 — upgrade code-loader.
- **Verify:** `fit` batch support "ok"; `compare` equivalent.

## E. Preprocess / per-row CPU

- **Signal:** `startup` grows with dataset size far faster than the samples you keep;
  generation handler cost grows with columns you never use.
- **Static tell:** an expensive per-row transform before a sampling/filter step;
  `df.iterrows()`; per-row parsing of repeated values.
- **Fix:** filter/subsample **before** the expensive transform; read only the needed columns
  (`df[col].to_numpy()`); memoize parsing over unique values.
- **Verify:** identical sample ids and outputs.

## F. Memory

- **Signal:** `fit.json` activation per sample high; generation worker `rss_end_gb` far
  above `rss_start_gb` (cache growth); `compare` exit 10.
- **Static tell:** copying a wide dataframe to reorder it; float64 intermediates; unbounded
  caches; large intermediates kept alive.
- **Fix:** carry a row permutation instead of a reordered copy; float32 where precision
  allows (only if outputs stay bit-identical — otherwise it is a lossy option); `del`
  large intermediates; bound every cache.
- **Verify:** `compare` equivalent and peak RSS not higher.

## G. Heavy metrics

- **Signal:** `metrics` share large; one metric many times inference.
- **Static tell:** distance transforms / skeletonization / surface distances over full
  volumes with sparse foreground; generic transform pipelines configured into a no-op
  (e.g. argmax → one-hot → argmax); the same transform recomputed for several metric
  variants.
- **Fix:** crop to the foreground bounding box (with margin) before volumetric ops and
  scatter back; special-case the degenerate configuration; compute a shared transform once
  and pass it to every variant.
- **Verify:** `compare` equivalent (cropping must not change results — keep enough margin).

## H. Heavy visualizers

- **Signal:** `visualizers` per-sample cost high (measured in a fresh process).
- **Static tell:** debug prints/statistics on large arrays (`print(x.unique())`); a
  visualizer running a full reconstruction to show only the raw input; several visualizers
  each rebuilding the same view.
- **Fix:** remove debug statistics; add a fast path for the simple case; give visualizers
  that share a source one shared entry point.
- **Don't:** make a visualizer depend on a cache filled by a metric or an encoder.

## I. Metadata

- **Signal:** a metadata handler many times inference.
- **Static tell:** a static configuration object rebuilt per sample; full-volume filters
  per sample; one metadata function computing many values each re-reading the input.
- **Fix:** build static configuration once at import; compute derived values once per
  sample and return them together (a dict metadata).

## J. Loss / latent / per-call loading

- **Signal:** loss or a generation handler with file reads on every call.
- **Static tell:** a model/pickle/table loaded inside a function that runs per call.
- **Fix:** memoize the **loader call** (`lru_cache(maxsize=1)`), not just the file; stream
  reductions instead of storing every per-sample result.

## K. Quadratic lookups

- **Signal:** a handler whose per-sample cost grows with dataset size; profile repeats at
  two sample counts show super-linear growth.
- **Static tell:** `list.index(sample_id)` or a linear search per sample.
- **Fix:** a dict built once, or direct indexing when ids are already positions.

## L–N. Data crossing into metrics and loss

- **L — float16 inputs:** metric/loss inputs arrive as float16; torch CPU ops may reject
  them. Cast to float32 at the boundary.
- **M — `NaN`/`None` metric values:** break numeric storage downstream. Return a
  meaningful float.
- **N — reshaping passed arrays:** `np.expand_dims` on the prediction/ground truth handed
  to a metric/loss breaks Tensorleap's wiring. Only reshape inside standalone tests.
  (Correctness issues that surface only on the platform — fix them when seen.)

## O. Thread contention

- **Signal:** CPU-bound handlers get slower per sample when several processes run.
- **Fix:** cap math-library threads per process (`OMP_NUM_THREADS`,
  `torch.set_num_threads`) when dataset code uses heavy numeric libraries.

## P. Reads not overlapped with compute

- **Signal:** a handler dominated by I/O wait (low CPU, high wall time).
- **Fix:** batch or prefetch reads (grouped preprocess helps); avoid synchronous remote
  reads per sample — cache fetched files on the data volume.

## Q. Cache writes that are never read

- **Signal:** a cache directory grows during a run with no hits for this access pattern.
- **Fix:** disable the cache for single-pass paths; keep it where keys repeat.

## R. Silent accelerator fallback

- **Signal:** `preflight` finding `gpu-unused` (GPU present, model on CPU); per-sample
  inference matching CPU cost.
- **Fix:** install the GPU build of the model runtime; fail loudly instead of falling back.

## S. A cache that does not cache

- **Signal:** a memoized step still shows one real execution per call.
- **Static tell:** hand-written caches whose store step is conditional on eviction.
- **Fix:** repair the cache; assert that a `put` is followed by a hit.

## T. Nondeterministic components (report; do not "fix" silently)

- **Signal:** `profile` lists nondeterministic outputs (they differ between two runs of the
  same code); `compare` uses a distribution check for them.
- **Static tell:** unseeded `np.random` / `random` in encoders or metadata.
- **Why it matters:** the platform regenerates samples (e.g. for visualization), so a
  randomized encoder shows a different input than the one evaluated.
- **Action:** report it. Seeding changes outputs — that is the user's decision (lossy gate).

## U. Outdated dependencies

- **Signal:** a failure or slowness traced into a library, fixed in a newer release.
- **Fix:** upgrade the pin (e.g. code-loader) — then `compare`: a newer code-loader can
  change outputs (e.g. float16 simulation of metric inputs), which makes it a behavior
  change to report, not a silent fix.
