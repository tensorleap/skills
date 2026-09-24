"""Tests for tl_perf profile / score / compare, on the synthetic integration.

    python -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_tl_perf import SyntheticEnv, tl_perf  # noqa: E402

FAST = ["--samples", "16", "--vis-samples", "6", "--diagnose-samples", "8",
        "--snapshot-samples", "4", "--batch-size", "4"]


def latest(synth, name):
    runs = os.path.join(synth.out, "runs")
    last = sorted(os.listdir(runs))[-1]
    with open(os.path.join(runs, last, name)) as fh:
        return json.load(fh)


class ProfileDetectsPlantedPathologies(unittest.TestCase):
    """One profile of an integration with a redundant cross-component decode, an O(n)
    per-sample scan, and a metric/visualizer post-processing cache."""

    @classmethod
    def setUpClass(cls):
        cls.synth = SyntheticEnv()
        cls.post_log = os.path.join(cls.synth.tmp.name, "post.log")
        cls.env = dict(SYNTH_REDUNDANT="1", SYNTH_DECODE_MS="3", SYNTH_SCAN_SIZE="600000",
                       SYNTH_POST_MS="1", SYNTH_POST_LOG=cls.post_log)
        proc = cls.synth.run("profile", *FAST, **cls.env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cls.profile = latest(cls.synth, "profile.json")
        score = cls.synth.run("score", **cls.env)
        assert score.returncode == 0, score.stdout + score.stderr
        cls.score = cls.synth.result("score.json")

    @classmethod
    def tearDownClass(cls):
        cls.synth.close()

    def test_every_generation_handler_runs_once_per_sample(self):
        for key, h in self.profile["generation"]["handlers"].items():
            self.assertAlmostEqual(h["calls_per_sample"], 1.0, msg=key)

    def test_redundant_decode_is_detected(self):
        diag = self.profile["diagnostics"]["server_order"]
        self.assertEqual(diag["reads"]["max_reads_of_one_file_in_one_sample"], 2)
        repeated = {r["function"]: r for r in diag["calls"]["repeated_calls"]}
        self.assertIn("input_encoder", repeated)
        self.assertIn("meta_mean", repeated["input_encoder"]["called_from"])
        self.assertAlmostEqual(repeated["input_encoder"]["calls_per_sample"], 2.0)

    def test_evidence_is_attributed_to_the_implicated_handlers(self):
        by_handler = {c["handler"]: c for c in self.score["candidates"]}
        self.assertTrue(any("re-invokes input_encoder" in e
                            for e in by_handler["metadata:mean_value"]["evidence"]))
        self.assertTrue(by_handler["input:x"]["evidence"])
        self.assertFalse(by_handler["metadata:scan_position"]["evidence"])

    def test_heaviest_planted_cost_ranks_first(self):
        self.assertEqual(self.score["candidates"][0]["handler"], "metadata:scan_position")

    def test_only_wired_handlers_are_profiled(self):
        metrics = set(self.profile["metrics"]["handlers"])
        self.assertEqual(metrics, {"metric:confidence", "loss:mse"})
        self.assertEqual(set(self.profile["visualizers"]["handlers"]), {"visualizer:bar"})

    def test_visualizers_do_not_inherit_metric_caches(self):
        """The metric fills the post-processing cache in the generation worker; the
        visualizer process must still compute it for every visualized sample."""
        vis_pid = latest(self.synth, "worker-visualize.json")["pid"]
        gen_pid = latest(self.synth, "worker-generate.json")["pid"]
        with open(self.post_log) as fh:
            pids = [int(line) for line in fh if line.strip()]
        n_vis = len(self.profile["visualizers"] and latest(self.synth, "plan-visualize.json")["vis_samples"])
        self.assertIn(gen_pid, pids)
        self.assertEqual(pids.count(vis_pid), n_vis)


class CompareVerdicts(unittest.TestCase):
    def setUp(self):
        self.synth = SyntheticEnv()

    def tearDown(self):
        self.synth.close()

    def profile(self, **env):
        proc = self.synth.run("profile", *FAST, **env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_lossless_fix_is_equivalent_and_faster(self):
        self.profile(SYNTH_REDUNDANT="1", SYNTH_DECODE_MS="4")                   # 2 decodes/sample
        self.profile(SYNTH_REDUNDANT="1", SYNTH_DECODE_MS="4", SYNTH_CACHE="1")  # shared cache
        proc = self.synth.run("compare")
        self.assertEqual(proc.returncode, tl_perf.EXIT_OK, proc.stdout + proc.stderr)
        report = self.synth.result("compare.json")
        self.assertTrue(report["equivalence"]["equivalent"])
        self.assertGreater(report["expected_total_seconds"]["gain"], 0.05)

    def test_each_change_must_earn_its_own_gain(self):
        """After an accepted fix, an unchanged re-run must not ride on the earlier win."""
        self.profile(SYNTH_REDUNDANT="1", SYNTH_DECODE_MS="4")
        self.profile(SYNTH_REDUNDANT="1", SYNTH_DECODE_MS="4", SYNTH_CACHE="1")
        self.assertEqual(self.synth.run("compare").returncode, tl_perf.EXIT_OK)   # accepted
        self.profile(SYNTH_REDUNDANT="1", SYNTH_DECODE_MS="4", SYNTH_CACHE="1")   # nothing changed
        proc = self.synth.run("compare")
        self.assertEqual(proc.returncode, tl_perf.EXIT_NO_GAIN, proc.stdout + proc.stderr)
        totals = self.synth.result("compare.json")["expected_total_seconds"]
        self.assertGreater(totals["cumulative_gain"], 0.05)       # still far better than baseline
        self.assertLess(abs(totals["gain"]), 0.05)                # but this step added nothing

    def test_behavior_change_is_not_equivalent(self):
        self.profile()
        self.profile(SYNTH_ALTER="1")
        proc = self.synth.run("compare")
        self.assertEqual(proc.returncode, tl_perf.EXIT_NOT_EQUIVALENT, proc.stdout + proc.stderr)
        fields = self.synth.result("compare.json")["equivalence"]["mismatched_fields"]
        self.assertTrue(any("scan_position" in f for f in fields), fields)

    def test_unchanged_code_is_equivalent_without_gain(self):
        # A real per-sample cost: at ~0.1 ms/sample, scheduler noise alone exceeds 5%.
        self.profile(SYNTH_DECODE_MS="3")
        self.profile(SYNTH_DECODE_MS="3")
        proc = self.synth.run("compare")
        self.assertEqual(proc.returncode, tl_perf.EXIT_NO_GAIN, proc.stdout + proc.stderr)

    def test_nondeterministic_outputs_use_the_distribution_check(self):
        self.profile(SYNTH_NOISE="1")
        with open(os.path.join(self.synth.out, "baseline", "determinism.json")) as fh:
            nondet = json.load(fh)
        self.assertTrue(any(f.startswith("input|x|") for f in nondet), nondet)
        self.profile(SYNTH_NOISE="1")
        proc = self.synth.run("compare")
        self.assertNotEqual(proc.returncode, tl_perf.EXIT_NOT_EQUIVALENT, proc.stdout + proc.stderr)
        eq = self.synth.result("compare.json")["equivalence"]
        self.assertTrue(eq["nondeterministic_fields"])
        self.assertTrue(all(v["ok"] for v in eq["nondeterministic_fields"].values()))


class EquivalenceUnitTest(unittest.TestCase):
    def test_values_equal_is_bit_exact_by_default(self):
        a = np.array([1.0, 2.0], dtype=np.float32)
        self.assertTrue(tl_perf.values_equal(a, a.copy())[0])
        self.assertFalse(tl_perf.values_equal(a, a + 1e-7)[0])
        self.assertTrue(tl_perf.values_equal(a, a + 1e-7, rtol=1e-5)[0])
        self.assertFalse(tl_perf.values_equal(a, a.astype(np.float64))[0])
        self.assertTrue(tl_perf.values_equal(np.array([np.nan]), np.array([np.nan]))[0])
        self.assertTrue(tl_perf.values_equal(float("nan"), float("nan"))[0])
        self.assertFalse(tl_perf.values_equal(1.0, 1.0000001)[0])

    def test_shifted_distribution_fails_and_stable_one_passes(self):
        rng = np.random.default_rng(0)
        samples = ["s%d" % i for i in range(20)]

        def run(offset):
            return {("f", s): float(rng.normal(offset, 1.0)) for s in samples}

        r1, r2 = run(0.0), run(0.0)
        nondet = tl_perf.determinism(r1, r2)
        self.assertIn("f", nondet)
        same = tl_perf.compare_snapshots(r1, run(0.0), nondet, 0, 0)
        shifted = tl_perf.compare_snapshots(r1, run(5.0), nondet, 0, 0)
        self.assertTrue(same["equivalent"])
        self.assertFalse(shifted["equivalent"])

    def test_deterministic_samples_stay_strict_inside_a_nondeterministic_field(self):
        r1 = {("f", "a"): 1.0, ("f", "b"): 2.0}
        r2 = {("f", "a"): 1.0, ("f", "b"): 2.5}      # only "b" is nondeterministic
        nondet = tl_perf.determinism(r1, r2)
        self.assertEqual(nondet["f"]["samples"], ["b"])
        changed = tl_perf.compare_snapshots(r1, {("f", "a"): 1.5, ("f", "b"): 2.2}, nondet, 0, 0)
        self.assertIn("f", changed["mismatched_fields"])


class SelectionUnitTest(unittest.TestCase):
    class FakeInteg:
        def __init__(self, ids, groups=None):
            self.sample_ids = ids
            self.groups = groups or {}

    def test_random_mode_is_a_permuted_subset(self):
        integ = self.FakeInteg({"training": list(range(100))})
        sel = tl_perf.select_samples(integ, 10, 0, "random")
        ids = [sid for _, sid in sel]
        self.assertEqual(len(ids), 10)
        self.assertNotEqual(ids, sorted(ids))
        self.assertEqual(sel, tl_perf.select_samples(integ, 10, 0, "random"))

    def test_contiguous_mode_is_a_sorted_window(self):
        integ = self.FakeInteg({"training": [5, 3, 9, 1, 7, 2, 8, 0, 6, 4]})
        ids = [sid for _, sid in tl_perf.select_samples(integ, 4, 1, "contiguous")]
        self.assertEqual(ids, list(range(ids[0], ids[0] + 4)))

    def test_grouped_datasets_select_whole_groups(self):
        groups = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
        integ = self.FakeInteg({"training": list(range(9))}, {"training": groups})
        sel = tl_perf.select_samples(integ, 4, 0, "random")
        self.assertTrue(all(isinstance(sid, list) and sid in groups for _, sid in sel))
        self.assertEqual(sum(len(sid) for _, sid in sel), 6)

    def test_chunks_never_mix_states(self):
        sel = [["training", 0], ["training", 1], ["training", 2], ["validation", 0]]
        out = list(tl_perf.chunks(sel, 2))
        self.assertEqual(out, [("training", [0, 1]), ("training", [2]), ("validation", [0])])


if __name__ == "__main__":
    unittest.main()
