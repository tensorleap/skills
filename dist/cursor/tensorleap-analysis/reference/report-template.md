# Report template

The deliverable is **one self-contained `report.html`** (images inlined as
data URIs by `tl_api.py inline-html` — author it with plain relative `src`
paths and let the script do the inlining; oversized images are downscaled and
recompressed automatically), plus a short **`report.md`** holding only the
executive summary and the action-item checklists (the part people paste into
tickets and Slack). Write both into `<out-dir>` so relative paths resolve.

**The report serves two audiences at once.** The prose and diagrams are for
an ML engineer working in the data's domain who has never opened Tensorleap
— write as a colleague in that domain (its terminology, its standard fixes;
skill Step 4); the collapsed "Explore in Tensorleap" box under each insight
is for users who know the platform and want to continue there.

## Language rules

- **"Failure Mode" is an insight TYPE** (the Insights panel's name for
  `low_performance`). Never use the phrase for anything else — not as a
  table column, not as generic prose ("each failure mode…"). When you need
  a generic word for what a section describes, say "issue", "pattern", or
  name it concretely.
- Each insight's heading names what is going wrong in plain ML terms
  ("Confident misclassification of non-cats in the cat region", "Positive
  labels on negative-toned reviews") — never "insight_1_low_performance".
- Never paste internal identifiers into the prose: no blob paths, filter
  JSON, artifact ids, or raw payload field names. Those live in
  `insights.json` for whoever wants them; the report speaks English.
- Any platform term you do use gets a one-line translation the first time
  (e.g. "severity 3 — the platform's highest").
- Your own analysis is presented on its own merits: say what you found and
  how ("profiling the group's metadata against the full run shows objects 3×
  smaller than average"), never as a comparison with what the platform did
  or didn't surface. Comparative framing adds nothing the reader can act on,
  and it misattributes — the platform's insight is what surfaced the group
  you are enriching.
- **State what is known plus the step that completes it — never what can't
  be done.** Every "we can't confirm / don't know X" is written as "doing Y
  will establish X", and subtle evidence is a discovery, not a weakness.
  Scope: this governs evidence, tooling, and next steps; model failures stay
  blunt ("misses 48 objects per image"). Framing never upgrades confidence
  in an unconfirmed cause.

## Page structure

1. **Title + meta line** (project/version, insight counts, server, the
   Insights-panel link).
2. **KPI strip** (`.tiles`): 3–4 stat tiles with the model-level numbers a
   domain expert checks first (from `population_metrics` — e.g. recall,
   precision, misses/image for detection; accuracy for classification),
   then one tile per insight type present with its count — the same numbers
   the reader sees in the Insights panel.
3. **Executive summary**: 3–5 sentences. This is where impact PRIORITY
   lives ("labeling the two corruption groups is the highest-leverage
   action") — the rest of the report is panel-ordered, not impact-ordered.
   Where the prose names a specific issue, link the phrase to its card's
   anchor ("a <a href="#insight-1">gaussian-noise group</a>…").
4. **Overview table**: a TOC of the report in body order — columns
   `Insight | Issue | Severity | Samples | First action`, with a subheader
   row per type. "Issue" holds the section's headline, compressed. The
   Insight cell links to the card's anchor (`<a href="#insight-4">#4</a>`).
5. **One `<h2>` group per insight type present**, in the panel's order,
   with a count badge and a one-line meaning (table below). Inside, one
   **card** per insight (anatomy below), severity-ordered.
6. **Notes**: short and terse — a few one-line bullets, no paragraphs.
   One line per insight without a card, stating why in half a sentence and
   ending in what the reader can do: an insight whose story is covered by
   another card ("covered by the crowded-scenes actions"), a group whose
   metrics are at population level ("no action needed"), or an insight where
   the subinsight check found no single story ("its subinsights repeat the
   stories above — a candidate for archiving in the panel"). Plus, when
   relevant: unrendered visualizations phrased as on-demand behavior (never
   as an error) and fetch errors. Word editorial outcomes neutrally and
   ground them in the data — never verdict labels on the insight
   ("incoherent", "not actionable").

| Panel name | payload `type` | One-line meaning for the group header |
|---|---|---|
| Failure Mode | `low_performance` | groups of samples where the model underperforms |
| Out of Distribution | `out_of_distribution` | samples in one subset unlike anything in the rest of the data |
| Duplication | `duplication` | near-identical samples within a subset |
| Data Leakage | `data_leakage` | near-identical samples on both sides of a split |
| Domain Gap | `domain_gap` | performance differs between two values of a metadata field |
| Mislabeled | `mislabeled_samples` | samples whose ground truth looks wrong |

When some of a type's insights have no card, the group's count badge
reconciles with the panel ("3 of 6") and each missing insight has its one
Notes line.

## Anatomy of an insight card (in order)

**One insight = one card = one failing population.** Never merge insights
into a combined card. When two insights resemble each other, apply the
playbook's "One failing population per insight" rule: differentiate (each
card stands on its measured difference, as content — never as commentary on
the report's structure) or keep only the clearer one. An insight that fails
the coherence gate goes through the subinsight rescue (skill Step 5): a
subinsight that isolates one clear story gets the card instead — its chips
carry both identities ("insight #3 · sub-insight #9") and its explore line
names the parent in the panel. No coherent core → no card; one terse Notes
line with the archive suggestion. Top-level insights are disjoint by
definition; never present non-overlap as a discovery.

**Every card speaks at exactly one granularity.** A card never contains
subinsight sections, lists, or one-liners — subinsights either promote to
their own card (skill Step 5) or don't appear. The only permitted parent/sub
cross-mention is a single orienting context sentence in prose ("this group
is the sharpest slice of the broader animals-on-roads cluster, insight #3").
The explore box may state the panel's sub-insight count as navigation info.

The card (`<section class="card sev3|sev2|sev1" id="insight-<N>">` —
sub-based cards use `id="insight-<N>-sub-<M>"`; these anchors are what the
overview table and executive summary link to) carries a left accent in
the severity color — always paired with the severity chip's text, never
color alone:

1. **Heading** (`<h3>`): the issue itself, unnumbered, no type prefix — the
   group header carries the type. Identity = the **platform insight #**:
   shown as a chip and in the overview table; prose cross-references between
   cards go by NAME ("the tiny-objects fix above"), never by number.
2. **Chips**: severity ("Severity 1 of 3"), insight #, sample count, key
   metric, latent space.
3. **Bottom line** (`<p class="lede">`): ONE bold sentence — what is going
   wrong and why it matters. A reader who stops here still got the point.
4. **Root cause** (`<p class="rootcause">`): `Root cause — <family>:` one
   caption sentence. The family vocabulary is fixed — `Data gap`,
   `Label quality`, `Split problem`, `Model behavior` (two families allowed
   when the diagnosis is genuinely mixed about ONE population) — so the
   label reads as a consistent grammar across every card and every report.
5. **Prose**: what kind of samples fail, how the model gets them wrong, the
   evidence in plain sentences, and one sentence translating the latent
   space (playbook guide).
6. **Facts row** (`.facts`): the split-composition bar and the
   group-vs-all-data metric contrast side by side (they stack on narrow
   screens). Count labels on the bar always; omit the contrast if
   `population_metrics` lacks the column.
7. **Samples**: 6 visible `<figure>`s (or `<blockquote>`s for text), each
   captioned with sample id + worst metric. A half-sentence before the grid
   names the view in domain terms ("predicted boxes over the camera frame")
   so GT isn't mistaken for prediction — orientation only, never selection
   rationale; the rest (≤18) inside
   `<details class="more">`, whose summary is exactly
   `Show <N> more samples` with N the hidden count — that wording, every
   card, every report. Watch total file size (`inline-html` prints it): aim
   under ~10 MB — cap hidden samples on large-resolution datasets.
8. **"What the samples show"** (`.observe`): the analyst's own observations
   from viewing the samples, first-person ("Looking at the samples, …") so
   it can't be mistaken for platform output. "Nothing beyond the platform's
   story" is a real, useful result.
9. **"Do next"** (`.donext`): the strongest-looking block on the card — 1–3
   concrete, quantified checklist items.
10. **Explore in Tensorleap** (`<details class="explore">`): exactly two
    sentences — the link ("Open version X's Insights panel and look for
    insight #N — '<panel name>', severity S"; the link selects the version,
    applies no filters) and a muted detail line (latent space · split
    counts · platform label/acquire counts). No filter JSON.

## HTML skeleton

Copy this shape; fill the `<article>`. Keep the CSS block as-is — colors are
validated for light and dark modes.

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
article { max-width: 880px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }
h1 { font-size: 1.6rem; line-height: 1.25; margin-bottom: .3rem; }
h2 { font-size: 1.3rem; margin-top: 3em; }
h2 .count { font-size: .8rem; font-weight: 600; color: var(--ink-2);
     border: 1px solid var(--line); border-radius: 999px;
     padding: .1rem .6rem; vertical-align: 2px; margin-left: .5em; }
h2 + .muted { margin-top: -.4rem; }
h3 { font-size: 1.15rem; margin: 0 0 .5rem; }
h4 { font-size: .95rem; margin: 0 0 .4rem; }
.meta, figcaption, .muted { color: var(--ink-2); font-size: .85rem; }
a { color: var(--acc); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
     gap: .6rem; margin: 1.2rem 0; }
.tile { background: var(--card); border-radius: 10px; padding: .8rem .9rem; }
.tile b { display: block; font-size: 1.45rem; line-height: 1.2; }
.tile span { font-size: .78rem; color: var(--ink-2); }
.card { background: var(--card); border-radius: 12px;
     padding: 1.2rem 1.3rem; margin: 1.2rem 0;
     border-left: 5px solid var(--ink-2); scroll-margin-top: 1rem; }
.card.sev3 { border-left-color: var(--sev3); }
.card.sev2 { border-left-color: var(--sev2); }
.card.sev1 { border-left-color: var(--sev1); }
.lede { font-weight: 600; font-size: 1.02rem; margin: .6rem 0; }
.rootcause { font-size: .9rem; color: var(--ink-2); margin: .4rem 0 1rem; }
.rootcause b { color: var(--ink); }
.chips { display: flex; flex-wrap: wrap; gap: .5rem; margin: .4rem 0; }
.chip { border: 1px solid var(--line); border-radius: 999px;
        padding: .1rem .6rem; font-size: .8rem; color: var(--ink-2); }
.chip.sev { color: var(--ink); font-weight: 600; }
.chip.sev::before { content: ""; display: inline-block; width: .55em;
        height: .55em; border-radius: 50%; margin-right: .4em;
        background: var(--sev-color, var(--ink-2)); }
.s3 { --sev-color: var(--sev3); } .s2 { --sev-color: var(--sev2); }
.s1 { --sev-color: var(--sev1); }
.facts { display: flex; flex-wrap: wrap; gap: 1.5rem; align-items: center;
     margin: 1rem 0; }
.facts > div { flex: 1 1 260px; }
.splitbar { display: flex; gap: 2px; height: 14px; border-radius: 4px;
            overflow: hidden; margin-bottom: .3rem; }
.splitbar span { min-width: 3px; }
.st-train { background: var(--st-train); } .st-val { background: var(--st-val); }
.st-test { background: var(--st-test); } .st-unl { background: var(--st-unl); }
.st-other { background: var(--st-other); }
.legend { display: flex; flex-wrap: wrap; gap: .8rem; font-size: .78rem;
          color: var(--ink-2); }
.legend b::before { content: ""; display: inline-block; width: .6em; height: .6em;
          border-radius: 2px; margin-right: .35em;
          background: var(--dot, var(--st-other)); }
.legend .train { --dot: var(--st-train); } .legend .val { --dot: var(--st-val); }
.legend .test { --dot: var(--st-test); } .legend .unl { --dot: var(--st-unl); }
.contrast { display: grid; grid-template-columns: 6.5rem 1fr 5rem;
            gap: .35rem .6rem; align-items: center; font-size: .82rem; }
.contrast .track { background: var(--line); border-radius: 3px; height: 10px; }
.contrast .fill { display: block; background: var(--acc); height: 100%;
                  border-radius: 3px; }
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
tr.typerow td { font-weight: 700; padding-top: .9rem;
         border-bottom: 2px solid var(--line); }
ul.actions { list-style: none; padding: 0; margin: 0; }
ul.actions li { padding: .3rem 0 .3rem 1.7rem; position: relative; }
ul.actions li::before { content: "☐"; position: absolute; left: .2rem; }
details { background: var(--bg); border: 1px solid var(--line);
          border-radius: 8px; padding: .6rem 1rem; margin: 1rem 0; }
summary { cursor: pointer; font-weight: 600; }
details.explore { border: none; background: none; padding: .2rem 0 0; }
details.explore summary { color: var(--acc); font-size: .9rem; }
.observe { border-left: 3px solid var(--acc); padding: .1rem 1rem;
           margin: 1rem 0; }
.observe .tag, .donext h4 { font-size: .75rem; font-weight: 700;
           letter-spacing: .04em; text-transform: uppercase; }
.observe .tag { color: var(--acc); }
.donext { background: color-mix(in srgb, var(--acc) 9%, var(--bg));
          border-radius: 8px; padding: .8rem 1rem; margin: 1rem 0; }
.donext h4 { color: var(--acc); margin-bottom: .5rem; }
</style>
</head>
<body>
<article>
  <h1>Tensorleap analysis — PROJECT / VERSION</h1>
  <p class="meta">VERSION evaluated DATE · N insights (P top-level, S sub-insights) ·
     <a href="LINKS.insights_panel">open in Tensorleap</a></p>

  <div class="tiles">
    <div class="tile"><b>0.71</b><span>recall, all data</span></div>
    <div class="tile"><b>0.80</b><span>precision, all data</span></div>
    <div class="tile"><b>6</b><span>Failure Mode insights</span></div>
    <div class="tile"><b>2</b><span>Duplication insights</span></div>
  </div>

  <h2>Executive summary</h2>
  <p>…3–5 sentences; priority lives here…</p>
  <div class="tablewrap"><table>
    <tr><th>Insight</th><th>Issue</th><th>Severity</th><th>Samples</th><th>First action</th></tr>
    <tr class="typerow"><td colspan="5">Failure Mode</td></tr>
    <tr><td><a href="#insight-1">#1</a></td><td>…</td><td>3 of 3</td><td>314</td><td>…</td></tr>
  </table></div>

  <h2>Failure Mode <span class="count">3 of 6</span></h2>
  <p class="muted">Groups of samples where the model underperforms. Root cause per card is
     the analysis's diagnosis, one of: data gap, label quality, split problem, model behavior.</p>

  <section class="card sev3" id="insight-1">
    <h3>Heavy gaussian noise breaks the classifier</h3>
    <div class="chips">
      <span class="chip sev s3">Severity 3 of 3</span>
      <span class="chip">insight #1</span>
      <span class="chip">314 samples</span>
      <span class="chip">accuracy 0.42</span>
      <span class="chip">image-non-semantic space</span>
    </div>
    <p class="lede">Every sample in this group carries heavy gaussian noise, none of them are
       training samples, and accuracy halves on them.</p>
    <p class="rootcause"><b>Root cause — Data gap:</b> the corruption never appears in
       training; a coverage gap, not a model defect.</p>
    <p>…prose with evidence + latent-space translation…</p>
    <div class="facts">
      <div>
        <div class="splitbar"><span class="st-test" style="width:70%"></span><span class="st-unl" style="width:30%"></span></div>
        <div class="legend"><span class="train"><b></b>training 0</span> <span class="test"><b></b>test 221</span> <span class="unl"><b></b>unlabeled 93</span></div>
      </div>
      <div class="contrast">
        <span>this group</span><span class="track"><span class="fill" style="width:48%"></span></span><span>0.42 acc</span>
        <span>all data</span><span class="track"><span class="fill" style="width:100%"></span></span><span>0.87 acc</span>
      </div>
    </div>
    <div class="grid">…6 figures…</div>
    <details class="more"><summary>Show 18 more samples</summary><div class="grid">…</div></details>
    <div class="observe"><span class="tag">What the samples show</span><p>…</p></div>
    <div class="donext"><h4>Do next</h4><ul class="actions"><li>…</li></ul></div>
    <details class="explore"><summary>Explore in Tensorleap</summary>
      <p><a href="DEEP_LINK">Open version VERSION's Insights panel</a> and look for
         insight #1 — "low performance", severity 3.</p>
      <p class="muted">Latent space: … · 221 test + 93 unlabeled samples · 88 samples
         pre-selected for labeling.</p>
    </details>
  </section>

  <h2>Notes</h2>
  <ul><li>…</li></ul>
</article>
</body>
</html>
```

## report.md (the paste-into-a-ticket companion)

Just: title line, the Insights-panel link, the executive-summary paragraph,
the overview table, and each card's "Do next" checklist under its headline
(with the panel insight # in the heading). No images, no evidence sections —
link to `report.html` for those.
