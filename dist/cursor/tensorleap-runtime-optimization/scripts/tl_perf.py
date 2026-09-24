#!/usr/bin/env python3
"""tl_perf — measurement CLI for the tensorleap-runtime-optimization skill.

Runs inside the integration's own Python environment (it imports code_loader).
Stdlib + numpy + code_loader; psutil is used when present. Python 3.8+.

Subcommands:
  preflight  environment, devices, code-loader features, integration + model load
  floor      standalone model inference time (percentiles, batch-size sweep)
  fit        per-sample size and server memory/disk headroom per batch size
  profile    every integration component, run the way Tensorleap runs it
  score      rank optimization candidates from a profile
  compare    equivalence + timing/memory delta against the baseline
  report     render report.json into report.md

Every subcommand writes JSON under --out (default: tensorleap/runtime-optimization/
in the integration root) and prints a short human summary.

Exit codes:
  0 ok            2 blocker                 3 integration invalid
  4 env mismatch  5 predicted not to fit    6 model won't load
  7 run failed    8 not equivalent          9 no gain
  10 memory regression                      11 invalid report.json
  64 not implemented
"""

import argparse
import contextlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import time

EXIT_OK = 0
EXIT_BLOCKER = 2
EXIT_INVALID_INTEGRATION = 3
EXIT_ENV_MISMATCH = 4
EXIT_NOT_FIT = 5
EXIT_MODEL_LOAD = 6
EXIT_RUN_FAILED = 7
EXIT_NOT_EQUIVALENT = 8
EXIT_NO_GAIN = 9
EXIT_MEMORY_REGRESSION = 10
EXIT_BAD_REPORT = 11
NOT_IMPLEMENTED = 64

DEFAULT_OUT = os.path.join("tensorleap", "runtime-optimization")
DEFAULT_BATCH_SIZES = "1,2,4,8,16,32,64"

# Measurement stability: keep code-loader's usage analytics off the timed path.
os.environ.setdefault("TL_DISABLE_ANALYTICS", "1")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def percentiles(values):
    """mean + P50/P90/P95/P99 (linear interpolation) + min/max/n, in the input's unit."""
    if not values:
        return {"n": 0}
    xs = sorted(values)
    n = len(xs)

    def q(p):
        if n == 1:
            return xs[0]
        pos = (n - 1) * p
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)

    return {
        "n": n,
        "mean": sum(xs) / n,
        "p50": q(0.50),
        "p90": q(0.90),
        "p95": q(0.95),
        "p99": q(0.99),
        "min": xs[0],
        "max": xs[-1],
    }


def finding(level, code, message, **extra):
    item = {"level": level, "code": code, "message": message}
    item.update(extra)
    return item


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)
        fh.write("\n")


def out_dir(args):
    return args.out if os.path.isabs(args.out) else os.path.join(args.root, args.out)


def fmt_ms(seconds):
    return "%.3f ms" % (seconds * 1000.0)


@contextlib.contextmanager
def captured_stdout():
    """User integration code prints freely; keep it out of our output but keep it."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


@contextlib.contextmanager
def working_dir(path):
    """Integration code resolves relative paths from its own root, as on the platform."""
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #

def cpu_info():
    info = {"logical_cores": os.cpu_count(), "machine": platform.machine(),
            "processor": platform.processor() or None}
    if sys.platform == "darwin":
        try:
            info["model"] = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    return info


def memory_info():
    info = {"total_gb": None, "available_gb": None, "source": None}
    try:
        import psutil  # optional
        vm = psutil.virtual_memory()
        info.update(total_gb=vm.total / 2 ** 30, available_gb=vm.available / 2 ** 30,
                    source="psutil")
        return info
    except ImportError:
        pass
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        info.update(total_gb=total / 2 ** 30, source="sysconf")
    except (ValueError, OSError, AttributeError):
        pass
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/meminfo") as fh:
                for line in fh:
                    if line.startswith("MemAvailable:"):
                        info["available_gb"] = int(line.split()[1]) / 2 ** 20
        except OSError:
            pass
    return info


def peak_rss_gb():
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes, Linux kilobytes.
        return peak / 2 ** 30 if sys.platform == "darwin" else peak / 2 ** 20
    except (ImportError, OSError):
        return None


def disk_info(path):
    usage = shutil.disk_usage(path)
    return {"path": os.path.abspath(path), "free_gb": usage.free / 2 ** 30,
            "total_gb": usage.total / 2 ** 30}


def nvidia_gpus():
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def framework_info(include_tensorflow=True):
    info = {}
    try:
        import onnxruntime as ort
        info["onnxruntime"] = {"version": ort.__version__,
                               "available_providers": ort.get_available_providers()}
    except ImportError:
        info["onnxruntime"] = None
    if include_tensorflow:
        try:
            with captured_stdout():
                import tensorflow as tf
            info["tensorflow"] = {
                "version": tf.__version__,
                "gpus": [d.name for d in tf.config.list_physical_devices("GPU")],
            }
        except ImportError:
            info["tensorflow"] = None
    return info


def code_loader_info():
    try:
        import code_loader  # noqa: F401
    except ImportError:
        return None
    try:
        from importlib.metadata import version
        ver = version("code-loader")
    except Exception:
        ver = None
    features = {}
    try:
        from code_loader.contract.datasetclasses import PreprocessResponse
        features["grouped_preprocess"] = hasattr(PreprocessResponse, "is_grouped")
    except ImportError:
        features["grouped_preprocess"] = False
    try:
        import code_loader.utils as cl_utils
        features["float16_metric_simulation"] = hasattr(cl_utils, "ENGINE_STORAGE_DTYPE")
    except ImportError:
        features["float16_metric_simulation"] = False
    return {"version": ver, "features": features}


# --------------------------------------------------------------------------- #
# Integration loading
# --------------------------------------------------------------------------- #

def read_entry_file(root):
    """entryFile from leap.yaml (plain line scan; no YAML dependency)."""
    path = os.path.join(root, "leap.yaml")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("entryFile:"):
                    value = line.split(":", 1)[1].strip().strip("'\"")
                    if value:
                        return value
    return "leap_integration.py"


def _state_name(state):
    return getattr(state, "name", str(state))


def _flat_ids(ids):
    if ids and isinstance(ids[0], (list, tuple)):
        return [sid for group in ids for sid in group]
    return list(ids)


class Integration:
    """A loaded integration: LeapLoader + check_dataset result + sample ids."""

    def __init__(self, root, entry):
        self.root = os.path.abspath(root)
        self.entry = entry
        self.loader = None
        self.result = None
        self.sample_ids = {}      # state name -> flat list of sample ids
        self.states = {}          # state name -> DataStateEnum
        self.grouped = False
        self.log = ""

    def load(self):
        if self.root not in sys.path:
            sys.path.insert(0, self.root)
        from code_loader import LeapLoader
        with working_dir(self.root), captured_stdout() as buf:
            self.loader = LeapLoader(self.root, self.entry)
            self.result = self.loader.check_dataset()
            if self.result.is_valid:
                raw = self.loader.get_preprocess_sample_ids()
                for state, ids in raw.items():
                    ids = list(ids)
                    self.grouped = self.grouped or bool(ids and isinstance(ids[0], (list, tuple)))
                    name = _state_name(state)
                    self.sample_ids[name] = _flat_ids(ids)
                    self.states[name] = state
        self.log = buf.getvalue()
        return self

    @property
    def valid(self):
        return bool(self.result and self.result.is_valid)

    def failed_payloads(self):
        if not self.result:
            return []
        return [{"name": p.name, "message": (getattr(p, "display", None) or {})}
                for p in self.result.payloads if not p.is_passed]

    def setup_summary(self):
        setup = self.result.setup if self.result else None
        if setup is None:
            return None
        return {
            "inputs": [{"name": i.name, "shape": list(i.shape)} for i in setup.inputs],
            "ground_truths": [{"name": o.name, "shape": list(o.shape)} for o in setup.outputs],
            "metadata": [m.name for m in setup.metadata],
            "metrics": [m.name for m in getattr(setup, "metrics", [])],
            "custom_losses": [c.name for c in setup.custom_losses],
            "visualizers": [v.name for v in setup.visualizers],
            "state_lengths": {k: len(v) for k, v in self.sample_ids.items()},
            "grouped": self.grouped,
        }

    def get_sample(self, state_name, sample_id):
        with working_dir(self.root), captured_stdout():
            return self.loader.get_sample(self.states[state_name], sample_id)


def find_model_loader():
    """The integration's @tensorleap_load_model function.

    code-loader stores the user's raw integration-test function on the binder; its
    __globals__ is the integration module's namespace, where the decorated loader lives.
    """
    from code_loader.inner_leap_binder import global_leap_binder
    test_fn = getattr(global_leap_binder, "integration_test_func", None)
    if test_fn is None:
        return None, "no @tensorleap_integration_test function is registered"
    found = [v for v in getattr(test_fn, "__globals__", {}).values()
             if callable(v) and getattr(v, "__qualname__", "").startswith("tensorleap_load_model.")]
    if not found:
        return None, "no @tensorleap_load_model function found in the integration module"
    if len(found) > 1:
        return None, "more than one @tensorleap_load_model function found"
    return found[0], None


class LoadedModel:
    def __init__(self, model, framework, fixed_batch_size):
        self.model = model
        self.framework = framework
        self.fixed_batch_size = fixed_batch_size

    def device(self):
        if self.framework == "onnxruntime":
            providers = self.model.get_providers()
            gpu = [p for p in providers if p not in ("CPUExecutionProvider", "AzureExecutionProvider")]
            return {"label": "GPU" if gpu else "CPU", "providers_used": providers}
        try:
            import tensorflow as tf
            gpus = tf.config.list_logical_devices("GPU")
        except ImportError:
            gpus = []
        return {"label": "GPU" if gpus else "CPU", "tensorflow_gpus": [g.name for g in gpus]}


def load_model(integ):
    loader_fn, error = find_model_loader()
    if loader_fn is None:
        raise RuntimeError(error)
    with working_dir(integ.root), captured_stdout():
        handle = loader_fn()
    model = getattr(handle, "model", handle)
    try:
        import onnxruntime as ort
        is_onnx = isinstance(model, ort.InferenceSession)
    except ImportError:
        is_onnx = False
    return LoadedModel(model, "onnxruntime" if is_onnx else "keras",
                       getattr(handle, "fixed_batch_size", None))


# --------------------------------------------------------------------------- #
# Model inputs + inference
# --------------------------------------------------------------------------- #

_ORT_DTYPES = {
    "tensor(float)": "float32", "tensor(double)": "float64", "tensor(float16)": "float16",
    "tensor(int64)": "int64", "tensor(int32)": "int32", "tensor(uint8)": "uint8",
    "tensor(int8)": "int8", "tensor(bool)": "bool",
}


def model_input_specs(lm):
    specs = []
    if lm.framework == "onnxruntime":
        for i in lm.model.get_inputs():
            shape = [d if isinstance(d, int) and d > 0 else None for d in i.shape]
            specs.append({"name": i.name, "shape": shape,
                          "dtype": _ORT_DTYPES.get(i.type, "float32")})
    else:
        for t in lm.model.inputs:
            shape = [d if isinstance(d, int) else None for d in list(t.shape)]
            dtype = getattr(t.dtype, "name", None) or str(t.dtype)
            specs.append({"name": getattr(t, "name", "input"), "shape": shape, "dtype": dtype})
    return specs


def _shape_matches(per_sample_shape, spec_shape):
    spec = spec_shape[1:]
    if len(per_sample_shape) != len(spec):
        return False
    return all(s is None or s == d for s, d in zip(spec, per_sample_shape))


def map_inputs(specs, dataset_inputs):
    """Model input -> dataset input encoder name: same name first, else the first unused
    encoder with a compatible per-sample shape. None if any model input has no match."""
    mapping, used = {}, set()
    for spec in specs:
        name = spec["name"].split(":")[0]
        if name in dataset_inputs and _shape_matches(dataset_inputs[name], spec["shape"]):
            choice = name
        else:
            choice = next((k for k, shape in dataset_inputs.items()
                           if k not in used and _shape_matches(shape, spec["shape"])), None)
        if choice is None:
            return None
        mapping[spec["name"]] = choice
        used.add(choice)
    return mapping


def real_input_pool(integ, specs, k):
    """Up to k real samples mapped onto the model inputs (see map_inputs), plus the mapping.
    (None, None) when the inputs can't be matched; synthetic inputs are used then."""
    import numpy as np
    for state, ids in integ.sample_ids.items():
        if not ids:
            continue
        first = integ.get_sample(state, ids[0])
        shapes = {name: np.asarray(a).shape for name, a in first.inputs.items()}
        mapping = map_inputs(specs, shapes)
        if mapping is None:
            return None, None
        pool = []
        for sid in ids[:k]:
            sample = first if sid == ids[0] else integ.get_sample(state, sid)
            pool.append([np.asarray(sample.inputs[mapping[s["name"]]]) for s in specs])
        return pool, mapping
    return None, None


def make_batch(specs, bs, pool, rng):
    import numpy as np
    batch = []
    for j, spec in enumerate(specs):
        if pool:
            rows = [pool[i % len(pool)][j] for i in range(bs)]
            batch.append(np.stack(rows).astype(spec["dtype"], copy=False))
            continue
        if any(d is None for d in spec["shape"][1:]):
            raise ValueError("input %r has dynamic dimensions %s and no dataset sample matches it"
                             % (spec["name"], spec["shape"]))
        shape = [bs] + spec["shape"][1:]
        if spec["dtype"].startswith("float"):
            batch.append(rng.random(shape).astype(spec["dtype"]))
        else:
            batch.append(np.zeros(shape, dtype=spec["dtype"]))
    return batch


def run_model(lm, specs, batch):
    import numpy as np
    if lm.framework == "onnxruntime":
        outs = lm.model.run(None, {s["name"]: a for s, a in zip(specs, batch)})
        return [np.asarray(o) for o in outs]
    out = lm.model(batch if len(batch) > 1 else batch[0], training=False)
    outs = out if isinstance(out, (list, tuple)) else [out]
    return [np.asarray(o) for o in outs]  # forces completion of async device work


def knee(entries, gain):
    """(entry, reached): the smallest batch size after which the next larger one adds less
    than `gain` P50 throughput (diminishing returns). A "fraction of peak" rule flips between
    runs when the curve is flat; the marginal-gain rule doesn't. reached=False means
    throughput was still climbing at the largest batch tried."""
    for cur, nxt in zip(entries, entries[1:]):
        tp_cur = cur["throughput_p50_samples_per_second"]
        tp_nxt = nxt["throughput_p50_samples_per_second"]
        if tp_nxt < tp_cur * (1.0 + gain):
            return cur, True
    return entries[-1], len(entries) == 1


def time_batches(lm, specs, batch, warmup, min_iters, max_iters, min_seconds):
    for _ in range(warmup):
        run_model(lm, specs, batch)
    times = []
    start = time.perf_counter()
    while len(times) < max_iters:
        t0 = time.perf_counter()
        run_model(lm, specs, batch)
        times.append(time.perf_counter() - t0)
        if len(times) >= min_iters and time.perf_counter() - start >= min_seconds:
            break
    return times


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #

def cmd_preflight(args):
    report = {
        "python": platform.python_version(),
        "platform": "%s-%s" % (sys.platform, platform.machine()),
        "cpu": cpu_info(),
        "memory": memory_info(),
        "disk": disk_info(args.root),
        "nvidia_gpus": nvidia_gpus(),
        "code_loader": code_loader_info(),
        "findings": [],
    }
    findings = report["findings"]

    if report["code_loader"] is None:
        findings.append(finding("blocker", "code-loader-missing",
                                "code_loader is not importable in this Python environment; "
                                "run tl_perf inside the integration's environment."))
        return _finish_preflight(args, report)

    mem = report["memory"]
    if mem["total_gb"] is not None and mem["total_gb"] < 8:
        findings.append(finding("warn", "low-ram", "Less than 8 GB of RAM (%.1f GB)." % mem["total_gb"]))
    if mem["available_gb"] is not None and mem["total_gb"] and mem["available_gb"] < 0.2 * mem["total_gb"]:
        findings.append(finding("warn", "ram-busy", "Less than 20%% of RAM is available (%.1f GB); "
                                "measurements will be noisy." % mem["available_gb"]))
    if report["disk"]["free_gb"] < 2:
        findings.append(finding("blocker", "low-disk", "Less than 2 GB free at %s." % report["disk"]["path"]))

    integ = Integration(args.root, args.entry or read_entry_file(args.root))
    try:
        integ.load()
    except Exception as exc:  # user code raised while loading
        findings.append(finding("invalid", "integration-load-failed", repr(exc)))
        report["integration"] = {"entry": integ.entry, "valid": False}
        return _finish_preflight(args, report)

    report["integration"] = {
        "entry": integ.entry,
        "valid": integ.valid,
        "general_error": integ.result.general_error,
        "failed_checks": integ.failed_payloads(),
        "setup": integ.setup_summary(),
    }
    if not integ.valid:
        findings.append(finding("invalid", "integration-invalid",
                                "check_dataset() failed; fix the integration before optimizing it."))

    uses_keras = True
    if not args.no_model:
        try:
            lm = load_model(integ)
            uses_keras = lm.framework == "keras"
            report["model"] = {"framework": lm.framework, "device": lm.device(),
                               "fixed_batch_size": lm.fixed_batch_size,
                               "inputs": model_input_specs(lm)}
        except Exception as exc:
            findings.append(finding("invalid", "model-load-failed", repr(exc)))

    report["frameworks"] = framework_info(include_tensorflow=uses_keras)
    _device_findings(report, findings)
    return _finish_preflight(args, report)


def _device_findings(report, findings):
    model = report.get("model")
    gpus = report.get("nvidia_gpus")
    if not model:
        return
    label = model["device"]["label"]
    if gpus and label == "CPU":
        findings.append(finding(
            "mismatch", "gpu-unused",
            "An NVIDIA GPU is present but the model runs on CPU (%s). Install the GPU build of "
            "the model runtime so the floor is measured on the GPU." % model["framework"],
            gpus=gpus))
    if not gpus and label == "CPU":
        findings.append(finding(
            "info", "cpu-floor",
            "The model runs on CPU here, so the local floor is a CPU floor. It is valid for local "
            "before/after comparisons; server inference may run on a GPU."))


def _finish_preflight(args, report):
    levels = {f["level"] for f in report["findings"]}
    if "blocker" in levels:
        code = EXIT_BLOCKER
    elif "invalid" in levels:
        code = EXIT_INVALID_INTEGRATION
    elif "mismatch" in levels:
        code = EXIT_ENV_MISMATCH
    else:
        code = EXIT_OK
    report["exit_code"] = code
    path = os.path.join(out_dir(args), "preflight.json")
    write_json(path, report)

    print("tl_perf preflight")
    cl = report.get("code_loader") or {}
    print("  python %s on %s, %s cores, %.1f GB RAM" % (
        report["python"], report["platform"], report["cpu"]["logical_cores"],
        report["memory"]["total_gb"] or 0))
    print("  code-loader %s  features=%s" % (cl.get("version"), cl.get("features")))
    integ = report.get("integration")
    if integ:
        setup = integ.get("setup") or {}
        print("  integration %s: %s  states=%s  grouped=%s" % (
            integ["entry"], "valid" if integ["valid"] else "INVALID",
            setup.get("state_lengths"), setup.get("grouped")))
    if report.get("model"):
        m = report["model"]
        print("  model: %s on %s  fixed_batch_size=%s" % (
            m["framework"], m["device"]["label"], m["fixed_batch_size"]))
    for f in report["findings"]:
        print("  [%s] %s: %s" % (f["level"].upper(), f["code"], f["message"]))
    print("  -> %s (exit %d)" % (path, code))
    return code


# --------------------------------------------------------------------------- #
# floor
# --------------------------------------------------------------------------- #

def cmd_floor(args):
    import numpy as np
    integ = Integration(args.root, args.entry or read_entry_file(args.root))
    try:
        integ.load()
        lm = load_model(integ)
    except Exception as exc:
        print("tl_perf floor: model could not be loaded: %r" % (exc,), file=sys.stderr)
        return EXIT_MODEL_LOAD

    specs = model_input_specs(lm)
    pool, mapping = real_input_pool(integ, specs, args.pool) if integ.valid else (None, None)
    rng = np.random.default_rng(0)

    if lm.fixed_batch_size:
        batch_sizes = [lm.fixed_batch_size]
    else:
        batch_sizes = [int(x) for x in args.batch_sizes.split(",") if x.strip()]

    sweep = []
    for bs in batch_sizes:
        entry = {"batch_size": bs}
        try:
            batch = make_batch(specs, bs, pool, rng)
            times = time_batches(lm, specs, batch, args.warmup, args.min_iters,
                                 args.max_iters, args.min_seconds)
        except Exception as exc:  # OOM or shape trouble: larger batches won't do better
            entry["error"] = repr(exc)
            sweep.append(entry)
            break
        stats = percentiles(times)
        entry.update(batch_seconds=stats,
                     per_sample_mean_seconds=stats["mean"] / bs,
                     throughput_samples_per_second=bs / stats["mean"],
                     throughput_p50_samples_per_second=bs / stats["p50"])
        sweep.append(entry)

    ok = [e for e in sweep if "error" not in e]
    if not ok:
        print("tl_perf floor: no batch size ran successfully: %s" % sweep, file=sys.stderr)
        return EXIT_MODEL_LOAD

    recommended, knee_reached = knee(ok, args.knee_gain)
    report = {
        "framework": lm.framework,
        "device": lm.device(),
        "environment": {"platform": "%s-%s" % (sys.platform, platform.machine()),
                        "cpu": cpu_info().get("model") or platform.processor()},
        "inputs": specs,
        "input_source": "dataset" if pool else "synthetic",
        "input_mapping": mapping,
        "fixed_batch_size": lm.fixed_batch_size,
        "sweep": sweep,
        "recommended_batch_size": recommended["batch_size"],
        "knee_gain": args.knee_gain,
        "knee_reached": knee_reached,
        "t_inf_per_sample_mean_seconds": recommended["per_sample_mean_seconds"],
        "peak_rss_gb": peak_rss_gb(),
    }
    path = os.path.join(out_dir(args), "floor.json")
    write_json(path, report)

    print("tl_perf floor: %s on %s (inputs: %s%s)" % (
        lm.framework, report["device"]["label"], report["input_source"],
        " %s" % mapping if mapping else ""))
    print("  %6s %12s %12s %12s %14s" % ("batch", "p50/batch", "p99/batch", "mean/sample", "samples/s"))
    for e in sweep:
        if "error" in e:
            print("  %6d  failed: %s" % (e["batch_size"], e["error"][:80]))
            continue
        b = e["batch_seconds"]
        print("  %6d %12s %12s %12s %14.1f" % (
            e["batch_size"], fmt_ms(b["p50"]), fmt_ms(b["p99"]),
            fmt_ms(e["per_sample_mean_seconds"]), e["throughput_samples_per_second"]))
    print("  recommended batch size %d; floor = %s per sample (mean)" % (
        report["recommended_batch_size"], fmt_ms(report["t_inf_per_sample_mean_seconds"])))
    if not knee_reached and not lm.fixed_batch_size:
        print("  note: throughput was still rising at batch %d; larger batches may be faster "
              "(re-run with a larger --batch-sizes)" % recommended["batch_size"])
    print("  -> %s" % path)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def cmd_not_implemented(args):
    print("tl_perf %s: not implemented yet" % args.command, file=sys.stderr)
    return NOT_IMPLEMENTED


def build_parser():
    parser = argparse.ArgumentParser(prog="tl_perf", description=__doc__.splitlines()[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".", help="integration root (default: .)")
    common.add_argument("--entry", default=None, help="entry file (default: leap.yaml entryFile)")
    common.add_argument("--out", default=DEFAULT_OUT, help="output dir, relative to --root")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("preflight", parents=[common],
                       help="environment, devices, code-loader features, integration + model load")
    p.add_argument("--no-model", action="store_true", help="skip loading the model")
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("floor", parents=[common],
                       help="standalone model inference time (percentiles, batch-size sweep)")
    p.add_argument("--batch-sizes", default=DEFAULT_BATCH_SIZES)
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--min-iters", type=int, default=20)
    p.add_argument("--max-iters", type=int, default=500)
    p.add_argument("--min-seconds", type=float, default=3.0)
    p.add_argument("--pool", type=int, default=8, help="real samples to cycle through")
    p.add_argument("--knee-gain", type=float, default=0.10,
                   help="recommend the batch after which the next one adds less than this "
                        "fraction of P50 throughput")
    p.set_defaults(func=cmd_floor)

    for name, help_text in (
            ("fit", "per-sample size and server memory/disk headroom per batch size"),
            ("profile", "every integration component, run the way Tensorleap runs it"),
            ("score", "rank optimization candidates from a profile"),
            ("compare", "equivalence + timing/memory delta against the baseline"),
            ("report", "render report.json into report.md")):
        p = sub.add_parser(name, parents=[common], help=help_text)
        p.set_defaults(func=cmd_not_implemented)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_BLOCKER
    args.root = os.path.abspath(args.root)
    try:
        return args.func(args)
    finally:
        # code-loader prints its integration-test status/warning table from an atexit
        # hook; it is about authoring, not runtime, so keep it out of our output.
        sys.stdout.flush()
        sys.stdout = open(os.devnull, "w")


if __name__ == "__main__":
    sys.exit(main())
