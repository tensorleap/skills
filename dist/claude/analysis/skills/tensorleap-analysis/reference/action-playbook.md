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

## Action-item style

Write action items an ML engineer can execute this week: name the class /
metadata slice / count ("label the 240 platform-selected night-time samples",
"add a per-class weight of ~3× for `ship` in the loss"), not generic advice
("improve the data"). One insight → 1–3 action items, most-impactful first.
When the platform already computed the remediation (aggressor_fixing counts,
automatic_tests), cite its numbers instead of inventing new ones.
