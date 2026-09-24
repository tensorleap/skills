---
name: tensorleap-runtime-optimization
description: Use when a Tensorleap integration evaluates slowly or runs out of memory, or when the user asks to "optimize my Tensorleap runtime", "speed up the evaluation", "why is my integration slow", "find the bottleneck", "reduce memory", or "pick a batch size". Measures the model's inference floor, checks the environment and server fit, profiles every integration component (preprocess, encoders, metadata, metrics, loss, visualizers) the way Tensorleap actually runs them, applies lossless optimizations one at a time with equivalence checks, validates on the Tensorleap server, and writes a report naming the remaining bottleneck with evidence.
---

# Optimizing a Tensorleap integration's runtime

> Under construction (v0.1.0 scaffold). The full procedure is being authored; until then,
> use `scripts/tl_perf.py --help` for the available measurement commands and
> `reference/perf-execution-model.md` for how Tensorleap runs each component.
