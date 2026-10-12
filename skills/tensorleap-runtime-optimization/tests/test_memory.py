"""Tests for the user-code memory pass (tl_perf profile, step 6), on the synthetic integration.

    python -m unittest discover -s tests -v
"""
import importlib.util
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_tl_perf import SyntheticEnv, tl_perf  # noqa: E402

FAST = ["--samples", "16", "--vis-samples", "6", "--diagnose-samples", "8",
        "--snapshot-samples", "4", "--batch-size", "4", "--memory-samples", "16", "--no-import-costs"]
FAST_WITH_IMPORT_COSTS = [a for a in FAST if a != "--no-import-costs"]
HAS_PANDAS = importlib.util.find_spec("pandas") is not None
HAS_MPL = importlib.util.find_spec("matplotlib") is not None


def user_memory(synth):
    with open(os.path.join(synth.out, "profile.json")) as fh:
        return json.load(fh)["user_memory"]


class MemoryPassFindsPlantedProblems(unittest.TestCase):
    """One profile with every memory pathology planted."""

    @classmethod
    def setUpClass(cls):
        cls.synth = SyntheticEnv()
        env = dict(SYNTH_MEM_UNUSED="64", SYNTH_MEM_DUP="48", SYNTH_MEM_STRINGS="200000",
                   SYNTH_MEM_LEAK="256", SYNTH_MEM_VIEW="64", SYNTH_CACHE="1", SYNTH_MEM_UNBOUNDED="1")
        if HAS_PANDAS:
            env["SYNTH_MEM_IMPORT"] = "1"
        if HAS_MPL:
            env["SYNTH_MEM_FIG"] = "1"
            env["SYNTH_MEM_LATE_IMPORT"] = "1"
        proc = cls.synth.run("profile", *FAST_WITH_IMPORT_COSTS, **env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cls.um = user_memory(cls.synth)
        proc = cls.synth.run("score")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        with open(os.path.join(cls.synth.out, "score.json")) as fh:
            cls.score = json.load(fh)
        cls.by_class = {}
        for f in cls.um["findings"]:
            cls.by_class.setdefault(f["class"], []).append(f)

    @classmethod
    def tearDownClass(cls):
        cls.synth.close()

    def paths(self, cls):
        return [f["path"] for f in self.by_class.get(cls, [])]

    def test_footprint_and_breakdown(self):
        self.assertGreater(self.um["footprint_gb"], 0.2)          # 64 + 2x48 + 64 MB + strings
        self.assertGreater(self.um["breakdown_gb"]["import"], 0.1)  # the import-time globals
        self.assertGreater(self.um["breakdown_gb"]["preprocess"], 0.07)   # the two preprocess copies
        self.assertIsNotNone(self.um["noise"])

    def test_largest_holder_is_listed_first(self):
        top = [h["path"] for h in self.um["holders"][:3]]
        self.assertTrue(any(p.endswith("UNUSED_TABLE") for p in top), top)

    def test_exact_narrower_dtype(self):
        unused = [f for f in self.by_class.get("M4", []) if f["path"].endswith("UNUSED_TABLE")]
        self.assertTrue(unused)
        self.assertTrue(unused[0]["lossless_hint"])

    def test_duplicate_copies(self):
        self.assertTrue(any(p.endswith("DUP_A") or p.endswith("DUP_B") for p in self.paths("M3")))

    def test_view_keeping_a_big_array_alive(self):
        self.assertTrue(any(p.endswith("KEPT_SLICE") for p in self.paths("M9")))

    def test_strings_and_object_heavy_containers(self):
        self.assertTrue(any(p.endswith("SAMPLE_PATHS") for p in self.paths("M5")))
        self.assertTrue(any(p.endswith("SAMPLE_PATHS") for p in self.paths("M10")))

    def test_growth_and_unbounded_cache(self):
        self.assertTrue(any(p.endswith("LEAKED") for p in self.paths("M8")))
        self.assertTrue(any(p.endswith("_decode_unbounded") for p in self.paths("M6")))
        self.assertGreater(self.um["growth_mb_per_1k_samples"], 50)    # 256 KB per call

    @unittest.skipUnless(HAS_MPL, "matplotlib not installed")
    def test_unclosed_figures(self):
        self.assertIn("Figure", self.paths("M8"))

    @unittest.skipUnless(HAS_PANDAS, "pandas not installed")
    def test_unused_import(self):
        self.assertIn("pandas", self.paths("M1"))
        f = next(f for f in self.by_class["M1"] if f["path"] == "pandas")
        self.assertEqual(f["variant"], "unused")
        self.assertIn("never referenced at leap_integration.py:", f["detail"])
        self.assertIn("pandas", self.um["import_costs_mb"])
        self.assertGreater(self.um["import_costs_seconds"]["pandas"], 0)
        # an import no sample needs is start-up every worker pays: a runtime candidate too
        c = next(c for c in self.score["candidates"] if c["handler"] == "startup:import:pandas")
        self.assertEqual(c["block"], "startup")
        self.assertAlmostEqual(c["expected_seconds"], self.um["import_costs_seconds"]["pandas"])
        m = next(c for c in self.score["memory"]["candidates"] if c["target"] == "pandas")
        self.assertTrue(any("importing it alone" in e for e in m["evidence"]))

    @unittest.skipUnless(HAS_MPL, "matplotlib not installed")
    def test_an_import_only_the_visualizers_need(self):
        f = next(f for f in self.by_class["M1"] if f["path"] == "matplotlib")
        self.assertEqual((f["variant"], f["stage"]), ("lazy", "visualizers"))
        self.assertIn("matplotlib", self.um["packages"]["used_by_stage"]["visualizers"])
        self.assertNotIn("matplotlib", self.um["packages"]["used_by_stage"].get("generation", []))
        # moving it only moves its cost: never a runtime candidate
        self.assertFalse(any(c["handler"] == "startup:import:matplotlib" for c in self.score["candidates"]))

    def test_per_handler_transient_peaks(self):
        h = self.um["handlers"]
        self.assertIn("input:x", h)
        self.assertEqual({e["phase"] for e in h.values()} >= {"generation", "metrics"}, True)


class MemoryPassCleanControl(unittest.TestCase):
    def test_no_findings_without_planted_problems(self):
        synth = SyntheticEnv()
        try:
            proc = synth.run("profile", *FAST)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            um = user_memory(synth)
            self.assertEqual([f for f in um["findings"] if f["class"] != "M12"], [])
            self.assertLess(um["footprint_gb"], 0.2)
        finally:
            synth.close()


class MemoryCompareVerdicts(unittest.TestCase):
    """compare --objective memory, and the runtime objective's footprint guard."""

    def setUp(self):
        self.synth = SyntheticEnv()

    def tearDown(self):
        self.synth.close()

    def profile(self, **env):
        proc = self.synth.run("profile", *FAST, SYNTH_DECODE_MS="4", **env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def compare(self, *extra):
        proc = self.synth.run("compare", *extra)
        report = self.synth.result("compare.json")
        return proc.returncode, report

    def test_lossless_memory_fix_is_accepted(self):
        self.profile(SYNTH_MEM_UNUSED="160")                  # baseline: an unused 160 MB table
        self.profile()                                        # the fix: table gone
        code, r = self.compare("--objective", "memory", "--runtime-tolerance", "0.5")
        self.assertEqual(code, tl_perf.EXIT_OK, r)
        self.assertGreater(r["memory_gate"]["drop_gb"], 0.1)

    def test_memory_fix_that_costs_runtime(self):
        self.profile(SYNTH_MEM_UNUSED="160")
        proc = self.synth.run("profile", *FAST, SYNTH_DECODE_MS="40")   # fixed, but much slower
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        code, r = self.compare("--objective", "memory", "--no-accept")
        self.assertEqual(code, tl_perf.EXIT_RUNTIME_REGRESSION, r)      # GREEN: noise-level tolerance
        code, r = self.compare("--objective", "memory", "--no-accept", "--runtime-tolerance", "100")
        self.assertEqual(code, tl_perf.EXIT_OK, r)

    def test_red_triage_widens_the_runtime_tolerance(self):
        self.profile(SYNTH_MEM_UNUSED="160")
        proc = self.synth.run("score", "--memory-symptom", "oom")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.synth.result("score.json")["memory"]["status"], "RED")
        self.profile()
        code, r = self.compare("--objective", "memory", "--no-accept")
        self.assertEqual(r["runtime_tolerance"], 0.15)
        self.assertEqual(r["memory_status"], "RED")

    def test_no_memory_gain_and_not_equivalent(self):
        self.profile()
        self.profile()
        code, _ = self.compare("--objective", "memory", "--no-accept")
        self.assertEqual(code, tl_perf.EXIT_NO_GAIN)
        self.profile(SYNTH_ALTER="1")
        code, _ = self.compare("--objective", "memory", "--no-accept")
        self.assertEqual(code, tl_perf.EXIT_NOT_EQUIVALENT)

    def test_runtime_objective_guards_the_user_footprint(self):
        self.profile()
        self.profile(SYNTH_MEM_UNUSED="256")                  # same speed, +256 MB per worker
        code, r = self.compare("--no-accept", "--min-gain", "-1")
        self.assertEqual(code, tl_perf.EXIT_MEMORY_REGRESSION, r)
        self.assertEqual(r["peak_rss_gb"]["source"], "user-code footprint (memory pass)")


class MemoryPriority(unittest.TestCase):
    """--priority memory: the memory loop first whatever the triage, a memory fix may cost up to
    15% runtime, and a runtime fix may not grow memory."""

    def setUp(self):
        self.synth = SyntheticEnv()

    def tearDown(self):
        self.synth.close()

    def test_triage_with_memory_first(self):
        t = tl_perf.memory_triage({"user_memory": {"footprint_gb": 0.1}}, 64.0, "test", priority="memory")
        self.assertEqual(t["status"], "GREEN")                                  # nothing is wrong ...
        self.assertEqual(t["order"], "memory loop first, then runtime")         # ... but memory goes first
        self.assertEqual(t["memory_loop"], "all lossless memory candidates")
        self.assertEqual((t["runtime_tolerance"], t["runtime_fix_memory_growth"]), (0.15, 0.0))
        r = tl_perf.memory_triage({"user_memory": {"footprint_gb": 0.1}}, 64.0, "test", priority="runtime")
        self.assertEqual((r["order"], r["runtime_tolerance"], r["runtime_fix_memory_growth"]),
                         ("runtime loop first, then memory", 0.03, 0.10))
        self.assertEqual(tl_perf.memory_triage({"user_memory": {"footprint_gb": 0.1}}, 64.0, "test")["order"],
                         "memory loop first, then runtime")

    def test_score_and_compare_follow_the_priority(self):
        proc = self.synth.run("profile", *FAST, SYNTH_DECODE_MS="4", SYNTH_MEM_UNUSED="160")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc = self.synth.run("score", "--priority", "memory")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("priority memory", proc.stdout)
        sc = self.synth.result("score.json")
        self.assertEqual((sc["priority"], sc["memory"]["status"]), ("memory", "GREEN"))
        proc = self.synth.run("profile", *FAST, SYNTH_DECODE_MS="4")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc = self.synth.run("compare", "--objective", "memory", "--no-accept")
        r = self.synth.result("compare.json")
        self.assertEqual(r["runtime_tolerance"], 0.15)
        proc = self.synth.run("compare", "--no-accept", "--min-gain", "-1")
        r = self.synth.result("compare.json")
        self.assertEqual(r["thresholds"]["max_mem_increase"], 0.0)


class EachPlantedFixIsAccepted(unittest.TestCase):
    """Remove one planted problem at a time: compare --objective memory accepts the fix."""

    CASES = {
        "duplicate copies": dict(SYNTH_MEM_DUP="64"),
        "view keeping a big array alive": dict(SYNTH_MEM_VIEW="128"),
        "object-heavy string list": dict(SYNTH_MEM_STRINGS="800000"),
        "growth with the samples": dict(SYNTH_MEM_LEAK="4096"),
    }

    def test_fixes(self):
        for label, env in self.CASES.items():
            with self.subTest(label):
                synth = SyntheticEnv()
                try:
                    proc = synth.run("profile", *FAST, SYNTH_DECODE_MS="4", **env)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    proc = synth.run("profile", *FAST, SYNTH_DECODE_MS="4")
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    proc = synth.run("compare", "--objective", "memory", "--runtime-tolerance", "0.5")
                    self.assertEqual(proc.returncode, tl_perf.EXIT_OK, proc.stdout + proc.stderr)
                finally:
                    synth.close()

    def test_memory_candidates_are_ranked(self):
        synth = SyntheticEnv()
        try:
            proc = synth.run("profile", *FAST, SYNTH_MEM_DUP="48", SYNTH_MEM_VIEW="96", SYNTH_MEM_UNUSED="64")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            proc = synth.run("score", "--objective", "memory")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            cands = synth.result("score.json")["memory"]["candidates"]
            priorities = [c["priority"] for c in cands]
            self.assertEqual(priorities, sorted(priorities, reverse=True))
            self.assertTrue(cands[0]["target"].endswith("KEPT_SLICE"), cands[0])   # 96 MB freed, exact
            classes = {c["class"] for c in cands}
            self.assertTrue({"M3", "M4", "M9"} <= classes, classes)
        finally:
            synth.close()


class CensusUnitTest(unittest.TestCase):
    def test_dtype_hints(self):
        import numpy as np
        c = tl_perf.Census()
        c.size(np.arange(3_000_000, dtype=np.int64), "ids")
        c.size(np.full(2_000_000, 0.1), "inexact")
        hints = {f["path"]: f for f in c.findings}
        self.assertIn("fit int32", hints["ids"]["detail"])        # values up to 3M
        self.assertFalse(hints["inexact"]["lossless_hint"])     # 0.1 is not exact in float32

    def test_sampled_container_size_is_scaled(self):
        c = tl_perf.Census()
        n = c.size(["x" * 100 + str(i) for i in range(50000)], "strings")
        self.assertGreater(n, 50000 * 100)


if __name__ == "__main__":
    unittest.main()
