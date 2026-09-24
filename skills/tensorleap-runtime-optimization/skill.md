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
on the Tensorleap server, and hand over a report that names what limits runtime now.

Work from the **integration repo root**, inside the **integration's own Python
environment** (the one that runs `leap_integration.py` — `poetry run` by default, or the
interpreter in `$TL_PY`). All measurements come from `{{scripts_dir}}/tl_perf.py`; you read
code, reason, and make changes. Artifacts go to `tensorleap/runtime-optimization/`; code
changes go to a branch, **one commit per accepted fix**.

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
- **Samples are not processed in your preprocess order** → per-file caches over a flat
  sample list mostly miss; group samples by file instead.
- **Preprocess** runs again in every worker process → keep it light.
- Dataset code (encoders, metadata, metrics, loss, visualizers) runs on **CPU**; only the
  model uses the GPU.
- Caches are **per worker process** → their memory multiplies; keep them small.

And the cost model: **every component is scored by its mean cost per sample relative to
the model's mean inference time per sample.** Work that costs many times the inference is
what limits runtime; work far below it is noise, however ugly the code.

## Operating principle: measure, then change; lossless first

- **Evidence before change.** Nothing gets changed because it *looks* slow. A change needs
  a measured signal (`tl_perf profile` / `score`) and, ideally, a matching static tell.
- **Lossless first.** A change is kept only if `tl_perf compare` shows the outputs are
  **equivalent** and runtime is **lower** with no memory regression. Changes that alter
  outputs are a separate, consent-gated step (Phase 5).
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
poetry run python {{scripts_dir}}/tl_perf.py <subcommand> [options]
```

(Or `$TL_PY {{scripts_dir}}/tl_perf.py …`. `--root` defaults to `.`; outputs go to
`tensorleap/runtime-optimization/`.)

| Subcommand | Does | Writes |
|---|---|---|
| `preflight` | environment, devices (and whether the model actually uses the GPU), code-loader features, integration validity, model load | `preflight.json` |
| `floor` | the model alone: warm-up, P50/P90/P95/P99 per batch size, recommended batch size (throughput knee) → **the scoring unit** | `floor.json` |
| `fit` | per-sample tensor size, rows per state, **measured** model memory per batch size, recommended batch size, and whether every metric/loss accepts a batch | `fit.json` |
| `profile` | every wired component, run the way Tensorleap runs it (fresh processes per pass: generation, sorted-order what-if, visualizers, diagnostics, output snapshot); the first run becomes the equivalence **baseline** | `runs/NNN/profile.json`, `profile.json`, `baseline/` |
| `score` | ranks candidates: expected seconds removable × confidence, with evidence | `score.json` |
| `compare` | latest run vs baseline: output equivalence, expected-runtime gain, memory | `runs/NNN/compare.json` |
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
| 9 | `compare`: equivalent but **no meaningful gain** (< 5%) | revert, unless the change is a prerequisite for the next one |
| 10 | `compare`: memory regression | revert, or shrink the cache and re-measure |
| 11 | `report`: invalid `report.json` | fix the listed fields, re-run |

## Phase 0 — Preflight and setup

1. **Environment.** Find the integration's Python environment (`pyproject.toml` → poetry;
   `requirements.txt` → the venv the user runs it with). Everything below runs in it.
2. **Repo state.** `git status`. If there are uncommitted changes, **ask** whether to commit
   them first — never mix the user's work with optimization commits. Then create a branch:
   `git switch -c tensorleap-runtime-optimization`.
3. **`tl_perf preflight`.** Act on the exit code (table above). Note in the log: device,
   code-loader version and features (`grouped_preprocess` needs ≥ 1.0.196), state sizes.
4. **Server check (for Phase 6, non-blocking):** `{{scripts_dir}}/perf_preflight.sh`.
   Exit 0 → the validation push is available. Exit 2 → continue locally; the report will
   say the result was not validated on a server. Exit 3 → ask the user to `leap auth
   login` when you reach Phase 6. Exit 6 → ask whether the server is remote (port-forward)
   or must be started, when you reach Phase 6.

**GATE:** `preflight` exits 0 (or 4 with the CPU-floor caveat noted).

## Phase 1 — Floor, fit, sanity

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

**GATE:** a floor exists and no metric/loss fails the batch check.

## Phase 2 — Read the code (hypotheses, not conclusions)

Read the integration end to end: preprocess, every encoder, metadata, metric, loss,
visualizer, and anything they call. Look for the **static tells** in
`{{reference_dir}}/perf-bottleneck-catalog.md` — for example one component calling another
component's function, the same file opened by several components, Python loops over rows
in metrics, per-call model/table loading, per-sample `list.index`, debug statistics on large
arrays, unbounded caches, unseeded randomness, data-dependent branches.

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
2. `tl_perf score --static tensorleap/runtime-optimization/static.json`.
3. Read, in this order:
   a. **worker failures / "not profiled"** lines → a component that crashes or can't be
      wired is fixed first (it breaks on the platform too);
   b. **nondeterministic outputs** → report them (catalog T); the equivalence check will
      use a distribution check for them;
   c. **shares** of expected runtime (generation / inference / metrics / visualizers /
      startup) and the **generation-to-inference ratio**;
   d. the **ranked candidates** and their **evidence**.
4. Log the baseline breakdown.

## Phase 4 — Lossless optimization loop

```
1. PICK     the highest-priority candidate that is worth it: ratio to inference >= 1, or
            >= 10% of expected runtime. Skip ones marked (minor). Startup is attacked only
            when it is large in absolute terms (it is paid once per worker).
2. EXPLAIN  why it is slow, from its evidence + the code (profile diagnostics list hot
            functions and repeated calls). No explanation -> no change.
3. CHANGE   one fix from the catalog / {{reference_dir}}/perf-levers.md. It must respect
            the execution model: rely on a cache ONLY where Tensorleap shares it (within
            one sample's encoders + metadata, or within one process across samples),
            never across processes, never from a visualizer.
4. MEASURE  tl_perf profile   then   tl_perf compare
5. DECIDE   exit 0  -> keep: commit ("perf(<component>): <change> — X -> Y ms/sample,
                       outputs equivalent"), log it, `tl_perf score`, go to 1
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

## Phase 5 — Behavior-changing options (consent required)

Only if, after Phase 4, **one component still dominates** (roughly: more than half of the
expected runtime, or many times the inference cost) **and** no lossless fix is left for it.
Follow `{{reference_dir}}/perf-lossy-options.md`: present each option with what changes, the
expected gain from the profile, and the cost; ask once; apply only what the user accepts,
one commit each; `tl_perf compare` must show **only** the declared fields changed. If the
user declines everything, that is a valid outcome — record it.

## Phase 6 — Server validation (push by default)

Validate the optimized integration on the Tensorleap server — **push by default, don't
ask**, unless the user said not to push. Use the batch size from Phase 1.

1. `{{scripts_dir}}/perf_preflight.sh` → exit 0 required (see Phase 0 for the others).
2. Model file: the one `@tensorleap_load_model` loads locally (ask only if it can't be
   inferred). Version name: `<integration>-perf-<yyyymmdd>`.
3. **Reconcile first — the server is the source of truth:** `leap run list -t Push`; if a
   push for this project is still in flight, wait for it; never re-push blind.
4. Push as a background shell (it can take longer than a foreground command allows):
   ```
   leap push -m <model> -n <version> -b <batch> --eval < /dev/null > push.log 2>&1
   ```
   Never use `--no-wait`. If `push.log` contains `View errors in interactive mode`, the
   push **failed** (older CLIs hang there): kill it and read `leap run logs <push-run-id>`.
5. Find the Evaluate run (`leap run list -t Evaluate`) and watch **that** run with a
   token-free background loop until it is terminal:
   ```
   while :; do s=$(leap run list -t Evaluate | grep "$RUN_ID")
     case "$s" in *FINISHED*|*FAILED*|*STOPPED*|*TERMINATED*) echo "$s"; break;; esac
     sleep 300; done
   ```
   If the watcher dies, the evaluation is unaffected — relaunch the watcher, never re-push.
6. Outcome: **FINISHED** → record the duration. **FAILED** → `leap run logs <run-id>`; an
   out-of-memory failure means the batch size or a cache is too large for the server: lower
   `-b` (re-run `fit` with the server's memory) and re-push with `-o <version>`; any other
   error is an integration bug to fix, re-verify with `compare`, and re-push.

**GATE:** the Evaluate reached a terminal state, or you recorded why validation was not
possible.

## Phase 7 — Report

Write `tensorleap/runtime-optimization/report.json` following
`{{reference_dir}}/perf-report-template.md`, then `tl_perf report` (exit 11 → fix and
re-run). Read `report.md` once as the reader would and fix what is unclear.

Your closing message names the deliverables — the branch and its commits, `report.md`,
the remaining bottleneck in one sentence — and stops.

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
- Never re-push blind or use `leap push --no-wait`.
- Never put secrets, credentials or data paths into the report beyond what the user's own
  repo already contains.
