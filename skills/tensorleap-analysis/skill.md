---
name: tensorleap-analysis
description: >
  Use when the user wants to analyze Tensorleap results for a model version,
  "analyze my tensorleap results", "leap analysis", "what did Tensorleap find",
  "insights report", or wants the platform's auto-generated insights
  (low-performance clusters, out-of-distribution, duplication, data leakage,
  domain gap, mislabeled samples) turned into a readable report. Fetches
  insights and their top sample visualizations straight from the Tensorleap
  server the leap CLI is logged into (local or remote) and writes a
  self-contained HTML report with evidence, embedded sample visualizations,
  and concrete action items for an ML engineer.
group: tensorleap
version: 0.1.0
globs: ["leap.yaml"]
alwaysApply: false
tools: [claude, cursor, copilot, agents]
scripts_dir: .tensorleap/scripts
reference_dir: .tensorleap/reference
---

# Analyzing Tensorleap results

You produce a self-contained HTML report (plus a short markdown companion
for tickets) from the insights Tensorleap generated for one evaluated model
version. The platform did the numeric analysis; you add the analyst layer:
explain it, LOOK at the failing samples yourself, report what the platform's
metadata can't show, and turn it all into action items the user can execute.
Do not relay the platform's output uncritically. You have the samples;
form an opinion.

All server access goes through one script (python3, stdlib only):

```
python3 {{scripts_dir}}/tl_api.py whoami
python3 {{scripts_dir}}/tl_api.py list-versions [--project NAME_OR_ID]
python3 {{scripts_dir}}/tl_api.py fetch --project ID --version ID --out DIR
                                        [--top-k 10] [--rank-by COL] [--asc]
                                        [--refresh]
python3 {{scripts_dir}}/tl_api.py render-charts DIR
python3 {{scripts_dir}}/tl_api.py summarize DIR
python3 {{scripts_dir}}/tl_api.py build-report DIR
```

> `{{scripts_dir}}/tl_api.py` lives in this skill's own directory, NOT in
> the user's project, you work in the project cwd (Step 2 reads its
> `leap.yaml`), so resolve the script's path relative to this skill file
> (an absolute path is safest) and never copy it into the project.

Exit codes: `0` ok · `2` bad args / ambiguous project · `3` not authenticated
· `4` server unreachable/error · `5` version has no insights · `6` matplotlib
missing (render-charts only) · `8` build-report input invalid. Auth and server URL come from the leap CLI's
own login (`~/.config/tensorleap/config.yaml`), the script talks to whichever
server `leap auth select` points at, exactly like the UI does.

## Step 1: Preflight

Run `bash {{scripts_dir}}/self_update.sh` first. Exit 0: nothing to do.
Exit 10: the skill's files were just refreshed, re-read this file and start
over. A `NOTE:` line means a project-local install has an update available:
ask the user before running the printed command. The script only acts on
Copilot/Cursor installs; Claude updates through the plugin marketplace.

Then run `whoami`. On exit 3, stop and tell the user to run `leap auth login`
(or `leap auth select <env>`); on exit 4 the server is unreachable, show the
api_url from the error and ask the user to check connectivity/port-forward.
Do not improvise other auth mechanisms.

## Step 2: Pick the version

- If the cwd has a `leap.yaml` with a project name/id, try
  `list-versions --project <that>`. Otherwise run `list-versions` bare, show
  the projects, and ask the user which one.
- Show the evaluated versions (name, serial, date, `hasInsightsArtifacts`)
  and ask the user which version to analyze. Prefer versions where
  `hasInsightsArtifacts` is true; if the chosen one is false, warn that fetch
  may find nothing.

## Step 3: Fetch

```
python3 {{scripts_dir}}/tl_api.py fetch --project <projectId> --version <versionId> \
    --out tensorleap-analysis/<version-name-or-serial>
```

On exit 4 after the API answered, every artifact download failed: the signed
URLs point at the server's storage host, which a port-forward to node-server
alone does not expose. Tell the user to point `leap auth` at the server's
public URL (the one the UI uses) and re-run with `--refresh`. A stderr
`warning:` with a partial failure count is not fatal, mention the gap in the
companion's Notes.
On exit 5 there are no insights for that version: tell the user to generate
insights in the UI (Population Exploration → Insights) or pick another
version, and stop. On success the out dir contains:

- `insights.json`, the digest you work from: parent insights with nested
  `subinsights`, each with `insightType` (full engine payload), `files`
  (local csv / top_panel / fixing_csv, the last only when the insight
  carries `aggressor_fixing`), `deep_link` (the insight opened and analyzed
  in the platform), `add_test_link` (present when the insight has engine-made
  `automatic_tests`; opens the platform, which offers to create the insight's
  regression test on the user's confirmation), `samples` (per sample id: downloaded `payload.json`
  + assets), `errors`, and top-level `counts`, plus `prediction_labels`
  (per prediction type, the class-name list the integration declared,
  class index i is `labels[i]`; empty if the integration declared none),
  `visualizers` (every visualizer the integration declared: name, data
  type, argument names), and `integration` (where the pushed code was
  extracted; null if the download failed). Each insight also carries
  `population` (`samples` = the group the report describes, for a failure
  mode the rows that actually underperform, otherwise every csv row; and
  `csv_rows`, the raw row count, internal only), `asset_resolution` (the
  pixel size of the largest downloaded sample image, the report's sample
  grid is sized from it) and, when its samples appear in another insight too, `overlaps`
  (`{insight, shared, of_this}` per other insight).
- `integration/`, the integration code exactly as it was pushed for this
  version (`integration.entry_file` names the entry file). If it's missing,
  fall back to the code in the cwd when a `leap.yaml` is present, and say
  in the companion's Notes that you read the local checkout, which may have
  drifted since the push.
- `insight_<i>_<type>/` per insight, `samples.csv`, optional
  `top_panel.json` and `fixing_samples.csv`, and `samples/<sample_id>/<dataType>/<visualizer>/…` with
  `payload.json` and any image assets.

For a failure mode, only the rows that actually underperform
(`is_low_perf_root_member`) are eligible, the latent neighbours the cluster
also holds are often healthy, and a card must not illustrate a failure with a
passing sample. Other insight types have no such split, so every row is
eligible. The eligible rows are then ranked by `aggressor_affinity_score` when
the CSV has it (highest first, the samples most representative of the
insight's population, and the order the user sees in the Insights panel), else
worst-first by the first `metrics.*` column containing `loss`/`entropy` (the
tail, not the group's identity, weigh observations accordingly); among
equally-ranked candidates, samples that have rendered visualizations are
preferred. If the project's real
quality metric is a different column (see `csv_columns` in the digest),
re-run fetch with `--rank-by <column>` (add `--asc` for higher-is-better
metrics).

Then render charts for non-image modalities:

```
python3 {{scripts_dir}}/tl_api.py render-charts tensorleap-analysis/<version>
```

Exit 6 means no matplotlib in this environment, fall back to compact
HTML tables built from the payload JSON (do NOT install anything).
render-charts also writes a `<name>.thumb.jpg` (max 640 px) beside every
larger image, the copies you view in Step 5. They never go in the report;
build-report rejects them.

**Repeat runs are cheap.** Blobs are cached (`~/.cache/tensorleap-analysis`)
and sample directories already present in `--out` are reused, so re-running
after a crash or a report edit costs seconds; `--refresh` forces a full
re-download. All artifact URLs arrive pre-signed in batches from the
server's analysis-export API, so there are no per-file signing round-trips.
The server must expose that API (a recent node-server); on older servers
`fetch` exits 4 with a clear message, and a contract-version mismatch exits
9, update whichever side is older.

## Step 4: Establish the domain

The report reads as if a domain expert wrote it, for domain experts.
Determine the domain and task from what you already have, the project name,
`prediction_labels`, the metadata fields, and the samples themselves (drone
footage, clinical notes, movie reviews announce themselves). **The
integration code in `integration/` is the most explicit source**: dataset
paths and file names, preprocessing steps, the class list, how metadata is
derived, what the loss and metrics measure, and comments and docstrings the
author wrote for themselves. You will read it in Step 5 for the visualizers
anyway, read it here first, for the subject matter. Do NOT ask the
user to confirm a domain you inferred. Ask only when the data genuinely
leaves you unable to tell what the task is, and then ask once,
specifically.

When the domain is specialized and a concrete gap would change the analysis,
run 2–4 targeted web searches: how practitioners interpret the pattern you
are seeing, what remediation is standard in the field, and the field's
terminology so issues are named in the reader's language
("underexposed AP views", not "dark images"). No internet access → proceed
on your own knowledge, silently.

Carry the lens through everything that follows, sample viewing, naming,
action items, metadata proposals (playbook: "The domain lens").

## Step 5: Analyze

Read `insights.json`, then per insight read its `top_panel.json` (ready-made
`summary.title`/`summary.sentence` when present) and skim `samples.csv`
headers for the metric/metadata columns.

Work through **`{{reference_dir}}/action-playbook.md`**, it maps every
insight type to the checks to run and the action items they produce,
including the low_performance decision tree (which csv rows are the failing
group, the wider-metadata-population check, train-aggressor split, overfitting
evidence, labeling/collection paths, and training adjustments like loss
terms and sample boosting). Cite payload
fields, not vibes.

**Count before you characterize.** Run
`summarize <out-dir>` once and work from its output, per insight it computes
the composition you would otherwise count by hand: the failing group's size
(`group_rows`, restricted to `is_low_perf_root_member == True` rows when the
column exists, the rest are latent neighbours the platform swept in, and
they are often healthy), split states, per-metadata-column value shares with
the all-data share beside them (over-representation is the ratio between the
two), and group-vs-all-data metric means. Characterize, contrast and act on
that group, and quote `group_rows` as its size, never `n_samples` (playbook:
"The cluster's csv is wider than the failing group"). Open `samples.csv`
itself only for a question the summary can't answer (e.g. a per-sample
cross-column join). The
worst samples are the tail, never present the tail's traits as the group's
identity, and treat the platform's mutual-information features as
*over-represented*, not *defining* (playbook: "Characterize by composition,
not by the tail").

**Know the visualizers before opening any sample.** Read the integration
code (`integration/`, entry file first, then the modules it imports) and
write yourself a one-liner per entry in the digest's `visualizers` list:
what it renders, from which tensor (input / ground truth / prediction), at
what granularity (a single instance vs the whole sample), and what the
visual encoding means. Report captions speak from these one-liners, never
from function names.

**Look at the samples yourself, mandatory for every insight you write up.**
Open the downloaded images (Read them) and text payloads. View the
`.thumb.jpg` copy when one exists; open the full-resolution original only
when the judgment hangs on fine detail, small objects, text inside the
image, subtle artifacts, any mislabeled-sample check, or whenever the thumb
leaves you unsure. The fetched samples are the failing rows in affinity
order, the order the user scrolls in the panel, so what you look at is what
they will see. Viewing happens at two levels:

- **Breadth, delegated.** Spawn one viewing agent per insight (they run in
  parallel) over that insight's full set of fetched samples, more images
  than a single context should hold, and their bulk does not belong in yours.
  Give it the insight's claim, the visualizer one-liners, and the domain
  lens; ask back for what recurs across the set versus what is occasional,
  **roughly how many distinct scenes / settings / subjects the set
  represents** (a plain visual judgment, no ids, no metadata), the best
  candidates for the card with captions, and any content that contradicts a
  human-readable label. What comes back is an input to your analysis, never
  text you paste into the report.
- **Proof, local.** **Coverage rule: before the card is final, you have
  viewed every sample that ships on it, visible and folded.** A reader can
  open the fold; a card whose own samples contradict its prose is the worst
  report you can produce, and an observation nobody in this context ever
  verified is exactly how that happens.

You are looking for what the platform cannot see:

- **Content contradicting a label/metadata value**, but ONLY when the value
  is human-interpretable (a word, not an opaque id) AND the contradiction is
  visible in the sample. Name the samples it happens in; opaque class ids
  get no judgment (a human couldn't either).
- **Patterns with no metadata**: do the failing samples share something the
  metadata doesn't capture, lighting, pose, occlusion, background, image
  quality, phrasing style? If yes, name it AND suggest adding it as a
  metadata field so the platform can track it.
- Report these in the insight's "What the samples show" block, clearly as
  your own observation (the reader must be able to tell platform evidence
  from analyst judgment).
- **Never scope an observation to the rendered images.** The card shows a
  handful of a group that runs to hundreds; "5 of the 6 images are night
  scenes" tells the reader about the report's layout, not about the data.
  Write the finding as what it is about the group ("the failures are
  overwhelmingly night scenes") and, when you want a number behind it, take
  it from the `summarize` output, which covers every member. A visual pattern the csv
  cannot count is still worth naming, unquantified, and it is exactly the
  case where you also propose the metadata field that would count it.
  **Calibrate every claim to its SOURCE, never to a headcount of images.**
  Affinity order is not a random draw, the top of a cluster's ranking is its
  tightest knot, and a single scene can fill it, so "most of the images I
  saw were X" establishes nothing about the group on its own. Which means:
  - **A column measures the trait** (brightness, object count, box area, any
    category): state it flat as a group property and carry the whole-group
    number from `summarize`, which covers every member, "the failures are
    night scenes; mean frame brightness 0.27 against 0.40 across all data".
    A dozen images are plenty to NAME a pattern; the column is what measures
    it, so a strong majority in the data earns a plain verdict even when the
    images you saw are not unanimous.
  - **Nothing measures it** (backlight, occlusion, pose, phrasing style):
    the honest ceiling is the samples reviewed, "steep high-altitude
    viewpoints recur across the samples reviewed", always paired with the
    metadata field that would turn it into a real share next run. When the
    viewing agent reports few distinct scenes, hedge harder still: a trait of
    two scenes is a trait of two scenes, whatever share of the images it fills.

  Say nothing about the samples that lack the trait, the group's identity is
  its measured composition, and "the rest are mixed" is a verdict on the
  insight, not a finding.

**Pick the evidence per insight** (playbook: "Pick the evidence"). State
what the insight asserts is wrong, audition every primary-evidence
candidate visualizer on 2–3 top samples, then commit to the 1–2 that let a
skeptical reader verify the claim, and keep that choice for every sample
on the card.

**Coherence gate, with a subinsight rescue.** Sometimes an insight does
not hold together as ONE story about one failing population (a grab-bag
cluster, unrelated worst members, no shared story, this happens: a latent
space, an algorithm edge case, anything). Then:

1. **Check its subinsights first** (same discipline: composition, metric
   signature, your own look at the samples). A subinsight that isolates one
   clear story gets its own card, standing in for the parent, its chips
   carry both identities ("insight #3 · sub-insight #9") and its explore
   line points at the parent in the panel. Several coherent subs → several
   cards.
2. **No coherent story → ignore the insight**, the report equivalent of
   archiving it in the panel. Nothing in the report; one terse line in the
   companion's Notes naming it a candidate for archiving, with the
   half-sentence of evidence
   ("its subinsights repeat the stories above").

Never force a narrative and never fold an incoherent insight into another
card. When two insights share a pattern, each gets its own card and a
cross-reference by name.

**Latent space**: every insight states which latent space it was found in,
with a one-line translation of what "similar" means there (guide in the
playbook).

**Subinsights are analysis inputs, never card content.** Analyze a parent's
subinsights with the full discipline (composition, metrics, your own look at
samples), then make a PROMOTION decision, one message at one granularity
per card:

- Subs merely refine the parent (same story, tighter slices, no distinct
  action) → the parent gets the card; subinsights appear nowhere in it.
- A sub carries a distinct story or action → promote it to its OWN card
  (chips: "insight #3 · sub-insight #9"). The parent keeps a card only if
  its story minus that sub still stands with its own action; otherwise the
  sub's card replaces it, and the parent appears only as one orienting
  sentence of context in the sub's prose.
- Never present a parent's message and a sub's sharper message in the same
  card ("animals on roads" + "cats on roads at night"), the reader can't
  tell which to act on.

## Step 6: Write the report

Follow **`{{reference_dir}}/report-template.md`** (report.json schema,
per-insight anatomy, language rules). You never write HTML: author
`<out-dir>/report.json`, content only, image paths relative to `<out-dir>`,
and the script renders the page. **The body is grouped by insight TYPE in the
Insights panel's order** (Failure Mode → Out of Distribution → Duplication →
Data Leakage → Domain Gap → Mislabeled; the template has the display names
and one-line meanings), one group per type present, ordered by severity
then affected-sample count within the group (the rendered table says so),
and the overview table mirrors the same order with a subheader
row per type. Each insight gets: chips, a bold
one-sentence **bottom line**, the **root-cause label** (Data gap / Label
quality / Split problem / Model behavior, the family your playbook walk
diagnosed, with a one-line caption; fixed vocabulary so it reads as a
consistent grammar), the **split-composition bar** and **metric-contrast
rows** (population
value from `population_metrics` in insights.json; omit if absent),
the samples (behind a collapsed "Samples (N)" fold that shows all of them
when opened), action items,
the **download button** (`download_csv`) when the digest has
`files.fixing_csv`, the platform's selected samples to label/acquire,
the **"Test" header button** (`add_test`) when the digest has `add_test_link`,
and the **"Analyze insight"** header button using the insight's
`deep_link` (lands on the insight already analyzed, opened as the
dashboard's top panel with its cluster filter applied, what the panel's
"Analyze" button does; insights without a cluster blob fall back to the
version-level Insights-panel link).
Then render and make it self-contained:

```
python3 {{scripts_dir}}/tl_api.py build-report <out-dir>
python3 {{scripts_dir}}/tl_api.py inline-html <out-dir>/report.html
```

build-report exit 8 means report.json is invalid or an image path didn't
resolve; inline-html exit 7 means a `src` didn't resolve, fix (stderr lists
which) and re-run; never ship a report with broken images. The result is ONE file
the user can mail or Slack. Also write `<out-dir>/report.md`, the
executive summary, summary table, per-insight action checklists, and the
**Notes** section (template: one line per insight without a card, ending in
what the reader can do; unrendered visualizations as on-demand behavior;
fetch errors). It is the paste-into-a-ticket companion; no images.

Modality handling per sample `payload.json` (`data.type`):

| `data.type` | Sample entry in report.json |
|---|---|
| `image`, `image_heatmap`, `mask_image` | `images`: the downloaded `.jpg`/`.png` from `assets/` |
| `bbox_image` | `images`: `boxes.jpg` rendered by render-charts next to the payload (GT and prediction decoders are separate visualizers, caption which one you show); the raw asset has no boxes |
| `text`, `mask_text` | `text`: the joined `data.body` tokens |
| `graph`, `hbar` | `images`: `chart.png` next to the payload (no matplotlib → a small HTML table in the card's prose instead) |
| `video`, `audio` | note it exists; don't inline media files |

Careful: `insights.json` lists files as of FETCH time, `chart.png` /
`boxes.jpg` appear on disk only after render-charts, so resolve them from the
payload's directory, not from the digest's file list.

**At most 6 insights get a card.** When the run has more, keep the 6 with
the strongest case, severity first, then how much of the data the finding
touches and whether it carries a distinct action, and give every other
insight its one-line Notes entry. Fewer than 6 worth carding → write fewer;
never pad.

Order insights by `severity` descending. **Lead every insight with what
is going wrong**, what fails and why, named in plain ML terms, and write for
an ML engineer with zero knowledge of Tensorleap internals: no blob paths,
filter JSON, or raw payload field names in the prose (the template's language
rules are binding). Keep the executive summary honest, if the insights are
low-severity or repetitive, say so. The HTML ends with the last insight
card, **no Notes section in the HTML.**

**Before finishing, read the rendered report as its reader would**, via
`<out-dir>/report.txt` (build-report's text linearization of the full page:
every sentence, chips, captions, link texts), plus the companion's Notes.
Never re-read report.html for this, report.txt is the same content without
the markup. Assembled text is where
clumsiness hides ("this insight is insight #2", repeated phrases, stale
numbers). Fix anything you would not have written in a single pass, in
report.json, then re-run build-report and inline-html. Ship
only what reads clean end-to-end.

**Your closing message names the deliverables and stops.** Two sentences at
most: the report is ready at `<path>/report.html`, with the ticket companion
at `<path>/report.md`. No findings, no summaries, no severity counts, no
recommended first action, no observations, everything you have to say lives
IN the report; the session message just hands it over. Anything discovered
along the way that isn't part of the analysis (e.g. a suspected data or
server irregularity) goes in the companion's Notes, not the closing
message.
Then answer follow-up questions from the analysis you already did, the
conversational depth is for when the user asks, never volunteered up front.
