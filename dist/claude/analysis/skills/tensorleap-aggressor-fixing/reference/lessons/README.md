# Lessons

What worked and what failed on earlier projects. One file per project.

## How to use them

- Read every file here before ranking insights. An installation may hold none.
- A lesson is a hint for ranking and for what to check. It is never a rule:
  the lever order in `SKILL.md` decides.
- Weigh each lesson by its **Strength**. One seed or a toy dataset is not proof.

## Format

One block per lesson, all fields filled:

| Field | Content |
|---|---|
| Task | task and data type |
| Insight | what the failing group was (kind, size or training share if known) |
| Lever | mislabels / post-processing / recipe / more data / augmentation / oversampling / other |
| What was done | one line |
| Outcome | group and global result against the noise reference, relative numbers |
| Strength | seeds, held-out check, toy or real data |
| Check it suggests | what to verify before repeating it |

## Writing a lesson

- At the end of a run, write one file here in this format.
- Use no customer, dataset, class or file names, no sample ids, and relative
  numbers only.
- **Do nothing else with the file.** It stays on the user's machine.
- [OPEN] How lessons return to Tensorleap. Nothing is sent until there is a
  channel that does not involve the user.
