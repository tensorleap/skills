# Report template

The deliverable is **one self-contained `report.html`**, but you never write
HTML. You write **`<out-dir>/report.json`** (schema below, content only:
headings, prose, sample paths, numbers), then
`tl_api.py build-report <out-dir>` renders `report.html` (layout, CSS, chips,
split bars, contrast bars, sample folds, all script-owned)
plus `report.txt`, a plain-text linearization for your final read-through.
Then `tl_api.py inline-html <out-dir>/report.html` embeds the images (and any
download button's CSV) as data URIs (oversized images are downscaled and
recompressed automatically). Image
paths in report.json are relative to `<out-dir>`. Alongside it you write a
short **`report.md`** holding only the executive summary and the action-item
checklists (the part people paste into tickets and Slack).

Exactly three fields are HTML fragments where inline markup (`<a>`, `<em>`)
is allowed: `executive_summary`, the `prose` paragraphs, and `observe`; in
those, `&`/`<` used as plain characters must be written as entities.
**Never use an em-dash anywhere in the report** (the script rewrites any that
slip through to a comma); use commas, colons, or a new sentence. Every
other string is HTML-escaped by the script: write plain text with characters
literal (`&`, `<`, quotes as-is), and any markup in them would render as
visible tags.

**The report serves two audiences at once.** The prose and diagrams are for
an ML engineer working in the data's domain who has never opened Tensorleap,
write as a colleague in that domain (its terminology, its standard fixes;
skill Step 4); the "Analyze insight" button on each insight
is for users who know the platform and want to continue there.

**The page is dark-only**, the Tensorleap product palette (Nunito Sans,
embedded; each insight type carries its own accent color). All of that is
script-owned; you never pick colors.

**A card is skimmable first, deep on demand.** What is visible by default:
heading, the "Test" and "Analyze insight" header buttons, chips, the bold
bottom line, and "Do next". The diagnosis (root cause, prose, split/contrast
bars) sits behind a collapsed **Analysis info** fold, your read of the samples
behind a collapsed **LLM <modality> analysis** fold (the script derives the
word, visual / textual / auditory, from the samples' modality, uniform
across every card), and the sample figures
behind a collapsed **Samples (N)** fold placed after it, the reader sees
the claim, then opens the evidence. The script does the folding; you still
write every field in full.

## Language rules

- **"Failure Mode" is an insight TYPE** (the Insights panel's name for
  `low_performance`). Never use the phrase for anything else, not as a
  table column, not as generic prose ("each failure mode…"). When you need
  a generic word for what a section describes, say "issue", "pattern", or
  name it concretely.
- Each insight's heading names what is going wrong in plain ML terms
  ("Confident misclassification of non-cats in the cat region", "Positive
  labels on negative-toned reviews"), never "insight_1_low_performance".
- Never paste internal identifiers into the prose: no blob paths, filter
  JSON, artifact ids, or raw payload field names. Those live in
  `insights.json` for whoever wants them; the report speaks English.
- Any platform term you do use gets a one-line translation the first time
  (e.g. "severity 3, the platform's highest").
- **A failure mode's sample count is the number that actually underperforms**
  (`population.samples` in the digest), and it is written plainly: "314
  samples". The platform's `n_samples` counts a wider latent neighbourhood
  the report does not discuss, never quote it, and never invent vocabulary
  for the difference ("core", "affected", "extended population", "root
  members" are all payload-side words that mean nothing to the reader).
- Your own analysis is presented on its own merits: say what you found and
  how ("profiling the group's metadata against the full run shows objects 3×
  smaller than average"), never as a comparison with what the platform did
  or didn't surface. Comparative framing adds nothing the reader can act on,
  and it misattributes, the platform's insight is what surfaced the group
  you are enriching.
- **Em-dashes sparingly.** At most one per paragraph across all prose fields
  (`executive_summary`, `prose`, `lede`, `observe`, action items), prefer a
  comma, colon, or a new sentence, and never two dash asides in one
  paragraph.
- **State what is known plus the step that completes it, never what can't
  be done.** Every "we can't confirm / don't know X" is written as "doing Y
  will establish X", and subtle evidence is a discovery, not a weakness.
  Scope: this governs evidence, tooling, and next steps; model failures stay
  blunt ("misses 48 objects per image"). Framing never upgrades confidence
  in an unconfirmed cause.

## Page structure

The HTML page runs header → KPI tiles → summary → overview table → insight
groups, and ends with the last card. Notes live in `report.md` alone
(item 6).

1. **Sticky header**, "Tensorleap analysis" eyebrow, `PROJECT · VERSION`
   title, an "Open in Tensorleap" button (the Insights-panel link), and a
   chip row: evaluation date (`evaluated`) and the insight counts (`meta`).
   The model/version identity lives in the title alone, no model chip, no
   severity-summary chip.
2. **KPI strip** (`.tiles`): 3–4 stat tiles with the model-level numbers a
   domain expert checks first (from `population_metrics`, e.g. recall,
   precision, misses/image for detection; accuracy for classification),
   then one tile per insight type present with its count, the same numbers
   the reader sees in the Insights panel.
3. **Executive summary**: 3–5 sentences. This is where impact PRIORITY
   lives ("labeling the two corruption groups is the highest-leverage
   action"), the rest of the report is panel-ordered, not impact-ordered.
   Where the prose names a specific issue, link the phrase to its card's
   anchor ("a <a href="#insight-1">gaussian-noise group</a>…").
4. **Overview table** (rendered under a "Findings" heading): a TOC of the
   report in body order, columns
   `Insight # | Issue | Severity | Samples | First action`, with a subheader
   row per type. "Issue" holds the section's headline, compressed. The
   Insight # cell links to the card's anchor (`<a href="#insight-4">#4</a>`).
   The Severity cell renders as a colored HIGH / MEDIUM / LOW word (the
   platform's vocabulary), derived from the row's `severity` digit; the
   script also prints a note under the heading that rows are ordered by
   severity, then affected-sample count, within each type, so the ordering
   below must actually hold.
5. **One `<h2>` group per insight type present**, in the panel's order,
   with a count badge and a one-line meaning (table below). Inside, one
   **card** per insight (anatomy below), ordered by severity and, within a
   severity, by the number of affected samples (descending).
6. **Notes, in `report.md` only, never in the HTML.** The HTML ends with
   the last insight card; the markdown companion closes with a `## Notes`
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
   relevant: unrendered visualizations phrased as on-demand behavior (never
   as an error) and fetch errors. Word editorial outcomes neutrally and
   ground them in the data, never verdict labels on the insight
   ("incoherent", "not actionable").

| Panel name | payload `type` | One-line meaning for the group header |
|---|---|---|
| Failure Mode | `low_performance` | groups of samples where the model underperforms |
| Out of Distribution | `out_of_distribution` | samples in one subset unlike anything in the rest of the data |
| Duplication | `duplication` | near-identical samples within a subset |
| Data Leakage | `data_leakage` | near-identical samples on both sides of a split |
| Domain Gap | `domain_gap` | performance differs between two values of a metadata field |
| Mislabeled | `mislabeled_samples` | samples whose ground truth looks wrong |

When some of a type's insights have no card, the group's count badge
reconciles with the panel ("3 of 6") and each missing insight has its one
Notes line.

## Anatomy of an insight card (in order)

**One insight = one card = one failing population.** Never merge insights
into a combined card. When two insights resemble each other, apply the
playbook's "One failing population per insight" rule: differentiate (each
card stands on its measured difference, as content, never as commentary on
the report's structure) or keep only the clearer one. An insight that fails
the coherence gate goes through the subinsight rescue (skill Step 5): a
subinsight that isolates one clear story gets the card instead, its header
carries both identities (the script derives "#3 · sub #9" from the card's
`id`) and its explore line names the parent in the panel. No coherent story → no card; one terse Notes
line with the archive suggestion. Top-level insights are disjoint by
definition; never present non-overlap as a discovery.

**Every card speaks at exactly one granularity.** A card never contains
subinsight sections, lists, or one-liners, subinsights either promote to
their own card (skill Step 5) or don't appear. The only permitted parent/sub
cross-mention is a single orienting context sentence in prose ("this group
is the sharpest slice of the broader animals-on-roads cluster, insight #3").
The explore box may state the panel's sub-insight count as navigation info.

Each card is one object in the schema's `cards` array. Its `id` is the
anchor (`insight-<N>`; sub-based cards use `insight-<N>-sub-<M>`) that the
overview table and executive summary link to. From the `severity` field
(1–3) the script derives the colored leading "Severity S of 3" chip, never
restate severity in `chips`. Card content, in render order:

1. **Heading** (`heading`): the issue itself, unnumbered, no type prefix,
   the group header carries the type. Identity = the **platform insight #**:
   the script derives it from `id` and renders it in the card header and the
   overview table; prose cross-references between cards go by NAME ("the
   tiny-objects fix above"), never by number.
   *Items 9's `add_test` ("Test") and 10's `explore` ("Analyze insight")
   render here as header buttons that open the platform in a new tab.*
2. **Chips** (`chips`): sample count, key metric, latent space (the severity
   chip is script-generated, and an "insight #N" chip is dropped, the
   header already carries the number). The count chip is
   `population.samples`, for a failure mode, the samples that actually
   underperform, and every other number on the card (composition, contrast,
   action items) describes that same set.
3. **Bottom line** (`lede`): ONE bold sentence, what is going
   wrong and why it matters. A reader who stops here still got the point.
   *Items 4–6 render inside the collapsed **Analysis info** fold.*
4. **Root cause** (`root_cause`): rendered as `Root cause · <family>:` plus
   your one caption sentence. The family vocabulary is fixed, `Data gap`,
   `Label quality`, `Split problem`, `Model behavior` (two families allowed
   when the diagnosis is genuinely mixed about ONE population), so the
   label reads as a consistent grammar across every card and every report.
5. **Prose** (`prose`, a list of paragraphs): what kind of samples fail, how
   the model gets them wrong, the evidence in plain sentences, and one
   sentence translating the latent space (playbook guide).
6. **Facts row** (`split` + `contrast`): the split-composition bar and the
   group-vs-all-data metric contrast side by side (they stack on narrow
   screens). Both describe the same samples the count chip names. Pass raw
   counts and values, the script computes widths and draws count labels.
   Omit `contrast` if `population_metrics` lacks the column, and label its
   baseline for what it is (a population mean is not a median, playbook:
   "Compare like with like"). Each contrast entry's `metric` names the
   metric AND carries its unit ("missed objects per image"); `group`/`all`
   are bare numbers. Two metrics is the useful maximum.
   *Item 8 (`observe`) renders here, before the samples, as the collapsed
   **LLM <modality> analysis** fold.*
7. **Samples** (`grid`, `view_intro`, `pair_labels`, `samples`): each sample
   entry carries a caption with sample id + worst metric, and either
   `images` (1–2 paths) or `text`. The whole block renders behind a
   collapsed **Samples (N)** fold, the summary names the total, so the
   reader always knows how much evidence exists. Opening the fold shows
   **every sample**, so ORDER the list best-evidence-first, and cap it so
   a card never buries its action items.
   `view_intro` is a half-sentence naming the view in domain terms
   ("predicted boxes over the camera frame") so GT isn't mistaken for
   prediction, orientation only, never selection rationale; it renders as
   a footnote at the top of the fold.
   **Two views of one sample go side by side, always**, pass both paths in
   ONE sample entry's `images` and the script renders them as a pair, and
   set `pair_labels` (e.g. `["GT", "Prediction"]`) so each view carries a
   corner tag naming what it shows.
   Comparing ground truth with prediction is the entire reason both are
   shown, and a reader cannot compare what does not share a horizontal line
   of sight. Never split the two views into two sample entries: stacked
   full-width views are the single worst layout the report can produce, two
   enormous images per sample, and the comparison destroyed.

   **Grid density is then chosen so each VIEW lands near its source width.**
   Take the insight's `asset_resolution.max_width` from `insights.json` as
   the source width per view; the grid itself is ~1090 px wide:

   | views per figure | source width per view | `grid` value | width per view |
   |---|---|---|---|
   | 1 | under 250 px | `default` (4-up) | ~265 px |
   | 1 | 250–550 px | `wide` (2-up) | ~535 px |
   | 1 | over 550 px | `solo` (1-up) | ~1090 px |
   | 2 | under 250 px | `wide` (2-up) | ~265 px |
   | 2 | 250 px and up | `solo` (1-up) | ~540 px |

   Thumbnail-scale data (MNIST, QuickDraw) stays 4-up, there is nothing
   more to see. Detection and segmentation frames land in `solo`. A dense
   1360 px frame shown four-across gives ~270 px per view: the reader cannot
   verify anything in it, and an unverifiable figure is worse than none.
   If a single figure would still tower over the page, show the one more
   probative view (Step 5 picked it) rather than shrinking both.
   Watch total file size (`inline-html` prints it): aim under ~20 MB, on
   large-resolution datasets carry fewer samples per card.
8. **"What the samples show"** (`observe`): the analyst's own observations
   from viewing the samples, first-person ("Looking at the samples, …") so
   it can't be mistaken for platform output. "Nothing beyond the platform's
   story" is a real, useful result. Observations are about the **group**,
   never about the figures on the card, no "5 of the 6 images", no "all the
   samples shown here". Numbers come from the `summarize` output, which
   covers every member. A pattern a column measures is stated as a plain
   group-level verdict carrying that number; a pattern only the eye can see
   is scoped to the samples reviewed and paired with the metadata field that
   would quantify it (skill Step 5: calibrate to the source, not to a count
   of images). Either way the samples lacking the trait are left
   uncharacterized.
9. **"Do next"** (`do_next`, `download_csv`): the strongest-looking block on
   the card, 1–3 concrete, quantified checklist items. When the insight
   carries `aggressor_fixing` (the platform's fixing button; the digest has
   `files.fixing_csv`), the block ends with a **download button** for that
   CSV: `download_csv` = `{"path": "<digest files.fixing_csv, relative to
   out-dir>", "label": "Download the N samples selected for labeling (CSV)"}`
  , the label names the platform's counts. The script embeds the file so the
   button works offline.
   When the digest carries `add_test_link` (the insight has engine-made
   `automatic_tests`), add `add_test` = `{"link": "<digest add_test_link>",
   "label": "Add regression test in Tensorleap", "tip": "<the test condition
   in one phrase, e.g. cross-entropy stays below 0.46 on this cluster>"}`,
   rendered as the **"Test" button in the card header**, which opens the
   platform; there the user confirms creating the insight's automatic test
   (nothing is created without their click). `label` + `tip` become the
   button's hover tooltip.
10. **Analyze insight** (`explore`): the second card-header button. `link` =
    the insight's `deep_link` from the digest, which lands on the insight
    already analyzed (opened as the dashboard's top panel with its cluster
    filter applied, exactly what the panel's "Analyze" button does); the
    label is fixed by the script ("Analyze insight"); `text` (panel
    type and severity) and `detail` (latent space · split counts · platform
    label/acquire counts) are optional and shown only as the button's hover
    tooltip. A sub-based card links its own sub-insight's `deep_link`. No
    filter JSON.

## report.json schema

Write `<out-dir>/report.json` with this shape, then run
`python3 tl_api.py build-report <out-dir>`. The script owns all layout and
CSS (the dark Tensorleap theme, with the brand font embedded), computes bar
widths from your raw numbers, generates the severity chips and the sample
folds, and exits 8 listing any image path that does not resolve, fix and
re-run.
It also writes `report.txt`, the text linearization you read for the final
review pass.

```json
{
  "project": "PROJECT", "version": "VERSION",
  "evaluated": "2026-07-28",
  "meta": "9 insights \u00b7 6 top-level, 3 sub",
  "insights_link": "LINKS.insights_panel from insights.json",
  "tiles": [
    {"value": "0.71", "label": "recall, all data"},
    {"value": "6", "label": "Failure Mode insights"}
  ],
  "executive_summary": "3\u20135 sentences; impact priority lives here; link phrases to cards: \u2026a <a href=\"#insight-1\">gaussian-noise group</a>\u2026",
  "overview": [
    {"type": "Failure Mode", "rows": [
      {"anchor": "insight-1", "num": "#1", "issue": "Heavy gaussian noise breaks the classifier",
       "severity": "3 of 3", "samples": "314", "first_action": "Label 100 noisy samples"}
    ]}
  ],
  "groups": [
    {"type": "Failure Mode", "count": "3 of 6",
     "meaning": "Groups of samples where the model underperforms. Root cause per card is the analysis's diagnosis, one of: data gap, label quality, split problem, model behavior.",
     "cards": [
       {"id": "insight-1", "severity": 3,
        "heading": "Heavy gaussian noise breaks the classifier",
        "chips": ["314 samples", "accuracy 0.42", "image-non-semantic space"],
        "lede": "Every sample in this group carries heavy gaussian noise, none of them are training samples, and accuracy halves on them.",
        "root_cause": {"family": "Data gap", "caption": "the corruption never appears in training; a coverage gap, not a model defect."},
        "prose": ["\u2026paragraphs with evidence + latent-space translation\u2026"],
        "split": [{"state": "training", "count": 0}, {"state": "test", "count": 221}, {"state": "unlabeled", "count": 93}],
        "contrast": [{"metric": "accuracy", "group": 0.42, "all": 0.87},
                     {"metric": "missed objects per image", "group": 57, "all": 11}],
        "grid": "default",
        "view_intro": "predicted boxes over the camera frame",
        "pair_labels": ["GT", "Prediction"],
        "samples": [
          {"caption": "test_41 \u00b7 loss 4.2",
           "images": ["insight_1_low_performance/samples/test_41/\u2026/image.jpg"]},
          {"caption": "test_87 \u00b7 loss 3.9",
           "images": ["\u2026/gt.jpg", "\u2026/pred.jpg"]},
          {"caption": "test_12 \u00b7 loss 3.7", "text": "joined data.body tokens for text modalities"}
        ],
        "observe": "Looking at the samples, \u2026",
        "do_next": ["\u2026 1\u20133 quantified checklist items \u2026"],
        "download_csv": {"path": "insight_1_low_performance/fixing_samples.csv",
                         "label": "Download the 88 samples selected for labeling (CSV)"},
        "explore": {"link": "the insight's deep_link from insights.json",
                    "text": "\u2014 a \"low performance\" insight, severity 3, opened with its cluster filter applied.",
                    "detail": "Latent space: \u2026 \u00b7 221 test + 93 unlabeled samples \u00b7 88 samples pre-selected for labeling."}}
     ]}
  ]
}
```

Field notes:

- `split` states map to bar colors by name (`training`/`train`, `validation`/
  `val`, `test`, `unlabeled`; anything else gets the neutral color). Include
  zero-count states you want in the legend ("training 0" is a finding).
- `contrast.group`/`contrast.all` are numbers; the script scales both bars to
  the larger one and prints the values verbatim.
- `grid` is `default`, `wide`, or `solo` (density table above).
- A sample entry has either `images` (1 path, or 2 for a side-by-side pair)
  or `text` (rendered as a quote with the caption as its muted line).
- `pair_labels` names the two views of every pair on the card, overlaid as
  corner tags on the images; set it whenever samples carry 2-image pairs.
- `download_csv.path` must resolve relative to `<out-dir>` (build-report
  exits 8 otherwise); `inline-html` embeds it up to 5 MB, larger files stay
  beside `report.html` as a relative link.
- Optional fields may be omitted (`evaluated`, `tiles`,
  `root_cause`, `split`, `contrast`, `view_intro`, `pair_labels`, `samples`,
  `observe`, `do_next`, `download_csv`, `add_test`, `explore`); everything
  else is required.

## report.md (the paste-into-a-ticket companion)

Just: title line, the Insights-panel link, the executive-summary paragraph,
the overview table, each card's "Do next" checklist under its headline (with
the panel insight # in the heading), and a closing `## Notes` section, the
only place Notes appear. No images, no evidence sections, link to
`report.html` for those.
