# Action-item playbook

How to turn each insight into evidence and action items. Follow the decision
tree for `low_performance`; use the per-type table for the rest. Always adapt
to the actual evidence — the playbook gives the branches, the insight payload
decides which branch applies, and your judgment fills in the specifics (which
class, which metadata slice, how many samples).

## Reading the evidence fields (all insight types)

| Field | Meaning | How to cite it |
|---|---|---|
| `severity` | Integer, higher = worse. Order the report by it. | "Severity 3 (highest in this run)" |
| `n_samples` | The cluster plus its latent neighbourhood. For `low_performance` this is NOT the failing group — never quote it (see below). | Internal only |
| `severity_metrics` | Per-metric `{metric_name, value, normalized_value}`. | "loss 2.1× the population median" |
| `metrics_info` | Per-metric statistics. For `low_performance`, "Cluster Median/Average" are the failing group's; "Outside Cluster" is everything beyond the wider cluster. | Table of mean/median per metric |
| `mutual_info_elements` | What characterizes the cluster: `features[{feature_name, feature_value, direction}]`, `value_in_cluster` vs `value_outside_cluster`, `score`, `is_unique` | "dominated by `weather=night` (78% in-cluster vs 12% outside)" — this is the heart of the story |
| `display_filters` | The dashboard filters that reproduce the cluster. | Include verbatim so the user can open it in the UI |
| `automatic_tests` | Suggested regression tests `{test_name, filter, metric_name, metric_value, operator}`. | Offer as "add this as a platform test" |
| `latent_space` | Which latent space produced the cluster. | Context only |
| `top_panel.summary` | Ready-made `title` + `sentence` (low_performance only). | Use as the section heading/lede |

## low_performance — decision tree (aggressor fixing)

Run these checks in order for every low-performance insight (and any
subinsight you elaborate). Each check names the payload fields that answer it.

**An `aggressor_fixing` payload is a MUST-surface suggestion.** When the
insight carries the payload (the Insights panel shows its Aggressor Fixing
button), the card's "Do next" MUST include the platform's suggestion with
its numbers — the samples to label and/or to acquire — regardless of which
branch your diagnosis takes. Your judgment may rank or sequence it ("after
the label audit"), never omit it. This binds every insight that has a card
(a sub-based card cites its parent's payload); an insight ignored through
the coherence gate is instead handled by its archive suggestion in Notes.

**The cluster's csv is wider than the failing group.** `samples.csv` holds
the cluster plus its latent neighbourhood, and `n_samples` counts all of it —
the non-members are frequently healthy: in a measured run the failing group's
median error was 7.8× the rest of the data while the others sat *below* the
population median. `is_low_perf_root_member == True` marks the samples that
actually underperform (the digest pre-counts them as `population.samples`).

- **Characterize, count and contrast on those rows only.** Composition, split
  bar, metadata majorities, metric contrast, the story itself.
  Mutual-information features are computed over the wider csv — re-check every
  one against the failing rows before naming the group by it.
- **The group's size is that count.** Never quote `n_samples`, and never quote
  the number the panel's filter shows; they describe the wider cluster, which
  the report does not discuss.
- **Compare like with like.** `metrics_info` "Cluster Median" is a median;
  `population_metrics` values are population *means*. Contrast median with
  median (compute it from `samples.csv` when the engine has none) and name the
  baseline you used — the rest of the data, or the whole population.

Insight types other than `low_performance` have no such split: every csv row
is a member.

**0. Is the wider metadata population failing too?**
Look at `mutual_info_elements`: the features characterizing the cluster define
a metadata population wider than the cluster itself.
- Failing as well → note in the report: *the whole metadata population is
  failing, not just this cluster — don't analyze the cluster/population diff.*
- Not failing → the diff is informative: analyze what separates the failing
  cluster from the healthy rest of its population (domain-gap-style
  comparison over the metadata).

**1. Can more data solve it?** Applies when the failing group is NOT dominated
by training data: train-sample share < 40% of the group, or the group's
training samples are < 5% of all training data (`is_train_aggressor`,
`cluster_extended_stats`, and the per-state breakdown in `samples.csv`).
If there are no (or a distinct minority of) test/val samples in the group,
also recommend rebalancing the splits.

- **Mostly training data (train aggressor)** → more data won't help:
  - Mislabel check: not automated for this path (different from the
    mislabeled-samples insight) — recommend a manual label review of the
    top samples; if mislabeled → **relabel**.
  - Labels fine → **model-side fixes**: capacity / architecture / training
    adjustments — **add a targeted loss term for the failing pattern, boost
    these samples in training via loss weighting or oversampling**.

- **Not a train aggressor** → data-side fixes, in this order:
  - **Overfitting?** `overfitting_metrics` / `overfitting_evidence` flag it
    (MAD-robust contrast between the wider cluster's training samples and the
    failing group; flagged above 2.0). If flagged → present the evidence and recommend **balancing the dataset —
    move samples from test to train** for this population.
  - **Unlabeled data available?** `aggressor_fixing` says what the platform
    already selected: `num_of_samples_to_label` (chosen via similarity
    search near the cluster, precision-bounded) and
    `num_of_samples_to_acquire`; its `csv_path` lists the selected samples.
    - Enough selected → **label the selected samples** (name the count).
    - Not enough → **label what's available**, plus **collect more real
      data** matching the cluster's metadata profile, or **use/create a
      synthetic-data pipeline** if one is connected.
  - **Neither feasible** → fall back to **training adjustments**: loss-term
    addition and sample boosting (loss weighting / oversampling), as above.

## Other insight types

| Type | What it means | Type-specific fields | Typical action items |
|---|---|---|---|
| `out_of_distribution` | Samples in `subset` sit far from the training distribution. | `subset` | Collect/label training data covering this region; targeted augmentation toward it; or exclude the samples if they're invalid/out-of-scope. |
| `duplication` | Near-duplicate samples inside `subset`. | `subset` | Deduplicate; make sure duplicates don't straddle splits; stop spending labeling budget on them. |
| `data_leakage` | Near-identical samples appear in both subsets — eval metrics are inflated. | `first_subset`, `second_subset` | Re-split so duplicates stay on one side; remove leaked samples from eval; re-evaluate and treat previous metrics as optimistic. |
| `domain_gap` | Performance differs across two values of a metadata field. | `metadata_name`, `domain_gap_score`, `domain_a`, `domain_b` | Balance training data across the domains; domain-specific augmentation; per-domain eval tracking; boost the weak domain via loss weighting. |
| `mislabeled_samples` | Candidate labeling errors in `subset`. | `subset` | Manually review the top samples (embed them in the report); relabel confirmed ones; re-evaluate. |

## Characterize by composition, not by the tail

Before naming any group, COUNT its composition over the full `samples.csv`
(distribution of the candidate metadata values, split states, classes). The
top-loss samples you look at are the TAIL — describe them as the tail
("the worst 50 over-represent X"), never as the group ("this is the X
slice") unless the full composition actually shows dominance. A group whose
tail is 40% X can still be 75% not-X overall.

Same discipline for the platform's correlation signals: an elevated
mutual-information feature means *over-represented*, not *defining*. Write
"X-tagged samples are over-represented (16% of the group vs 5% elsewhere)
and dominate the failure tail" — never "this is the X group" off a
correlation alone.

## One failing population per insight

Every insight the report elaborates must be about ONE failing population.
Mixed cause TYPES about that population are fine (label quality and model
behavior can both be true of the same group); different populations under
the same cause type ("bad on roads and also bad on oceans") are different
insights — check the parent's subinsights first, they often isolate the
single-population cores.

When two insights look alike, MEASURE before writing: composition and metric
signatures over their CSVs. Then:

- A real difference exists → each section stands on its difference, named in
  its heading (e.g. one group's misses concentrate in held-out data).
- You cannot name a difference → elaborate only the insight with the clearer
  single-population story (severity as tiebreak); the other gets one Notes
  line saying what it shares with the kept one and its # in the Insights
  panel.

Write the difference as the insight's own content, never as commentary on
the report's structure ("what makes this group its own insight…", "unlike
insight #2…") — state the facts and let the distinction be self-evident.
Top-level insights are disjoint groups by definition, so never present
non-overlap as a discovery or a differentiator.

A parent and its subinsight are NOT two populations — they are one
population at two zoom levels, and a card carries one message at one zoom.
When a sub is promoted to a card, the look-alike rule above applies between
it and its parent's card exactly as between two top-level insights: either
the parent's remaining story stands on a measured difference with its own
action, or only one of the two zooms gets a card.

## Look for yourself (mandatory per insight)

The platform clusters by metrics, metadata and embeddings — it cannot read
an image or a sentence. You can. For every insight you write up, open its
top samples and ask:

1. **Does the content contradict a label or metadata value?** Two
   preconditions before claiming a mislabel / wrong metadata:
   - the value is **human-interpretable** (a word: "blond", "positive", a
     readable class name). For an opaque id (class 3, an encoded value),
     first try `prediction_labels` in insights.json — the label names the
     integration declared per prediction type, where a class index is the
     position in that list (class 3 → `labels[3]`). Only if the map is
     empty or its entries are themselves opaque, make NO claim and say the
     labels aren't judgeable from outside;
   - the sample **visibly contradicts** it (a dark-haired face where the
     value says blond; a hostile review where it says positive).
   When both hold, report it by sample ("sample X's tag says blond, the
   image shows dark hair") — naming the samples, never counting the
   displayed ones. A metadata tag disagreeing with the image is a metadata
   problem; call it a *label* problem only when it's the label itself.
2. **What do these samples share that the metadata can't express?**
   Lighting, pose, occlusion, background clutter, image quality, phrasing
   style, topic. If you spot one, name it AND recommend adding it as a
   metadata field — that makes the pattern trackable in the platform from
   the next run on.
3. **Do the members actually belong together?** If the composition is
   diffuse, the worst samples have nothing visible in common, and the
   metadata story is weak, the insight fails the coherence gate. Check its
   subinsights before giving up — they often isolate a coherent story that
   deserves the card instead (skill Step 5). If no core emerges, ignore the
   insight (the report equivalent of archiving it in the panel) with one
   terse Notes line — never force a narrative or merge it into another
   card.

Your observations go in the insight's "What the samples show" block, worded
as your own reading ("Looking at the samples, …") so it never masquerades as
platform output. Confirming the platform's story is a valid, useful
observation — write it.

State observations about the GROUP, never about the rendered set. "Five of
the six images shown", "most of the samples on this card", "all six frames"
describe the report's layout; the insight has hundreds of members and the
card shows a handful of them. Counts come from `samples.csv`, which covers
every member; a visual pattern the csv cannot count gets named without a
number, and that gap is the argument for the metadata field you propose.

The samples you review arrive in affinity order, which is not a random draw:
the top of a cluster's ranking is its tightest knot, and one scene or one
capture session can fill it. So a trait's prevalence in what you viewed is
never evidence of its prevalence in the group. Promote an observation to a
plain group-level verdict ("the failures are night scenes") only on the
strength of a column measured over every member — a strong majority there
earns the verdict even when the images you saw are not unanimous. With no
column behind it, the claim stays scoped to the samples reviewed and comes
with the metadata field that would measure it next run.

Present what you add on its own merits — the method and the insight
("profiling the metadata shows…", "comparing the group's object sizes to the
full run…"), never as a gap report on the tool ("the platform didn't
surface…"). The reader needs your insight, not an attribution ledger; and
the attribution would be wrong anyway — the platform's insight surfaced the
group your analysis enriches.

## Pick the evidence — which visualizers make the card

An integration can declare many visualizers per sample (raw input, GT and
prediction overlays, instance crops, grids, bar plots, videos, 3D). The
card embeds the 1–2 that prove the insight's claim, chosen per insight:

1. **State the claim first.** What is this insight asserting is wrong? The
   selection criterion is then concrete — which visualizer lets a skeptical
   reader verify that claim with their own eyes? Not "most informative" in
   the abstract; informative *for the claim*.
2. **Audition, don't infer.** Open the top 2–3 samples in every
   primary-evidence candidate (for object detection that means both the
   instance-level and the whole-image views). Do not derive the choice from
   priors — latent-space granularity, metric signature, and metadata
   correlations only order which candidate you open first. The right
   visualizer is the one in which the samples' shared failure is visible
   when they sit side by side. This is image work, so it belongs in the same
   viewing agent that covers the insight's breadth (skill Step 5) — ask it
   which view makes the failure visible, then confirm on the samples you
   view yourself.
3. **Commit per insight.** One choice for every sample on the card —
   comparability across samples beats per-sample optimality. Supporting
   evidence (a confidence bar plot next to an instance crop) is a second
   slot, not a second story.
4. **Name the view, keep the reasoning internal.** The card names, once,
   what the chosen view renders in domain terms (from your visualizer
   one-liners, skill Step 5) — "ground-truth lanes over the camera frame",
   never a function name. Why this view was chosen stays out of the
   report: the samples either show the story on their own or the choice
   was wrong.
5. **Embeddability constrains, never decides silently.** Video and
   interactive 3D don't ship in static HTML: embed the best static proxy
   (a representative frame, a rendered view) and say the full version is
   one click away via the deep link.

The audition doubles as the coherence gate: when no visualizer at any
granularity makes the group cohere, that is the "no coherent story" path —
archive, don't force.

## The domain lens

You write as a colleague in the data's domain (skill Step 4 establishes it).
The lens changes four things:

1. **What you look for in samples**: domain-meaningful patterns, not generic
   visual attributes. X-ray: positioning, exposure, laterality markers,
   portable-vs-fixed acquisition. Aerial imagery: altitude, weather,
   time of day. Clinical text: section boilerplate, negation, abbreviation
   style. Reviews: sarcasm, mixed verdicts.
2. **How issues are named**: the field's terminology —
   "underexposed AP views", not "dark images".
3. **Action items**: the fixes practitioners in that field actually take
   (window-level augmentation for radiography, annotation-guideline passes
   for NER), not generic advice.
4. **Metadata proposals**: the domain's standard fields (view position,
   acquisition device, time of day) — these make the "add metadata"
   recommendations concrete.

Guardrails:

- **Data properties, never individual diagnoses.** The lens interprets
  acquisition, labeling, and distribution patterns; it never asserts what a
  specific sample clinically or factually shows ("several members look like
  portable bedside captures" — yes; a diagnosis for one image — never).
- Web-sourced knowledge follows the same confidence rules as everything
  else: hedge what you aren't sure of, and it never upgrades an unconfirmed
  root cause to a fact.
- The evidence discipline (composition first, interpretable-label gate,
  coherence gate) is unchanged — the lens shapes language and hypotheses,
  not the standard of proof.

## Latent space — what "similar" means for this group

Every insight carries `latent_space`: the representation in which its
samples clustered together. Always name it in the insight and translate it
in one line, because it tells the reader in what SENSE the group is a group:

| Name (typical) | Translation |
|---|---|
| `classification-semantic` | similar in the features that drive the model's class decision — the model treats these samples alike |
| `image-non-semantic` | visually similar (low-level appearance), regardless of class |
| `foreground` | similar main subject/foreground, background discounted |
| `balanced` | a general-purpose mix of semantic and visual similarity |

Unknown name → write "grouped in the project's '<name>' representation" and
move on; never guess. The latent space also colors the diagnosis: a cluster
in a non-semantic space is about appearance (corruptions, lighting, domains),
while a cluster in the classification-semantic space is about how the model
reasons (confusions, label boundaries).

## Action-item style

Write action items an ML engineer can execute this week: name the class /
metadata slice / count ("label the 240 platform-selected night-time samples",
"add a per-class weight of ~3× for `ship` in the loss"), not generic advice
("improve the data"). One insight → 1–3 action items, most-impactful first.
When the platform already computed the remediation (aggressor_fixing counts,
automatic_tests), cite its numbers instead of inventing new ones.

The evidence-field table above is for YOUR reading, not for quoting: the
report never shows raw payload fields, blob paths, or filter JSON. Lead every
insight with **what is going wrong** — what fails, how the model gets it wrong,
the likely root cause — and translate the platform's numbers into sentences
an outsider follows ("78% of the failing samples are night-time images vs
12% elsewhere").
