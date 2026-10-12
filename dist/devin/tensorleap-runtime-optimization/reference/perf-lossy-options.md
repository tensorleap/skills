# Options that change behavior (last resort, consent required)

Use this page **only** after the lossless loop is exhausted **and** one component still
dominates (see `skill.md`, Phase 5). Every option here changes what the user sees in
Tensorleap. Present options; never apply one without an explicit yes.

## How to present an option

For each option give, in one short block:

1. **What changes** — exactly which output (visualizer, metadata column, metric) and how.
2. **Expected gain** — from the profile: the component's cost × samples it runs on, and
   the resulting share of total runtime (e.g. "metadata `image_stats` is 15 ms/sample, 85% of
   generation; dropping its texture statistics saves ≈ 9 ms/sample, −35% expected
   runtime").
3. **Cost** — what the user loses or what gets less precise, and who it affects (e.g.
   "those columns disappear from population exploration and insights").
4. **Reversibility** — one commit, revertable.

Then ask, once, for the options together:

> These changes would make evaluation faster but change what you see in Tensorleap:
> [options]. Which, if any, should I apply?

Apply only the accepted ones, one commit each, and run `tl_perf compare` afterwards: it
**should** report the declared fields as changed (exit 8) and **nothing else** — any other
difference means the change leaked beyond what was agreed; revert it.

## The menu

| Option | Typical target | Gain driver | Behavior change |
|---|---|---|---|
| Smaller visualizer output | heavy image/video visualizers | pixels rendered and stored | lower-resolution images in the UI |
| Fewer visualizers | many visualizers of the same source | visualizers × visualized samples | some views disappear |
| Drop or merge metadata | expensive or redundant metadata | per-sample metadata cost | columns disappear from analysis |
| Cheaper metadata estimates | full-resolution statistics | computing on a downsampled copy | values change (approximate) |
| Lower-precision metadata | high-cardinality float metadata | storage/transfer | rounded values |
| Approximate metrics | volumetric/distance metrics | fewer points, thresholds or scales | metric values change |
| Seeding random augmentations | nondeterministic encoders (catalog T) | — (correctness) | inputs change from random to fixed |

Record each decision (accepted / declined) in the report's `lossy_options`.
