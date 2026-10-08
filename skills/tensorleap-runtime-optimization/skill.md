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
version: 0.1.0
globs: ["leap_integration.py", "leap.yaml"]
alwaysApply: false
tools: [claude, cursor, copilot, agents, devin]
scripts_dir: .tensorleap/scripts
reference_dir: .tensorleap/reference
---

# Optimizing a Tensorleap integration's runtime

You take an existing, working Tensorleap integration (`leap_integration.py` + `leap.yaml`
at the repo root) from "it's slow and nobody knows why" to a measured, component-level
picture, apply every **lossless** optimization the evidence supports, validate the result
on the Tensorleap server, and hand over a report that names what limits runtime now. The
same flow reduces the **memory the integration's own code holds** per worker process —
first, by default: memory wins conflicts with runtime, because a smaller footprint usually
also makes the run faster.

Work from the **integration repo root**, inside the **integration's own Python
environment** (the one that runs `leap_integration.py` — `poetry run` by default, or the
interpreter in `$TL_PY`). All measurements come from `{{scripts_dir}}/tl_perf.py`; you read
code, reason, and make changes. Artifacts go to `tensorleap/runtime-optimization/`.
`tl_perf` keeps the code history with any version control or none: an accepted `compare`
saves the fix as `fixes/NN.patch` (changes made before the baseline: `00`), and `tl_perf
restore` undoes what wasn't accepted. In a git repo, also commit each accepted fix on a
branch, **one commit per fix**. "Commit" in this skill means that git commit; in any other
repo, skip it and never run that tool.

## What you are optimizing (and what that implies)

Tensorleap does not run your integration as one script. It runs components in separate
worker processes, on batches or single samples, in an order you don't control. An
optimization is only real if it helps **there**. Read
`{{reference_dir}}/perf-execution-model.md` before the first change; the short version:

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
- **Memory first: memory wins conflicts with runtime.** This is the default (`--priority
  memory`), offline and online, because a smaller footprint usually also makes the run
  faster: more workers fit, no out-of-memory restarts, less copying. The memory loop runs
  first, a memory fix may cost up to +15% runtime, and a runtime fix is kept only if it does
  not grow memory. `tl_perf score` still triages memory (GREEN / AMBER / RED). The report
  leads with memory only where there is memory pressure — offline, a status of AMBER or RED;
  online, an out-of-memory kill, a pod near its memory limit, a server memory warning, or an
  AMBER/RED status — and otherwise leads with time.
- **Unless the user puts runtime first.** With `--priority runtime` the triage decides:
  **RED** (out-of-memory reported or seen, or one worker's footprint over half the memory
  budget) → the memory loop runs first and a memory fix may cost up to +15% runtime;
  **AMBER** (a large footprint, a structure over 1 GB per worker, or growth with the
  samples) → runtime loop first, then the memory loop, memory fixes within noise (3%);
  **GREEN** → runtime loop, then only **free** memory wins (no runtime cost).
- **The catalog is where you start, not where you stop.** Candidates come from
  measurement: `profile` / `score` rank every component by cost, whether or not its problem
  is in `{{reference_dir}}/perf-bottleneck-catalog.md`. When the top cost matches no catalog
  class, find the cause yourself — the profile's hot functions and repeated calls, a
  focused `cProfile` of that component, the code — and fix it the same way (one change,
  `compare`-verified). Fix **correctness bugs** you meet on the way first (a component that
  raises on some samples, a metric wrong on batches, a loss that divides by the wrong axis):
  they break the platform run no matter how fast it is. Log every problem no catalog class
  describes as **new**, and tag it `"catalog": "new"` in the report.
- **Run autonomously; ask only when blocked.** Infer everything you can from the repo and
  the artifacts. The questions you may need to ask: uncommitted changes in a git repo
  (Phase 0), the model file to push if it can't be inferred (Phase 6), the
  behavior-changing options (Phase 5), and whether to run online diagnostics (Phase 6.0,
  asked once; never when unattended). Nothing else is a reason to stop.
- **Keep `tensorleap/runtime-optimization/optimization-log.md`.** Append as you go: each
  candidate, the evidence, what you tried, the `compare` verdict, the commit or patch. It is
  the source of the final report and survives an interrupted session.

## The measurement tool

Run every subcommand from the repo root through the integration's environment:

```
poetry run python {{scripts_dir}}/tl_perf.py <subcommand> [options]
```

(Or `$TL_PY {{scripts_dir}}/tl_perf.py …`. `--root` defaults to `.`; outputs go to
`tensorleap/runtime-optimization/`.)

| Subcommand | Does | Writes |
|---|---|---|
| `preflight` | environment, devices (and whether the model actually uses the GPU), code-loader features, integration validity, model load | `preflight.json` |
| `floor` | the model alone: warm-up, P50/P90/P95/P99 per batch size, recommended batch size (throughput knee) → **the scoring unit** | `floor.json` |
| `fit` | per-sample tensor size, rows per state, **measured** model memory per batch size, recommended batch size, and whether every metric/loss accepts a batch | `fit.json` |
| `profile` | every wired component, run the way Tensorleap runs it (fresh processes per pass: generation, sorted-order what-if, visualizers, diagnostics, output snapshot, **user-code memory**); the first run becomes the equivalence **baseline** | `runs/NNN/profile.json`, `runs/NNN/memory.json`, `profile.json`, `baseline/` |
| `score` | ranks candidates: expected seconds removable × confidence, with evidence; always triages memory (GREEN / AMBER / RED) and ranks **memory candidates** (`--objective memory` lists them first) | `score.json` |
| `compare` | latest run: output equivalence vs the **baseline**; gain and memory vs the **last accepted run** (exit 0 makes it the new reference). `--objective memory` judges a memory fix: footprint drop, runtime within the triage tolerance | `runs/NNN/compare.json` |
| `report` | renders your `report.json` into `report.md` and the published `report.html` (one self-contained page): Part 1 offline, Part 2 online (only after an approved diagnostics run) | `report.md`, `report.html` |
| `online collect` / `analyze` | Phase 6B only: follow the diagnostics run and keep its logs; stable-window statistics, bottleneck, offline-vs-online | `online/` |

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
| 20 | `online collect`: the run is still going | run the same command again (it resumes) |
| 21 | `online collect`: the visualization's pace is steady | ask the user to stop the run in the Tensorleap UI, then run the same command again until exit 0 |

Memory options: `profile --memory-samples N` (samples per state in the memory pass),
`--no-memory`, `--no-import-costs`; `score --memory-symptom none|high|oom`, `--memory-gb`,
`--red-share` / `--amber-share` / `--amber-holder-gb` (triage thresholds); `compare
--objective memory`, `--min-memory-gain`, `--runtime-tolerance`, `--max-mem-increase`.

## Phase 0 — Preflight and setup

1. **Environment.** Find the integration's Python environment (`pyproject.toml` → poetry;
   `requirements.txt` → the venv the user runs it with). Everything below runs in it.
2. **Repo state (git repos only).** `git status`. If there are uncommitted changes, **ask**
   whether to commit them first — never mix the user's work with optimization commits. Then
   create a branch: `git switch -c tensorleap-runtime-optimization`. (`tl_perf` gives its
   output directory its own `.gitignore`: only the report, the log and `static.json` are
   ever committed.)
3. **`tl_perf preflight`.** Act on the exit code (table above). Note in the log: device,
   code-loader version and features (`grouped_preprocess` needs ≥ 1.0.196), state sizes.
4. **Server check (for Phase 6, non-blocking):** `{{scripts_dir}}/perf_preflight.sh`.
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
5. **Priority.** Memory is the default: memory wins every conflict with runtime, and
   nothing needs to be passed. Only when the user says speed matters more than memory —
   "as fast as possible, memory is fine", "runtime is what matters" — pass `--priority
   runtime` to every `tl_perf score` call (compare, online analyze and report read it from
   `score.json`), write `"priority": "runtime"` in `report.json`, and quote the words you
   based it on in the log and in the report's `priority_reason`. A request that asks for
   both without saying which matters more keeps the default.

**GATE:** a floor exists and no metric/loss fails the batch check.

## Phase 2 — Read the code (hypotheses, not conclusions)

Read the integration end to end: preprocess, every encoder, metadata, metric, loss,
visualizer, and anything they call. Look for the **static tells** in
`{{reference_dir}}/perf-bottleneck-catalog.md` — for example one component calling another
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
- **imports**: packages imported at module level that evaluation never uses (training,
  plotting, experiment-tracking or data-prep tools, often pulled in by a helper module),
  names imported and never referenced, and heavy packages imported at the top that only one
  visualizer or metric needs (catalog M1);
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
4. **Import check.** `profile` records which stage really runs each third-party package
   (preprocess, generation, metrics and loss, visualizers), times each candidate import
   alone in a fresh interpreter (memory and seconds), and lists imported names its files
   never reference. Read its `M1` findings:
   - **unused** — a package the integration imports but no stage ran: remove the import (or
     move it into the one function that needs it, e.g. an offline tool). Every worker
     process pays its import memory and time for nothing, so it is a memory candidate and a
     start-up candidate (`startup:import:<package>`) at once — a both-win.
   - **lazy** — a package only the metrics or only the visualizers run: import it inside
     them. That keeps it out of every worker's start-up and out of the memory of the phases
     before them; it does **not** remove the cost (each worker still imports it once when
     that stage starts), so judge it as memory, never claim it as a runtime gain.
   - names imported but never referenced, in files whose package *is* used elsewhere, are
     cosmetic: tidy them only alongside a real fix.
   A package below `M1`'s size (fewer than 20 modules) is not worth a change. If
   `--no-import-costs` was used, re-profile without it before acting on an import. An
   import kept for what it does at import time (a plugin or registry it fills, a backend it
   selects) is used even though none of its code runs later: read the import before
   removing it — a clean load and `compare`'s equivalence check catch a wrong removal.
5. Log the baseline breakdown, runtime and memory.

## Phase 4 — Lossless optimization loops (runtime 4R, memory 4M)

Run the loops in the order the priority gives: **memory (the default)** → 4M (every
lossless memory candidate, up to +15% runtime each), then 4R (no memory growth). With
**`--priority runtime`** the memory triage decides: **RED** → 4M, then 4R; **AMBER** → 4R,
then 4M; **GREEN** → 4R, then 4M for free wins only. Re-run `tl_perf score` after every
kept fix — a fix can change the status. Log every conflict between the two — a memory fix
kept at a runtime cost, a runtime fix rejected because it grew memory — for the report's
`tradeoffs`.

### 4R — runtime loop

```
1. PICK     the highest-priority candidate that is worth it — catalogued or not: ratio to
            inference >= 1, or >= 10% of expected runtime. Skip ones marked (minor).
            Startup (import + preprocess, paid by every worker) counts once in the
            expected total — `score` and `compare` both include it — so a preprocess fix
            is judged like any other; it earns exit 0 when startup is a real share. An
            unused import is its own start-up candidate (`startup:import:<package>`).
2. EXPLAIN  why it is slow, from its evidence + the code (profile diagnostics list hot
            functions and repeated calls; if no catalog class fits, profile that component
            with cProfile and read the hot path). No explanation -> no change.
3. CHANGE   one fix from the catalog / {{reference_dir}}/perf-levers.md. It must respect
            the execution model: rely on a cache ONLY where Tensorleap shares it (within
            one sample's encoders + metadata, or within one process across samples),
            never across processes, never from a visualizer, never from a metric or loss
            to what generation computed.
4. MEASURE  tl_perf profile   then   tl_perf compare
5. DECIDE   exit 0  -> keep: commit ("perf(<component>): <change> — X -> Y ms/sample,
                       outputs equivalent"), log it, `tl_perf score`, go to 1.
                       compare has made this run the reference: the next change
                       must beat THIS run, not the original baseline
            exit 8  -> revert (`tl_perf restore`), read the mismatching
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
            cost runtime (an unused import, an import only the metrics or visualizers need,
            columns never read, a duplicate copy, a leak).
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
Follow `{{reference_dir}}/perf-lossy-options.md`: present each option with what changes, the
expected gain from the profile, and the cost; ask once; apply only what the user accepts,
one commit each; `tl_perf compare` must show **only** the declared fields changed. If the
user declines everything, that is a valid outcome — record it.

## Phase 6 — Server validation

Two modes. **6A smoke validation is the default**: it proves the optimized integration still
works on the Tensorleap server. **6B online diagnostics** also measures how the integration
behaves on the platform — what limits the run there, with evidence from the run's own
logs — but it is a long, server-heavy run, so it happens **only after the user explicitly
says yes** in 6.0. Diagnostics read the run; they never change server settings.

### 6.0 — Choose the mode (ask once)

1. **Unattended?** If you are running unattended (the operator's prompt says so, or there
   is no way to get an answer from the user), don't ask: run **6A** and record
   `"mode": "smoke", "authorized_by_user": false`. An unattended run never runs 6B.
2. Otherwise ask **once**, stating all of it — even if the user already asked for
   diagnostics, get a yes:
   - what it does: pushes the optimized integration on the **whole dataset** (rows per
     state from `fit.json`), runs the full evaluation with visualizations, and collects the
     server's logs while it runs;
   - how long and how heavy: as long as a full evaluation of that dataset on their server
     — on a large dataset, hours. A long visualization runs only until its pace is steady
     over enough samples (the diagnostics need its pace and per-sample cost, not every
     sample), and then you ask them to stop the run in the Tensorleap UI (the CLI can't
     stop a run); a short one runs to the end. The server adds workers as it needs them and
     stays busy until then;
   - the option: to test on less data, they can give a **cap** — the same number of
     samples for every state, like the integration's `sample_limit_per_split`. With a small
     cap a phase may not reach a steady pace; the report then says so;
   - what they get: **Part 2 — Online** of the report — what limits the run
     on the platform and whether it is on the critical path, with log evidence; for the
     evaluation and the visualization, the root cause of a slow phase (what set the pace,
     where that time went, the part to change); whether the server's CPU / memory / worker / GPU settings fit the run; memory
     per worker; how far the run is from the model's floor; what to fix in the integration
     and what to ask Tensorleap for;
   - the default: "no" runs the smoke check (about 50 samples per state, minutes).
   In the same message, ask whether the server uses Tensorleap's **automatic** resource
   settings or **manual** ones (CPU and memory per pod set by hand). Don't ask separately;
   if they don't know, the report gives advice for both.
3. A clear yes → **6B** with `"mode": "diagnostics", "authorized_by_user": true`, and the
   cap if they gave one. Anything else — no, no answer, "maybe later" → **6A** with
   `"mode": "smoke", "authorized_by_user": false`.

### 6A — Smoke validation (default)

Prove the optimized integration still **works** on the Tensorleap server: every component
runs there without errors. This is a smoke test, not a runtime measurement (Phases 1–4
measured runtime), so it runs on a **small subset — about 50 samples per state** — and
finishes in minutes. **Push by default, don't ask**, unless the user said not to push. Use
the batch size from Phase 1, capped at the subset size.

1. `{{scripts_dir}}/perf_preflight.sh` → exit 0 required (see Phase 0 for the others).
2. Model file: the one `@tensorleap_load_model` loads locally (ask only if it can't be
   inferred). Version name: `<integration>-perf-smoke-<yyyymmdd>`.
3. **Cap the subset in place**, on the last accepted code (step 6 removes it with
   `tl_perf restore`). In the `@tensorleap_preprocess` function, cap each state right where
   its `PreprocessResponse` is built — and change nothing else:
   - list of ids: `sample_ids[:50]`;
   - grouped response: the first groups that together hold about 50 samples;
   - `length=` form: `length=min(length, 50)`.

   Mark it `# smoke-validation cap: not for merge`; don't commit it. Check that the capped
   integration still loads: `tl_perf preflight --out tensorleap/runtime-optimization/smoke`
   (exit 0; the separate `--out` keeps the real `preflight.json` intact).
4. **Reconcile first — the server is the source of truth:** `leap run list -t Push`; if a
   push for this project is still in flight, wait for it; never re-push blind.
5. Push, as a background shell:
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
   code is uploaded by then), run `tl_perf restore`: it removes the cap and leaves
   `leap.yaml` as the push left it. To re-push later, re-apply the cap the same way.
7. **Finish the deliverables before you wait for anything.** As soon as the push is
   submitted (current CLIs — the server chains the Evaluate itself) or the Evaluate exists
   (older CLIs), write `report.json` with `server_validation` = `{"mode": "smoke",
   "authorized_by_user": false, "status": "SUBMITTED" or "IN PROGRESS", "job": "<push or
   evaluate run id>"}`, run `tl_perf report`, and
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
   between the push and the evaluate trigger): with the cap re-applied, re-push over the
   same version, `leap push -m <model> -o <version> -b <batch> -u metric --eval --yes`, then
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
    integration bug: fix it in the optimized code, re-verify with `compare`, and
    re-run the smoke push.

**GATE (6A):** the Evaluate reached a terminal state, or you recorded why validation was not
possible.


### 6B — Online diagnostics (only after the user's yes in 6.0)

One authorized run on the whole dataset, or on the user's cap from 6.0. Each phase —
evaluation, then visualization — gets past its start-up (workers are added while the pace
climbs); the analysis finds the steady pace that follows. The run size is not estimated in
advance. Everything below reads that run; nothing changes server settings, and there is no
second diagnostics run (the 6A one-retry rule for a server-side rejection still applies).

1. Same preparation as 6A steps 1–2. Version name: `<integration>-perf-diag-<yyyymmdd>`.
2. **Whole dataset unless the user gave a cap.** No cap → push the optimized code as is.
   A cap → apply it in place before the push (a running evaluation can't be resized from
   here):
   - If the integration already reads a per-state limit from its config (the
     integration skill's `sample_limit_per_split` in `project_config.yaml`), set it to the
     user's number and change nothing else.
   - Otherwise cap each state in `@tensorleap_preprocess` exactly as in 6A step 3, with
     the user's number instead of 50.

   Mark it `# diagnostics cap: not for merge` (don't commit it), and check with `tl_perf
   preflight --out tensorleap/runtime-optimization/diag`.
3. Reconcile and push exactly as 6A steps 4–5 (the full evaluation with visualizations;
   batch size from Phase 1). If you capped, go back to the optimized code as in 6A step 6.
4. **Start collecting right away.** `leap run logs` keeps only the most recent part of each
   worker's log, and workers removed during the run disappear, so the logs are gathered
   repeatedly while the run is in progress:
   ```
   tl_perf online collect --job <push-run-id> --for 540
   ```
   It follows the push to its evaluation, polls progress and logs every `--interval`
   seconds (default 30), and keeps the merged logs under
   `tensorleap/runtime-optimization/online/` (identity fields removed; never committed).
   **Exit 20 = still running: run the same command again** — it resumes where it stopped.
   **Exit 21 = the visualization has what the diagnostics need**: a steady pace over enough
   samples (a fifth of the visualization, at least 200, at most 1,000), with at least a
   quarter of it and 5 minutes still to go — so a short or nearly finished visualization is
   never cut. It printed the pace, the samples and the time a stop saves: tell the user in
   one line to stop that run in the Tensorleap UI, then keep running the same command — it
   reads the last logs and exits 0 once the run has stopped (the analysis projects the full
   visualization time from the steady pace). If the user wants the whole visualization instead, continue with
   `--full-visualization`. Exit 0 = the evaluation reached a terminal state and its final
   logs are in. Run it in the foreground, one call after another, until exit 0: every gap
   in collection lowers the confidence of the numbers.
5. **Finish the deliverables before the run ends**, as in 6A step 7: right after the push
   is submitted write `report.json` with `server_validation` = `{"mode": "diagnostics",
   "authorized_by_user": true, "status": "SUBMITTED", "job": "<push run id>"}`, run
   `tl_perf report`, and commit.
6. **Server rejections and failures** follow 6A steps 9–10. For the one retry, start the
   overwrite push (`leap push -m <model> -o <version> -b <batch> -u metric --eval --yes`,
   no `--no-wait`) as a background shell that must stay alive until it creates the
   evaluation, then collect with the **new** push run id: `collect` finds the evaluation of
   that version even though it is not chained on the server. A run that fails is still
   analyzed: its failure (the server's reason, or an out-of-memory) is the primary finding.
7. **Analyze:** `tl_perf online analyze` (add `--settings-mode automatic|manual` with the
   user's answer from 6.0; leave it out if they didn't say). It covers the **evaluation**
   (with the start-up that leads into it) and the **visualization** only. The platform's
   analysis after evaluation (post-processing: embeddings, insights and the other steps)
   shows as one row of time and is not analyzed: no statistics, findings or root causes
   come from it. It splits each phase into
   warm-up, stable window and tail; computes per-component statistics (mean, P50/P90/P95/P99 where the logs allow,
   share of time) **over the stable window only**; attributes the bottleneck (what waits
   on what, and whether it is on the critical path); reads memory per worker (peaks,
   limits, restarts, out-of-memory); and compares the run with the offline measurements
   (floor, generation, metrics, visualizers, memory). For each of those phases that took at
   least a tenth of the run it builds a **root cause**: which side set the pace (who waited on
   whom), where that side's time went, the mechanism, the part to change, what is ruled
   out, how much of the phase is explained and a confidence — links that are inferred
   rather than measured are marked. Under each root cause it lists the **mechanisms** that
   generic rules found in that phase's own measurements: which step dominates, count × unit
   cost (and work done once per row where the unit is a batch), repeated work (the same
   data loaded, generated or computed again), growth over time (memory or a backlog that
   keeps rising), and serial work beside idle capacity (one core of many, one busy pod among
   idle ones, a hand-off on the evaluation's own thread). Each mechanism carries its cost
   and the engine part where it happens; mechanisms in shorter phases follow the root
   causes. The rules read shapes, not known problems: report what they find, and never
   turn it into a checklist of platform features. It judges the **server settings** (CPU, memory, worker
   pods and processes, GPUs) against what the run used. With memory as the priority (the
   default, from `score.json` or `--priority`) it builds a **memory root cause**: which pods hold
   and reserve the most memory, when they peak, how much of it is the integration's own code
   (from the offline memory pass), and the part to change. It leads with it only under
   **memory pressure** — a pod killed for memory, a peak at 85% of a limit, a memory warning
   from the server or the engine, or the offline memory check at AMBER or RED — and says
   which; otherwise the runtime root causes lead and the memory root follows them. It
   writes `online/analysis.json`.
   If a phase never reached a steady pace, it says so: report that and the numbers seen,
   never treat an unstable run as stable.
8. Update `server_validation` in `report.json` (status, duration, notes) and write
   `online_comparison`: one or two sentences on how the online picture differs from Part 1
   and why (for example a visualized-sample count far above the one assumed offline). Re-run
   `tl_perf report` — it renders **Part 2 — Online** from `online/analysis.json` — and
   commit. Every bottleneck claim in Part 2 comes from the analysis with its
   evidence; don't add claims the analysis doesn't support. When a phase has no root
   cause (its engine logs have no step-level timing) or a low confidence, say so — don't
   fill the gap with a guess. Settings advice is a recommendation for the user or
   Tensorleap; never change server settings. Integration fixes it suggests
   go to "Remaining issues"; platform-side needs go to `tensorleap_actions`. Don't apply
   any of them in this phase.

**GATE (6B):** the evaluation reached a terminal state, `online collect` exited 0, and
`online analyze` wrote its analysis — or you recorded why not.

## Phase 7 — Report

The report follows `{{reference_dir}}/perf-report-template.md`: write
`tensorleap/runtime-optimization/report.json`, then `tl_perf report` (exit 11 → fix and
re-run). If Phase 6 already produced it (validation in progress), finalize it here with the
Evaluate's outcome; if there was no server validation, write it now. Tag every entry in
`optimizations` with its `kind` (`performance`, `correctness`, `prerequisite` or
`memory`) and its `catalog` class (a letter, an `M` class, or `new` when no class
describes it), a short `title`, and its `memory` effect when measured. Fill `memory`
(status and reasons from `score`, the largest remaining holder), `tradeoffs` (every
memory/runtime conflict you logged in Phase 4) and the `owner` of the remaining bottleneck;
`tl_perf report` adds the footprint and time breakdowns before → after from the profiles.
Read `report.md` once as the reader would, fix what is unclear, and commit it with the log
and `static.json`.

The report has two parts, each answer-first with its details in a collapsed appendix:
**Part 1 — Offline** (always: what limits the integration's own code, what you changed,
what remains) and **Part 2 — Online** (only after an approved diagnostics run; otherwise one
line saying it was not run). A part leads with memory only when there is memory pressure;
otherwise it leads with time. `tl_perf report`
also writes **`report.html`, the published report**: one self-contained page (no external
files — it can be mailed or posted as is). Re-run `tl_perf report` whenever `report.json`
or the online analysis changes, so both files match. Don't commit `report.html` (it is
rebuilt from `report.json`).

Your closing message names the deliverables — the branch and its commits (git) or the
`fixes/` patches, `report.md` and `report.html` (the one to share), the remaining bottleneck
in one sentence (and the largest remaining memory holder when the memory status was not
GREEN) — and stops.

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
- Never commit a smoke or diagnostics cap, or leave it in the code once the push uploaded it.
- Never run online diagnostics (6B) without the user's explicit yes in 6.0, never when
  unattended, and never change server settings from this skill.
- Never put secrets, credentials or data paths into the report beyond what the user's own
  repo already contains.
- Never save memory by changing values (rounding dtypes, truncating, dropping data an
  output reads) without an explicit yes; never let a memory fix cost more runtime than the
  triage allows.
