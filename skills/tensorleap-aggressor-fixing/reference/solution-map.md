# Solution map: cause → first fix → second → escalation

Pick the row whose cause survived Step 4's checks. Try the **first** fix
first; go to the second only when the first was run and failed its gate.
If a fix fails, go back to the hypothesis, not sideways to a more exotic
method. Fixes marked *(needs approval)* cross a Step 0 boundary.

## Routes

| Route | Signals | Start by testing |
|---|---|---|
| **Labels** | Members you would relabel; overlap with a mislabeled-samples insight; high-confidence "errors" whose label doesn't fit the image | Soft-label / drop / down-weight the disputed members, then rerun the baseline |
| **Coverage** | `is_train_aggressor` false; few training members; overfitting flagged | Rebalance toward the group's training members, plus augmentation that varies the concept |
| **Shortcut / shift** | A cue shared by members and unrelated to the label (watermark, background, source, lighting); masking it at inference changes the prediction | Hide or randomize the cue in training (crop, inpaint, random erase); augmentation matched to the shift |
| **Model** | `is_train_aggressor` true, labels fine; group fails on its own training samples | Head-only retrain with the group upweighted, then targeted loss / weighting, then capacity |
| **Threshold / inference** | Loss near normal, thresholded metric bad; group routable at inference by class, size or a production field | Threshold sweep on the group; per-class threshold; resolution / tiling / crop change |

## By cause

| Cause | First | Second | Escalation |
|---|---|---|---|
| Label noise | Soft-label / drop / down-weight disputed members | Staged training: noisy data first, clean data last | Dynamic confident-disagreement filtering *(needs approval if it relabels)* |
| Sparse annotation (FPs are real unlabeled objects) | Mask unlabeled regions out of the loss | Pseudo-labels from dense sources, validated on a dense reference | Complete the labels *(needs approval)* |
| Underrepresentation | Oversample / upweight the group's training members (de-duplicated) | Targeted augmentation of the concept | Targeted collection or synthetic data *(needs approval)* |
| Low diversity (many near-copies) | Drop duplicates, then targeted augmentation | Oversample the distinct members only | Collection *(needs approval)* |
| Overfitting | Augmentation + early stop on the group | Group-aware split / dedup *(needs approval for a re-split)* | Replay + distillation anchor |
| Shortcut | Crop / inpaint / random-erase the cue | Counterfactual / copy-paste the cue onto other classes | Group DRO / JTT |
| Domain or acquisition shift (lighting, sensor, source) | Augmentation matched to the shift (brightness / gamma / blur / noise) | Target fine-tune + replay on the shifted training members | CORAL / DANN |
| Pipeline-induced shift | Fix the preprocessing, rerun the baseline | — | — |
| Neighbouring-class confusion | Check the labelling convention in train, make it consistent (soft labels) | Upweight the confused pair; class-pair margin loss | Split / merge the class *(needs approval)* |
| Class imbalance | Class-balanced / focal loss, or prior correction at inference | Per-class threshold | Dedicated stage |
| Decision layer (representation is fine) | Head-only retrain on a balanced set with the group upweighted (DFR) | Fine-tune neck + head, backbone frozen | Full fine-tune |
| Capacity | Train longer (group train loss still falling) | Specialist head for the group | Larger backbone |
| Scale | Inference at another resolution / tiling | Fine-tune at the new resolution | Different stride / backbone |
| Miscalibration / threshold | Threshold sweep, temperature | Per-group threshold, only if the group is identifiable at inference | Shift-aware calibration |
| Cue absent from the image, present in metadata | Add the metadata as a model input (with a missingness flag) | — | Abstention on the group |

## Detection ladders

Entry signal for both: the cluster is flagged on a thresholded metric while
its loss is near normal. Members are instances (boxes): the training unit
is the image they sit in, the scoring unit is the instance and the image
(skill Step 7).

**FP cluster**, in order:

1. **Sparse annotation:** are the FPs real unlabeled objects? Look at 20.
   If yes, mask them out of the loss; never raise the threshold over a label
   problem.
2. **Threshold:** background scored just above τ. Sweep τ up; per-class τ
   when the cluster is one predicted class.
3. **Tiling / context:** tiles without full-image context. Inference change.
4. **Shortcut:** an irrelevant feature triggers the box. Copy-paste /
   counterfactual augmentation.
5. **Neighbouring class:** a real object of a nearby class, counted as
   FP + FN. Labelling-convention route.
6. **Training fallback:** head retrain with the cluster's training members
   as weighted hard negatives, backbone and BN frozen. Watch recall: one
   copy-paste fix cut FPs 130 → 11 and true positives 321 → 206.

**FN cluster**, in order:

1. **Threshold:** objects scored but below τ. Sweep τ down; per-class or
   per-size τ when routable.
2. **Prior / scale:** no score at any τ, the cluster correlates with object
   size. Re-run at another resolution / with tiling first.
3. **Head retrain with cluster weights,** backbone frozen. Closes the gap →
   a decision-layer problem, ship the head. Doesn't → representation.
4. **Full reweight / fine-tune.** Watch global precision: lifting these
   scores also lifts background that shares their features.

## A per-group fix is deployable only if the group is identifiable at inference

By predicted class, by predicted geometry, or by a field available in
production. Latent-space membership alone is not available at inference.
Without a routing key, the choice is a global change shown with its global
effect, or a training fix.

## Not first steps

- A general fix for the whole symptom (all FPs, all hard negatives) before
  the platform's groups have been tried.
- Upweighting before the labels were checked.
- Fine-tuning on the group without replay.
- Unfreezing the backbone / BN before a frozen run was tried.
- Stacking several changes in one run.
