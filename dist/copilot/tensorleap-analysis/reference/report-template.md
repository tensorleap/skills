# Report template

The deliverable is **one self-contained `report.html`** (images inlined as
data URIs by `tl_api.py inline-html` — author it with plain relative `src`
paths and let the script do the inlining), plus a short **`report.md`**
holding only the executive summary and the action-item checklists (the part
people paste into tickets and Slack). Write both into `<out-dir>` so relative
paths resolve.

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

## Content per finding (order by severity, descending)

1. Heading: the failure mode. Severity chip (label text always, color never
   alone) + sample count + subset makeup.
2. The failure mode first: what kind of samples fail, how the model gets
   them wrong, the likely root cause. Then the evidence in plain sentences.
3. Representative samples: `<figure>` grid for images/charts, `<blockquote>`
   for text samples, each captioned with sample id + its worst metric.
4. Sub-clusters inside `<details>` — elaborate only the ones that add
   information; one-line the rest, and say when they just repeat the parent.
5. Action items: 1–3 checklist items, concrete and quantified, most
   impactful first.
6. "See it in Tensorleap": one plain sentence pointing at the version's
   Insights panel finding number.

End with an appendix: samples without renderings, findings not elaborated,
fetch errors. Keep the executive summary honest — if the findings are
low-severity or repetitive, say so.

## HTML skeleton

Copy this shape; fill the `<article>`. Keep the CSS block as-is (light/dark
via `prefers-color-scheme`; severity colors pass contrast on both surfaces
and always carry a text label).

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tensorleap analysis — PROJECT / VERSION</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --line: #e4e2dc;
  --card: #f4f3f0; --sev3: #d03b3b; --sev2: #ec835a; --sev1: #fab219;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --line: #3a3936;
          --card: #242320; }
}
body { background: var(--bg); color: var(--ink); margin: 0;
       font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
article { max-width: 860px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }
h1 { font-size: 1.6rem; line-height: 1.25; }
h2 { font-size: 1.2rem; margin-top: 2.5em; padding-top: 1em;
     border-top: 1px solid var(--line); }
.meta, figcaption, .muted { color: var(--ink-2); font-size: .85rem; }
.chips { display: flex; flex-wrap: wrap; gap: .5rem; margin: .4rem 0 1rem; }
.chip { border: 1px solid var(--line); border-radius: 999px;
        padding: .1rem .6rem; font-size: .8rem; color: var(--ink-2); }
.chip.sev { color: var(--ink); font-weight: 600; }
.chip.sev::before { content: ""; display: inline-block; width: .55em;
        height: .55em; border-radius: 50%; margin-right: .4em;
        background: var(--sev-color, var(--ink-2)); }
.sev3 { --sev-color: var(--sev3); } .sev2 { --sev-color: var(--sev2); }
.sev1 { --sev-color: var(--sev1); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
        gap: .75rem; margin: 1rem 0; }
figure { margin: 0; }
figure img { width: 100%; border-radius: 6px; display: block; }
blockquote { border-left: 3px solid var(--line); margin: 1rem 0;
             padding: .25rem 1rem; color: var(--ink-2); font-style: italic; }
blockquote .muted { display: block; font-style: normal; margin-top: .35rem; }
.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: .45rem .6rem;
         border-bottom: 1px solid var(--line); }
ul.actions { list-style: none; padding: 0; }
ul.actions li { padding: .35rem 0 .35rem 1.7rem; position: relative; }
ul.actions li::before { content: "☐"; position: absolute; left: .2rem; }
details { background: var(--card); border-radius: 8px;
          padding: .6rem 1rem; margin: 1rem 0; }
summary { cursor: pointer; font-weight: 600; }
</style>
</head>
<body>
<article>
  <h1>Tensorleap analysis — PROJECT / VERSION</h1>
  <p class="meta">Generated DATE from N insights (P findings, S sub-clusters) on SERVER.</p>

  <h2>Executive summary</h2>
  <p>…3–6 sentences…</p>
  <div class="tablewrap"><table>
    <tr><th>#</th><th>Finding</th><th>Severity</th><th>Samples</th><th>Top action</th></tr>
    <tr><td>1</td><td>…</td><td>3 of 3</td><td>4,772</td><td>…</td></tr>
  </table></div>

  <h2>1. Failure mode: …</h2>
  <div class="chips">
    <span class="chip sev sev3">Severity 3 of 3</span>
    <span class="chip">4,772 samples</span>
    <span class="chip">81% training</span>
  </div>
  <p>…failure mode, root cause, evidence…</p>
  <div class="grid">
    <figure><img src="insight_1_…/samples/…/data.jpg" alt="deer labeled cat">
      <figcaption>validation_9158 — deer → "cat", loss 16.1</figcaption></figure>
  </div>
  <blockquote>"…text sample…"<span class="muted">training_816 — labeled pos, loss 7.4</span></blockquote>
  <details><summary>Sub-clusters (5)</summary><ul><li>…</li></ul></details>
  <h3>Action items</h3>
  <ul class="actions"><li>…</li></ul>
  <p class="muted">See it in Tensorleap: open PROJECT → version VERSION →
     Insights panel → finding #1.</p>

  <h2>Appendix</h2>
  <ul><li>…</li></ul>
</article>
</body>
</html>
```

## report.md (the paste-into-a-ticket companion)

Just: title line, the executive-summary paragraph, the summary table, and
each finding's action-item checklist under its failure-mode heading. No
images, no evidence sections — link to `report.html` for those.
