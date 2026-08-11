---
name: tensorleap-analysis
description: >
  Use when the user wants to analyze Tensorleap results for a model version —
  "analyze my tensorleap results", "leap analysis", "what did Tensorleap find",
  "insights report" — or wants the platform's auto-generated insights
  (low-performance clusters, out-of-distribution, duplication, data leakage,
  domain gap, mislabeled samples) turned into a readable report. Fetches
  insights and their top sample visualizations straight from the Tensorleap
  server the leap CLI is logged into (local or remote) and writes a markdown
  report with evidence, embedded sample visualizations, and concrete action
  items for an ML engineer.
group: tensorleap
version: 0.1.0
globs: ["leap.yaml"]
alwaysApply: false
tools: [claude, cursor, copilot, agents]
scripts_dir: .tensorleap/scripts
reference_dir: .tensorleap/reference
---

# Analyzing Tensorleap results

You produce a markdown report from the insights Tensorleap generated for one
evaluated model version. The platform already did the numeric analysis — your
job is to fetch it, explain it, show the evidence, and turn it into action
items the user can execute. Phase-1 scope: the platform's insights verbatim,
no extra metric crunching.

All server access goes through one script (python3, stdlib only):

```
python3 {{scripts_dir}}/tl_api.py whoami
python3 {{scripts_dir}}/tl_api.py list-versions [--project NAME_OR_ID]
python3 {{scripts_dir}}/tl_api.py fetch --project ID --version ID --out DIR
                                        [--top-k 10] [--rank-by COL] [--asc]
python3 {{scripts_dir}}/tl_api.py render-charts DIR
```

Exit codes: `0` ok · `2` bad args / ambiguous project · `3` not authenticated
· `4` server unreachable/error · `5` version has no insights · `6` matplotlib
missing (render-charts only). Auth and server URL come from the leap CLI's
own login (`~/.config/tensorleap/config.yaml`) — the script talks to whichever
server `leap auth select` points at, exactly like the UI does.

## Step 1 — Preflight

Run `whoami`. On exit 3, stop and tell the user to run `leap auth login`
(or `leap auth select <env>`); on exit 4 the server is unreachable — show the
api_url from the error and ask the user to check connectivity/port-forward.
Do not improvise other auth mechanisms.

## Step 2 — Pick the version

- If the cwd has a `leap.yaml` with a project name/id, try
  `list-versions --project <that>`. Otherwise run `list-versions` bare, show
  the projects, and ask the user which one.
- Show the evaluated versions (name, serial, date, `hasInsightsArtifacts`)
  and ask the user which version to analyze. Prefer versions where
  `hasInsightsArtifacts` is true; if the chosen one is false, warn that fetch
  may find nothing.

## Step 3 — Fetch

```
python3 {{scripts_dir}}/tl_api.py fetch --project <projectId> --version <versionId> \
    --out tensorleap-analysis/<version-name-or-serial>
```

On exit 5 there are no insights for that version: tell the user to generate
insights in the UI (Population Exploration → Insights) or pick another
version, and stop. On success the out dir contains:

- `insights.json` — the digest you work from: parent insights with nested
  `subinsights`, each with `insightType` (full engine payload), `files`
  (local csv / top_panel), `samples` (per sample id: downloaded `payload.json`
  + assets), `errors`, and top-level `counts`.
- `insight_<i>_<type>/` per insight — `samples.csv`, optional
  `top_panel.json`, and `samples/<sample_id>/<dataType>/<visualizer>/…` with
  `payload.json` and any image assets.

Samples are ranked worst-first automatically by the first `metrics.*` CSV
column containing `loss`/`entropy`; among equally-ranked candidates, samples
that have rendered visualizations are preferred. If the project's real
quality metric is a different column (see `csv_columns` in the digest),
re-run fetch with `--rank-by <column>` (add `--asc` for higher-is-better
metrics).

Then render charts for non-image modalities:

```
python3 {{scripts_dir}}/tl_api.py render-charts tensorleap-analysis/<version>
```

Exit 6 means no matplotlib in this environment — fall back to compact
markdown tables built from the payload JSON (do NOT install anything).

## Step 4 — Analyze

Read `insights.json`, then per insight read its `top_panel.json` (ready-made
`summary.title`/`summary.sentence` when present) and skim `samples.csv`
headers for the metric/metadata columns.

Work through **`{{reference_dir}}/action-playbook.md`** — it maps every
insight type to the checks to run and the action items they produce,
including the low_performance decision tree (extended-population check,
train-aggressor split, overfitting evidence, labeling/collection paths, and
training adjustments like loss terms and sample boosting). Cite payload
fields, not vibes.

Subinsights: nest them under their parent. Elaborate only the ones that add
information (higher severity, different metadata story, different action);
one-line the rest.

## Step 5 — Write the report

Follow **`{{reference_dir}}/report-template.md`**. Write to
`<out-dir>/report.md` so image links are relative. Modality handling per
sample `payload.json` (`data.type`):

| `data.type` | Embed as |
|---|---|
| `image`, `image_heatmap`, `bbox_image`, `mask_image` | the downloaded `.jpg`/`.png` from `assets/` |
| `text`, `mask_text` | blockquote of the joined `data.body` tokens |
| `graph`, `hbar` | `chart.png` next to the payload (or md table fallback) |
| `video`, `audio` | link the downloaded file, note it can't be inlined |

Order insights by `severity` descending. **Lead every finding with its
failure mode** — what fails and why, named in plain ML terms — and write for
an ML engineer with zero knowledge of Tensorleap internals: no blob paths,
filter JSON, or raw payload field names in the prose (the template's language
rules are binding). Keep the executive summary honest — if the insights are
low-severity or repetitive, say so. Close with the appendix of missing
visualizations and fetch errors from the digest.

Finish by telling the user the report path and the one action you'd do first.
