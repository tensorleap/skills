<!-- BEGIN TENSORLEAP SKILL: tensorleap-aggressor-fixing -->
<!-- Tensorleap skill 'tensorleap-aggressor-fixing' v0.1.0, generated from skills/tensorleap-aggressor-fixing/skill.md; do not edit here. -->
# Fixing Tensorleap aggressors

## Flow

| # | Step | User |
|---|---|---|
| 0 | Preflight: the leap CLI is the latest | upgrades if asked |
| 1 | Read | |
| 2 | Check the analysis | |
| 3 | Intake: findings and questions | asks |
| 4 | Inventory | |
| 5 | Drop | |
| 6 | Look, then rank | |
| 7 | Plan: targets and actions | approves |
| 8 | Run: train, check, decide the next step | approves each next step |
| 9 | Report | reads |

Reference A (levers) and Reference B (noise and wins) are looked up while
ranking, planning and judging results.

## Starting point

- The user already did what they could. The only reference is the model
  (or models) they bring.
- Aim for a win clearly larger than seed noise. A win just above noise is not
  worth presenting.
- Test data never enters training. Insights on test data pushed to
  Tensorleap may be used. Every number is compared against a noise reference
  and stated with its confidence.
- You write a plan, the user approves it, then you train.
- Numbers marked "about" are defaults: change them when the case calls for
  it and give the reason in the plan. The points above do not change.

## Words to use with the user

In user messages, use the plain wording below. Name a Tensorleap term
only once, at first use, and only when the user will see it in the
platform. Internal terms never reach the user.

| Our term | What we say to the user |
|---|---|
| Failure mode / aggressor | "A group of samples the model gets wrong for a shared reason." First use: "Tensorleap shows these as *failure modes*." |
| Insight | Platform term; name it once: "Tensorleap's *insights* (the groups it found)". |
| Training aggressor | "Most of this group is already in your training data, so adding more of the same data probably won't fix it." |
| To-label list | "Samples from your unlabeled pool that look like this group; labeling them should help the model here." |
| Label Next | First use: "Tensorleap's *Label Next*: samples picked to cover what your training data is missing, not tied to one failure." |
| Hybrid | "Both: the samples for these groups plus some general-coverage picks." |
| Lever | Never said. Name the action: "fix labels", "tune the threshold", "add data", "retrain with…". |
| Guard | "Your overall score must not drop." |
| Noise / yardstick | "How much the score moves when the same model is simply retrained. A real gain has to be clearly bigger than that." |
| Fetch similar | Never said. Describe it: "more samples like these". |
| Paired bootstrap / confidence | Say how sure we are in plain words that fit the case. |

## 0. Preflight

The Tensorleap tools reach you through the leap CLI (`leap mcp`), so the CLI
must be the latest release.

1. Compare the installed CLI with the latest release:
   `leap --version` and
   `curl -s https://api.github.com/repos/tensorleap/leap-cli/releases/latest | grep -m1 '"tag_name"'`.
   - `leap` missing: tell the user to install it
     (`curl -s https://raw.githubusercontent.com/tensorleap/leap-cli/master/install.sh | bash`),
     then restart their assistant so it loads the Tensorleap tools. Stop.
   - Older than the latest: tell the user which version they have and which
     is the latest, and ask them to upgrade (`leap cli upgrade` prints the
     command), then restart their assistant. Below v0.0.163 there is no
     `leap mcp` at all: stop. Otherwise continue on the installed version
     if they prefer, and say that some tools may be missing.
   - The latest release can't be read (offline): say so and continue.
2. Run `bash .tensorleap/scripts/self_update.sh`. Exit 0: nothing to do. Exit 10:
   the skill's files were just refreshed; re-read this file and start over.
   Relay any `NOTE:` line to the user.

## 1. Read

1. The user's model version on Tensorleap (`tl_status`, `tl_list_projects`,
   `tl_list_versions`).
2. The analysis export, `tensorleap-analysis/<version>/`. If it does not exist,
   run the `tensorleap-analysis` skill first.
   - Work from `insights.json` and the `summarize` output. They hold every
     insight and sub-insight.
   - The written report is screened (at most 6 cards). Read it, but never
     treat it as the list of insights.
   - The report's action items are input. The lever order (Reference A)
     decides.
3. The platform directly, for what the export does not answer: `tl_query`,
   `tl_list_samples`, `tl_view_samples`, `tl_get_integration_code`.
4. The lessons in `.tensorleap/reference/lessons/`, if any are installed. They are hints
   with a stated strength, never rules.
5. What an evaluation costs: the duration of the user's evaluation job
   (`tl_list_jobs`). Over about half a day is expensive. This decides how
   hard you work to avoid a re-evaluation (section 2) and when you stop
   (section 8).

If the server withholds data (Free Tier, or AI access turned off), say what is
missing and never rebuild it another way.

## 2. Check that the analysis can be trusted

Run these before the inventory. A problem here is not a stop. Report it in the
intake message (section 3): what you found, how you are working around it, and
what a re-evaluation would add.

Before asking for a re-evaluation, try to work around it: re-score from dumped
predictions, run filtered insights, or change the insight settings. Ask for a
re-evaluation freely when evaluation is cheap (section 1). When it is
expensive, work around it and say what the workaround cannot cover.

- **Insight runs on filters.** Always run an insight run that excludes
  training (validation only) yourself; it is usually cheap. Take the best
  insights from both runs, the default and the validation-only one, into the
  inventory, and note which run each came from. The default run also gives
  the training-aggressor column. You may also run insights on other metadata
  divisions that look worth separating.
- **Partial analysis.** Compare the sample counts on the platform with the
  user's full training and validation sets. If only a subset was
  analyzed, say so and ask how it was drawn. Recommend analyzing all of
  train only if you think it will add value, and say what it would add.
  Training-aggressor shares are shares of the analyzed set, and picks can
  only resemble groups that set holds.
- **Insight metrics against the user's KPI.** Not a routine step; it can
  also come up later, while looking at samples. Signs: members' predictions
  look right on the samples; many members sit just under a threshold; the KPI
  has no single threshold (AP, AUC) while the insight metric has one; the
  user scores after a post-processing step the integration lacks.
  - Read from the integration code how the metrics are computed: threshold,
    post-processing, matching rule, score range, one setting for all
    conditions or several. This reading is the prepared answer for the
    intake (section 3), where the user confirms or corrects it.
  - After the intake, before section 4: where the user's way differs,
    re-score the group's members their way from dumped predictions.
  - A group that no longer fails there is a metric artifact: keep it out of
    the targets and say so.
  - A group that a threshold change recovers goes to A.2 A.
  - If the user has not fixed an operating point, report each target at
    two or three candidate settings.
  - If many groups change and re-scoring cannot cover them, ask for a
    re-evaluation with the user's settings before planning, weighed by
    its cost (section 1).

## 3. Intake: findings and one round of questions

One message, after sections 1 and 2. Open with what section 2 found and how
you are working around it. Then ask.

Ask as multiple-choice questions (the `AskUserQuestion` tool where it exists,
at most four per call, so split into two calls; otherwise numbered options in
text), your recommendation first. Prepare each answer before asking, so the
user mostly confirms. Ask again later only when a finding makes a new
question necessary, and say which finding.

- **Scoring and deployment.** Read the KPI, threshold, post-processing and
  matching rule from the integration code and offer them as the answer: "you
  score with X at threshold Y; is that how you deploy?"
- **Fine-tune or from scratch.** Recommend the one that fits what you see:
  their training code, how they retrained before, how large the data change
  will be. Give the reason; there is no default.
- **Another seed or an earlier run of the same recipe.** Only they know. Say
  the fallback: without one you use a bootstrap, which is rougher
  (Reference B).
- **Labeling budget.** No targets are chosen yet, so anchor it on the
  platform's to-label total across all failure modes: "Tensorleap suggests
  about N samples to label across the problem groups it found. Can you label
  N, fewer, or more?" The plan (section 7) splits the budget by target.
  If more is possible and there is unlabeled data, offer to add Label Next
  picks. If you believe the hybrid suits this case better, recommend it
  and say why (A.3).
- Only if section 2 found it: how the analyzed subset was drawn, and whether
  all of train can be analyzed.

## 4. Inventory

One row per insight:

| Insight | Type | Group size | Worse than all data (x) | Training aggressor, share of training | Platform to-label count | Label problem? | Over-represented metadata |
|---|---|---|---|---|---|---|---|

- Quote `groupSize` and the platform's `aggressor_fixing` counts as given.
- **Check the wider metadata slice.** Query the metric by the over-represented
  fields (`tl_query`): is the whole slice failing, or only the cluster?
  - Whole slice failing: the target may be the slice, not the cluster. Find
    what separates this cluster from the rest of the slice; that difference
    is the lead for a fix.
  - **Compare with the same content, not only with all data.** Compare the
    group with non-members of the same content (class, object count or size).
    If it is not worse than those, it is hard content, not a failure mode:
    rank it lower. This is common in segmentation, with raw pixel counts and
    with Dice-like rates alike.
  - One value stands out (a patient, a camera, a source): check whether it is
    mislabeled, wrong or simply different before treating it as a model
    failure.
  - **A block that scores near zero: open its own samples.** When one value,
    or one value × class block, scores near zero, look at members of that
    block, not at samples from elsewhere in the cluster. Check that the
    labelled target is visible and plausible before calling it a model failure
    or a data gap.
  - If the label cannot be confirmed from the input (no visible object, a
    different object, one label stamped on a whole burst), send the block to
    A.1. Keep it out of the targets, and report the group's score with and
    without it.
  - Verify on samples before naming a cause.
- **Other insight types, if present.** Current evaluations produce Failure
  Modes; other types appear only in some runs. Out-of-Distribution (from an
  unlabeled analysis) and Domain Gap (when requested in the insight settings)
  point to more data (A.3). Duplication or Data Leakage across splits inflates
  the KPI every comparison rests on: raise it to the user.
- **Overfitting flag** (`overfitting_metrics`). Name the kind and choose by it:
  a split imbalance is a recommendation to the user (we do not touch
  test); a lack of variation may be answered by augmentation or more data.

## 5. Drop

- Groups under about 1.2x worse than all data on the user's KPI, as targets
  for that KPI. A group that fails on another metric can still move the
  model forward: keep it and say which metric it fails on. Under 1.2x
  against samples of the same content usually means the metric is at fault,
  not the model.
- Parents fully covered by their sub-insights.
- Groups where you cannot point to a single cause in the samples or the
  metadata. Mark them "no single cause I can point to", and do not make
  them targets on their own. On one benchmark (NICO) the to-label lists of
  such groups came out close to the pool's base rate; check a list before
  using it.

Groups too small to show a win above noise on their own (wide bootstrap
interval on the user's model) are not dropped. Several failure modes are
usually fixed together, and their samples may still be worth adding. Judge
them with the targets they are fixed with, and say that their own score
cannot show a win.

## 6. Look, then rank

- Look at samples of each remaining insight before assigning a lever. Check
  labels first. Follow the analysis skill's rules for looking: count the
  composition before describing the tail, remember the top-ranked samples are
  not a random draw, and judge a label only when it is human-readable.
- Rank by lever order (Reference A), then by share of total error.
- You may unify insights you believe share one cause, on a shared metadata
  slice or what the samples show. Count them as one target and list the
  original ids.

## 7. Plan: targets and actions

One message, approved once. Open with your proposed targets and get them
confirmed together with the plan:

- the target insights. There is no set number; targeting all of them
  together is a valid choice,
- the metric and the guard (the global KPI must not drop),
- whether any group or slice matters more to the user than its share of
  the error suggests.

Then the plan as a table, one row per target:

| Target | What we will change | Data (platform pick / agent pick) | Expected gain against noise (Reference B) | Cost (training + evaluation time) | What we need from you |
|---|---|---|---|---|---|

- Below it, list the insights you did not choose, one line each on why.
- Cost: from the evaluation time (section 1) and the way you will train. A
  fine-tune is usually cheaper than a from-scratch retrain; when the user
  has not chosen, give both.
- Then ask once, as multiple choice: approve the plan as a whole, change it,
  or go through it failure by failure. On a change, update the table and ask
  again. Failure by failure: one target at a time, each approved, changed or
  dropped. If nothing is left, use the fallback below.

State which one you ran, fine-tune or from scratch; a fine-tune result is not
verified for a from-scratch retrain, and the other way round.

**When failure modes were found but no way to fix them can be argued with
confidence**, there is still a plan. First say what the gap is, as far as
the evidence shows. For example: the pool may hold too few samples like the
failing ones (more targeted unlabeled data is needed), the model may lack the
capacity to separate them, or the labels may not be learnable from the
input. Say what you checked and what you could not. Then say what is still
on the table: Label Next (if there is an unlabeled pool and a labeling
budget), and global post-processing (A.2 A) if you think it may work in this
case. Say plainly that these aim at a global-only win (Reference B), not at
the failure modes. Stop only when neither applies.

## 8. Run: train, check, decide the next step

1. **Train** as approved: fine-tune or from scratch as agreed, with the new
   to-label picks weighted (A.3).
2. **Check it yourself before Tensorleap.** Score the global KPI and each
   target group, on its members as defined on the user's model, against
   the user's model on the same validation set, with the noise reference
   (Reference B).
3. **Decide the next step and get the user's approval** (multiple
   choice, your recommendation first): another training of the same fix, a
   different fix, or a Tensorleap evaluation of this model. Give the check's
   numbers with it.
4. **After a Tensorleap evaluation:** push the model as a new version of the
   same project, evaluate it with the user's settings, and repeat the
   filtered insight runs of section 2. Match the new insights to the earlier
   targets by members, not by number. When a group returns with most of its
   members after a fix aimed at it, consider why before choosing again. Look
   at its remaining members. It may be a label problem (A.1), picks that were
   too few or sampled too lightly, or a group that more data will not move.
   Use your judgment and give the reason. The next round starts at section 4
   on the new version's insights. No new intake; ask only if something
   changed (the labeling budget is used up, for example).

**When to stop.** After each training, if anything moved, another round is
justified. Moved means the global KPI improved beyond the noise reference
(Reference B), or a target group's failing members clearly dropped (about
10 % or more of them now pass). If nothing moved:

- evaluation cheap (section 1): stop after two rounds in a row with nothing
  moved;
- evaluation expensive: stop after one.

After three rounds, stop and regroup with the user if no further lever
looks viable, even when small things still move.

Every message that stops or changes course (a workaround in section 2, the
fallback in section 7, a stop here) has the same shape: the verdict, what
was checked, why we stop or switch, and what would change the answer.

## 9. Report

Against the user's model: global KPI, each target insight, the guard, the
confidence of each number, what was not done, and the next step. For a solved
insight that carries `automatic_tests`, offer the platform's regression test.

Every decision is made on validation. At the end only, and only if the
user agrees, report the test KPI once as a final check; nothing is
decided on it.

Then write one lesson file in `.tensorleap/reference/lessons/`, in the format its README
gives. Keep it anonymous: no user name, data, sample ids or file paths;
describe the task, the data type and what worked. Save it and leave it; it
will be sent to Tensorleap once that path exists.

## Reference A: Levers, in order

### A.1 Mislabels

- Raise mislabeled clusters first.
- Do not wait for a Mislabeled insight; current evaluations do not produce
  one. A near-zero block found in the slice check (section 4) is enough to
  start this step.
- Run a small random audit outside the cluster to tell a local problem from a
  wide one. A wide label problem is the biggest gain and goes to the top.
- Give the user the candidate list to review and relabel; the Tensorleap
  MCP cannot change labels.

### A.2 Cheap checks

**A. Post-processing and inference (no training).** When an insight points to
it, or when you think it may help in this case. It involves no training, so
you may run it before proposing the plan if it takes up to about an hour, and
present the result in the plan. If it takes longer, put it in the plan as an
item.

- Dump predictions once. Try thresholds (global and per class), size or
  component filters, flip/scale TTA, detection matching and suppression.
- Fit on one half of validation, check on the other. Deployable rules only,
  never insight member ids.
- Re-score each target insight's members with the post-processed model if it
  takes under about half an hour. This model becomes the reference for the
  later levers.

**B. Training recipe (one retrain each).** Only for a specific finding in the
code that an insight explains: augmentation off, a train/inference
preprocessing mismatch, a poor checkpoint rule. One or two candidates.

### A.3 More data

- Guideline: insights that are not training aggressors, or whose members are
  under about 5 % of training.
- Use the labeling budget from the intake (section 3). Choose first and give
  the reason: only the to-label picks for the target groups, only Label Next,
  or both (the hybrid). Label Next needs an unlabeled pool. It ranks by
  coverage, not by failure; say so when you present it. The hybrid was the
  best recipe on one classification dataset. Present it as a suggested
  practice, not a proven one.
- The platform's to-label list is the base and is presented as a Tensorleap
  result. View the picks. Thin near-duplicates by scene, time, camera or
  source; refill from the next ranks; report how many were thinned and by
  which field. You may add picks; mark each row `platform` or `agent`.
- Zero to label: the platform searched one latent space and found no
  candidates precise enough. It does not mean the pool has none. Raise the
  insight as "needs targeted labeling" and say this. It may also go to A.4.
  Expanding the list with fetch similar is untested; do not do it as a
  default. Use it only when the failure mode looks very coherent.
- For a sub-insight, take the picks from its `aggressor_fixing` list,
  not from its Analyze view, which does not show them (reported bug).
- The output is a labeling list. Training waits for the labels, and new labels
  are spot-checked first.
- In the retrain, sample the new to-label picks more often than the rest;
  you choose the weight. They are few, and at plain weight they may not move
  their group. This is not the oversampling of A.4.
- Detection and segmentation only: background images (no objects) need no
  labels. Screen them with the model at low confidence and add them in a
  small share (about 5 %).

### A.4 Oversampling, augmentation, other

Last, and at most one such run in a plan. No known recipe: propose only what
you can back with a stated mechanism on a cause you verified on samples.

- Targeted augmentation, when a transform can imitate the cause.
- Oversampling, only if (a) held-out members fail beyond their class or
  content mix, (b) training members fail beyond theirs, (c) the labels are
  learnable from the input. One run at a moderate dose (x5 to x10, group
  under about a third of draws).
- Larger changes: a loss term, a different input resolution (often helps
  segmentation of small or thin structures), a different model input, an
  architecture change. Propose them like any lever, with the mechanism and
  the cost (usually a from-scratch retrain), and run them once the user
  approves. Compare against a from-scratch retrain of the current recipe.

## Reference B: Noise and what counts as a win

**Noise, cheapest first**

1. Another seed or earlier run from the user (one pair is rough; say so).
2. Otherwise a bootstrap on the single model, for the plan's yardstick.
3. After training: paired bootstrap, ours against theirs, same validation set.
4. A confirmation seed of our recipe is a suggestion to the user, not a
   required step. Extra seeds are expensive.
5. Baseline seeds only if the user asks.

Head-to-head against another solution: no seeds, paired bootstrap on the same
validation set.

**Wins**

- **Aggressor win (the main claim):** the global KPI rises and the target
  insight improves on its members as defined on the user's model. When a
  large share of the members change (missed to found, for example), that is
  clear in itself. No more runs needed.
- **Global-only win:** an improvement of the global KPI that is large enough
  is a win. Judge "large enough" against the noise yardstick: the interval
  above zero, and the gain well beyond what a rerun could give. Say how you
  judged it. Suggest one extra seed run to validate it; it is the user's
  choice, not a condition.
- **Group fix with a small global cost:** not a win. Report it as the
  user's call.
- **From-scratch retrain, aggressor up, global flat:** say the group gain may
  be partly a new-seed effect and offer one control run.

## Known limits (for maintainers)

- The same-content check in section 4 is due to move to the analysis skill.
- Sub-insight picks: the Analyze view does not show them (reported bug);
  A.3 works around it.
- How the user reviews and relabels (A.1): a candidates list plus a
  review page is not built yet.
- How lessons return to Tensorleap is not decided.
- An HTML version of the plan (section 7) is planned.
<!-- END TENSORLEAP SKILL: tensorleap-aggressor-fixing -->
