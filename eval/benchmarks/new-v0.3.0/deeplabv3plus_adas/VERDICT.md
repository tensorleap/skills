# deeplabv3plus_adas — new-v0.3.0 verdict (manual, 2 eval attempts)

- Attempt 1: push 6aabc4af FINISHED; eval 6aabc4f8 FAILED at ~84min in Visualize Samples - MinIO NoSuchKey (cluster digest) + k8s Insufficient memory. Infrastructure, not integration.
- Attempt 2 (unchanged code, re-pushed via -o -u metric): push 6aabeae6 FINISHED, eval 6aabeb09 FINISHED (107min). Hit the SAME NoSuchKey signal at ~50min but as a non-fatal WARNING this time; job continued to completion.
- Effective verdict: PASS. Original authoring metrics (93 turns, push at 13:45) are the comparable numbers; the eval retry added no agent turns.
