"""Tests for tl_perf fit and report.

    python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_tl_perf import SyntheticEnv, tl_perf  # noqa: E402

SMALL = ["--batch-sizes", "1,2,4,8"]


class FitTest(unittest.TestCase):
    def setUp(self):
        self.synth = SyntheticEnv()

    def tearDown(self):
        self.synth.close()

    def test_fit_recommends_a_batch_size(self):
        proc = self.synth.run("fit", *SMALL)
        self.assertEqual(proc.returncode, tl_perf.EXIT_OK, proc.stdout + proc.stderr)
        fit = self.synth.result("fit.json")
        self.assertIn(fit["recommended_batch_size"], (1, 2, 4, 8))
        self.assertEqual(fit["per_sample_tensor_bytes"], 4 * (8 + 4))    # x[8] + label[4], float32
        self.assertEqual(fit["rows_total"], 32)
        self.assertTrue(all(t["fits"] for t in fit["table"]))
        self.assertEqual(fit["batch_support"], {"metric:confidence": "ok", "loss:mse": "ok"})

    def test_oversize_is_predicted_not_to_fit(self):
        proc = self.synth.run("fit", *SMALL, "--memory-gb", "0.01")
        self.assertEqual(proc.returncode, tl_perf.EXIT_NOT_FIT, proc.stdout + proc.stderr)
        self.assertIsNone(self.synth.result("fit.json")["recommended_batch_size"])

    def test_batch_incompatible_metric_is_flagged(self):
        proc = self.synth.run("fit", *SMALL, SYNTH_BAD_BATCH="1")
        self.assertEqual(proc.returncode, tl_perf.EXIT_OK, proc.stdout + proc.stderr)
        support = self.synth.result("fit.json")["batch_support"]
        self.assertEqual(support["metric:confidence"], "returns no per-sample batch dimension")
        self.assertEqual(support["loss:mse"], "ok")


class HelpersTest(unittest.TestCase):
    def test_linear_fit(self):
        a, b = tl_perf._linear_fit([1, 2, 4, 8], [1.5, 2.0, 3.0, 5.0])
        self.assertAlmostEqual(a, 1.0)
        self.assertAlmostEqual(b, 0.5)

    def test_has_batch_dim(self):
        import numpy as np
        self.assertTrue(tl_perf._has_batch_dim(np.zeros(2), 2))
        self.assertTrue(tl_perf._has_batch_dim({"a": np.zeros(2), "b": np.ones(2)}, 2))
        self.assertTrue(tl_perf._has_batch_dim([["cm"], ["cm"]], 2))
        self.assertFalse(tl_perf._has_batch_dim(np.zeros(1), 2))
        self.assertFalse(tl_perf._has_batch_dim(np.float32(1.0), 2))


VALID_REPORT = {
    "title": "Runtime optimization — synthetic integration",
    "summary": "Generation was 3x inference; one lossless fix applied.",
    "optimizations": [{
        "problem": "The metadata re-ran the input encoder's decode for every sample",
        "change": "One small cache shared by the encoder and the metadata",
        "evidence": "input_encoder ran 2.0x per sample; the same file was read 2x per sample",
        "equivalence": "bit-identical on 32 samples (tl_perf compare exit 0)",
        "before": "12.4 ms/sample", "after": "8.1 ms/sample", "gain": "-35%",
    }],
    "remaining_bottleneck": {"component": "metadata:scan_position", "share": "61%",
                             "evidence": "5.9 ms per sample, 450x inference",
                             "explanation": "a linear scan per sample"},
    "lossy_options": [{"option": "drop scan_position", "gain": "-60%",
                       "cost": "the metadata column disappears", "decision": "pending"}],
    "server_validation": {"status": "FINISHED", "job": "abc123", "duration": "4 min"},
    "tensorleap_actions": [{"need": "confirm the batch size reached during evaluation",
                            "evidence": "local batch 8 vs observed rows", "impact": "unknown"}],
    "coverage_caveats": ["the noise branch never ran on this data"],
}


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_report(self, doc=None, raw=None):
        path = os.path.join(self.out, "report.json")
        with open(path, "w") as fh:
            fh.write(raw if raw is not None else json.dumps(doc))
        return tl_perf.main(["report", "--root", self.out, "--out", self.out])

    def test_valid_report_renders_every_section(self):
        self.assertEqual(self.run_report(VALID_REPORT), tl_perf.EXIT_OK)
        with open(os.path.join(self.out, "report.md")) as fh:
            md = fh.read()
        for heading in ("# Runtime optimization", "## Environment", "## Runtime breakdown",
                        "## Optimizations applied", "## Remaining bottleneck",
                        "## Options that would change behavior", "## Server validation",
                        "## Recommended Tensorleap actions", "## What was not verified"):
            self.assertIn(heading, md)
        self.assertIn("metadata:scan_position", md)
        self.assertIn("bit-identical on 32 samples", md)

    def test_missing_required_field_exits_11(self):
        doc = dict(VALID_REPORT)
        del doc["remaining_bottleneck"]
        self.assertEqual(self.run_report(doc), tl_perf.EXIT_BAD_REPORT)

    def test_optimization_without_equivalence_exits_11(self):
        doc = json.loads(json.dumps(VALID_REPORT))
        del doc["optimizations"][0]["equivalence"]
        self.assertEqual(self.run_report(doc), tl_perf.EXIT_BAD_REPORT)

    def test_invalid_json_exits_11(self):
        self.assertEqual(self.run_report(raw="{not json"), tl_perf.EXIT_BAD_REPORT)


if __name__ == "__main__":
    unittest.main()
