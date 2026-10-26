# Experiment card template

Copy to `tensorleap/aggressor-fixing/<experiment>.md`. Fill sections 1-7
**before** the run and show them to the user; fill 8-9 after. Do not edit
1-7 after the run; if the plan changed, write a new card.

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

## 3. Failure-mode metric (agreed with the user)
| | Value |
|---|---|
| Metric(s) | <in the user's evaluation terms; scored by the user's eval code> |
| Operating point | <matched recall / fixed threshold; the same for every run> |
| Baseline | <value> |
| Noise band | <seeds: min–max over n · bootstrap: 95% interval over validation · none: "single seed, unvalidated"> |
| Target | <value, beyond the band> |

## 4. Global KPI
| | Value |
|---|---|
| Metric | <the user's KPI, on validation> |
| Baseline | <value> |
| Non-regression band | <seeds: mean − 2·sd · bootstrap: lower bound of the 95% interval> |

## 5. The one change
<what changes vs the parent, and nothing else>

## 6. Control
<same recipe without the targeted change (replay only); for a selection, a random selection of the same size>

## 7. Side-effect slices
- <neighbouring group / confused class / groups that failed the label check>

## 8. Run log
| Run | Command | Seed | Checkpoint | Commit |
|---|---|---|---|---|

## 9. Results
| Run | Failure-mode metric (by instance / by sample) | Global KPI | Side-effect slices | Verdict (FM / global) |
|---|---|---|---|---|
| Parent | | | | — |
| Control | | | | |
| This run | | | | |

For a sample-level group fill one number; for an instance-level group fill
both (share of old instances still firing / caught, and share of member
images still failing).

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
- **Global within band:** the KPI stays inside section 4's band. It does
  not need to improve.
- A run is a success when the failure mode is solved or partial **and**
  global is within band.
- With no noise band, say so in the verdict: "single seed, unvalidated
  against noise".
