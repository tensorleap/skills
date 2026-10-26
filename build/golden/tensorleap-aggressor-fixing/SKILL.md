---
name: tensorleap-aggressor-fixing
description: >
  Use when the user wants to fix the failure modes (aggressors) Tensorleap
  found, "fix this aggressor", "retrain to fix the cluster", "training
  recommendation", "how do I fix insight #N", "fine-tune on the failing
  group", or wants to go from a Tensorleap insights analysis to a controlled
  fine-tune / retrain experiment. Inventories every fixable aggressor, ranks
  them with the user, puts the platform's labelling selection first, picks a
  dedicated fix per aggressor, writes a pre-run gate (hypothesis, fixed
  member ids, a failure-mode metric agreed with the user, noise band,
  global non-regression band), runs one variable at a time against a
  control, and scores two axes: failure mode solved, global KPI not
  regressed.
group: tensorleap
---

# Fixing Tensorleap aggressors

You take the failure modes (aggressors) Tensorleap found for one evaluated
model version and turn them into controlled training experiments that fix
them. The platform tells you **which** samples fail together; you decide,
with the user, **which** of those groups to fix, **how** each one should be
fixed, and you prove the fix with an experiment whose pass/fail rules were
written down before it ran.

Two rules shape everything below:

- **Fix the aggressor, not the general problem.** A fix aimed at the
  platform's coherent group beats a fix aimed at the whole symptom. Measured:
  on a detector whose pain was false positives, fine-tuning on the platform's
  FP aggressors cut the aggressors' firing rate from 0.52 to 0.34 with no
  global regression; fine-tuning on *all* false positives cut it less
  (0.41) and raised global false boxes per image by about 50%. A general
  fix ("oversample every FP", "add more hard negatives") is the fallback,
  never the first proposal.
- **Success has two axes.** (1) The failure mode is solved, measured on the
  group's fixed member ids at a matched operating point, against a target
  set before the run. (2) The global KPI does not regress beyond its noise
  band; it does not need to improve. A fix that solves the failure mode
  without regressing is a success even when the global KPI does not move.
  Global ceilings ("fixing this cluster is worth +X") are information, never
  a reason to drop a failure mode.

Work from the user's training repo (the one with `leap.yaml` and the
training code). Experiment records go to `tensorleap/aggressor-fixing/`.
One script (python3, stdlib only) writes a group's member ids:

```
python3 scripts/members.py tensorleap-analysis/<version> --insight N --out tensorleap/aggressor-fixing/<experiment>.ids.csv
```

> `scripts/members.py` lives in this skill's own directory, NOT in
> the user's project; resolve its path relative to this skill file (an
> absolute path is safest) and never copy it into the project.

Record the run for Tensorleap at the start and the end: one anonymous
`skill_run` analytics event each (skill name, stage, outcome, duration;
`TL_DISABLE_ANALYTICS=1` turns it off), run with this skill's absolute path:

```bash
python3 <this skill's dir>/scripts/skill_run.py tensorleap-aggressor-fixing started
python3 <this skill's dir>/scripts/skill_run.py tensorleap-aggressor-fixing finished --outcome solved|partial|not_solved
python3 <this skill's dir>/scripts/skill_run.py tensorleap-aggressor-fixing failed --outcome <short_snake_case_reason>
```

## Step 0: Boundaries

State these to the user before anything else and keep them for the whole
run. Ask before crossing any of them.

| Allowed | Not allowed |
|---|---|
| Train and validation data | Touching the test set, for training, tuning, or "a quick look". The test set is scored once, by the user, at the end |
| Labelling: the platform's selected samples, the group's disputed labels, soft labels | Relabelling the measuring members yourself and then scoring on them |
| Re-weighting, re-sampling, augmenting, dropping, cropping or masking training samples | Moving samples between validation and train **without asking** (a re-split changes what "global" means and shrinks what can be measured) |
| Fine-tuning, re-training, freezing, changing the loss or the schedule | A new foundation model, CLIP, an external dataset or a new production dependency, unless the user approves it |

If the best fix needs something on the right, say so, name what it would
take, and keep it as a recommendation; do not fake it with something on the
left.

## Step 1: Load the analysis

You need the Tensorleap insights for the version the user wants to fix.

- If the cwd has a `tensorleap-analysis/<version>/insights.json` from the
  `tensorleap-analysis` skill, work from it (and its `report.md` when
  present).
- Otherwise run the `tensorleap-analysis` skill first, or at least its
  export and digest (`tl_export_analysis` + `tl_api.py digest`), so you have
  `insights.json` and each insight's `samples.csv`.

Per low-performance insight read: the group's members (`samples.csv` rows,
only `is_low_perf_root_member == True` when that column exists),
`summary.contrast`, `summary.split`, `is_train_aggressor`,
`overfitting_metrics` / `overfitting_evidence`, `mutual_info_elements`,
`aggressor_fixing` (the platform's labelling selection, with
`files.fixing_csv`), and `overlaps`. Read the other insight types too:
duplication, data leakage, mislabeled samples and domain gap feed the fixes.

**What a member is.** In classification a member is a sample. In detection
and segmentation with element instances on, a member is an instance: a
predicted box for an FP group, a ground-truth box for an FN group
(`builtin_instance_metadata_is_instance == "1"`, parent in
`builtin_instance_metadata_original_sample_id`). The **training unit** is
then the image the instance sits in; the **scoring unit** is both the
instance and the image (Step 7). `members.py` resolves instance rows to
their parent sample and keeps the instance name.

**What the platform's metric saw.** Read how the integration computes the
metric the group was flagged on (loss, a custom score, a thresholded
metric) and at which operating point. Groups are bounded by that metric: a
false-alarm score defined only for boxes under 0.65 confidence cannot
produce a high-confidence-FP group. Say in the inventory what the metric
could not see, so a group is not mistaken for the whole symptom.

Then read the training code: the model, the loss, the sampler, the
augmentations, the optimizer, the schedule, the batch size, where BN lives,
how a checkpoint is loaded, and how the validation metric and the operating
threshold are computed. You cannot propose a fix you cannot wire.

## Step 2: Cheap wins before any training

Check these first and propose any that apply as their own items. They
often change which aggressors are real.

1. **Operating threshold.** Is the platform's metric computed at the
   threshold the model is actually deployed at? A group flagged at the
   wrong threshold may vanish at the right one (a missed-malignancy group
   "missed 24" at p = 0.5 and caught most of them at the operating 0.06). If
   not, fix the integration's threshold and re-evaluate before anything
   else.
2. **Duplicates and leakage.** Duplicates in training are dropped; leaked
   pairs are moved to one side. These are allowed under Step 0 and change
   the baseline, so they come first.
3. **Missing metadata.** If a group's shared cue is visible but not in the
   metadata (a watermark, low light, blur, a source), propose adding it to
   the integration and re-pushing. The platform then names the cue itself.
4. **Inference-side probes.** A fix that needs no training (threshold per
   class, test-time crop or mask of a shortcut, resolution, tiling) both
   tests the hypothesis and may be the fix. If masking the watermark at
   inference flips the predictions, the shortcut is confirmed before any
   training.

## Step 3: Inventory every aggressor, rank, choose with the user

List **every** low-performance insight and sub-insight as a candidate, not
only the top one, in one table:

| # | Group (one phrase) | Members (train / val / test) | Contrast | Provisional route | Label first? | Fixable here? | Rank |
|---|---|---|---|---|---|---|---|

- **Group:** what the samples share, named after you looked at them (the
  analysis skill's Step 5), e.g. "corner watermark", "vans labelled car",
  "dark tiles", "objects in hand".
- **Provisional route:** one of *labels*, *coverage*, *shortcut / shift*,
  *model*, *threshold / inference*, from the platform's signals only
  (`is_train_aggressor`, `overfitting_evidence`, `mutual_info_elements`,
  overlaps with the mislabeled / duplication insights). It is a
  hypothesis, Step 4 tests it; no fix is proposed yet.
- **Label first?** When the insight carries `aggressor_fixing`, the first
  recommendation for that group is the platform's: label the
  `num_of_samples_to_label` selected samples (`files.fixing_csv`) and
  acquire the `num_of_samples_to_acquire`, with the counts. Same for a
  group whose members you would relabel. This is the data-side fix and it
  stays on the list whatever happens next.
- **Fixable here?** Under Step 0's boundaries: yes / needs labels / needs
  new data / needs a new model / not a real failure (contrast under ~1.2x).
- **Rank** by viability: a clear cue, enough members in train and in
  validation to train and to measure, and a route the training code can
  act on in one change. A group with no validation members
  cannot be measured: it ranks low unless the user allows a re-split.

Also cross-check cues across groups: if one group carries a cue (a
watermark in the right corner), check whether the same cue appears in the
other groups' samples. One shortcut often explains several insights.

Show the table and ask two things: **is labelling available in this
engagement** (then the labelling items run first, or in parallel, and the
training fixes are measured on top of them), and **which aggressors to
fix**, with your recommendation first. A group the user asks for by name
takes priority: those fits are where the targeted fix pays off most.
Groups not picked stay in the table and come back in Step 8.

## Step 4: Diagnose each chosen aggressor

Before training, try to disprove the hypothesis cheaply, in this order
(stop when one settles it):

1. **Labels first.** Look at 20 members next to 20 healthy neighbours.
   Count members whose label you would dispute. Never upweight a group
   before its labels are checked: upweighting noise teaches the noise.
2. **Train share.** `is_train_aggressor` true means the model fails on its
   own training samples: more data of the same kind won't help; labels,
   capacity or a targeted loss / weighting will. False means the group is
   underrepresented in training or overfit; see `overfitting_evidence`.
3. **Inference probe.** A threshold sweep on the group, a mask or crop of
   the suspected cue, a resolution change. Loss near normal but the
   thresholded metric bad means the decision layer, not the representation.

Then propose **one dedicated training fix per aggressor** from
`reference/solution-map.md`, the one matching the route that
survived: what this skill can do from the training side, with or without
the labels (oversample, upweight or target-augment the group's existing
training members, a loss term, a threshold). Use the other insights: a
mislabeled-samples overlap points at labels, a duplication overlap means
drop before you reweight. When labelling is costly this run doubles as the
**sensitivity test**: run it first, and its result says whether the labels
are worth buying. If the group responds, buy them; if it does not move,
more data of the same kind is unlikely to help and the route is elsewhere
(labels, model, threshold). Show the fix to the user before writing the
gate.

## Step 5: Write the pre-run gate

For every experiment, write the card from
`reference/experiment-card.md` to
`tensorleap/aggressor-fixing/<experiment>.md` **before** the run, and show
it to the user. It fixes:

1. The hypothesis, in one sentence, and what result would refute it.
2. The group's **fixed member ids**, written by `members.py` from the
   baseline version's export (never the re-clustered insight of the new
   version). The training members are the ids used for the fix; the
   validation members are the ids used to measure. Results on training
   members alone are never reported as the result. If the user allows a
   re-split, keep a measuring share in validation; moving every member
   into training leaves nothing to measure.
3. **The failure-mode metric, agreed with the user.** Propose the
   candidates for this group, in the user's own evaluation terms, with a
   recommendation: "recall on the members at the deployed threshold looks
   like the fit; alternatives are false boxes per member image, or
   per-box precision at matched recall. One, or several?" One or several
   can be targets. Each is scored by the user's evaluation code (build it
   when none exists), never by re-reading the platform's metric. Fix the
   operating point (same recall or same threshold for every run), the
   baseline value, the noise band and the target.
4. The global KPI, its baseline value and its non-regression band.
5. **The noise band**, for both axes, by the first of these that is
   affordable: (a) the baseline recipe retrained on 2-3 seeds, which gives
   the spread of the global KPI and of the group's own metric (small
   groups are noisy: one swung from 87% to 100% flagged across seeds of
   the same recipe); (b) a bootstrap interval over the validation
   samples, free, computed from the baseline's predictions; (c) none, in
   which case every verdict carries "single seed, unvalidated against
   noise". A floor taken from one lucky seed turns noise into a verdict.
6. The one variable this run changes, and its parent run.
7. The control: the same recipe without the targeted change (replay only),
   and, when the fix selects samples, a random selection of the same size.
   The cluster was picked because it scored badly; retraining noise alone
   moves it.
8. Side-effect slices to watch: neighbouring groups, the classes the fix
   pushes away from, any group that failed the label check.

## Step 6: Run

- **Fine-tune before retraining.** Start from the deployed checkpoint;
  retrain from scratch only when a fine-tune cannot express the fix (a new
  input, a new head, a new augmentation the backbone must see from
  epoch 0).
- **Freeze first.** First fine-tune with the backbone frozen and every
  batch-norm layer in eval mode (statistics and affine frozen), training
  only the neck and head. Unfreeze only if that cannot move the group.
  Unfrozen BN re-fits its statistics to the new batch mix and drifts the
  whole model: measured, an unfrozen fine-tune regressed even the replay
  control, and freezing removed the regression.
- **Check BN against the batch regime.** If the fix changes the batch
  composition (oversampling, a dedicated sampler) or the batch size, and BN
  trains, confirm the running statistics still match the evaluation data.
- **Replay always.** Never fine-tune on the group alone; mix it into
  ordinary training data.
- **One variable per run**, compared to its parent. Do not stack fixes in
  the first run.
- **Targeted selection.** Upweight / oversample the group's training
  members (de-duplicated), not every sample sharing its symptom. For an
  instance-level group the selected unit is the image; an FP group's
  instances have no label to upweight, they are hard negatives on those
  images.
- Log the exact command, seed, checkpoint and commit in the card.

## Step 7: Evaluate

On validation (or the dev split the user names), never the test set:

1. Score the agreed failure-mode metric on the fixed member ids at the
   matched operating point, for the run, its parent and the control.
2. **Instance-level groups are scored twice.** The new model produces its
   own instances, so the old member ids are not in its output. By
   instance: match the old members into the new predictions (an FP member
   still fires when a new box overlaps it at IoU ≥ 0.5 above the operating
   threshold; an FN member is caught when its ground-truth box is now
   detected) and report the share still firing / now caught. By sample:
   the share of member images that still have any false box, or any
   missed object. The sample level catches new instances the fix created
   in the same images, which the instance match cannot see. Both go on the
   card.
3. Score the global KPI against the band.
4. Score the side-effect slices. A drop on a group that failed the label
   check may be the model learning to miss real objects: report it as a
   caution, not a win.
5. Verdict per axis, from the card's rules: solved / partial / not solved;
   within band / regressed. Partial counts: a mechanism halved with the
   global KPI flat is a direction to continue, not a failure.
6. Fill the card's results table. Say what part of the gain the targeted
   selection explains (run vs control) and what part the recipe explains
   (control vs parent).

Optionally push the new model as a new version and re-run the analysis:
the old group should be dissolved and no comparable group should appear
nearby. Keep scoring the old fixed ids either way.

## Step 8: Report and follow up

Write `tensorleap/aggressor-fixing/summary.md`:

- Per aggressor: hypothesis, fix, the two-axis verdict with numbers,
  control, noise band, caveats.
- **Hypotheses this run confirmed or refuted**, and the follow-up runs each
  result suggests (one variable each).
- The inventory table from Step 3 again: the labelling items still open,
  the aggressors not yet tackled, and what each needs.

Then **ask the user** whether to continue: the next follow-up for an
aggressor in progress, or a new aggressor from the table. Do not stop
silently with fixable groups left over, and do not start a new one without
asking.

Field evidence behind these rules: `reference/field-lessons.md`.
