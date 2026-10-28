# Measuring: checking the ruler and reading a global change

The ruler is everything between a model and its score: the labels of the
measuring set, the matching rule that decides whether a prediction counts
(a class match, a box at IoU ≥ 0.5, a mask overlap, a keypoint tolerance),
and the scoring pipeline (the user's metric code and the operating-point
matching). Each has an error of its own. A difference between two models
smaller than that error cannot be resolved by the agreed metric on the
original labels.

Aggressor fixing is more exposed to this than ordinary training: aggressors
and label noise co-locate, so the fix is measured exactly where the labels
are weakest. A model that is right where the label is wrong looks worse,
and in tasks whose metric charges both a miss and a false alarm for the
same prediction it is charged twice.

None of this costs anything when nothing is wrong. The check is tiered and
the review runs only on disagreements, only when the check failed.

## Checking the ruler (skill Step 4, item 4)

Under the agreed target and guard metrics, on validation, never test.

**Tier 0, always, free.** From the export alone:

- the share of the group's validation errors where the baseline is
  confident (confident errors concentrate label noise);
- the overlap with a mislabeled-samples insight, when the export has one;
- whether the samples are correlated (frames, clips, patients, sessions)
  and labelled per sample;
- whether the matching rule has a tolerance and the labels are known to be
  sparse.

No images looked at, no user involved. With no trigger the card says
"ruler: no trigger" and the check is over.

**Tier 1, a sample, when a trigger fires.** Triggers: a high confident-error
share; an expected effect within about two noise bands of the baseline;
correlated samples; a tolerant matching rule on sparse labels; and, after
a run, the symptom below. Take about 50 of the baseline's errors on the
group's validation members and 50 of its global errors, at the operating
point, and class each one:

| Class | Meaning | Example |
|---|---|---|
| Model error | The label is right, the prediction is wrong | A background patch scored as an object |
| Label error | The prediction is right, the label is wrong or missing | A visible object with no box; a class convention applied inconsistently |
| Matching-rule error | Prediction and label agree, the rule says no | A box at IoU 0.4 on the right object; a boundary outside the tolerance |
| Ambiguous | Cannot be called from the image | Occluded, truncated, out of focus |

The skill does the classing from the exported visualisations and crops and
reports the non-model-error share with a bootstrap interval and the count
of ambiguous items. The user is asked only about the ambiguous items, and
only when they are enough to flip the decision. For correlated data, also
check the neighbouring samples automatically: the same object labelled in
one frame and not the next is a label error, and the cluster unit becomes
the bootstrap's resampling unit.

**Decision, per axis**, comparing the non-model-error share to the noise
band of that axis's metric:

| Non-model-error share | What decides that axis |
|---|---|
| Well below the band | The agreed metric on the original labels |
| Comparable to or above the band, on the group's members | The blind disagreement review for the target metric; protocol on the card before the run |
| Comparable to or above the band, globally | Either widen the guard band and say so ("regressions under X% not resolvable"), or review a sample of the global disagreements |

The check gates the verdict, not the run: the protocol must be on the card
before the run; adjudication happens after it, on disagreements only.

## Reading a global change (skill Step 7, item 5)

Split the gap between the run and its parent into four parts, in this
order, and report how much each explains. Stop reading the gap as a
regression until the last part is known.

1. **Scoring pipeline.** Score the run under the parent's decode as well
   as its own (each run may tune its own post-processing on the gate
   split; what it adds is reported, not credited to the training change).
   Look for duplicate predictions on an already-matched object and for a
   confidence shift that is not uniform across groups; a uniform rescale
   cannot move the curve and matched recall already absorbs it.
2. **Near-misses under the matching rule.** The only prediction on a real
   object, but shifted: a box at IoU 0.3-0.5, a boundary outside the
   tolerance, the right class ranked second. Count them separately.
3. **Label error.** Missing, wrong or inconsistent labels among the
   disagreements; the review below decides them.
4. **What remains:** real new false alarms and real misses.

**Symptom.** When every variant moves by a similar amount, the control
included, suspect the ruler or the recipe before the selection. Measured
once: every fine-tune of a detector, replay-only control included, looked
1-4% worse than its parent on the original labels. That gap was the size
of the label error itself.

## The blind disagreement review

The deciding measure where the ruler failed. Do not relabel the set; label
only where the two models disagree, blind to which model made what.

- **Unit:** the task's prediction unit: a sample (classification), a box
  (detection), a region (segmentation), a keypoint.
- **Items:** a labelled object one model detects and the other misses; a
  prediction fired by only one model (no match at the rule's tolerance in
  the other); for classification, the samples the two models label
  differently.
- **Blind page:** the crop and the prediction, without which model made it.
- **Answers:** clear positive / real positive with a missing or wrong label
  / not a positive / ambiguous.
- **Score per model:** real hits = clear + missing-label positives; real
  false alarms = not a positive; ambiguous dropped.
- **Compare against the parent's own trade-off curve** (its threshold
  sweep), not one point: the fix wins when it sits above the curve, more
  real hits at the same real false alarms.
- **Interval:** bootstrap over items, resampled by the cluster unit when
  samples are correlated. Sample and scale up when there are too many
  items.
- **Reuse:** an answer is reused across comparisons when the image, the
  item type and the location (IoU ≥ 0.5) match, so each new model costs
  only its new disagreements.
- **Who adjudicates:** the skill, from the crops, for every item it can
  call with confidence; the user sees the ambiguous residue and the
  interval, not every item.
- Report the agreed metric on the original labels beside the review, and
  say which one decides.

Two similar models agree on most of their predictions. The disagreements
are a small set, and exactly the set that decides which model is better.

## Worked examples

**Detection, sparse boxes, video.** Validation and test left many real
objects without a box, and neighbouring frames had the object boxed in one
and not the other. Most of the baseline's "false alarms" at its operating
threshold were real objects with no label. Every fine-tune, control
included, looked 1-4% worse on the original labels; the blind review put
the aggressor-fixing model above the baseline's threshold curve on test
(slightly fewer real hits, far fewer real false alarms) and tied within
the interval on validation; the targeted aggressor cases dropped by about
40-45% on both. Cleaning the training labels (dropping frames with likely
missing boxes) did not on its own recover the original-label recall: the
measurement problem and the training-data problem are separate.

**Classification.** The same procedure with samples as items: the ruler
check classes the baseline's confident errors on the group; the review
shows the two models' disagreements blind and asks for the class. A
neighbouring-class confusion group usually fails the check on the label
convention, not on the image.

**Segmentation.** Items are regions where the two masks disagree beyond
the tolerance; near-misses are boundary disagreements inside it; the
review asks whether the region is the target class.

## Standing rules

- Never edit validation or test labels to fix the ruler. Change how you
  score, keep the agreed metric on the original labels beside it, and file
  the label errors you found as labelling items (skill Step 8).
- Cleaning training labels is a different problem from fixing the
  measurement; doing one says nothing about the other.
- Weakly labelled pools used as negatives contain real positives; filter
  them (drop the baseline's high-confidence candidates) before training.
- Post-processing belongs to the model and may differ per run; the scorer
  (labels, matching rule, metric code, operating-point matching) is
  identical for every run.
