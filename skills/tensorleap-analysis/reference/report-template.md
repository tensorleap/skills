# Report template

The deliverable is **one self-contained `report.html`** (images inlined as
data URIs by `tl_api.py inline-html` — author it with plain relative `src`
paths and let the script do the inlining; large lossless PNGs are recompressed
to JPEG automatically when PIL is importable), plus a short **`report.md`**
holding only the executive summary and the action-item checklists (the part
people paste into tickets and Slack). Write both into `<out-dir>` so relative
paths resolve.

**The report serves two audiences at once.** The prose and diagrams are for
an ML engineer who has never opened Tensorleap; the collapsed "Explore in
Tensorleap" box under each finding is for users who know the platform and
want to continue there. Rules for the prose:

- Each finding's heading names a **failure mode** in plain ML terms
  ("Confident misclassification of non-cats in the cat region", "Positive
  labels on negative-toned reviews") — never "insight_1_low_performance".
- Never paste internal identifiers into the prose: no blob paths, filter
  JSON, artifact ids, or raw payload field names. Those live in
  `insights.json` for whoever wants them; the report speaks English.
- Any platform term you do use gets a one-line translation the first time
  (e.g. "severity 3 — the platform's highest").

## Anatomy of a finding (in order)

**One finding = one section.** Never merge findings into a combined section;
if two findings share a pattern, each gets its own full section and a
cross-reference. If a finding fails the coherence gate (skill Step 4), it
gets NO section — one honest line in the appendix instead.

1. **Heading**: what the finding IS. Prefix `Failure mode:` only when the
   model is failing on a group of samples (low_performance,
   out_of_distribution, domain_gap). Dataset-integrity findings —
   duplication, data_leakage, mislabeled_samples — are prefixed
   `Data issue:`; never call something a failure mode when the model isn't
   the thing failing. Chips: severity (text label always — color never
   alone), sample count, key metric, **latent space** (e.g.
   "classification-semantic space").
2. **Taxonomy strip** — the interpretive scheme. Four fixed families:
   `Data gap · Label quality · Split problem · Model behavior`; highlight the
   diagnosed one (from the playbook walk) and caption WHY in one line. The
   strip is identical on every finding, so readers learn the grammar once.
   Multiple families may be active when the diagnosis is genuinely mixed.
3. **Prose**: what kind of samples fail, how the model gets them wrong,
   likely root cause, evidence in plain sentences. Include one sentence
   translating the latent space: in what sense are this group's samples
   "similar" (see the playbook's latent-space guide).
4. **Mini-diagrams** (pure HTML/CSS, populated from the digest):
   - *Split composition bar*: where the group's samples live
     (training/validation/test/unlabeled), fixed colors, count labels below —
     labels always, never color alone.
   - *Metric contrast*: "this group" vs "rest of data" for the group's
     dominant metric — population value from `population_metrics` in
     insights.json; **omit the row if population_metrics lacks the column**.
5. **Samples**: 6 visible `<figure>`s (or `<blockquote>`s for text), each
   captioned with sample id + worst metric; the rest (≤18) inside
   `<details class="more">` — they're embedded too, so the file works
   offline; no JavaScript. Watch the total file size (`inline-html` prints
   it): aim under ~10 MB — it downscales/recompresses automatically, but
   for large-resolution datasets also cap the hidden samples (e.g. 6
   instead of 18) rather than shipping a bloated file.
6. **"What the samples show"** (`<div class="observe">`): the analyst's own
   observations from actually viewing the samples — label errors visible by
   eye, shared attributes the metadata doesn't capture (+ the suggestion to
   add them as metadata). Written in first person of the analysis ("Looking
   at the samples, …") so the reader can tell it apart from platform
   evidence. If your look revealed nothing beyond the platform's story, say
   that in one line — it's a real result.
7. **Action items**: 1–3 checklist items, concrete and quantified.
8. **`<details class="explore">` "Explore in Tensorleap"** — exactly two
   sentences, no repetition: (1) the link, phrased "Open version X's
   Insights panel and look for finding #N — '<platform name>', severity S"
   (the link selects the version and opens the panel; it applies no
   filters); (2) a muted detail line: "Latent space: … · <split counts> ·
   <platform label/acquire counts>". No "manual path" line — the link
   sentence already names the destination. No filter JSON.

End with an appendix: findings dropped as incoherent (one line each, with
their platform finding #), samples whose visualizations aren't rendered yet
— phrase it as normal on-demand behavior ("not rendered yet; can be
triggered from the UI"), never as an error — and fetch errors. Keep the
executive summary honest — if the findings are low-severity or repetitive,
say so.

## HTML skeleton

Copy this shape; fill the `<article>`. Keep the CSS block as-is — the state
and severity colors are validated for both light and dark modes; the split
bar's count labels are mandatory (two light-mode state colors rely on them).

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
  --st-train: #2a78d6; --st-val: #eb6834; --st-test: #1baf7a;
  --st-unl: #eda100; --st-other: #52514e; --acc: #2a78d6;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --line: #3a3936;
          --card: #242320; --st-train: #3987e5; --st-val: #d95926;
          --st-test: #199e70; --st-unl: #c98500; --st-other: #c3c2b7;
          --acc: #3987e5; }
}
body { background: var(--bg); color: var(--ink); margin: 0;
       font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
article { max-width: 860px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }
h1 { font-size: 1.6rem; line-height: 1.25; }
h2 { font-size: 1.2rem; margin-top: 2.5em; padding-top: 1em;
     border-top: 1px solid var(--line); }
.meta, figcaption, .muted { color: var(--ink-2); font-size: .85rem; }
a { color: var(--acc); }
.chips { display: flex; flex-wrap: wrap; gap: .5rem; margin: .4rem 0 1rem; }
.chip { border: 1px solid var(--line); border-radius: 999px;
        padding: .1rem .6rem; font-size: .8rem; color: var(--ink-2); }
.chip.sev { color: var(--ink); font-weight: 600; }
.chip.sev::before { content: ""; display: inline-block; width: .55em;
        height: .55em; border-radius: 50%; margin-right: .4em;
        background: var(--sev-color, var(--ink-2)); }
.sev3 { --sev-color: var(--sev3); } .sev2 { --sev-color: var(--sev2); }
.sev1 { --sev-color: var(--sev1); }
.taxonomy { display: flex; flex-wrap: wrap; gap: .4rem; margin: .75rem 0 .25rem; }
.tx { border: 1px solid var(--line); border-radius: 6px; padding: .15rem .6rem;
      font-size: .8rem; color: var(--ink-2); }
.tx.active { background: var(--ink); color: var(--bg); border-color: var(--ink);
             font-weight: 600; }
.splitbar { display: flex; gap: 2px; height: 14px; border-radius: 4px;
            overflow: hidden; margin: .75rem 0 .3rem; }
.splitbar span { min-width: 3px; }
.st-train { background: var(--st-train); } .st-val { background: var(--st-val); }
.st-test { background: var(--st-test); } .st-unl { background: var(--st-unl); }
.st-other { background: var(--st-other); }
.legend { display: flex; flex-wrap: wrap; gap: 1rem; font-size: .8rem;
          color: var(--ink-2); margin-bottom: 1rem; }
.legend b::before { content: ""; display: inline-block; width: .6em; height: .6em;
          border-radius: 2px; margin-right: .35em;
          background: var(--dot, var(--st-other)); }
.legend .train { --dot: var(--st-train); } .legend .val { --dot: var(--st-val); }
.legend .test { --dot: var(--st-test); } .legend .unl { --dot: var(--st-unl); }
.contrast { display: grid; grid-template-columns: 8.5rem 1fr 4.5rem;
            gap: .4rem .6rem; align-items: center; font-size: .85rem;
            margin: .75rem 0 1rem; }
.contrast .track { background: var(--card); border-radius: 3px; height: 10px; }
.contrast .fill { background: var(--acc); height: 100%; border-radius: 3px; }
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
details.explore summary { color: var(--acc); }
.observe { border-left: 3px solid var(--acc); padding: .1rem 1rem;
           margin: 1rem 0; }
.observe .tag { font-size: .75rem; font-weight: 700; letter-spacing: .04em;
           text-transform: uppercase; color: var(--acc); }
</style>
</head>
<body>
<article>
  <h1>Tensorleap analysis — PROJECT / VERSION</h1>
  <p class="meta">Generated DATE from N insights (P findings, S sub-clusters) on SERVER.
     <a href="LINKS.insights_panel">Open this version's insights in Tensorleap</a>.</p>

  <h2>Executive summary</h2>
  <p>…3–6 sentences…</p>
  <div class="tablewrap"><table>
    <tr><th>#</th><th>Finding</th><th>Severity</th><th>Samples</th><th>Top action</th></tr>
  </table></div>

  <h2>1. Failure mode: …</h2>
  <div class="chips">
    <span class="chip sev sev3">Severity 3 of 3</span>
    <span class="chip">314 samples</span>
    <span class="chip">accuracy 0.42</span>
  </div>
  <div class="taxonomy">
    <span class="tx active">Data gap</span><span class="tx">Label quality</span>
    <span class="tx">Split problem</span><span class="tx">Model behavior</span>
  </div>
  <p class="muted">Diagnosis: the corruption never appears in training — a coverage gap, not a model defect.</p>

  <p>…failure mode, root cause, evidence…</p>

  <div class="splitbar">
    <span class="st-test" style="width:70%"></span>
    <span class="st-unl" style="width:30%"></span>
  </div>
  <div class="legend">
    <span class="train"><b></b>training 0</span>
    <span class="val"><b></b>validation 0</span>
    <span class="test"><b></b>test 221</span>
    <span class="unl"><b></b>unlabeled 93</span>
  </div>

  <div class="contrast">
    <span>this group</span><span class="track"><span class="fill" style="width:48%"></span></span><span>0.42 acc</span>
    <span>rest of data</span><span class="track"><span class="fill" style="width:100%"></span></span><span>0.87 acc</span>
  </div>

  <div class="grid">
    <figure><img src="insight_1_…/data.png" alt="…"><figcaption>test_1855 — loss 5.8</figcaption></figure>
    <!-- …6 visible… -->
  </div>
  <details class="more"><summary>Show 18 more samples</summary>
    <div class="grid"><!-- …the rest, also relative src, inlined too… --></div>
  </details>

  <div class="observe"><span class="tag">What the samples show</span>
    <p>Looking at the samples: the worst members' visible content contradicts
       their labels (…). The group also shares … which no metadata field
       captures — worth adding as metadata so the platform can track it.</p>
  </div>

  <h3>Action items</h3>
  <ul class="actions"><li>…</li></ul>

  <details class="explore"><summary>Explore in Tensorleap</summary>
    <p><a href="DEEP_LINK">Open version VERSION's Insights panel</a> and look
       for finding #1 — "low performance", severity 3.</p>
    <p class="muted">Latent space: balanced · 221 test + 93 unlabeled samples
       · 88 unlabeled samples pre-selected for labeling.</p>
  </details>

  <h2>Appendix</h2>
  <ul><li>…</li></ul>
</article>
</body>
</html>
```

## report.md (the paste-into-a-ticket companion)

Just: title line, the executive-summary paragraph, the summary table, and
each finding's action-item checklist under its failure-mode heading, plus the
finding's deep link as a plain URL. No images, no evidence sections — link to
`report.html` for those.
