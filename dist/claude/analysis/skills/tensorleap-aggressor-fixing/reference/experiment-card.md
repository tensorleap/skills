# Experiment card template

Copy to `tensorleap/aggressor-fixing/<experiment>.md`. Fill sections 1-8
**before** the run and show them to the user; fill 9-10 after. Do not edit
1-8 after the run; if the plan changed, write a new card.

```markdown
# <experiment name>: <aggressor, one phrase>

Baseline version: <Tensorleap version name / serial>  ·  Insight: #<n>  ·  Date: <YYYY-MM-DD>
Parent run: <run name or "deployed checkpoint">  ·  Control: <run name>

## 1. Hypothesis
<one sentence: why this group fails>
Refuted if: <the result that would show the hypothesis wrong>

## 2. The group (fixed ids)
- Ids file: `tensorleap/aggressor-fixing/<experiment>.ids.csv` (written by `members.py` from `tensorleap-analysis/<version>`, insight #<n>)
- Level: sample | instance (training unit: image; scoring unit: instance and image)
- Members: <n> total · <n> train (used for the fix) · <n> validation (used to measure) · <n> test (not used)
- Labelling item from the inventory: <"label N selected samples (platform selection)" | none> · status: <done / pending / not available>

## 3. Target metric(s) (agreed with the user in Step 3)
| | Value |
|---|---|
| Metric(s) | <in the user's evaluation terms; scored by the user's eval code> |
| Operating point | <matched recall / matched threshold; the same rule for every run> |
| Baseline | <value> |
| Noise band | <seeds: min–max over n · bootstrap: 95% interval over validation, resampled by <cluster unit> · none: "single seed, unvalidated"> |
| Target | <value, beyond the band> |

## 4. Guard metrics (agreed with the user in Step 3)
| Metric | Baseline | Non-regression band | Gate split |
|---|---|---|---|
| <the user's global KPI> | <value> | <seeds: mean − 2·sd · bootstrap: lower bound of the 95% interval> | <validation / dev, not carved from train> |
| <neighbouring class / look-alike positives / inference cost> | | | |

## 5. Ruler status (Step 4, item 4)
| Axis | Trigger | Non-model-error share | Decides |
|---|---|---|---|
| Target | <none / which> | <share ± interval, or "not checked"> | <agreed metric on original labels / blind disagreement review> |
| Guards | <none / which> | | |

Review protocol, when it decides an axis: unit <sample / box / region> · items <disagreements between this run and its parent> · answers <clear / missing-label positive / not a positive / ambiguous> · adjudicated by <the skill; user sees the ambiguous residue> · reuse <image + item type + location at IoU ≥ 0.5>.

## 6. The one change
<what changes vs the parent, and nothing else>

## 7. Control
<same recipe without the targeted change (replay only); for a selection, a random selection of the same size>

## 8. Side-effect slices
- <neighbouring group / confused class / groups that failed the label check / look-alike positives>

## 9. Run log
| Run | Command | Seed | Checkpoint | Decode (thresholds, NMS, max det, image size) | Commit |
|---|---|---|---|---|---|

## 10. Results
| Run | Target metric (by instance / by sample) | Guards | Side-effect slices | Verdict (target / guards) |
|---|---|---|---|---|
| Parent | | | | — |
| Control | | | | |
| This run | | | | |

For a sample-level group fill one number; for an instance-level group fill
both (share of old instances still firing / caught, and share of member
images still failing).

- Decomposition of the global change (run vs parent): scoring pipeline <> · near-misses <> · label error <> · real <>
- Review verdict, where the ruler decides: <real hits / real false alarms vs the parent's curve, with interval>; agreed metric on the original labels beside it: <>
- Gain from the targeted selection (run vs control): <>
- Gain from the recipe (control vs parent): <>
- Caveats: <seeds, split, label-check failures>
- Next: <the follow-up this result suggests, one variable>
```

## Verdict rules

- **Failure mode solved:** the target in section 3 is reached and the gain
  is beyond the noise band. **Partial:** a real move (beyond the band)
  short of the target. **Not solved:** within the band. For an
  instance-level group both levels must agree; when they disagree (old
  instances gone, new ones appeared on the same images) the verdict is
  partial and the card says why.
- **Guards within band:** every metric in section 4 stays inside its band.
  None needs to improve. One guard regressed beyond its band fails the
  run whatever the target did.
- A run is a success when the failure mode is solved or partial **and**
  every guard is within band.
- Where section 5 names the review as the deciding measure, that axis's
  verdict comes from the review (above / on / below the parent's curve,
  with the interval); the agreed metric on the original labels is reported
  beside it and does not overrule it.
- A global change is read only after its decomposition line is filled;
  the regression that counts is the "real" part.
- With no noise band, say so in the verdict: "single seed, unvalidated
  against noise".
