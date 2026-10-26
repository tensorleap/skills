# Field lessons

What the rules in the skill came from. Three engagements: a classification
benchmark with planted aggressors, an object detector in production whose
main pain was false positives, and a public skin-lesion competition
(ISIC 2024). In each one an independent agent, without Tensorleap, analysed
the same model for comparison.

The detector and ISIC rounds were run **without labelling** to see what
training-side fixes alone could achieve. That was a test constraint, not
the product's rule: the skill recommends the platform's labelling selection
first and measures the training fixes on top of it.

## Planted-aggressor classification benchmark

- **Result:** given the Tensorleap aggressors, the agent fixed the
  watermark shortcut (random-erase the corner badge: badge crops 73% → 96%
  accurate, pool accuracy 86% → 93%), improved low-light images, and fixed
  the van / truck / car mix-up the user asked for.
- **The requested aggressor was solved cleanly** because it was picked for
  being a good fit. → Choose aggressors with the user and rank them by
  viability (Step 3).
- **Blur was named in the analysis and then forgotten.** The agent never
  came back to it. → Inventory every aggressor first, and ask about the
  remaining ones at the end (Steps 3 and 8).
- **Versus the independent agent:** it found the watermark, the
  animal-as-person confusion and the blur by looking at samples. The fixes
  were the same; Tensorleap added the low-light group.

## Object detector, false-positive pain

- **The first proposal was general**: oversample every false positive. It
  had little to do with the platform's analysis. → Fix the aggressor, not
  the symptom (Step 5's rule).
- **Experiment:** fine-tune on (A) all FPs, (B) only the Tensorleap FP
  aggressors, and a replay-only control. Round 1, unfrozen, drifted
  globally, even the control (0.061 → 0.066 false boxes per image).
  → Freeze first (Step 6).
- **Round 2, backbone and BN frozen** (false boxes per dev image / share of
  aggressors still firing):

  | Run | Global | Aggressors firing |
  |---|---|---|
  | Baseline | 0.061 | 0.52 |
  | Control (replay only) | 0.059 | 0.46 |
  | A: all FPs | 0.090 | 0.41 |
  | B: Tensorleap aggressors | 0.061 | **0.34** |

  - B fixed the aggressors with no global regression ("objects in hand"
    0.59 → 0.14, "holsters" 0.46 → 0.11).
  - About two thirds of the aggressor gain came from the targeted
    selection; the control alone reached 0.46. → Always run a control
    (Step 5).
  - The global gain came from freezing, not from the selection.
  - One group (bystanders' hands) did not improve in any run.
  - **Caution:** on groups that failed the label check (mostly unlabeled
    real objects), B dropped from 0.62 to 0.44: possibly the model learning
    to miss real objects. → Watch side-effect slices (Step 7).
  - Single seed, dev split only.
- **Versus the independent agent:** no overlap with the Tensorleap groups
  (max Jaccard 0.24).

## ISIC 2024 (public competition)

- Every insight was a training aggressor. The fix that worked was
  oversampling hard negatives to 25% of the training sample, which the
  independent agent also found. Tensorleap added a small consistent gain
  on top (+0.003 pAUC on the 4-seed mean, positive in all 4 paired seeds).
- **The threshold mattered first.** A missed-malignancy card said "24
  missed" at p = 0.5; at the operating threshold of 0.06 most were
  caught. The agent went for a general training fix before suggesting
  re-evaluation at the true threshold and adding metadata. → Cheap wins first (Step 2).
- **Judging by the global KPI alone hid a partial fix.** Gamma
  augmentation halved the dark-tile mechanism (logit shift +2.57 → +1.27,
  flagged share 40% → 35%) with pAUC within seed noise. A direction to
  continue, not a failure. → Two-axis success (the skill's top rules).
- **Noise from one seed.** A global floor was taken from the best of a
  recipe's 4 seeds; that recipe's other 3 seeds would have "failed" it.
  A small group swung 87% → 100% flagged across seeds. → Bands and targets from
  seed spread (Step 5).
- The agent retrained, re-evaluated, got new insights and planned the next
  run. Fine on a small dataset; on a large one, confine each run's
  evaluation to its fixed groups and validation set.
