# Report template

The deliverable is **the report in the project's Reports tab**. The platform
renders it; you write only what it says: **`<out-dir>/report.json`** (schema
below: headings, prose, numbers, and which samples to show). Then
`tl_api.py check-report <out-dir>` checks the file and writes `report.txt`,
a plain-text linearization for your final read-through, and
`tl_publish_report` uploads it. Layout, colors, bars, folds, the findings
table and each finding's status and buttons are the platform's. So are the
samples: you name the sample and the view, never a file, and the platform
shows it with its own control for that kind of view (a picture, a clip in a
player, a text, a chart). From there a reader can save the report as one
self-contained HTML file to mail or Slack. Alongside it you write a short
**`report.md`**, the part people paste into tickets and Slack (last section).

Exactly three fields take inline markup: `summary`, the `analysis`
paragraphs, and `observations`. There `<a href="#insight-N">` (a link to a
finding in this report), `<em>`, `<strong>` and `<code>` are shown, and
`&`/`<` used as plain characters must be written as entities.
**Never use an em-dash anywhere in the report** (check-report rewrites any
that slip through, in report.json itself); use commas, colons, or a new
sentence. Every other string is shown exactly as written: plain text with
characters literal (`&`, `<`, quotes as-is). Markup there would show as
visible tags, so check-report rejects it.

**The reader has never opened Tensorleap.** The report is for an ML engineer
working in the data's domain: write as a colleague in that domain (its
terminology, its standard fixes; skill Step 4). The platform's vocabulary
and the integration's names stay out of every string the reader sees: no
"Tensorleap", no "insight", no "Failure Mode", no visualizer, function or
field identifiers. Say "finding", "group", "issue" or "pattern", and name a
view by what it shows. check-report rejects those three words, and a
declared visualizer name written as an identifier (exit 8). People who know
the platform continue there from the card's own buttons, which the platform
adds.

**The reader reviews the report in place.** Every card shows the finding's
current status on the platform and an **Acknowledge** button (**Back to
review** once it is acknowledged); the header counts the reviewed findings.
**Open proof** opens the finding on the platform's dashboard, and **Add the
regression test** appears on a card that carries `test`. All of it is the
platform's: nothing for you to write beyond `test`, and never mention it in
the prose.

**A card is skimmable first, deep on demand.** What is visible by default:
title, status and buttons, chips, the bold bottom line, and "Do next". The
diagnosis (root cause, analysis, split/contrast bars) sits behind a
collapsed **Analysis info** fold, your read of the samples behind a
collapsed **LLM <modality> analysis** fold (the word, visual / textual /
auditory, is the report's `modality`, uniform across every card), and the
samples behind a collapsed **Samples (N)** fold placed after it: the reader
sees the claim, then opens the evidence. The platform does the folding; you
still write every field in full.

## Language rules

- **The platform's vocabulary stays out of the report** (above). "Failure
  Mode", "Out of Distribution" and the other panel names are how YOU find a
  group's type; the report gives each group a title in plain words (table
  below) and carries the type in `kind`. When you need a generic word for
  what a section describes, say "finding", "issue", "pattern", or name it
  concretely.
- Each finding's title names what is going wrong in plain ML terms
  ("Confident misclassification of non-cats in the cat region", "Positive
  labels on negative-toned reviews"), never "insight_1_low_performance".
- Never paste internal identifiers into the report: no blob paths, filter
  JSON, artifact ids, raw payload field names, visualizer or function
  names. Those live in `insights.json` for whoever wants them; the report
  speaks English.
- A technical term the reader may not know gets a one-line translation the
  first time (e.g. "severity 3 of 3, the highest").
- **A finding's sample count is `population.samples` in the digest**, and
  it is written plainly: "314 samples", or with the data's own noun
  ("314 clips").
- Your own analysis is presented on its own merits: say what you found and
  how ("profiling the group's metadata against the full run shows objects 3×
  smaller than average"), never as a comparison with what a tool did or
  didn't surface. Comparative framing adds nothing the reader can act on.
- **State what is known plus the step that completes it, never what can't
  be done.** Every "we can't confirm / don't know X" is written as "doing Y
  will establish X", and subtle evidence is a discovery, not a weakness.
  Scope: this governs evidence, tooling, and next steps; model failures stay
  blunt ("misses 48 objects per image"). Framing never upgrades confidence
  in an unconfirmed cause.

## Page structure

The report runs header → KPI tiles → summary → findings table → groups,
and ends with the last card. Notes live in `report.md` alone (item 6).

1. **Header**, "Model analysis", the `PROJECT · VERSION` title, the
   evaluation date (`evaluated`: the day the version's newest Evaluate job
   with status FINISHED was last updated, from `tl_list_jobs`; leave it out
   when you cannot tell) and
   `coverage`: how many findings the report shows out of how many were
   found, in exactly those words ("3 findings shown · 9 found"). "Found"
   counts every group of the run, sub-groups included (`counts.total` in
   the digest). The review progress beside it is the platform's. The
   model/version identity lives in the title alone.
2. **KPI strip** (`tiles`): stat tiles with the model-level numbers a
   domain expert checks first (from `population_metrics`, e.g. recall,
   precision, misses/image for detection; accuracy for classification),
   the headline metric once per split present (training, validation, test)
   so a gap is visible, then one tile per type present with how many groups
   of that type were found, counted as in `coverage`. That tile's label is
   plain words ("groups where the model underperforms", table below) and it
   carries the type's `kind`, which gives it the type's color.
3. **Executive summary** (`summary`): 3–5 sentences. This is where impact
   PRIORITY lives ("labeling the two corruption groups is the
   highest-leverage action"), the rest of the report is panel-ordered, not
   impact-ordered. Where the prose names a specific issue, link the phrase
   to its card ("a <a href="#insight-1">gaussian-noise group</a>…").
4. **Findings table**: a TOC of the report in body order. The platform
   builds it from the findings themselves, with a subheader row per group:
   the finding's number, `shortTitle` (the card's title, compressed),
   `severity` as a colored HIGH / MEDIUM / LOW word, `sampleCount`,
   `firstAction`, and its status. A note under the heading says rows are
   ordered by severity, then affected-sample count, within each group, so
   the ordering below must actually hold.
5. **One group per type present**, in the panel's order, with a `count`
   badge and a one-line `meaning` (table below). Inside, one **card** per
   finding (anatomy below), ordered by severity and, within a severity, by
   the number of affected samples (descending).
6. **Notes, in `report.md` only, never in the report.** The report ends
   with the last card; the markdown companion closes with a `## Notes`
   section. Short and terse, a few one-line bullets, no paragraphs.
   One line per insight without a card, stating why in half a sentence and
   ending in what the reader can do: an insight whose story is covered by
   another card ("covered by the crowded-scenes actions"), a group whose
   metrics are at population level ("no action needed"), an insight where
   the subinsight check found no single story ("its subinsights repeat the
   stories above, a candidate for archiving in the panel"), an insight
   holding too few samples to generalize from, or one that repeats a carded
   insight's samples (`overlaps` in the digest names the shared count, say
   which card it duplicates), or an insight past the 6-card cap (skill
   Step 6). Every insight in the run is either a card or a Notes line,
   nothing is silently dropped. Plus, when
   relevant: visualizations the platform has not rendered yet, phrased as
   on-demand behavior (never as an error); a view that failed to render,
   which is a different case and gets the line skill Step 5 describes; and
   export notes. Word editorial outcomes neutrally and
   ground them in the data, never verdict labels on the insight
   ("incoherent", "not actionable").

| Panel name | `kind` | Group `title` | Tile label | Group `meaning` |
|---|---|---|---|---|
| Failure Mode | `low_performance` | Where the model underperforms | groups where the model underperforms | Groups of samples where the model underperforms. |
| Out of Distribution | `out_of_distribution` | Samples unlike the rest of the data | groups unlike the rest of the data | Samples in one subset unlike anything in the rest of the data. |
| Duplication | `duplication` | Near-identical samples | groups of near-identical samples | Near-identical samples within a subset. |
| Data Leakage | `data_leakage` | Copies on both sides of a split | groups copied across a split | Near-identical samples on both sides of a split. |
| Domain Gap | `domain_gap` | Conditions where performance differs | conditions where performance differs | Performance differs between two values of one recorded property. |
| Mislabeled | `mislabeled_samples` | Labels that look wrong | groups whose labels look wrong | Samples whose ground truth looks wrong. |

Write the tile label in the singular when the count is 1 ("group where the
model underperforms"). The `low_performance` group's `meaning` also names
the root cause vocabulary: "Groups of samples where the model underperforms. Root
cause per card is the analysis's diagnosis, one of: data gap, label
quality, mislabeling, split problem, model behavior."

When some of a type's insights have no card, the group's `count`
reconciles with what was found ("3 of 6") and each missing insight has its
one Notes line.

## Anatomy of a card (in order)

**One insight = one card = one failing population.** Never merge insights
into a combined card. When two insights resemble each other, apply the
playbook's "One failing population per insight" rule: differentiate (each
card stands on its measured difference, as content, never as commentary on
the report's structure) or keep only the clearer one. An insight that fails
the coherence gate goes through the subinsight rescue (skill Step 5): a
subinsight that isolates one clear story gets the card instead, and its
`id` carries both identities (the report shows "#3 · sub #9"). No coherent
story → no card; one terse Notes line with the archive suggestion.
Top-level insights are disjoint by definition; never present non-overlap as
a discovery.

**Every card speaks at exactly one granularity.** A card never contains
subinsight sections, lists, or one-liners, subinsights either promote to
their own card (skill Step 5) or don't appear. The only permitted parent/sub
cross-mention is a single orienting context sentence in the analysis ("this
group is the sharpest slice of the broader animals-on-roads group above").

Each card is one object in its group's `findings` array. Its `id` names the
insight it stands for (`insight-<N>`; sub-based cards use
`insight-<N>-sub-<M>`; N and M are the groups' own numbers in the digest,
their `index`, and check-report refuses a number the export does not
hold): it is the anchor the executive summary links to,
and how the platform finds the finding's status, proof and test. From the
`severity` field (1–3) the platform derives the colored "Severity S of 3"
chip, never restate severity in `chips`. Card content, in render order:

1. **Title** (`title`): the issue itself, unnumbered, no type prefix, the
   group carries the type. The platform shows the number ("#3") from `id`
   in the card header and the findings table; prose cross-references
   between cards go by NAME ("the tiny-objects fix above"), never by
   number. Beside it go the three cells of the findings table: `shortTitle`
   (the title, compressed), `sampleCount` ("314") and `firstAction` (the
   first "Do next" item in a few words).
2. **Chips** (`chips`): sample count, key metric, latent space. The count
   chip is `population.samples`, and every other number on the card
   (composition, contrast, action items) describes that same set. The key
   metric is one that tells the group from all data: when the group is
   wrong by construction (accuracy 0), show its loss instead.
   `Mislabeled` is the only verdict chip, and the platform shows it on
   every card whose root cause family is `Mislabeling`; never write it
   yourself (check-report rejects a chip that is just that word in any
   spelling, "Mislabeling", "Coherently mislabeled", exit 8). Chips that
   merely mention it ("88 mislabeled candidates") are untouched.
3. **Bottom line** (`bottomLine`): ONE bold sentence, what is going
   wrong and why it matters. A reader who stops here still got the point.
   *Items 4–6 render inside the collapsed **Analysis info** fold.*
4. **Root cause** (`rootCause`): `family` plus your one `caption`
   sentence, rendered as `Root cause · <family>:`. The family vocabulary is
   fixed, `Data gap`, `Label quality`, `Mislabeling`, `Split problem`,
   `Model behavior` (two families, comma-separated as in
   `"Label quality, Model behavior"`, are allowed when the diagnosis is
   genuinely mixed about ONE population), so the label reads as a
   consistent grammar across every card and every report; check-report
   refuses any other word.
   `Mislabeling`: the group's annotations are consistently wrong and the
   model is actually right, annotation work, not model work (playbook "Look
   for yourself" §1 has the rule); always alone, only in the
   `low_performance` group (exit 8 otherwise), and it gets the `Mislabeled`
   chip. `Label quality`: everything else about labels, noisy or ambiguous
   annotations, inconsistent guidelines, scattered mislabels mixed with
   real model failures; still a model problem that labels make worse or
   harder to measure.
5. **Analysis** (`analysis`, a list of paragraphs): what kind of samples
   fail, how the model gets them wrong, the evidence in plain sentences,
   and one sentence translating the latent space (playbook guide).
6. **Facts row** (`split` + `contrast`): the split-composition bar and the
   group-vs-all-data metric contrast side by side (they stack on narrow
   screens). Both describe the same samples the count chip names. Pass raw
   counts and values, the platform computes widths and draws count labels.
   Take both numbers of an entry from the insight's `summary.contrast` in
   the digest, which holds the mean and the median for the group and for
   all data: the same statistic on both sides (playbook: "Compare like
   with like"), rounded to what a reader needs. Leave `contrast` empty
   when the digest has none for the group. Each entry's `metric` names the
   metric, its unit and the statistic ("mean missed objects per image");
   `group`/`all` are bare numbers. Two metrics is the useful maximum.
   *Item 8 (`observations`) renders here, before the samples, as the
   collapsed **LLM <modality> analysis** fold.*
7. **Samples** (`samples`: `layout`, `intro`, `viewLabels`, `items`): each
   item carries a caption with sample id + worst metric, and either the
   sample to show (`sampleId` + `visualizers`) or `text`. The whole block
   renders behind a collapsed **Samples (N)** fold, the summary names the
   total, so the reader always knows how much evidence exists. Opening the
   fold shows **every sample**, so ORDER the list best-evidence-first, and
   cap it so a card never buries its action items.

   **A sample is named, never embedded.** `sampleId` is the sample's id in
   the export: its key under the group's `samples` in the digest, and its
   folder under that group's `samples/` (a sub-group's is
   `insight_<i>_<type>/sub_<j>_<type>/samples/`). A top-level card may show
   samples exported under its sub-groups, which are part of it; a sub-based
   card shows its own. `visualizers` holds the view to show: the name of
   the folder that holds that view's `payload.json`
   (`samples/<sample id>/<data type>/<this name>/`), written exactly as it
   is there. It can differ from the `visualizer_name` inside the payload,
   and one name can sit under two data types (a picture, and an overlay of
   it): the platform shows them together as it does in its own panels, and
   leaves out one that failed to render.
   The platform shows the view from its own storage with its own control
   for that kind: it plays a clip, shows a text, draws heatmaps, masks,
   boxes and charts (skill Step 6 says which view comes first: the sample
   in its own form). check-report exits 8 for a sample of another group, a
   view the export did not download and a view that failed to render, so a
   card can only show what the export holds.

   `intro` is a half-sentence naming the view in domain terms
   ("predicted boxes over the camera frame") so GT isn't mistaken for
   prediction, orientation only, never selection rationale; it renders as
   a footnote at the top of the fold.
   **Two views of one sample go side by side, always**, pass both names in
   ONE item's `visualizers` and the platform renders them as a pair, and
   set `viewLabels` (e.g. `["Label", "Model's answer"]`) so each view
   carries a tag naming what it shows.
   Comparing ground truth with prediction is the entire reason both are
   shown, and a reader cannot compare what does not share a horizontal line
   of sight. Never split the two views into two items: stacked full-width
   views are the single worst layout the report can produce, two enormous
   pictures per sample, and the comparison destroyed.

   **`layout` is then chosen so each VIEW lands near its source width.**
   Take the insight's `asset_resolution.max_width` from `insights.json` as
   the source width per view; the samples area is about 1000 px wide:

   | views per item | source width per view | `layout` | width per view |
   |---|---|---|---|
   | 1 | under 250 px | `default` (4-up) | ~240 px |
   | 1 | 250–550 px | `wide` (2-up) | ~490 px |
   | 1 | over 550 px | `solo` (1-up) | ~1000 px |
   | 2 | under 250 px | `wide` (2-up) | ~240 px |
   | 2 | 250 px and up | `solo` (1-up) | ~490 px |

   Thumbnail-scale data (MNIST, QuickDraw) stays 4-up, there is nothing
   more to see. Detection and segmentation frames land in `solo`. A dense
   1360 px frame shown four-across gives ~240 px per view: the reader cannot
   verify anything in it, and an unverifiable figure is worse than none.
   If a single item would still tower over the page, show the one more
   probative view (Step 5 picked it) rather than shrinking both.
   The table is for pictures; `asset_resolution` measures only those. A
   clip plays at any size: alone it takes `wide` (two per row), paired with
   a second view `solo`. A chart, and a picture that is itself a grid of
   frames, need the room of `solo`.
8. **"What the samples show"** (`observations`): the analyst's own
   observations from viewing the samples, first-person ("Looking at the
   samples, …") so it can't be mistaken for a tool's output. "Nothing
   beyond the numbers above" is a real, useful result. Observations are
   about the **group**, never about the pictures on the card, no "5 of the
   6 images", no "all the samples shown here". Numbers come from the
   `summarize` output, which covers every member. A pattern a column
   measures is stated as a plain group-level verdict carrying that number;
   a pattern only the eye can see is scoped to the samples reviewed and
   paired with the metadata field that would quantify it (skill Step 5:
   calibrate to the source, not to a count of images). Either way the
   samples lacking the trait are left uncharacterized.
9. **"Do next"** (`doNext`, `test`): the strongest-looking block on the
   card, 1–3 concrete, quantified checklist items. When the insight
   carries `aggressor_fixing` (the platform's own selection of samples to
   label or acquire; the digest has `files.fixing_csv`), one item names
   that selection with its counts ("label the 88 samples already selected
   for it"); the list itself is one click away, through the card's Open
   proof button.
   When the digest carries `add_test_link` (the insight has engine-made
   `automatic_tests`), add `test` = `{"hint": "<the test condition in one
   phrase, e.g. cross-entropy stays below 0.46 on this group>"}`. The card
   then offers **Add the regression test**, which opens the platform; there
   the user confirms creating the test (nothing is created without their
   click). For a sub-based card, look at the sub-insight's own
   `add_test_link` and take the hint from its own test.

## report.json schema

Write `<out-dir>/report.json` with this shape, then run `check-report`
on `<out-dir>` (SKILL.md Step 6). It exits 8 listing everything
that is wrong (a missing or mistyped field, a sample or view the export
does not hold, platform vocabulary, misplaced markup): fix all of it and
re-run. It also writes `report.txt`, the text linearization you read for
the final review pass.

```json
{
  "schemaVersion": 1,
  "project": "PROJECT", "version": "VERSION",
  "evaluated": "2026-07-28",
  "coverage": "1 finding shown · 9 found",
  "modality": "visual",
  "tiles": [
    {"value": "0.71", "label": "recall, all data"},
    {"value": "6", "label": "groups where the model underperforms", "kind": "low_performance"}
  ],
  "summary": "3–5 sentences; impact priority lives here; link phrases to cards: …a <a href=\"#insight-1\">gaussian-noise group</a>…",
  "groups": [
    {"title": "Where the model underperforms", "kind": "low_performance", "count": "1 of 6",
     "meaning": "Groups of samples where the model underperforms. Root cause per card is the analysis's diagnosis, one of: data gap, label quality, mislabeling, split problem, model behavior.",
     "findings": [
       {"id": "insight-1", "severity": 3,
        "title": "Heavy gaussian noise breaks the classifier",
        "shortTitle": "Gaussian noise breaks the classifier",
        "sampleCount": "314", "firstAction": "Label 100 noisy samples",
        "chips": ["314 samples", "accuracy 0.42", "image-non-semantic space"],
        "bottomLine": "Every sample in this group carries heavy gaussian noise, none of them are training samples, and accuracy halves on them.",
        "rootCause": {"family": "Data gap", "caption": "the corruption never appears in training; a coverage gap, not a model defect."},
        "analysis": ["…paragraphs with evidence + latent-space translation…"],
        "split": [{"state": "training", "count": 0}, {"state": "test", "count": 221}, {"state": "unlabeled", "count": 93}],
        "contrast": [{"metric": "accuracy", "group": 0.42, "all": 0.87},
                     {"metric": "missed objects per image", "group": 57, "all": 11}],
        "observations": "Looking at the samples, …",
        "samples": {
          "layout": "default",
          "intro": "predicted boxes over the camera frame",
          "viewLabels": ["Label", "Model's answer"],
          "items": [
            {"caption": "test_41 · loss 4.2", "sampleId": "test_41", "visualizers": ["image"]},
            {"caption": "test_87 · loss 3.9", "sampleId": "test_87", "visualizers": ["gt_boxes", "pred_boxes"]},
            {"caption": "test_12 · loss 3.7", "visualizers": [], "text": "a short excerpt of the sample, quoted word for word"}
          ]},
        "doNext": ["… 1–3 quantified checklist items …"],
        "test": {"hint": "accuracy stays above 0.8 on this group"}}
     ]}
  ]
}
```

Field notes:

- `schemaVersion` is `1`.
- `modality` is `visual`, `textual` or `auditory`: what most of the
  samples you reviewed are. It names the "LLM <modality> analysis" fold.
- `kind` is one of `low_performance`, `out_of_distribution`,
  `duplication`, `data_leakage`, `domain_gap`, `mislabeled_samples` (table
  above). It never shows as text.
- `split` states map to bar colors by name (`training`/`train`,
  `validation`/`val`, `test`, `unlabeled`; anything else gets the neutral
  color). Include zero-count states you want in the legend ("training 0" is
  a finding).
- `contrast.group`/`contrast.all` are numbers; the platform scales both
  bars to the larger one and prints the values verbatim.
- `samples.layout` is `default`, `wide`, or `solo` (density table above).
- A sample item has either `sampleId` with `visualizers` (1 name, or 2 for
  a side-by-side pair) or `text` (rendered as a quote with the caption as
  its muted line; `visualizers` is then `[]`).
- `samples.viewLabels` names the two views of every pair on the card, in
  order; set it whenever items carry two views, `[]` otherwise.
- Required: at the top `schemaVersion`, `project`, `version`, `modality`,
  `tiles`, `summary` and `groups`; in a tile `value` and `label`; in a
  group `title` and `findings`; in a finding `id`, `title`, `bottomLine`
  and the five lists `chips`, `analysis`, `split`, `contrast` and `doNext`
  (a list even when it is empty); in `samples` `layout`, `viewLabels` and
  `items`; in an item `caption` and `visualizers`; and, wherever the
  object is present, `rootCause.family` and `caption`, `test.hint`, a
  split's `state` and `count`, a contrast's `metric`, `group` and `all`.
  Everything else may be omitted: `evaluated`, `coverage`, a tile's and a group's `kind`, a
  group's `count` and `meaning`, and a finding's `severity`, `shortTitle`,
  `sampleCount`, `firstAction`, `rootCause`, `observations`, `samples`,
  `test`, and `samples.intro`.
- Only `schemaVersion`, `severity`, a split's `count` and a contrast's
  `group` and `all` are numbers. Every other value is text in quotes, also
  a tile's `value`, `sampleCount` and a group's `count`.
- The whole file stays under 1 MB: it is text, the pictures are not in it.

## report.md (the paste-into-a-ticket companion)

Just: title line, the published report's link (added after publishing),
the executive-summary paragraph, the findings table (number, finding,
severity, samples, first action), each card's "Do next" checklist under its
title (with the panel insight # in the heading), and a closing `## Notes`
section, the only place Notes appear (page structure, item 6). No images,
no evidence sections: the published report has those.

report.md is for the team's own tickets, read by people who use the
platform: it may use the platform's words (the panel's insight numbers,
"archive"), which report.json never does.
