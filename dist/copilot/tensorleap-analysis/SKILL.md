---
name: tensorleap-analysis
description: Use when the user wants to analyze Tensorleap results for a model version — "analyze my tensorleap results", "leap analysis", "what did Tensorleap find", "insights report" — or wants the platform's auto-generated insights (low-performance clusters, out-of-distribution, duplication, data leakage, domain gap, mislabeled samples) turned into a readable report. Fetches insights and their top sample visualizations straight from the Tensorleap server the leap CLI is logged into (local or remote) and writes a self-contained HTML report with evidence, embedded sample visualizations, and concrete action items for an ML engineer.
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
                                        [--fast-local] [--refresh]
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
  + assets), `errors`, and top-level `counts`, plus `prediction_labels`
  (per prediction type, the class-name list the integration declared —
  class index i is `labels[i]`; empty if the integration declared none),
  `visualizers` (every visualizer the integration declared: name, data
  type, argument names), and `integration` (where the pushed code was
  extracted; null if the download failed). Each insight also carries
  `population` (`affected` = every row of its csv, `core` = the failing core
  for a failure mode, null otherwise), `asset_resolution` (the pixel size of
  the largest downloaded sample image — the report's sample grid is sized
  from it) and, when its samples appear in another insight too, `overlaps`
  (`{insight, shared, of_this}` per other insight).
- `integration/` — the integration code exactly as it was pushed for this
  version (`integration.entry_file` names the entry file). If it's missing,
  fall back to the code in the cwd when a `leap.yaml` is present — and say
  in the report's Notes that you read the local checkout, which may have
  drifted since the push.
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

**Repeat runs are cheap.** Blobs are cached (`~/.cache/tensorleap-analysis`)
and sample directories already present in `--out` are reused, so re-running
after a crash or a report edit costs seconds; `--refresh` forces a full
re-download. On a **localhost** server add `--fast-local`: storage URLs are
signed locally instead of one API call per file, and one object listing
replaces one listing call per sample (measured 3m47s → 10s on a 6-insight
version). It self-calibrates against a single API-issued URL, verifies the
bytes it produces, and silently falls back to the API path on any doubt —
so it is safe to pass always, and it does nothing on remote servers.

## Step 4 — Establish the domain

The report reads as if a domain expert wrote it, for domain experts.
Determine the domain and task from what you already have — the project name,
`prediction_labels`, the metadata fields, and the samples themselves (drone
footage, clinical notes, movie reviews announce themselves). Do NOT ask the
user to confirm a domain you inferred. Ask only when the data genuinely
leaves you unable to tell what the task is — and then ask once,
specifically.

When the domain is specialized and a concrete gap would change the analysis,
run 2–4 targeted web searches: how practitioners interpret the pattern you
are seeing, what remediation is standard in the field, and the field's
terminology so issues are named in the reader's language
("underexposed AP views", not "dark images"). No internet access → proceed
on your own knowledge, silently.

Carry the lens through everything that follows — sample viewing, naming,
action items, metadata proposals (playbook: "The domain lens").

## Step 5 — Analyze

Read `insights.json`, then per insight read its `top_panel.json` (ready-made
`summary.title`/`summary.sentence` when present) and skim `samples.csv`
headers for the metric/metadata columns.

Work through **`reference/action-playbook.md`** — it maps every
insight type to the checks to run and the action items they produce,
including the low_performance decision tree (core vs affected samples, the
wider-metadata-population check, train-aggressor split, overfitting
evidence, labeling/collection paths, and training adjustments like loss
terms and sample boosting). Cite payload
fields, not vibes.

**Count before you characterize.** Compute the insight's full composition
from its `samples.csv` (metadata values, split states) BEFORE naming it. For
a failure mode, count the **failing core** — the `samples.csv` rows with
`is_low_perf_root_member == True` — not every row: the rest are latent
neighbours the platform swept in as *affected*, and they are often healthy.
Characterize, contrast and act on the core; report both counts
(playbook: "Core vs affected samples"). The
worst samples are the tail — never present the tail's traits as the group's
identity, and treat the platform's mutual-information features as
*over-represented*, not *defining* (playbook: "Characterize by composition,
not by the tail").

**Know the visualizers before opening any sample.** Read the integration
code (`integration/`, entry file first, then the modules it imports) and
write yourself a one-liner per entry in the digest's `visualizers` list:
what it renders, from which tensor (input / ground truth / prediction), at
what granularity (a single instance vs the whole sample), and what the
visual encoding means. Report captions speak from these one-liners — never
from function names.

**Look at the samples yourself — mandatory for every insight you write up.**
Open the downloaded images (Read them) and text payloads for the insight's
top samples. You are looking for what the platform cannot see:

- **Content contradicting a label/metadata value** — but ONLY when the value
  is human-interpretable (a word, not an opaque id) AND the contradiction is
  visible in the sample. Report per sample, generalize only as far as you
  checked; opaque class ids get no judgment (a human couldn't either).
- **Patterns with no metadata**: do the failing samples share something the
  metadata doesn't capture — lighting, pose, occlusion, background, image
  quality, phrasing style? If yes, name it AND suggest adding it as a
  metadata field so the platform can track it.
- Report these in the insight's "What the samples show" block, clearly as
  your own observation (the reader must be able to tell platform evidence
  from analyst judgment).

**Pick the evidence per insight** (playbook: "Pick the evidence"). State
what the insight asserts is wrong, audition every primary-evidence
candidate visualizer on 2–3 top samples, then commit to the 1–2 that let a
skeptical reader verify the claim — and keep that choice for every sample
on the card.

**Coherence gate — with a subinsight rescue.** Sometimes an insight does
not hold together as ONE story about one failing population (a grab-bag
cluster, unrelated worst members, no shared story — this happens: a latent
space, an algorithm edge case, anything). Then:

1. **Check its subinsights first** (same discipline: composition, metric
   signature, your own look at the samples). A subinsight that isolates one
   clear story gets its own card, standing in for the parent — its chips
   carry both identities ("insight #3 · sub-insight #9") and its explore
   line points at the parent in the panel. Several coherent subs → several
   cards.
2. **No coherent story → ignore the insight** — the report equivalent of
   archiving it in the panel. Nothing in the body; one terse line in Notes
   naming it a candidate for archiving, with the half-sentence of evidence
   ("its subinsights repeat the stories above").

Never force a narrative and never fold an incoherent insight into another
card. When two insights share a pattern, each gets its own card and a
cross-reference by name.

**Latent space**: every insight states which latent space it was found in,
with a one-line translation of what "similar" means there (guide in the
playbook).

**Subinsights are analysis inputs, never card content.** Analyze a parent's
subinsights with the full discipline (composition, metrics, your own look at
samples), then make a PROMOTION decision — one message at one granularity
per card:

- Subs merely refine the parent (same story, tighter slices, no distinct
  action) → the parent gets the card; subinsights appear nowhere in it.
- A sub carries a distinct story or action → promote it to its OWN card
  (chips: "insight #3 · sub-insight #9"). The parent keeps a card only if
  its story minus that sub still stands with its own action; otherwise the
  sub's card replaces it, and the parent appears only as one orienting
  sentence of context in the sub's prose.
- Never present a parent's message and a sub's sharper message in the same
  card ("animals on roads" + "cats on roads at night") — the reader can't
  tell which to act on.

## Step 6 — Write the report

Follow **`reference/report-template.md`** (HTML skeleton, per-insight
anatomy, language rules). **The body is grouped by insight TYPE in the
Insights panel's order** (Failure Mode → Out of Distribution → Duplication →
Data Leakage → Domain Gap → Mislabeled; the template has the display names
and one-line meanings) — one `<h2>` group per type present, sections as
`<h3>` inside, severity-ordered within the group, and the summary table
mirrors the same order with a subheader row per type. Each insight gets:
severity chips, a bold
one-sentence **bottom line**, the **root-cause label** (Data gap / Label
quality / Split problem / Model behavior — the family your playbook walk
diagnosed, with a one-line caption; fixed vocabulary so it reads as a
consistent grammar), the **split-composition bar** and **metric-contrast
rows** (population
value from `population_metrics` in insights.json; omit the row if absent),
6 visible samples + the rest behind `<details>` "Show more", action items,
and the collapsed **"Explore in Tensorleap"** box using `deep_link` (a
version-level link that opens the Insights panel with the version selected —
it applies no filters; tell the reader the insight's # in the list).
Write `<out-dir>/report.html` with plain relative `src` paths, then make it
self-contained:

```
python3 scripts/tl_api.py inline-html <out-dir>/report.html
```

Exit 7 means some `src` paths didn't resolve — fix them (stderr lists which)
and re-run; never ship a report with broken images. The result is ONE file
the user can mail or Slack. Also write `<out-dir>/report.md` — just the
executive summary, summary table, and per-insight action checklists (the
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

Order insights by `severity` descending. **Lead every insight with what
is going wrong** — what fails and why, named in plain ML terms — and write for
an ML engineer with zero knowledge of Tensorleap internals: no blob paths,
filter JSON, or raw payload field names in the prose (the template's language
rules are binding). Keep the executive summary honest — if the insights are
low-severity or repetitive, say so. Close with the terse Notes section
(template: one line per insight without a card, ending in what the reader
can do; unrendered visualizations as on-demand behavior; fetch errors).

**Before finishing, read the rendered report as its reader would** — every
sentence, chips, captions, link texts, the Notes. Assembled text is where
clumsiness hides ("this insight is insight #2", repeated phrases, stale
numbers). Fix anything you would not have written in a single pass. Ship
only what reads clean end-to-end.

**Your closing message names the deliverables and stops.** Two sentences at
most: the report is ready at `<path>/report.html`, with the ticket companion
at `<path>/report.md`. No findings, no summaries, no severity counts, no
recommended first action, no observations — everything you have to say lives
IN the report; the session message just hands it over. Anything discovered
along the way that isn't part of the analysis (e.g. a suspected data or
server irregularity) goes in the report's Notes, not the closing message.
Then answer follow-up questions from the analysis you already did — the
conversational depth is for when the user asks, never volunteered up front.
