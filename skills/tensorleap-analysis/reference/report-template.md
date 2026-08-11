# Report template

Write the report to `<out-dir>/report.md` so every embedded file path is a
simple relative link. Follow this skeleton; drop sections that have no
content rather than leaving them empty.

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

## 1. <title — top_panel summary sentence, or constructed from type + dominant metadata>

**Severity <s> · <n_samples> samples · <subset/state makeup>**

What the platform found, in plain language, and why it matters for this
model. Cite the evidence: dominant metadata (`mutual_info_elements`), metric
contrast (`severity_metrics` / `metrics_info`).

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

### Reproduce in the UI

`display_filters` verbatim (fenced JSON), so the user can pull up the same
cluster.

## Appendix

- Samples without rendered visualizations: <count> (list per insight).
- Insights not elaborated (low severity / duplicative): one line each.
- Fetch errors, if any (from insights.json `errors`).
```
