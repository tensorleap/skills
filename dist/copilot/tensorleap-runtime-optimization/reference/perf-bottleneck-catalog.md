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
  from a visualizer or a metric — both run in other processes (§1 of the execution model;
  see V).

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
  by reading whole files in preprocess; an image decoded in full (`cv2.imread`,
  `np.array(Image.open(p))`) only to read its width and height.
- **Fix:** write a small manifest (row counts, shapes) once, when the data is produced; read
  that instead. Keep file order identical. For image sizes, read the file header (PNG
  `IHDR`, JPEG `SOF`, or `PIL.Image.open(p).size`, which parses only the header).
- **Verify:** same sample ids in the same order; sizes equal to the decoded shape on every
  sampled file (header and pixel data can disagree for EXIF-rotated JPEGs — check).

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
- **Static tell:** an expensive per-row transform (filtering, normalization, parsing)
  applied to every row before a sampling/filter step that keeps only some of them;
  `df.iterrows()`; per-row regex/date parsing of values that repeat across rows; per-group
  boolean masks over the full frame in a Python loop (`df[df.key == k]` for every key).
- **Fix:** filter/subsample **before** the expensive transform (keep any pass that decides
  shapes or padding widths over all rows); read only the needed columns
  (`df[col].to_numpy()`); parse each distinct value once (`pd.unique`, then map back);
  replace per-group masks with one `groupby` or one stable sort + segment boundaries.
- **Verify:** identical sample ids and outputs. Startup counts once in the expected total
  (`score` / `compare`), so on small datasets a preprocess fix is often the biggest win.

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
  variants; a detection decode that takes the argmax over **all** anchors × classes and only
  then applies the confidence threshold; a Python double loop over all detection pairs
  (including those below the threshold) for rotated-box IoU.
- **Fix:** crop to the foreground bounding box (with margin) before volumetric ops and
  scatter back; special-case the degenerate configuration; compute a shared transform once
  and pass it to every variant. For detection decodes: threshold on the per-anchor max
  first, then argmax only the survivors (identical kept set when the threshold is on the
  max score); filter pairs by threshold before pairwise geometry. When the loss and several
  metrics each decode the same predictions, making the decode itself cheap helps them
  all — a cache shared between metrics of one batch is legitimate, one shared with
  generation or visualizers is not.
- **Verify:** `compare` equivalent (cropping must not change results — keep enough margin).

## H. Heavy visualizers

- **Signal:** `visualizers` per-sample cost high (measured in a fresh process).
- **Static tell:** debug prints/statistics on large arrays (`print(x.unique())`); a
  visualizer running a full reconstruction to show only the raw input; several visualizers
  each rebuilding the same view; a new matplotlib `Figure` (subplots, axes, legend) built on
  every call; channel-order/layout changes done as numpy gathers on full-size images
  (`x.transpose(1, 2, 0)[..., ::-1]` then `astype`).
- **Fix:** remove debug statistics; add a fast path for the simple case; give visualizers
  that share a source one shared entry point. For matplotlib: build the figure once per
  process and update the artists' data each call (`set_data`, titles, limits), then draw —
  bit-identical output is possible when every piece of per-call state is reset. For layout:
  `cv2.split` / `cv2.merge` or one `np.ascontiguousarray` instead of chained gathers.
- **Don't:** make a visualizer depend on a cache filled by a metric or an encoder. Don't
  swap the drawing library or lower resolution/dpi as a "speedup" — the pixels change, so it
  is a Phase 5 option.

## I. Metadata

- **Signal:** a metadata handler many times inference.
- **Static tell:** a static configuration object rebuilt per sample; full-volume filters
  per sample; one metadata function computing many values each re-reading the input; a
  dict metadata reading each field through a fresh pandas row (`df.iloc[i][col]` per field);
  a generic feature-extraction framework (DataFrame build, pivot, impute) run per sample on
  a single signal; several features recomputing the same transform (e.g. one wavelet
  transform per peak-count width).
- **Fix:** build static configuration once at import; compute derived values once per
  sample and return them together (a dict metadata); take the row once
  (`row = df.iloc[i]`, or a numpy row) and read every field from it; call the framework's
  feature functions directly for a single signal and share intermediates between features
  that use the same transform. When a library routine dominates, a narrower equivalent
  is allowed only if `compare` is bit-identical on many samples — test it on far more than
  the snapshot before keeping it.

## J. Loss / latent / per-call loading

- **Signal:** loss or a generation handler with file reads on every call; a large
  `startup` spent in model calls.
- **Static tell:** a model/pickle/table loaded inside a function that runs per call; a
  derived artifact (e.g. class centroids from running the model over the whole training set)
  computed at import — every worker pays it — often with one model call per sample; a loss
  that re-runs the model on re-fetched inputs.
- **Fix:** memoize the **loader call** (`lru_cache(maxsize=1)`), not just the file; stream
  reductions instead of storing every per-sample result; compute a derived artifact once,
  cache it on disk keyed by the identity of everything it depends on (model file, data file:
  path + size + mtime, or a hash), and batch the model calls that build it.
- **Don't:** switch a loss from re-running the model to using the latent/prediction tensor
  it receives and call it lossless because local outputs match — on the platform those
  tensors reach the loss in reduced precision (float16), so the values change. It is a
  Phase 5 option.

## K. Quadratic lookups

- **Signal:** a handler whose per-sample cost grows with dataset size; profile repeats at
  two sample counts show super-linear growth.
- **Static tell:** `list.index(sample_id)` or a linear search per sample; a very large
  unlabeled state given as a plain list of ids — code-loader checks each fetched unlabeled
  sample's membership in that list, which is linear per sample (quadratic per state).
- **Fix:** a dict built once, or direct indexing when ids are already positions. For the
  membership check, give the state's ids as a list subclass with a set-backed (or
  arithmetic, for ranges) `__contains__` — and make it **pickle as a plain list**
  (`__reduce__` returning `(list, (list(self),))`): the platform may unpickle
  `PreprocessResponse` contents in a process that cannot import your integration, so a
  custom class there fails the push (only a server push reveals this; local checks pass).

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

## V. Metrics that reload source data

- **Signal:** a metric (or loss) costs about as much as a sample decode; profile diagnostics
  show file reads or decoder calls under metric handlers.
- **Static tell:** a metric that looks a sample up again (by index or global state) and
  reloads/decodes its image or row to get something it could derive from the tensors it is
  given (a size, a resized shape, the ground truth).
- **Cause:** metrics run on batches in their own process, fed the batch's tensors; nothing
  from that sample's generation is warm there, so the reload pays the full decode again.
- **Fix:** compute from the tensors the metric receives (e.g. derive shapes from the input
  tensor); if something is truly needed from the source, pass it as metadata or an extra
  ground-truth output instead of reloading.
- **Verify:** `compare` equivalent; `tl_perf` runs metrics in a separate process, so the
  gain is only credited when it holds there.

## W. Full-size layout and dtype churn in encoders

- **Signal:** an input encoder costs several times what its decode alone costs; profile hot
  functions are `astype`, `transpose`, `clip`, `cvtColor` on full-size arrays.
- **Static tell:** channel reorder and float conversion on the full-size image before a
  resize; a `clip` that cannot change anything (8-bit input divided by 255 is already in
  [0, 1]); a transpose to CHW that copies the float array again.
- **Fix:** do per-channel operations (resize, channel reorder, CHW layout) while the data is
  still small and uint8, then one fused conversion to float; skip a clip only where it is
  provably a no-op for the input dtype (keep it for other dtypes).
- **Verify:** `compare` bit-identical — reordering float operations can change results, so
  only reorders that are exact (per-channel, integer-domain) qualify.
