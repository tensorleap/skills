# tensorleap-runtime-optimization

An AI-assistant skill that takes a working Tensorleap integration from "the evaluation is
slow (or runs out of memory) and nobody knows why" to a measured answer. It measures the
model's own inference speed, profiles every integration component the way Tensorleap runs
it, keeps only the fixes that leave every output identical, validates the result on your
Tensorleap server, and writes a report that names what limits runtime and memory now.

**How it works, step by step:** [`how-it-works.html`](how-it-works.html), next to this file,
is one self-contained page with flow charts and worked examples. GitHub shows it as source,
so download it and open it in a browser:

```bash
curl -fsSLO https://raw.githubusercontent.com/tensorleap/skills/main/skills/tensorleap-runtime-optimization/docs/how-it-works.html && open how-it-works.html
```

(`xdg-open` on Linux. The page works offline. This folder isn't installed with the skill.)

## What you need

- **A working integration**: `leap_integration.py` and `leap.yaml` at the repo root, and
  `check_dataset()` passing. If it doesn't pass yet, fix it first with the
  [`tensorleap-integration-creation`](../../tensorleap-integration-creation/skill.md) skill.
- **The integration's own Python environment**, with code-loader installed. The skill
  runs everything through `poetry run` by default. For another environment, set `TL_PY`
  to its interpreter.
- **A model file that loads locally**: the `.onnx` or `.h5` that `@tensorleap_load_model`
  loads.
- **For the server steps** (optional): the Tensorleap CLI (`leap`), logged in to your
  server with `leap auth login`. Without it, the local measurement and fixes still run, and
  the report says the result was not validated on a server.
- **For online diagnostics** (optional): a Tensorleap server whose engine writes the
  runtime timing lines: per-component percentiles, wait vs. work spans, and each pod's CPU
  and memory against its limits. Upgrade an older server with `leap server upgrade` (the
  [`tensorleap-install`](../../tensorleap-install/skill.md) skill can walk you through it).
  On a server without those lines the diagnostics still run, but on coarser log lines, and
  the report lowers its confidence and says why.

## Install

**Claude Code** (plugin `optimization` in the `tensorleap` marketplace):

```
/plugin marketplace add tensorleap/skills
/plugin install optimization@tensorleap
```

If you added the marketplace before this skill existed, run
`/plugin marketplace update tensorleap` first.

**GitHub Copilot, Cursor, Devin, AGENTS.md**: use the `install.sh` commands in the
[repo README](../../../README.md). They install every skill in this repo, this one included,
as a folder named `tensorleap-runtime-optimization`.

## Run it

Open your assistant in the **integration repo root**, ideally on a quiet machine (other
heavy jobs skew the timings), and describe the problem. The skill activates on runtime and
memory language. For example:

> Our Tensorleap evaluation of this integration is slow. Find out why, optimize it without
> changing its outputs, and give me the report.

Say more when it applies, because it changes what the skill does:

- **Memory symptoms**: "the workers run out of memory" or "the evaluation keeps getting
  killed". Memory is the default priority anyway; a reported symptom raises its urgency.
- **Speed over memory**: "runtime is what matters, memory is fine" makes runtime the
  priority.
- **Visualized samples**: on a large dataset, say how many samples your evaluation
  visualizes. Otherwise visualizer cost is weighted as if every sample were visualized.
- **No push**: "don't push to the server" keeps everything local.

It runs on its own and asks you only these:

1. **Uncommitted changes** (git repos): whether to commit them first, so your work never
   mixes with its fixes.
2. **The model file to push**, only if it can't tell which one.
3. **Fixes that change outputs** (for example lower resolution or a narrower dtype), only
   if one component still dominates after every lossless fix, or memory can only shrink by
   changing values. Each option comes with its expected gain and cost. Declining all of
   them is a valid outcome.
4. **Online diagnostics, yes or no.** In the same question it offers a per-state sample
   cap. It doesn't ask whether your server uses automatic or manual resource settings: it
   reads that from the run. The default, "no", runs the short smoke check described below.

When it runs unattended, it asks nothing and never runs online diagnostics.

## What it does

| Phase | What happens |
|---|---|
| 0. Preflight | Finds the environment, checks devices (is a GPU present, and does the model actually use it?), code-loader, the integration, and the server connection. In a git repo it works on a branch named `tensorleap-runtime-optimization`. |
| 1. Floor and fit | Times the model alone per batch size, which gives the **floor**: the yardstick every component is scored against. Measures memory per batch size, recommends a batch size, and checks that every metric and loss accepts a batch. |
| 2. Read the code | Reads every component for waste and bugs. These findings are hypotheses. Only measurements trigger a change. |
| 3. Baseline profile | Runs the components in fresh worker processes, the way Tensorleap does, and records their time, memory and outputs. That run becomes the equivalence baseline. |
| 4. Lossless loops | One change per measurement. A fix is kept only if every output stays bit-identical and the run gets faster (or the per-worker memory gets smaller). Memory goes first by default. |
| 5. Options that change outputs | Offered only as described above, and applied only with your yes. |
| 6. Server | **Smoke check** (default) or **online diagnostics** (only with your yes). See below. |
| 7. Report | Writes the two-part report and names the remaining bottleneck, with evidence. |

### On the server

- **Smoke check (default).** Pushes the optimized integration on a subset of about 50
  samples per state, as version `<integration>-perf-smoke-<yyyymmdd>`, and runs an
  Evaluate. It finishes in minutes. It proves that every component still runs on the
  platform. It is not a speed measurement.
- **Online diagnostics (only after your yes).** Pushes the whole dataset (or your cap) as
  `<integration>-perf-diag-<yyyymmdd>`, runs the full evaluation with visualizations, and
  collects the server's logs while it runs. On a large dataset this can take hours. Once
  the visualization's pace has held steady over enough samples, the assistant asks you to
  **stop the run in the Tensorleap UI** (the CLI can't stop a run), because the rest of
  the visualization adds no new information. A short visualization runs to the end. The
  analysis covers the evaluation (with its start-up) and the visualization. Post-processing
  shows as one row of time and is not analyzed. Diagnostics only read the run: they never
  change server settings.

Any sample cap is applied for the push only and removed right after. It is never committed.

## What you get

All output goes under `tensorleap/runtime-optimization/` in your repo:

- **`report.html`, the one to share**: one self-contained page you can mail or post as is.
  **Part 1 (offline)** is always present: what limits your integration's own code, what was
  changed, and what remains. **Part 2 (online)** appears only after an approved diagnostics
  run: what limits the run on the server, whether it is on the critical path, whether the
  server's CPU, memory, worker and GPU settings fit the run, and what to ask Tensorleap
  for. Each part starts with the answer, and the details are in collapsed appendices.
- `report.md` (the same report in Markdown) and `optimization-log.md` (every candidate,
  the evidence, and the verdict).
- **The fixes.** Every accepted fix is saved as a patch in `fixes/`. In a git repo, each is
  also one commit on the `tensorleap-runtime-optimization` branch. `tl_perf restore` undoes
  anything that wasn't accepted.

## Good to know

- **Lossless means equivalent outputs**, bit-identical by default, on the sampled snapshot,
  checked by the tool and never assumed. Code paths the sampled data never runs aren't
  covered; the report lists them.
- **It never alters your data or model weights**, never applies a fix that changes
  outputs without your yes, and never changes server settings.
- **It doesn't replace integration authoring.** If `check_dataset()` fails, the skill stops
  and points at the error.

## More

- [`how-it-works.html`](how-it-works.html): every phase in detail, the bottleneck catalog,
  and results on test integrations.
- [`../skill.md`](../skill.md): the instructions the assistant follows. This file is
  authoritative.
- [`../reference/`](../reference/): the execution model, bottleneck catalog, levers,
  output-changing options and report template the skill reads.
