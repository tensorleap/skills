# Report template

Write the report to `<out-dir>/report.md` so every embedded file path is a
simple relative link. Follow this skeleton; drop sections that have no
content rather than leaving them empty.

**Write for an ML engineer who has never opened Tensorleap's internals.**
The report is about their MODEL's failure modes, not about the platform:

- Each finding's heading names a **failure mode** in plain ML terms
  ("Confident misclassification of non-cats in the cat region", "Positive
  labels on negative-toned reviews") — never "insight_1_low_performance".
- Never paste internal identifiers into the prose: no blob paths, filter
  JSON, artifact ids, or raw payload field names. Those live in
  `insights.json` for whoever wants them; the report speaks English.
- Any platform term you do use gets a one-line translation the first time
  (e.g. "severity 3 — the platform's highest").
- To point the reader back to the platform, reference what they can see:
  "open this version in Tensorleap → Insights panel → finding #1", not a
  filter object.

```markdown
# Tensorleap analysis — <project name> / <version name>

*Generated <date> from <n> insights (<parents> findings, <subs> sub-clusters)
on <api_url>.*

## Executive summary

3–6 sentences: the one or two findings that matter most, their likely root
cause, and the highest-leverage action. A reader who stops here should still
know what to do next.

| # | Finding | Type | Severity | Samples | Top action |
|---|---------|------|----------|---------|-----------|
| 1 | <title> | low_performance | 3 | 4,772 | Label 240 selected samples |

## 1. <failure mode — what fails and how, in ML terms>

**Severity <s> of 3 · <n_samples> samples · <subset/state makeup>**

The failure mode first: what kind of samples fail, how the model gets them
wrong, and the most likely root cause (mislabels? confusion frontier?
under-representation? split imbalance?). Then the evidence in plain
language: which metadata characterizes the failing samples ("78% night-time
images vs 12% elsewhere"), how much worse the metrics are. Use the platform's
ready-made summary sentence as the lede when present (top_panel), otherwise
construct one from the dominant metadata.

### Representative samples

Image grid (markdown table of `![](relative/path.jpg)`, 3–5 per row) for
image-like visualizers; for `text` payloads quote the (joined) token body as
a blockquote; for `graph`/`hbar` embed the rendered `chart.png` (or a compact
data table if charts weren't rendered). Caption each sample with its id and
worst metric value from `samples.csv`. If some samples had no rendered
visualization, say how many.

### Sub-clusters

One line each: `sub_<i>` — n samples, what distinguishes it. Elaborate (own
evidence + samples + actions) ONLY the ones that add information beyond the
parent: markedly higher severity, a different dominant metadata story, or a
different recommended action. Say explicitly when the sub-clusters just
repeat the parent's pattern.

### Action items

- [ ] Concrete, quantified, playbook-derived actions (1–3, most impactful
      first). Reference the decision-tree branch that produced each.

### See it in Tensorleap

One plain sentence: "Open <project> → version <name> → Insights panel →
finding #<index> ('<its name there>') to explore this cluster
interactively." No filter JSON, no blob paths.

## Appendix

- Samples without rendered visualizations: <count> (list per insight).
- Insights not elaborated (low severity / duplicative): one line each.
- Fetch errors, if any (from insights.json `errors`).
```
