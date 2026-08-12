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
| `n_samples` | Cluster size. | "4,772 samples (9% of the population)" |
| `severity_metrics` | Per-metric `{metric_name, value, normalized_value}`. | "loss 2.1× the population median" |
| `metrics_info` | Per-metric statistics inside the cluster. | Table of mean/median per metric |
| `mutual_info_elements` | What characterizes the cluster: `features[{feature_name, feature_value, direction}]`, `value_in_cluster` vs `value_outside_cluster`, `score`, `is_unique` | "dominated by `weather=night` (78% in-cluster vs 12% outside)" — this is the core of the story |
| `display_filters` | The dashboard filters that reproduce the cluster. | Include verbatim so the user can open it in the UI |
| `automatic_tests` | Suggested regression tests `{test_name, filter, metric_name, metric_value, operator}`. | Offer as "add this as a platform test" |
| `latent_space` | Which latent space produced the cluster. | Context only |
| `top_panel.summary` | Ready-made `title` + `sentence` (low_performance only). | Use as the section heading/lede |

## low_performance — decision tree (aggressor fixing)

Run these checks in order for every low-performance insight (and any
subinsight you elaborate). Each check names the payload fields that answer it.

**0. Is the extended metadata population failing too?**
Look at `mutual_info_elements`: the features characterizing the cluster define
a metadata population wider than the cluster itself.
- Failing as well → note in the report: *the whole metadata population is
  failing, not just this cluster — don't analyze the cluster/population diff.*
- Not failing → the diff is informative: analyze what separates the failing
  cluster from the healthy rest of its population (domain-gap-style
  comparison over the metadata).

**1. Can more data solve it?** Applies when the cluster is NOT dominated by
training data: train-sample share < 40% of the cluster, or the cluster's
training samples are < 5% of all training data (`is_train_aggressor`,
`cluster_extended_stats`, and the per-state breakdown in `samples.csv`).
If there are no (or a distinct minority of) test/val samples in the cluster,
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
    (MAD-robust contrast between the extended cluster's training samples and
    the core cluster; flagged above 2.0). If flagged → present the evidence
    (extended vs core comparison) and recommend **balancing the dataset —
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
  single-population story (severity as tiebreak); the other gets an appendix
  line saying what it shares with the kept one and its # in the Insights
  panel.

Write the difference as the insight's own content, never as commentary on
the report's structure ("what makes this group its own insight…", "unlike
insight #2…") — state the facts and let the distinction be self-evident.
Top-level insights are disjoint groups by definition, so never present
non-overlap as a discovery or a differentiator.

## Look for yourself (mandatory per insight)

The platform clusters by metrics, metadata and embeddings — it cannot read
an image or a sentence. You can. For every insight you write up, open its
top samples and ask:

1. **Does the content contradict a label or metadata value?** Two
   preconditions before claiming a mislabel / wrong metadata:
   - the value is **human-interpretable** (a word: "blond", "positive", a
     readable class name). Opaque ids (class 3, an encoded value) are
     uninterpretable — a human couldn't judge them from the sample either,
     so make NO claim and say the labels aren't judgeable from outside;
   - the sample **visibly contradicts** it (a dark-haired face where the
     value says blond; a hostile review where it says positive).
   When both hold, report it per sample ("sample X's tag says blond, the
   image shows dark hair") and only generalize as far as you actually
   checked. A metadata tag disagreeing with the image is a metadata
   problem; call it a *label* problem only when it's the label itself.
2. **What do these samples share that the metadata can't express?**
   Lighting, pose, occlusion, background clutter, image quality, phrasing
   style, topic. If you spot one, name it AND recommend adding it as a
   metadata field — that makes the pattern trackable in the platform from
   the next run on.
3. **Do the members actually belong together?** If the composition is
   diffuse, the worst samples have nothing visible in common, and the
   metadata story is weak, the insight fails the coherence gate: drop it to
   the appendix rather than forcing a narrative or merging it into another
   insight. A real signal buried in a dropped insight (e.g. its tail
   over-represents a tagged subgroup) still gets its one appendix line.

Your observations go in the insight's "What the samples show" block, worded
as your own reading ("Looking at the samples, …") so it never masquerades as
platform output. Confirming the platform's story is a valid, useful
observation — write it.

Present what you add on its own merits — the method and the insight
("profiling the metadata shows…", "comparing the group's object sizes to the
full run…"), never as a gap report on the tool ("the platform didn't
surface…"). The reader needs your insight, not an attribution ledger; and
the attribution would be wrong anyway — the platform's insight surfaced the
group your analysis enriches.

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
insight with the **failure mode** — what fails, how the model gets it wrong,
the likely root cause — and translate the platform's numbers into sentences
an outsider follows ("78% of the failing samples are night-time images vs
12% elsewhere").
