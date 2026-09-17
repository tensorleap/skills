# yolov5_visdrone — baseline v0.2.0 verdict (manual annotation)

- Harness report (`yolov5_visdrone.md`): FAIL — Evaluate 6aa936582f329df70cd36e79 FAILED.
- Cause: infrastructure. Host disk at 90.7% = Elasticsearch high watermark → cluster red,
  the run's new inspection index had no assigned primary → `no_shard_available_action_exception`
  during Build Model, before any sample was evaluated. Full log: `evaluate-6aa93658.log`.
- Fix: freed disk (docker build cache + engine dev images) → 180 GiB free, ES yellow, no red indices.
- Re-run of the UNCHANGED integration (same code, same model, same cap):
  `leap push -m weights/yolov5s-visdrone.onnx -o v1-cap250 -b 8 -u metric --yes`
  → Push 6aa9441d2f329df70cd36e8a FINISHED, Evaluate 6aa9443a2f329df70cd36e94 FINISHED (2026-09-15 16:14).
- Effective benchmark verdict: PASS (integration valid on platform). Turn/token metrics from the
  harness report remain the comparable work metrics (105 turns; see `yolov5_visdrone.json`).
