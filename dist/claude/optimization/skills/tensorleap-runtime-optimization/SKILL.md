---
name: tensorleap-runtime-optimization
description: >
  Use when a Tensorleap integration evaluates slowly or runs out of memory, or
  when the user asks to "optimize my Tensorleap runtime", "speed up the
  evaluation", "why is my integration slow", "find the bottleneck", "reduce
  memory", or "pick a batch size". Measures the model's inference floor, checks
  the environment and server fit, profiles every integration component
  (preprocess, encoders, metadata, metrics, loss, visualizers) the way
  Tensorleap actually runs them, applies lossless optimizations one at a time
  with equivalence checks, validates on the Tensorleap server, and writes a
  report naming the remaining bottleneck with evidence.
group: tensorleap
---

# Optimizing a Tensorleap integration's runtime

You take an existing, working Tensorleap integration (`leap_integration.py` + `leap.yaml`
at the repo root) from "it's slow and nobody knows why" to a measured, component-level
picture, apply every **lossless** optimization the evidence supports, validate the result
on the Tensorleap server, and hand over a report that names what limits runtime now. The
same flow reduces the **memory the integration's own code holds** per worker process when
that is the problem (or when it comes for free).

Work from the **integration repo root**, inside the **integration's own Python
environment** (the one that runs `leap_integration.py` — `poetry run` by default, or the
interpreter in `$TL_PY`). All measurements come from `scripts/tl_perf.py`; you read
code, reason, and make changes. Artifacts go to `tensorleap/runtime-optimization/`; code
changes go to a branch, **one commit per accepted fix**.

## What you are optimizing (and what that implies)

Tensorleap does not run your integration as one script. It runs components in separate
worker processes, on batches or single samples, in an order you don't control. An
optimization is only real if it helps **there**. Read
`reference/perf-execution-model.md` before the first change; the short version:

- Encoders and metadata of the **same sample** run together in one process → a small cache
  they share is the standard lossless fix for repeated work.
- **Visualizers** run later, one sample at a time, in a process where nothing else ran
  first → they must be efficient on their own; they never see caches from encoders,
  metadata or metrics.
- **Metrics and loss** run on batches in their own process, fed the batch's tensors → a
  cache warmed by encoders or metadata does not help them; a metric that reloads source
  data should compute from the tensors it is given instead.
- **Samples are not processed in your preprocess order** → per-file caches over a flat
  sample list mostly miss; group samples by file instead.
- **Preprocess** runs again in every worker process → keep it light.
- Dataset code (encoders, metadata, metrics, loss, visualizers) runs on **CPU**; only the
  model uses the GPU.
- Caches are **per worker process** → their memory multiplies; keep them small.

And the cost model: **every component is scored by its mean cost per sample relative to
the model's mean inference time per sample.** Work that costs many times the inference is
what limits runtime; work far below it is noise, however ugly the code.

For memory, the unit is the **user-code footprint of one worker process**: what the
integration's own code holds — imported libraries, module globals, the preprocess result,
caches, per-call temporaries — measured by `profile` with the model **not** loaded (the
model's memory is `fit`'s concern). Every worker process pays it, because preprocess runs
in each and caches live in each; a smaller footprint lets more workers fit and avoids
out-of-memory failures.

## Operating principle: measure, then change; lossless first

- **Evidence before change.** Nothing gets changed because it *looks* slow. A change needs
  a measured signal (`tl_perf profile` / `score`) and, ideally, a matching static tell.
- **Lossless first.** A change is kept only if `tl_perf compare` shows the outputs are
  **equivalent** and either runtime is **lower** with no memory regression (runtime loop),
  or the footprint is **lower** with runtime within the allowed tolerance (memory loop).
  Changes that alter outputs are a separate, consent-gated step (Phase 5).
- **Runtime first, unless memory is the problem.** `tl_perf score` triages memory:
  **RED** (out-of-memory reported or seen, or one worker's footprint over half the memory
  budget) → the memory loop runs first and a memory fix may cost up to +15% runtime;
  **AMBER** (a large footprint, a structure over 1 GB per worker, or growth with the
  samples) → runtime loop first, then the memory loop, memory fixes within noise (3%);
  **GREEN** → runtime loop, then only **free** memory wins (no runtime cost).
- **The catalog is where you start, not where you stop.** Candidates come from
  measurement: `profile` / `score` rank every component by cost, whether or not its problem
  is in `reference/perf-bottleneck-catalog.md`. When the top cost matches no catalog
  class, find the cause yourself — the profile's hot functions and repeated calls, a
  focused `cProfile` of that component, the code — and fix it the same way (one change,
  `compare`-verified). Fix **correctness bugs** you meet on the way first (a component that
  raises on some samples, a metric wrong on batches, a loss that divides by the wrong axis):
  they break the platform run no matter how fast it is. Log every problem no catalog class
  describes as **new**, and tag it `"catalog": "new"` in the report.
- **Run autonomously; ask only when blocked.** Infer everything you can from the repo and
  the artifacts. The questions you may need to ask: uncommitted changes in the repo
  (Phase 0), the model file to push if it can't be inferred (Phase 6), and the
  behavior-changing options (Phase 5). Nothing else is a reason to stop.
- **Keep `tensorleap/runtime-optimization/optimization-log.md`.** Append as you go: each
  candidate, the evidence, what you tried, the `compare` verdict, the commit. It is the
  source of the final report and survives an interrupted session.

## The measurement tool

Run every subcommand from the repo root through the integration's environment:

```
poetry run python scripts/tl_perf.py <subcommand> [options]
```

(Or `$TL_PY scripts/tl_perf.py …`. `--root` defaults to `.`; outputs go to
`tensorleap/runtime-optimization/`.)

| Subcommand | Does | Writes |
|---|---|---|
| `preflight` | environment, devices (and whether the model actually uses the GPU), code-loader features, integration validity, model load | `preflight.json` |
| `floor` | the model alone: warm-up, P50/P90/P95/P99 per batch size, recommended batch size (throughput knee) → **the scoring unit** | `floor.json` |
| `fit` | per-sample tensor size, rows per state, **measured** model memory per batch size, recommended batch size, and whether every metric/loss accepts a batch | `fit.json` |
| `profile` | every wired component, run the way Tensorleap runs it (fresh processes per pass: generation, sorted-order what-if, visualizers, diagnostics, output snapshot, **user-code memory**); the first run becomes the equivalence **baseline** | `runs/NNN/profile.json`, `runs/NNN/memory.json`, `profile.json`, `baseline/` |
| `score` | ranks candidates: expected seconds removable × confidence, with evidence; always triages memory (GREEN / AMBER / RED) and ranks **memory candidates** (`--objective memory` lists them first) | `score.json` |
| `compare` | latest run: output equivalence vs the **baseline**; gain and memory vs the **last accepted run** (exit 0 makes it the new reference). `--objective memory` judges a memory fix: footprint drop, runtime within the triage tolerance | `runs/NNN/compare.json` |
| `report` | renders your `report.json` into `report.md` | `report.md` |

**Exit code → action** (all subcommands):

| Exit | Meaning | Do |
|---|---|---|
| 0 | ok | continue |
| 2 | blocker (`preflight`: missing code-loader, disk; `score`/`compare`: missing inputs) | fix the named cause (wrong environment? run `profile` first?) and re-run |
| 3 | integration invalid (`check_dataset()` fails) | stop optimizing; the integration must work first — fix the reported error or hand over to the integration-authoring workflow |
| 4 | environment mismatch (a GPU is present but the model runs on CPU) | install the GPU build of the model runtime, re-run `preflight`; if impossible, continue with a CPU floor and say so in the report |
| 5 | `fit`: predicted not to fit even at the smallest batch | report it as a blocker with the numbers; look for memory candidates first |
| 6 | the model can't be loaded standalone | check `@tensorleap_load_model` loads a local `.onnx`/`.h5`; fix, re-run |
| 7 | a profiling pass failed | read `runs/NNN/worker-*.log`; usually a component raising on some sample — fix the integration bug (it would fail on the platform too), re-run |
| 8 | `compare`: outputs **not equivalent** | revert the change (see Phase 4) |
| 9 | `compare`: equivalent but **no meaningful gain** over the last accepted run (runtime < 5%; memory objective: footprint drop < max(5%, 64 MB)) | revert, unless the change is a prerequisite for the next one |
| 10 | `compare` (runtime objective): the user-code footprint grew (> 10%, or > 5% when memory is AMBER/RED) | revert, or shrink the cache and re-measure |
| 11 | `report`: invalid `report.json` | fix the listed fields, re-run |
| 12 | `compare --objective memory`: the memory fix costs more runtime than the triage allows | revert, or find a cheaper way to save the same memory |

Memory options: `profile --memory-samples N` (samples per state in the memory pass),
`--no-memory`, `--no-import-costs`; `score --memory-symptom none|high|oom`, `--memory-gb`,
`--red-share` / `--amber-share` / `--amber-holder-gb` (triage thresholds); `compare
--objective memory`, `--min-memory-gain`, `--runtime-tolerance`, `--max-mem-increase`.

## Phase 0 — Preflight and setup

1. **Environment.** Find the integration's Python environment (`pyproject.toml` → poetry;
   `requirements.txt` → the venv the user runs it with). Everything below runs in it.
2. **Repo state.** `git status`. If there are uncommitted changes, **ask** whether to commit
   them first — never mix the user's work with optimization commits. Then create a branch:
   `git switch -c tensorleap-runtime-optimization`. (`tl_perf` gives its output directory
   its own `.gitignore`: only the report, the log and `static.json` are ever committed.)
3. **`tl_perf preflight`.** Act on the exit code (table above). Note in the log: device,
   code-loader version and features (`grouped_preprocess` needs ≥ 1.0.196), state sizes.
4. **Server check (for Phase 6, non-blocking):** `scripts/perf_preflight.sh`.
   Exit 0 → the validation push is available. Exit 2 → continue locally; the report will
   say the result was not validated on a server. Exit 3 → ask the user to `leap auth
   login` when you reach Phase 6. Exit 6 → ask whether the server is remote (port-forward)
   or must be started, when you reach Phase 6.

**GATE:** `preflight` exits 0 (or 4 with the CPU-floor caveat noted).

## Phase 1 — Floor, fit, sanity

Measure on a quiet machine: `preflight` warns when the CPU is busy, and `floor`/`profile`
record the load they ran under — a busy machine shifts both the floor and the recommended
batch size.

1. `tl_perf floor` — the model's own speed per batch size. The **floor** (mean inference
   time per sample at the recommended batch size) is the unit every later number is
   compared to. If it says the knee was not reached, re-run with larger `--batch-sizes`.
2. `tl_perf fit` (pass `--memory-gb <server RAM>` when the server's memory is known). Note
   the recommended batch size and any **batch-support** failure — a metric/loss that can't
   take a batch is a correctness problem the local integration test never shows (it runs
   batch 1). Fix those first (catalog D).
3. Sanity, from the three JSON files: GPU present *and* used; batch size sensible;
   nothing obviously wrong (debug flags, full-dataset work in preprocess, huge per-sample
   tensors). Write each finding to the log.
4. **Memory symptom.** Re-read the user's request and quote, in the log, every word it says
   about memory. Out-of-memory failures, killed or restarted workers, "runs out of memory"
   → `--memory-symptom oom`. Any other complaint — "uses a lot of memory", "memory-heavy",
   "workers are big" → `--memory-symptom high`. Only when the request says nothing about
   memory → `none`. Note the server's memory as `--memory-gb` when known. Both go to
   every `tl_perf score` call.

**GATE:** a floor exists and no metric/loss fails the batch check.

## Phase 2 — Read the code (hypotheses, not conclusions)

Read the integration end to end: preprocess, every encoder, metadata, metric, loss,
visualizer, and anything they call. Look for the **static tells** in
`reference/perf-bottleneck-catalog.md` — for example one component calling another
component's function, the same file opened by several components, Python loops over rows
in metrics, per-call model/table loading, per-sample `list.index`, debug statistics on large
arrays, unbounded caches, unseeded randomness, data-dependent branches.

Then read for **waste in general**, not only the catalogued patterns:
- work repeated per sample that could run once (at import, in preprocess, once per file);
- results computed and then thrown away, or computed twice under different names;
- round trips between representations (PIL ↔ numpy, float64 ↔ float32, tensor ↔ array,
  DataFrame ↔ dict);
- Python loops over array elements, and copies of large arrays;
- I/O, parsing or regex compilation inside per-sample code;
- **bugs**: code that is wrong for some inputs, shapes or batch sizes, dead branches that
  hide errors, silent `except:` blocks.

(Work done at more precision or resolution than the output needs is real waste too, but
removing it changes outputs — that belongs to Phase 5.)

Write what you find to `tensorleap/runtime-optimization/static.json`:

```json
[{"handler": "metadata:image_stats", "code": "A", "message": "calls load_image() of the encoder"}]
```

(`handler` = `<kind>:<name>` as `tl_perf` names components: `input:`, `gt:`, `metadata:`,
`metric:`, `loss:`, `visualizer:`.) These raise a candidate's confidence; they never create
one by themselves. Note **data-dependent branches** (`if domain == …`) separately — the
equivalence check only covers branches the sampled data executes.

## Phase 3 — Baseline profile

1. `tl_perf profile` (defaults: 200 samples per state, 50 visualized samples). The first
   run also becomes the **baseline** for equivalence, taken twice to detect
   nondeterministic outputs. For a large or slow dataset use `--samples` / `--max-seconds`
   to keep one run within minutes; keep the same settings for every later run.
2. `tl_perf score --static tensorleap/runtime-optimization/static.json` (plus
   `--memory-symptom` / `--memory-gb` from Phase 1).

   **Visualized samples.** Visualizers run on a subset of samples, not on every one. By
   default `score`, `compare` and `report` weight visualizers by *every* sample, which
   hugely overstates them on a large dataset. When the dataset is much larger than what
   the evaluation visualizes, pass `--visualized-samples <n>` (the number of samples the
   user's evaluation visualizes) to `score`, `compare` and `report` alike, keep it fixed
   for the whole loop, and state the value you assumed in the report.
3. Read, in this order:
   a. **worker failures / "not profiled"** lines → a component that crashes or can't be
      wired is fixed first (it breaks on the platform too);
   b. **nondeterministic outputs** → report them (catalog T); the equivalence check will
      use a distribution check for them;
   c. **shares** of expected runtime (generation / inference / metrics / visualizers /
      startup) and the **generation-to-inference ratio**;
   d. the **ranked candidates** and their **evidence**;
   e. the **memory triage** (status, reasons, budget and where it came from) and the
      footprint breakdown: held by imports / the preprocess result / caches and growth, how
      much more preprocess needs at its peak than it keeps, which stage sets the peak, and
      the "unattributed" part (memory freed but kept by an allocator or a native library —
      it shrinks when the peak that grew it shrinks; never chase it as a holder).
4. Log the baseline breakdown, runtime and memory.

## Phase 4 — Lossless optimization loops (runtime 4R, memory 4M)

Run the loops in the order the memory triage gives: **RED** → 4M, then 4R; **AMBER** →
4R, then 4M; **GREEN** → 4R, then 4M for free wins only. Re-run `tl_perf score` after every
kept fix — a fix can change the status.

### 4R — runtime loop

```
1. PICK     the highest-priority candidate that is worth it — catalogued or not: ratio to
            inference >= 1, or >= 10% of expected runtime. Skip ones marked (minor).
            Startup (import + preprocess, paid by every worker) counts once in the
            expected total — `score` and `compare` both include it — so a preprocess fix
            is judged like any other; it earns exit 0 when startup is a real share.
2. EXPLAIN  why it is slow, from its evidence + the code (profile diagnostics list hot
            functions and repeated calls; if no catalog class fits, profile that component
            with cProfile and read the hot path). No explanation -> no change.
3. CHANGE   one fix from the catalog / reference/perf-levers.md. It must respect
            the execution model: rely on a cache ONLY where Tensorleap shares it (within
            one sample's encoders + metadata, or within one process across samples),
            never across processes, never from a visualizer, never from a metric or loss
            to what generation computed.
4. MEASURE  tl_perf profile   then   tl_perf compare
5. DECIDE   exit 0  -> keep: commit ("perf(<component>): <change> — X -> Y ms/sample,
                       outputs equivalent"), log it, `tl_perf score`, go to 1.
                       compare has made this run the reference: the next change
                       must beat THIS run, not the original baseline
            exit 8  -> revert (git checkout -- . / git restore), read the mismatching
                       fields in compare.json, understand why, try another fix
            exit 9  -> revert; the change didn't matter
            exit 10 -> revert, or bound the cache and re-measure

STOP when no candidate is worth it, or every worth-it candidate had 3 attempts without a
kept fix. After every kept fix the ranking changes — the next limit is often something
that was always there, now visible.
```

Rules of the loop:

- **One change per measurement.** Two changes in one run make the verdict meaningless.
- **Prefer changes that win in both orders.** `profile` reports a sorted-order what-if; a
  gain visible only there depends on processing order and is not delivered.
- **Keep sample settings fixed** between baseline and later runs; `compare` compares
  per-sample means on the same sample set.
- If the baseline itself was wrong (e.g. you fixed a crash in Phase 3), re-take it:
  `tl_perf profile --set-baseline`.

### 4M — memory loop

```
1. PICK     the top memory candidate from `tl_perf score --objective memory` that is worth
            it (not marked minor: at least max(5% of the footprint, 64 MB)). Candidates of
            unknown size (a growing container, an unbounded cache, open figures) are worth
            a look when the status is AMBER/RED. GREEN: only candidates whose fix cannot
            cost runtime (an unused import, columns never read, a duplicate copy, a leak).
2. EXPLAIN  what holds the memory and why, from the census and the code: which object,
            created where, alive since when, needed by whom. A peak set inside preprocess
            means several large temporaries alive at once — read preprocess for full-width
            reads, copies and intermediates kept until the end. No explanation -> no change.
3. CHANGE   one fix from the memory classes of the catalog (M1-M12). The narrower dtype,
            the codes for repeated strings, the slice, the dropped column must leave every
            output identical — if not, it is a Phase 5 option.
4. MEASURE  tl_perf profile   then   tl_perf compare --objective memory
5. DECIDE   exit 0  -> keep: commit ("mem(<component>): <change> — X -> Y GB per worker,
                       outputs equivalent, runtime +Z%"), log it, `tl_perf score`, go to 1
            exit 8  -> revert; the change altered outputs
            exit 9  -> revert; the memory saving was too small to count
            exit 12 -> revert; it cost more runtime than the triage allows — look for a
                       cheaper way to save the same memory

STOP when no memory candidate is worth it, or (GREEN) no free win is left, or every
worth-it candidate had 3 attempts without a kept fix.
```

Rules of the memory loop:

- **Footprint is per worker process.** Bytes held once at import or in the preprocess
  result are paid by every worker; prefer removing them over trimming per-call temporaries.
- **Memory-mapped and file-backed data are not held memory** (`np.load(mmap_mode="r")`),
  and they stay exact. A one-time conversion written next to the data must be reported
  (disk use) and must fall back cleanly when that location is read-only.
- **Never trade correctness for memory silently**: a float64 → float32 conversion of values
  that are not exactly representable, a truncated string, a dropped column that any output
  reads — those change outputs. `compare` will say so; offer them in Phase 5.
- A runtime fix that the memory loop later undoes needs both verdicts: keep the memory fix
  only if `compare --objective memory` accepts it with the runtime cost in tolerance.

## Phase 5 — Behavior-changing options (consent required)

Only if, after Phase 4, **one component still dominates** (roughly: more than half of the
expected runtime, or many times the inference cost) **and** no lossless fix is left for it
— or the memory triage is still RED/AMBER and the remaining memory holders can only shrink
by changing values (narrower dtypes that round, lower-resolution stored data, dropping
columns that feed an output).
Follow `reference/perf-lossy-options.md`: present each option with what changes, the
expected gain from the profile, and the cost; ask once; apply only what the user accepts,
one commit each; `tl_perf compare` must show **only** the declared fields changed. If the
user declines everything, that is a valid outcome — record it.

## Phase 6 — Server smoke validation (push by default)

Prove the optimized integration still **works** on the Tensorleap server: every component
runs there without errors. This is a smoke test, not a runtime measurement (Phases 1–4
measured runtime), so it runs on a **small subset — about 50 samples per state** — and
finishes in minutes. **Push by default, don't ask**, unless the user said not to push. Use
the batch size from Phase 1, capped at the subset size.

1. `scripts/perf_preflight.sh` → exit 0 required (see Phase 0 for the others).
2. Model file: the one `@tensorleap_load_model` loads locally (ask only if it can't be
   inferred). Version name: `<integration>-perf-smoke-<yyyymmdd>`.
3. **Build the subset on a throwaway branch**, so the cap never touches the optimized code:
   `git switch -c tensorleap-runtime-optimization-smoke`. In the `@tensorleap_preprocess`
   function, cap each state right where its `PreprocessResponse` is built — and change
   nothing else:
   - list of ids: `sample_ids[:50]`;
   - grouped response: the first groups that together hold about 50 samples;
   - `length=` form: `length=min(length, 50)`.

   Mark it `# smoke-validation cap: not for merge` and commit. Check that the capped
   integration still loads: `tl_perf preflight --out tensorleap/runtime-optimization/smoke`
   (exit 0; the separate `--out` keeps the real `preflight.json` intact).
4. **Reconcile first — the server is the source of truth:** `leap run list -t Push`; if a
   push for this project is still in flight, wait for it; never re-push blind.
5. Push from the smoke branch, as a background shell:
   - **Current CLIs** — `leap push -h` says that with `--eval`, `--no-wait` lets the
     server run the evaluation itself once the push finishes:
     ```
     leap push -m <model> -n <version> -b <batch> --eval --no-wait --yes < /dev/null > push.log 2>&1
     ```
     It returns once the push has started; the Evaluate no longer depends on this
     session. Follow it with `leap run info <push-job-id>` (status, and the chained
     Evaluate's id) or `leap run list -t Evaluate`.
   - **Older CLIs** (no such sentence in `leap push -h`): the same command **without**
     `--no-wait` — there, `--eval` creates the Evaluate from your machine after the push
     finishes, so **keep the push process alive until the Evaluate exists**. Until `leap
     run list -t Evaluate` shows the new run, don't let the session end: keep checking
     (`sleep` between checks), or — only if your environment re-invokes you when a
     background job finishes — wait for that notification.

   `--yes` acknowledges pre-push warnings instead of waiting at a prompt. If `push.log`
   contains `View errors in interactive mode`, the push **failed** (older CLIs hang there):
   kill it and read `leap run logs <push-run-id>`. The first push to a server can take long
   (it may pull a large base image).
6. **Back to the optimized code:** once the push job shows in `leap run list -t Push` (the
   code is uploaded by then), `git switch tensorleap-runtime-optimization` and delete the
   smoke branch (`git branch -D tensorleap-runtime-optimization-smoke`). If you must
   re-push later, recreate the smoke branch the same way.
7. **Finish the deliverables before you wait for anything.** As soon as the push is
   submitted (current CLIs — the server chains the Evaluate itself) or the Evaluate exists
   (older CLIs), write `report.json` with `server_validation` = `{"status": "SUBMITTED" or
   "IN PROGRESS", "job": "<push or evaluate run id>"}`, run `tl_perf report`, and
   **commit** the report, the log and `static.json`. A long push or evaluation, or a
   session that ends, must never leave the work without a committed report.

   **Wait in the foreground.** Poll with one foreground command at a time (`sleep 120 &&
   leap run info <push-job-id>`), not a background timer followed by ending your turn: an
   unattended session that ends its turn is over, and whatever it was waiting for is
   lost. Then:
8. Find the Evaluate run (`leap run list -t Evaluate`) and watch **that** run with a
   token-free background loop until it is terminal:
   ```
   while :; do s=$(leap run list -t Evaluate | grep "$RUN_ID")
     case "$s" in *FINISHED*|*FAILED*|*STOPPED*|*TERMINATED*) echo "$s"; break;; esac
     sleep 300; done
   ```
   If the watcher dies, the evaluation is unaffected — relaunch the watcher, never re-push.
   **Push finished but no Evaluate exists** (older CLIs: the push process was killed
   between the push and the evaluate trigger): from the smoke branch, re-push over the same
   version, `leap push -m <model> -o <version> -b <batch> -u metric --eval --yes`, then
   watch the new run. (On an overwrite the CLI asks what changed; `-u metric` answers
   "full re-evaluation" without a prompt.)
9. **The server rejects the Evaluate at creation** — the Push is FINISHED but the Evaluate
   is FAILED immediately with empty logs, or the CLI prints a 4xx (e.g. `400 Bad Request`):
   retry **once** with the overwrite command above. If it is rejected again, **stop**. The
   integration passed every push stage, so this is a server-side problem the integration
   can't fix: record the exact error, the run ids and what you tried as a Recommended
   Tensorleap action, and finish the report as not server-validated. Don't guess at batch
   sizes or flags.
10. Outcome: **FINISHED** → update `server_validation` in `report.json` (status, duration,
    `"notes": "smoke subset: <n> samples per state"`), re-run `tl_perf report`, and commit
    the update. **FAILED** → `leap run logs <run-id>`; an out-of-memory failure means the
    batch size or a cache is too large for the server: lower `-b` (re-run `fit` with the
    server's memory) and re-push with `-o <version> -u metric`. Any other error is an
    integration bug: fix it on the optimization branch, re-verify with `compare`, and
    re-run the smoke push.

**GATE:** the Evaluate reached a terminal state, or you recorded why validation was not
possible.

## Phase 7 — Report

The report follows `reference/perf-report-template.md`: write
`tensorleap/runtime-optimization/report.json`, then `tl_perf report` (exit 11 → fix and
re-run). If Phase 6 already produced it (validation in progress), finalize it here with the
Evaluate's outcome; if there was no server validation, write it now. Tag every entry in
`optimizations` with its `kind` (`performance`, `correctness`, `prerequisite` or
`memory`) and its `catalog` class (a letter, an `M` class, or `new` when no class
describes it). Fill `memory` (status and reasons from `score`, the largest remaining
holder); `tl_perf report` adds the footprint breakdown before → after from the profiles.
Read `report.md` once as the reader would, fix what is unclear, and commit it with the log
and `static.json`.

Your closing message names the deliverables — the branch and its commits, `report.md`,
the remaining bottleneck in one sentence (and the largest remaining memory holder when the
memory status was not GREEN) — and stops.

## Equivalence: what "lossless" means here

- **Bit-identical by default** for every input, ground truth, metadata value, prediction,
  metric, loss and visualizer output on the snapshot samples. A tolerance
  (`compare --rtol/--atol`) is allowed only with a stated reason (e.g. a reordered float
  sum) and goes into the report.
- **Nondeterministic outputs** (differ between two runs of unchanged code) are checked by
  distribution; report them — seeding them is a behavior change for the user to decide.
- **Only exercised code is verified.** Branches the snapshot samples never execute are not
  covered — list them under `coverage_caveats`.
- **Never trust a claim of equivalence** — a commit message, a comment, "it should be the
  same". Only `compare` decides.

## Never

- Never keep a change that `compare` did not accept (exit 0), or apply a behavior-changing
  option without an explicit yes.
- Never measure runtime with `@tensorleap_integration_test` or a hand-rolled loop — only
  `tl_perf` models how Tensorleap runs the components.
- Never make a visualizer depend on a cache filled elsewhere, and never assume a cache
  survives across worker processes.
- Never alter the user's source data or the model weights.
- Never re-push blind. Use `--no-wait` only together with `--eval`, and only on a CLI whose
  `leap push -h` says the server then runs the evaluation itself.
- Never let the smoke-validation cap reach the optimization branch.
- Never put secrets, credentials or data paths into the report beyond what the user's own
  repo already contains.
- Never save memory by changing values (rounding dtypes, truncating, dropping data an
  output reads) without an explicit yes; never let a memory fix cost more runtime than the
  triage allows.
