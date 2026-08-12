---
name: tensorleap-analysis
description: >
  Use when the user wants to analyze Tensorleap results for a model version —
  "analyze my tensorleap results", "leap analysis", "what did Tensorleap find",
  "insights report" — or wants the platform's auto-generated insights
  (low-performance clusters, out-of-distribution, duplication, data leakage,
  domain gap, mislabeled samples) turned into a readable report. Fetches
  insights and their top sample visualizations straight from the Tensorleap
  server the leap CLI is logged into (local or remote) and writes a
  self-contained HTML report with evidence, embedded sample visualizations,
  and concrete action items for an ML engineer.
group: tensorleap
---

# Analyzing Tensorleap results

You produce a self-contained HTML report (plus a short markdown companion
for tickets) from the insights Tensorleap generated for one evaluated model
version. The platform did the numeric analysis; you add the analyst layer:
explain it, LOOK at the failing samples yourself, report what the platform's
metadata can't show, and turn it all into action items the user can execute.
Do not relay the platform's output uncritically — you have the samples;
form an opinion.

All server access goes through one script (python3, stdlib only):

```
python3 scripts/tl_api.py whoami
python3 scripts/tl_api.py list-versions [--project NAME_OR_ID]
python3 scripts/tl_api.py fetch --project ID --version ID --out DIR
                                        [--top-k 10] [--rank-by COL] [--asc]
python3 scripts/tl_api.py render-charts DIR
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
python3 scripts/tl_api.py fetch --project <projectId> --version <versionId> \
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
python3 scripts/tl_api.py render-charts tensorleap-analysis/<version>
```

Exit 6 means no matplotlib in this environment — fall back to compact
HTML tables built from the payload JSON (do NOT install anything).

## Step 4 — Analyze

Read `insights.json`, then per insight read its `top_panel.json` (ready-made
`summary.title`/`summary.sentence` when present) and skim `samples.csv`
headers for the metric/metadata columns.

Work through **`reference/action-playbook.md`** — it maps every
insight type to the checks to run and the action items they produce,
including the low_performance decision tree (extended-population check,
train-aggressor split, overfitting evidence, labeling/collection paths, and
training adjustments like loss terms and sample boosting). Cite payload
fields, not vibes.

**Count before you characterize.** Compute the finding's full composition
from its `samples.csv` (metadata values, split states) BEFORE naming it. The
worst samples are the tail — never present the tail's traits as the group's
identity, and treat the platform's mutual-information features as
*over-represented*, not *defining* (playbook: "Characterize by composition,
not by the tail").

**Look at the samples yourself — mandatory for every finding you write up.**
Open the downloaded images (Read them) and text payloads for the finding's
top samples. You are looking for what the platform cannot see:

- **Content contradicting a label/metadata value** — but ONLY when the value
  is human-interpretable (a word, not an opaque id) AND the contradiction is
  visible in the sample. Report per sample, generalize only as far as you
  checked; opaque class ids get no judgment (a human couldn't either).
- **Patterns with no metadata**: do the failing samples share something the
  metadata doesn't capture — lighting, pose, occlusion, background, image
  quality, phrasing style? If yes, name it AND suggest adding it as a
  metadata field so the platform can track it.
- Report these in the finding's "What the samples show" block, clearly as
  your own observation (the reader must be able to tell platform evidence
  from analyst judgment).

**Coherence gate**: if, after the evidence and your own look at the samples,
a finding does not hold together as ONE failure mode (a grab-bag cluster,
unrelated worst members, no shared story) — do not force a narrative and do
NOT fold it into another section. Leave it out and give it one honest line
in the appendix. Never combine multiple findings into a merged section;
when two findings share a pattern, give each its own section and
cross-reference.

**Latent space**: every finding states which latent space it was found in,
with a one-line translation of what "similar" means there (guide in the
playbook).

Subinsights: nest them under their parent. Elaborate only the ones that add
information (higher severity, different metadata story, different action);
one-line the rest.

## Step 5 — Write the report

Follow **`reference/report-template.md`** (HTML skeleton, per-finding
anatomy, language rules). Each finding gets: severity chips, the
**failure-mode taxonomy strip** (Data gap / Label quality / Split problem /
Model behavior — highlight the family your playbook walk diagnosed, caption
why), the **split-composition bar** and **metric-contrast rows** (population
value from `population_metrics` in insights.json; omit the row if absent),
6 visible samples + the rest behind `<details>` "Show more", action items,
and the collapsed **"Explore in Tensorleap"** box using `deep_link` (a
version-level link that opens the Insights panel with the version selected —
it applies no filters; tell the reader the finding's # in the list).
Write `<out-dir>/report.html` with plain relative `src` paths, then make it
self-contained:

```
python3 scripts/tl_api.py inline-html <out-dir>/report.html
```

Exit 7 means some `src` paths didn't resolve — fix them (stderr lists which)
and re-run; never ship a report with broken images. The result is ONE file
the user can mail or Slack. Also write `<out-dir>/report.md` — just the
executive summary, summary table, and per-finding action checklists (the
paste-into-a-ticket companion; no images).

Modality handling per sample `payload.json` (`data.type`):

| `data.type` | Embed as |
|---|---|
| `image`, `image_heatmap`, `mask_image` | `<figure>` with the downloaded `.jpg`/`.png` from `assets/` |
| `bbox_image` | `boxes.jpg` rendered by render-charts next to the payload (GT and prediction decoders are separate visualizers — caption which one you show); the raw asset has no boxes |
| `text`, `mask_text` | `<blockquote>` of the joined `data.body` tokens |
| `graph`, `hbar` | `chart.png` next to the payload (or an HTML table fallback) |
| `video`, `audio` | note it exists; don't inline media files |

Careful: `insights.json` lists files as of FETCH time — `chart.png` /
`boxes.jpg` appear on disk only after render-charts, so resolve them from the
payload's directory, not from the digest's file list.

Order insights by `severity` descending. **Lead every finding with its
failure mode** — what fails and why, named in plain ML terms — and write for
an ML engineer with zero knowledge of Tensorleap internals: no blob paths,
filter JSON, or raw payload field names in the prose (the template's language
rules are binding). Keep the executive summary honest — if the insights are
low-severity or repetitive, say so. Close with the appendix of missing
visualizations and fetch errors from the digest.

**Before finishing, read the rendered report as its reader would** — every
sentence, chips, captions, link texts, the appendix. Assembled text is where
clumsiness hides ("this finding is finding #2", repeated phrases, stale
numbers). Fix anything you would not have written in a single pass. Ship
only what reads clean end-to-end.

Finish by telling the user the report path and the one action you'd do first.
