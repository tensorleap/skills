"""Tests for scripts/tl_perf.py.

Run from the skill directory, in a Python environment that has code-loader, onnxruntime,
onnx and numpy:

    python -m unittest discover -s tests -v

CLI tests run tl_perf in a subprocess: code-loader keeps process-global state.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
TL_PERF = os.path.join(SCRIPTS, "tl_perf.py")
SYNTH = os.path.join(HERE, "synthetic_integration")

sys.path.insert(0, SCRIPTS)
import tl_perf  # noqa: E402


def build_model(path):
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    rng = np.random.default_rng(0)
    weights = numpy_helper.from_array(rng.standard_normal((8, 4)).astype(np.float32), "W")
    graph = helper.make_graph(
        [helper.make_node("MatMul", ["x", "W"], ["logits"]),
         helper.make_node("Softmax", ["logits"], ["y"], axis=1)],
        "synthetic",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, ["batch", 8])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, ["batch", 4])],
        initializer=[weights])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, path)


class SyntheticEnv:
    """Temp data + model for the synthetic integration, and a tl_perf runner."""

    def __init__(self, n=16):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data")
        self.out = os.path.join(self.tmp.name, "out")
        os.makedirs(self.data)
        rng = np.random.default_rng(1)
        for split in ("train", "val"):
            for i in range(n):
                np.save(os.path.join(self.data, "%s_%d.npy" % (split, i)),
                        rng.random(8).astype(np.float32))
        self.model = os.path.join(self.tmp.name, "model.onnx")
        build_model(self.model)
        self.env = dict(os.environ, SYNTH_DATA_DIR=self.data, SYNTH_MODEL_PATH=self.model,
                        SYNTH_N=str(n))

    def run(self, *args, **env):
        full_env = dict(self.env, **env)
        proc = subprocess.run([sys.executable, TL_PERF] + list(args) + ["--root", SYNTH, "--out", self.out],
                              env=full_env, capture_output=True, text=True, timeout=300)
        return proc

    def result(self, name):
        with open(os.path.join(self.out, name)) as fh:
            return json.load(fh)

    def close(self):
        self.tmp.cleanup()


class PreflightCliTest(unittest.TestCase):
    def setUp(self):
        self.synth = SyntheticEnv()

    def tearDown(self):
        self.synth.close()

    def test_valid_integration_exits_0(self):
        proc = self.synth.run("preflight")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = self.synth.result("preflight.json")
        self.assertTrue(report["integration"]["valid"])
        self.assertEqual(report["integration"]["setup"]["state_lengths"],
                         {"training": 16, "validation": 16})
        self.assertEqual(report["model"]["framework"], "onnxruntime")
        self.assertEqual(report["model"]["device"]["label"], "CPU")
        self.assertIn("grouped_preprocess", report["code_loader"]["features"])

    def test_broken_integration_exits_3(self):
        proc = self.synth.run("preflight", SYNTH_BREAK="1")
        self.assertEqual(proc.returncode, tl_perf.EXIT_INVALID_INTEGRATION, proc.stdout + proc.stderr)
        self.assertFalse(self.synth.result("preflight.json")["integration"]["valid"])


class FloorCliTest(unittest.TestCase):
    def setUp(self):
        self.synth = SyntheticEnv()

    def tearDown(self):
        self.synth.close()

    def test_floor_measures_and_recommends(self):
        proc = self.synth.run("floor", "--batch-sizes", "1,2,4", "--warmup", "1",
                              "--min-iters", "5", "--min-seconds", "0")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        floor = self.synth.result("floor.json")
        self.assertEqual(floor["input_source"], "dataset")
        self.assertEqual(floor["input_mapping"], {"x": "x"})
        self.assertIn(floor["recommended_batch_size"], (1, 2, 4))
        self.assertGreater(floor["t_inf_per_sample_mean_seconds"], 0)
        for entry in floor["sweep"]:
            self.assertGreaterEqual(entry["batch_seconds"]["n"], 5)
            self.assertLessEqual(entry["batch_seconds"]["p50"], entry["batch_seconds"]["p99"])

    def test_missing_model_exits_6(self):
        proc = self.synth.run("floor", SYNTH_MODEL_PATH="/nonexistent/model.onnx")
        self.assertEqual(proc.returncode, tl_perf.EXIT_MODEL_LOAD, proc.stdout + proc.stderr)


class PreflightExitCodeTest(unittest.TestCase):
    """Every preflight exit code, from the finding levels that produce it."""

    def exit_for(self, levels, tmp):
        report = {"python": "3", "platform": "x", "cpu": {"logical_cores": 1},
                  "memory": {"total_gb": 1.0}, "findings": [
                      tl_perf.finding(level, "c", "m") for level in levels]}
        args = type("A", (), {"out": tmp, "root": tmp})()
        return tl_perf._finish_preflight(args, report)

    def test_levels_map_to_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.exit_for([], tmp), tl_perf.EXIT_OK)
            self.assertEqual(self.exit_for(["info", "warn"], tmp), tl_perf.EXIT_OK)
            self.assertEqual(self.exit_for(["mismatch"], tmp), tl_perf.EXIT_ENV_MISMATCH)
            self.assertEqual(self.exit_for(["invalid", "mismatch"], tmp), tl_perf.EXIT_INVALID_INTEGRATION)
            self.assertEqual(self.exit_for(["blocker", "invalid"], tmp), tl_perf.EXIT_BLOCKER)

    def test_gpu_present_but_model_on_cpu_is_a_mismatch(self):
        findings = []
        tl_perf._device_findings({"nvidia_gpus": ["A100, 40 GB"],
                                  "model": {"framework": "onnxruntime", "device": {"label": "CPU"}}},
                                 findings)
        self.assertEqual([f["code"] for f in findings], ["gpu-unused"])
        self.assertEqual(findings[0]["level"], "mismatch")

    def test_cpu_only_host_is_informational(self):
        findings = []
        tl_perf._device_findings({"nvidia_gpus": None,
                                  "model": {"framework": "keras", "device": {"label": "CPU"}}},
                                 findings)
        self.assertEqual([(f["level"], f["code"]) for f in findings], [("info", "cpu-floor")])


class HelperTest(unittest.TestCase):
    def test_percentiles(self):
        p = tl_perf.percentiles([float(x) for x in range(1, 101)])
        self.assertEqual(p["n"], 100)
        self.assertAlmostEqual(p["mean"], 50.5)
        self.assertAlmostEqual(p["p50"], 50.5)
        self.assertAlmostEqual(p["p99"], 99.01)
        self.assertEqual(tl_perf.percentiles([]), {"n": 0})

    def test_knee_on_flat_curve_is_stable(self):
        def e(bs, tp):
            return {"batch_size": bs, "throughput_p50_samples_per_second": tp}
        # Throughput flattens after 4: both noisy variants must pick 4.
        run1 = [e(1, 259), e(2, 413), e(4, 692), e(8, 711), e(16, 747), e(32, 781), e(64, 820)]
        run2 = [e(1, 255), e(2, 407), e(4, 692), e(8, 730), e(16, 755), e(32, 811), e(64, 858)]
        self.assertEqual(tl_perf.knee(run1, 0.10), (run1[2], True))
        self.assertEqual(tl_perf.knee(run2, 0.10), (run2[2], True))

    def test_knee_not_reached_when_still_rising(self):
        entries = [{"batch_size": b, "throughput_p50_samples_per_second": 100.0 * b} for b in (1, 2, 4)]
        self.assertEqual(tl_perf.knee(entries, 0.10), (entries[-1], False))

    def test_map_inputs_prefers_name_then_shape(self):
        specs = [{"name": "input_1", "shape": [None, 350, 250, 1]}]
        dataset = {"image": (350, 250, 1), "image_noise": (350, 250, 1)}
        self.assertEqual(tl_perf.map_inputs(specs, dataset), {"input_1": "image"})
        specs = [{"name": "image_noise", "shape": [None, 350, 250, 1]}]
        self.assertEqual(tl_perf.map_inputs(specs, dataset), {"image_noise": "image_noise"})
        specs = [{"name": "x", "shape": [None, 10]}]
        self.assertIsNone(tl_perf.map_inputs(specs, dataset))


if __name__ == "__main__":
    unittest.main()
