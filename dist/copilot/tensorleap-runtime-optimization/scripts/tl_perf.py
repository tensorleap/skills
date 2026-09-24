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
"""

import argparse
import collections
import contextlib
import cProfile
import datetime
import enum
import functools
import inspect
import io
import json
import math
import os
import platform
import pstats
import random
import shutil
import subprocess
import sys
import time
import traceback

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


OUT_GITIGNORE = """# tl_perf measurement artifacts: large and machine-specific. Only the deliverables are kept.
*
!.gitignore
!report.md
!report.json
!optimization-log.md
!static.json
"""


def ensure_out(path):
    """Create the output dir with its own .gitignore, so measurement artifacts never land
    in the user's commits whatever the repo's own .gitignore says."""
    os.makedirs(path, exist_ok=True)
    ignore = os.path.join(path, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8") as fh:
            fh.write(OUT_GITIGNORE)


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


def load_info():
    """1-minute load average relative to cores: timings taken on a busy machine are noisy
    and shift batch-size recommendations."""
    try:
        load1 = os.getloadavg()[0]
    except (OSError, AttributeError):
        return None
    cores = os.cpu_count() or 1
    return {"load1": load1, "cores": cores, "busy": load1 > 0.5 * cores}


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

    def load_light(self):
        """What a Tensorleap worker does at startup: import the integration and run
        preprocess (no validation probes). Timed as startup_seconds."""
        if self.root not in sys.path:
            sys.path.insert(0, self.root)
        from code_loader import LeapLoader
        t0 = time.perf_counter()
        with working_dir(self.root), captured_stdout() as buf:
            self.loader = LeapLoader(self.root, self.entry)
            self.loader.exec_script()
            raw = self.loader.get_preprocess_sample_ids()
            # The first get_sample of a process lazily probes every handler once per state
            # (code-loader's metadata-type table). It is a once-per-worker cost: pay it here
            # so it lands in startup instead of skewing the first sample.
            probe = getattr(self.loader, "_metadata_name_to_type", None)
            if callable(probe):
                try:
                    probe()
                except Exception:
                    pass
        self.startup_seconds = time.perf_counter() - t0
        self.groups = {}
        for state, ids in raw.items():
            ids = list(ids)
            name = _state_name(state)
            self.states[name] = state
            if ids and isinstance(ids[0], (list, tuple)):
                self.grouped = True
                self.groups[name] = [list(g) for g in ids]
            self.sample_ids[name] = _flat_ids(ids)
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

    report["load"] = load_info()
    if report["load"] and report["load"]["busy"]:
        findings.append(finding("warn", "machine-busy",
                                "Load average %.1f on %d cores: timings will be noisy and the "
                                "recommended batch size may shift. Measure on a quiet machine."
                                % (report["load"]["load1"], report["load"]["cores"])))
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
        "load": load_info(),
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
# Handler instrumentation (the registry code-loader dispatches through)
# --------------------------------------------------------------------------- #

class Recorder:
    """Per-call timings of instrumented handlers, tagged with the current block/sample."""

    def __init__(self):
        self.calls = []          # (block, kind, name, sample_key, seconds, rows)
        self.block = None
        self.sample = None
        self.rows = 1

    def set(self, block, sample=None, rows=1):
        self.block, self.sample, self.rows = block, sample, rows

    def wrap(self, kind, name, fn):
        recorder = self

        @functools.wraps(fn)
        def timed(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                recorder.calls.append((recorder.block, kind, name, recorder.sample,
                                       time.perf_counter() - t0, recorder.rows))

        # code-loader reads handler annotations with inspect.getfullargspec, which ignores
        # __wrapped__ but honors __signature__: keep the handler's own signature visible.
        try:
            timed.__signature__ = inspect.signature(fn, follow_wrapped=False)
        except (TypeError, ValueError):
            pass
        return timed


def iter_registry():
    """(handler object, kind, name) for every registered handler."""
    from code_loader.inner_leap_binder import global_leap_binder
    sc = global_leap_binder.setup_container
    if getattr(sc, "preprocess", None) is not None:
        yield sc.preprocess, "preprocess", "preprocess"
    for h in getattr(sc, "inputs", None) or []:
        yield h, "input", h.name
    for h in getattr(sc, "ground_truths", None) or []:
        yield h, "gt", h.name
    for h in getattr(sc, "metadata", None) or []:
        yield h, "metadata", h.name
    for h in getattr(sc, "metrics", None) or []:
        yield h, "metric", h.metric_handler_data.name
    for h in getattr(sc, "custom_loss_handlers", None) or []:
        yield h, "loss", h.custom_loss_handler_data.name
    for h in getattr(sc, "visualizers", None) or []:
        yield h, "visualizer", h.visualizer_handler_data.name
    if getattr(sc, "custom_latent_space", None) is not None:
        yield sc.custom_latent_space, "custom_latent_space", "custom_latent_space"


def _user_function(fn, root, depth=0):
    """The integration's own function behind a registered handler. code-loader registers
    wrapper closures (numpy_encoder_function, inner_without_validate, ...) that hold the
    user function in a closure cell."""
    fn = inspect.unwrap(fn)
    code = getattr(fn, "__code__", None)
    if code is not None and _is_user_frame(code.co_filename, root):
        return fn
    if depth >= 4:
        return None
    for cell in getattr(fn, "__closure__", None) or ():
        try:
            content = cell.cell_contents
        except ValueError:
            continue
        if callable(content) and hasattr(content, "__code__"):
            found = _user_function(content, root, depth + 1)
            if found is not None:
                return found
    return None


def handler_function_names(root):
    """{"kind:name": the integration function behind it}, to attribute diagnostics to
    handlers. Built-in handlers (not defined in the integration) are omitted."""
    root = os.path.realpath(root)
    names = {}
    for handler, kind, name in iter_registry():
        fn = getattr(handler, "function", None)
        user = _user_function(fn, root) if fn is not None else None
        if user is not None:
            names["%s:%s" % (kind, name)] = user.__name__
    return names


def instrument_registry(recorder):
    for handler, kind, name in iter_registry():
        fn = getattr(handler, "function", None)
        if fn is not None and kind != "preprocess":
            handler.function = recorder.wrap(kind, name, fn)


def handler_stats(recorder, blocks, n_rows, exclude=()):
    """Per-handler stats; calls tagged with a sample in `exclude` (process warm-up) are left out."""
    out = {}
    for block, kind, name, sample, seconds, rows in recorder.calls:
        if block not in blocks or sample in exclude:
            continue
        e = out.setdefault("%s:%s" % (kind, name),
                           {"kind": kind, "name": name, "block": block, "_t": [], "_r": 0})
        e["_t"].append(seconds)
        e["_r"] += rows
    for e in out.values():
        times = e.pop("_t")
        e.pop("_r")
        e["calls"] = len(times)
        e["calls_per_sample"] = len(times) / n_rows if n_rows else None
        e["per_call_seconds"] = percentiles(times)
        e["per_sample_mean_seconds"] = sum(times) / n_rows if n_rows else None
    return out


def current_rss_gb():
    try:
        import psutil
        return psutil.Process().memory_info().rss / 2 ** 30
    except ImportError:
        return peak_rss_gb()


# --------------------------------------------------------------------------- #
# How the integration test wires the model and handlers (= what the platform runs)
# --------------------------------------------------------------------------- #

class Unsupported(Exception):
    def __init__(self, message, gt_missing=False):
        Exception.__init__(self, message)
        self.gt_missing = gt_missing


def _node_type(node):
    t = getattr(node, "type", None)
    return getattr(t, "value", str(t))


def mapping_info():
    """(model_inputs {index: dataset input name}, [(kind, name, {arg: (type, source)})]).

    Only handlers wired in the integration test run on the platform, so only those are
    profiled."""
    from code_loader.inner_leap_binder import global_leap_binder
    kinds = {"Metric": "metric", "CustomLoss": "loss", "Visualizer": "visualizer"}
    model_inputs, handlers = {}, []
    for conn in getattr(global_leap_binder, "mapping_connections", None) or []:
        ntype = _node_type(conn.node)
        if ntype.startswith("Input") and not conn.node_inputs:
            suffix = ntype[len("Input"):]
            model_inputs[int(suffix) if suffix.isdigit() else 0] = conn.node.name
        elif ntype in kinds:
            args = {arg: (_node_type(nm), nm.name) for arg, nm in (conn.node_inputs or {}).items()}
            handlers.append((kinds[ntype], conn.node.name, args))
    return model_inputs, handlers


def resolve_arg(ntype, source, inputs, gts, preds):
    if ntype.startswith("Input"):
        if source in inputs:
            return inputs[source]
        raise Unsupported("input %r is not produced by an input encoder" % source)
    if ntype == "GroundTruth":
        if not gts or source not in gts:
            raise Unsupported("ground truth %r unavailable" % source, gt_missing=True)
        return gts[source]
    suffix = ntype[len("Prediction"):] if ntype.startswith("Prediction") else ""
    if suffix.isdigit():
        return preds[int(suffix)]
    raise Unsupported("argument type %s is not modeled" % ntype)


def model_feed_names(specs, model_inputs, row_inputs):
    """Dataset input name feeding each model input, from the mapping (else by shape)."""
    import numpy as np
    if model_inputs and len(model_inputs) == len(specs) and \
            all(v in row_inputs for v in model_inputs.values()):
        return [model_inputs[k] for k in sorted(model_inputs)]
    mapping = map_inputs(specs, {k: np.asarray(v).shape for k, v in row_inputs.items()})
    if mapping is None:
        raise RuntimeError("cannot match dataset inputs to model inputs %s" % specs)
    return [mapping[s["name"]] for s in specs]


# --------------------------------------------------------------------------- #
# Sample selection and fetching
# --------------------------------------------------------------------------- #

def _py(value):
    return value.item() if hasattr(value, "item") and not hasattr(value, "__len__") else value


def _sort_key(sid):
    first = sid[0] if isinstance(sid, list) else sid
    return (0, first, "") if isinstance(first, (int, float)) else (1, 0, str(first))


def select_samples(integ, per_state, seed, mode="random", first_state_only=False):
    """[[state, sample_id | [group ids]], ...].

    random:     a random subset in a random order — how the server presents samples to a
                worker by default.
    contiguous: a window of consecutive ids in sorted order — the sorted-order what-if.
    Grouped datasets are selected by whole groups."""
    rng = random.Random(seed)
    selection = []
    for state, ids in integ.sample_ids.items():
        if not ids:
            continue
        units = [[_py(s) for s in g] for g in integ.groups[state]] if state in integ.groups \
            else [_py(s) for s in ids]
        if mode == "contiguous":
            units = sorted(units, key=_sort_key)
            size = _units_for(units, per_state)
            start = rng.randrange(0, max(1, len(units) - size + 1))
            chosen = units[start:start + size]
        else:
            order = list(range(len(units)))
            rng.shuffle(order)
            chosen = [units[i] for i in order[:_units_for([units[i] for i in order], per_state)]]
        selection.extend([state, u] for u in chosen)
        if first_state_only:
            break
    return selection


def _units_for(units, rows):
    """How many leading units (samples or groups) cover `rows` rows."""
    count = total = 0
    for u in units:
        if total >= rows:
            break
        total += len(u) if isinstance(u, list) else 1
        count += 1
    return count


def snapshot_selection(integ, per_state):
    """First rows of each state in sorted id order: stable across code changes."""
    selection = []
    for state, ids in integ.sample_ids.items():
        units = [[_py(s) for s in g] for g in integ.groups[state]] if state in integ.groups \
            else [_py(s) for s in ids]
        units = sorted(units, key=_sort_key)
        selection.extend([state, u] for u in units[:_units_for(units, per_state)])
    return selection


def row_key(state, rid):
    return "%s/%s" % (state, rid)


def entry_rows(sid):
    return len(sid) if isinstance(sid, list) else 1


def fetch(integ, state, sid):
    with working_dir(integ.root), captured_stdout():
        if isinstance(sid, list):
            if hasattr(integ.loader, "get_samples"):
                return integ.loader.get_samples(integ.states[state], sid)
            return [integ.loader.get_sample(integ.states[state], s) for s in sid]
        return integ.loader.get_sample(integ.states[state], sid)


def sample_rows(sample, sid):
    """[(row_id, inputs, gt | None, metadata)] for a fetched sample or group."""
    import numpy as np
    if isinstance(sample, list):
        return [r for s, m in zip(sample, sid) for r in sample_rows(s, m)]

    def as_dict(d, pick):
        return None if d is None else {k: pick(v) for k, v in d.items()}

    if not isinstance(sid, list):
        return [(sid, as_dict(sample.inputs, np.asarray), as_dict(sample.gt, np.asarray),
                 dict(sample.metadata or {}))]
    rows = []
    for j, rid in enumerate(sid):
        def pick(v, j=j):
            return np.asarray(v[j])

        meta = {k: (v[j] if isinstance(v, (list, tuple)) and len(v) == len(sid) else v)
                for k, v in (sample.metadata or {}).items()}
        rows.append((rid, as_dict(sample.inputs, pick), as_dict(sample.gt, pick), meta))
    return rows


def chunks(selection, batch_size):
    """Consecutive entries of one state, batch_size rows at a time (metrics never mix states)."""
    state, chunk, rows = None, [], 0
    for st, sid in selection:
        if chunk and (st != state or rows >= batch_size):
            yield state, chunk
            chunk, rows = [], 0
        state = st
        chunk.append(sid)
        rows += entry_rows(sid)
    if chunk:
        yield state, chunk


def _stack(rows, field):
    import numpy as np
    if rows[0][field] is None:
        return None
    return {k: np.stack([r[field][k] for r in rows]) for k in rows[0][field]}


# --------------------------------------------------------------------------- #
# Diagnostics: file reads and repeated component calls
# --------------------------------------------------------------------------- #

_READERS = (
    ("builtins", "open"), ("numpy", "load"), ("numpy", "fromfile"), ("numpy", "loadtxt"),
    ("PIL.Image", "open"), ("cv2", "imread"), ("pandas", "read_parquet"),
    ("pandas", "read_csv"), ("pandas", "read_pickle"), ("pandas", "read_feather"),
    ("pyarrow.parquet", "read_table"), ("nibabel", "load"), ("imageio", "imread"),
    ("skimage.io", "imread"), ("torch", "load"), ("tensorflow.io", "read_file"),
)


class ReadTracker:
    """Records which files each sample reads, through common readers the integration has
    already imported (nested reader calls count once)."""

    def __init__(self):
        self.events = []          # (sample_key, reader, path)
        self.sample = None
        self._depth = 0
        self._saved = []
        self._skip = tuple(os.path.realpath(p) for p in {sys.prefix, sys.base_prefix})

    def _path(self, args, kwargs):
        target = args[0] if args else (kwargs.get("file") or kwargs.get("path")
                                       or kwargs.get("filename") or kwargs.get("fp"))
        try:
            return os.path.abspath(os.fspath(target))
        except TypeError:
            return None

    def _wrap(self, label, fn):
        tracker = self

        @functools.wraps(fn)
        def reading(*args, **kwargs):
            if tracker.sample is not None and tracker._depth == 0:
                path = tracker._path(args, kwargs)
                if path is None:
                    if label != "builtins.open":
                        tracker.events.append((tracker.sample, label, "<object>"))
                elif not path.startswith(tracker._skip) and not path.endswith((".py", ".pyc", ".so")):
                    tracker.events.append((tracker.sample, label, path))
            tracker._depth += 1
            try:
                return fn(*args, **kwargs)
            finally:
                tracker._depth -= 1
        return reading

    def install(self):
        import builtins
        for modname, attr in _READERS:
            if modname == "builtins":
                owner = builtins
            elif modname == "tensorflow.io":
                tf = sys.modules.get("tensorflow")
                owner = getattr(tf, "io", None) if tf is not None else None
            else:
                owner = sys.modules.get(modname)
            if owner is None or not hasattr(owner, attr):
                continue
            fn = getattr(owner, attr)
            try:
                setattr(owner, attr, self._wrap("%s.%s" % (modname, attr), fn))
                self._saved.append((owner, attr, fn))
            except (AttributeError, TypeError):
                continue

    def uninstall(self):
        for owner, attr, fn in self._saved:
            setattr(owner, attr, fn)
        self._saved = []

    def summary(self, n_rows):
        per_sample = {}
        for sample, label, path in self.events:
            per_sample.setdefault(sample, []).append((label, path))
        repeated, with_repeat = {}, 0
        for events in per_sample.values():
            counts = collections.Counter(p for _, p in events if p != "<object>")
            worst = max(counts.values()) if counts else 0
            if worst > 1:
                with_repeat += 1
            for p, c in counts.items():
                if c > 1:
                    repeated[p] = max(repeated.get(p, 0), c)
        distinct = {p for _, _, p in self.events if p != "<object>"}
        return {
            "samples": n_rows,
            "reads": len(self.events),
            "reads_per_sample": len(self.events) / n_rows if n_rows else None,
            "distinct_files": len(distinct),
            "samples_with_repeated_reads": with_repeat,
            "max_reads_of_one_file_in_one_sample": max(repeated.values()) if repeated else (1 if self.events else 0),
            "repeated_examples": sorted(repeated.items(), key=lambda kv: -kv[1])[:5],
            "by_reader": dict(collections.Counter(label for _, label, _ in self.events)),
        }


def _is_user_frame(filename, root):
    if not filename or filename.startswith("<") or filename == "~":
        return False
    path = os.path.realpath(filename)
    return path.startswith(root + os.sep) and "site-packages" not in path \
        and os.sep + ".venv" + os.sep not in path


def _origins(stats, key, root, depth=0, seen=None):
    """First user-code callers on every call path into `key`; <tensorleap> when a path
    reaches the top without passing through user code (a platform-dispatched call)."""
    seen = seen if seen is not None else set()
    origins = set()
    for caller in stats.get(key, (0, 0, 0, 0, {}))[4]:
        if caller in seen:
            continue
        seen.add(caller)
        if _is_user_frame(caller[0], root):
            origins.add(caller[2])
        elif depth < 8 and stats.get(caller, (0, 0, 0, 0, {}))[4]:
            origins |= _origins(stats, caller, root, depth + 1, seen)
        else:
            origins.add("<tensorleap>")
    return origins or {"<tensorleap>"}


def profile_analysis(prof, root, n_rows, handler_names):
    stats = pstats.Stats(prof).stats
    root = os.path.realpath(root)
    per = float(n_rows or 1)
    handler_fns = set(handler_names.values())

    def label(key):
        f, line, name = key
        if f == "~":
            return name
        rel = os.path.relpath(os.path.realpath(f), root) if _is_user_frame(f, root) else os.path.basename(f)
        return "%s (%s:%d)" % (name, rel, line)

    user = [(k, v) for k, v in stats.items() if _is_user_frame(k[0], root)]
    builtin = [(k, v) for k, v in stats.items() if k[0] == "~" and "lsprof" not in k[2]]
    hot = [{"function": label(k), "calls_per_sample": v[1] / per,
            "cumulative_seconds_per_sample": v[3] / per, "own_seconds_per_sample": v[2] / per}
           for k, v in sorted(user, key=lambda kv: -kv[1][3])[:12]]
    hot_builtin = [{"function": label(k), "calls_per_sample": v[1] / per,
                    "own_seconds_per_sample": v[2] / per}
                   for k, v in sorted(builtin, key=lambda kv: -kv[1][2])[:8]]
    repeated = []
    # Ignore cheap helpers: a repeat matters only if it costs a noticeable slice of a sample.
    top_cost = max([v[3] / per for _, v in user] or [0.0])
    floor_cost = max(5e-5, 0.02 * top_cost)
    for k, v in user:
        calls = v[1] / per
        if calls <= 1.0 + 1e-9 or v[3] / per < floor_cost:
            continue
        origins = _origins(stats, k, root)
        is_component = k[2] in handler_fns
        if is_component or len(origins) > 1:
            repeated.append({"function": k[2], "where": label(k), "calls_per_sample": calls,
                             "cumulative_seconds_per_sample": v[3] / per,
                             "called_from": sorted(origins), "is_component": is_component})
    repeated.sort(key=lambda r: -r["cumulative_seconds_per_sample"])
    return {"samples": n_rows, "hot_functions": hot, "hot_builtins": hot_builtin,
            "repeated_calls": repeated[:10]}


# --------------------------------------------------------------------------- #
# Output snapshots (for equivalence checks)
# --------------------------------------------------------------------------- #

def flatten_output(value, prefix="value", depth=0):
    import numpy as np
    if isinstance(value, enum.Enum):
        yield prefix, str(value.value)
        return
    if depth > 6:
        yield prefix, repr(value)[:2000]
        return
    if hasattr(value, "numpy") and callable(value.numpy):
        try:
            value = value.numpy()
        except Exception:
            pass
    if isinstance(value, dict):
        for k, v in value.items():
            for item in flatten_output(v, "%s.%s" % (prefix, k), depth + 1):
                yield item
    elif isinstance(value, np.ndarray):
        yield prefix, value
    elif isinstance(value, np.generic):
        yield prefix, value.item()
    elif value is None or isinstance(value, (bool, int, float, str)):
        yield prefix, value
    elif isinstance(value, (list, tuple)):
        try:
            arr = np.asarray(value)
            if arr.dtype != object:
                yield prefix, arr
                return
        except Exception:
            pass
        yield prefix, repr(value)[:2000]
    elif hasattr(value, "__dict__") and not isinstance(value, type):   # LeapImage, ...
        for k, v in vars(value).items():
            for item in flatten_output(v, "%s.%s" % (prefix, k), depth + 1):
                yield item
    else:
        yield prefix, repr(value)[:2000]


def split_batch(value, j):
    import numpy as np
    if hasattr(value, "numpy") and callable(value.numpy):
        try:
            value = value.numpy()
        except Exception:
            pass
    if isinstance(value, dict):
        return {k: split_batch(v, j) for k, v in value.items()}
    if isinstance(value, np.ndarray) and value.ndim >= 1:
        return value[j]
    if isinstance(value, (list, tuple)) and len(value) > j:
        return value[j]
    return value


class Snapshot:
    def __init__(self):
        self.samples = []
        self.arrays = {}
        self.meta = {}

    def add_sample(self, key):
        self.samples.append(key)
        return len(self.samples) - 1

    def put(self, kind, name, i, value):
        import numpy as np
        for field, v in flatten_output(value):
            k = ("%s|%s|%s" % (kind, name, field)).replace("/", "_")
            if isinstance(v, np.ndarray):
                self.arrays["%s|%d" % (k, i)] = v
            else:
                self.meta.setdefault(k, {})[str(i)] = v

    def save(self, directory):
        import numpy as np
        os.makedirs(directory, exist_ok=True)
        np.savez_compressed(os.path.join(directory, "snapshot.npz"), **self.arrays)
        write_json(os.path.join(directory, "snapshot.json"),
                   {"samples": self.samples, "meta": self.meta})


def load_snapshot(directory):
    """{(field, sample_key): value}"""
    import numpy as np
    meta = _read_json(os.path.join(directory, "snapshot.json"))
    if meta is None:
        return None
    samples = meta["samples"]
    values = {}
    with np.load(os.path.join(directory, "snapshot.npz"), allow_pickle=False) as z:
        for k in z.files:
            field, i = k.rsplit("|", 1)
            values[(field, samples[int(i)])] = z[k]
    for field, per_sample in meta["meta"].items():
        for i, v in per_sample.items():
            values[(field, samples[int(i)])] = v
    return values


def values_equal(a, b, rtol=0.0, atol=0.0):
    """(equal, detail). Bit-identical unless a tolerance is declared."""
    import numpy as np
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        if not (isinstance(a, np.ndarray) and isinstance(b, np.ndarray)):
            return False, "type differs"
        if a.shape != b.shape:
            return False, "shape %s vs %s" % (a.shape, b.shape)
        if a.dtype != b.dtype:
            return False, "dtype %s vs %s" % (a.dtype, b.dtype)
        if a.dtype.kind in "fc":
            if rtol or atol:
                ok = bool(np.allclose(a, b, rtol=rtol, atol=atol, equal_nan=True))
            else:
                try:
                    ok = bool(np.array_equal(a, b, equal_nan=True))
                except TypeError:  # numpy < 1.19
                    ok = bool(np.array_equal(np.nan_to_num(a), np.nan_to_num(b)))
            if ok:
                return True, ""
            diff = np.abs(a.astype(np.float64) - b.astype(np.float64))
            return False, "max abs diff %.3g" % float(np.nanmax(diff)) if diff.size else "differs"
        return (True, "") if np.array_equal(a, b) else (False, "values differ")
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True, ""
        if rtol or atol:
            return math.isclose(a, b, rel_tol=rtol, abs_tol=atol), "%r vs %r" % (a, b)
        return a == b, "%r vs %r" % (a, b)
    return a == b, "%r vs %r" % (a, b)


def field_stats(values, field, samples=None):
    """Mean/std over ALL elements of a field across samples. (Per-sample means are
    degenerate for normalized outputs: every softmax row averages 1/classes.)"""
    import numpy as np
    total = sq = 0.0
    n = 0
    for (f, s), v in values.items():
        if f != field or (samples is not None and s not in samples):
            continue
        if isinstance(v, np.ndarray) and v.dtype.kind in "biuf" and v.size:
            a = v.astype(np.float64).ravel()
            a = a[np.isfinite(a)]
            total += float(a.sum())
            sq += float(np.square(a).sum())
            n += a.size
        elif isinstance(v, (bool, int, float)) and math.isfinite(float(v)):
            total += float(v)
            sq += float(v) ** 2
            n += 1
    if not n:
        return None
    mean = total / n
    return {"n": n, "mean": mean, "std": math.sqrt(max(0.0, sq / n - mean ** 2))}


def determinism(run1, run2):
    """Pairs (field, sample) whose value differs between two runs of unchanged code, with
    per-field stats of both runs for the distribution-level check."""
    nondet = {}
    for key, v in run1.items():
        if key in run2 and not values_equal(v, run2[key])[0]:
            nondet.setdefault(key[0], []).append(key[1])
    fields = {}
    for field, samples in nondet.items():
        fields[field] = {"samples": samples,
                         "run1": field_stats(run1, field, set(samples)),
                         "run2": field_stats(run2, field, set(samples))}
    return fields


def _within_band(cur, r1, r2, stat, rel):
    if cur is None or r1 is None or r2 is None:
        return True
    # 3x the baseline's own run-to-run spread, a relative allowance, and a float32-precision
    # floor (values that differ only by rounding must not count as a shift).
    band = max(3.0 * abs(r1[stat] - r2[stat]), rel * abs(r1[stat]),
               1e-6 * max(1.0, abs(r1["mean"])))
    return abs(cur[stat] - r1[stat]) <= band


def compare_snapshots(base, cur, nondet, rtol, atol):
    mismatched, distribution, missing, checked = {}, {}, [], 0
    for key, bv in base.items():
        field, sample = key
        if key not in cur:
            missing.append("%s @ %s" % key)
            continue
        if field in nondet and sample in set(nondet[field]["samples"]):
            continue
        checked += 1
        ok, detail = values_equal(bv, cur[key], rtol, atol)
        if not ok:
            mismatched.setdefault(field, []).append({"sample": sample, "detail": detail})
    for field, info in nondet.items():
        stats = field_stats(cur, field, set(info["samples"]))
        ok = _within_band(stats, info["run1"], info["run2"], "mean", 0.02) and \
            _within_band(stats, info["run1"], info["run2"], "std", 0.05)
        distribution[field] = {"ok": ok, "baseline": info["run1"], "current": stats}
    extra = sorted({f for (f, _s) in cur} - {f for (f, _s) in base})
    equivalent = not mismatched and not missing and all(d["ok"] for d in distribution.values())
    return {"equivalent": equivalent, "checked_values": checked,
            "mismatched_fields": {f: {"samples": len(v), "examples": v[:3]} for f, v in mismatched.items()},
            "nondeterministic_fields": distribution, "missing": missing[:20],
            "new_fields": extra[:20]}


# --------------------------------------------------------------------------- #
# Workers (each runs in a fresh process: no cache survives between them)
# --------------------------------------------------------------------------- #

def _load_worker(plan, result):
    integ = Integration(plan["root"], plan["entry"]).load_light()
    result["pid"] = os.getpid()
    result["startup_seconds"] = integ.startup_seconds
    result["state_lengths"] = {k: len(v) for k, v in integ.sample_ids.items()}
    result["grouped"] = integ.grouped
    return integ


def _run_scored_handlers(integ, handlers, state, rows, inputs, gts, preds, recorder, unsupported,
                         on_output=None):
    import numpy as np
    row_ids = np.array([r[0] for r in rows])
    for kind, name, args in handlers:
        if kind not in ("metric", "loss"):
            continue
        try:
            tensors = {arg: resolve_arg(t, s, inputs, gts, preds) for arg, (t, s) in args.items()}
        except Unsupported as exc:
            if not exc.gt_missing:
                unsupported["%s:%s" % (kind, name)] = str(exc)
            continue
        call = integ.loader.run_metric if kind == "metric" else integ.loader.run_custom_loss
        if recorder is not None:
            recorder.set(kind, row_key(state, rows[0][0]), rows=len(rows))
        with working_dir(integ.root), captured_stdout():
            out = call(name, row_ids, integ.states[state], tensors)
        if on_output is not None:
            on_output(kind, name, out)


def worker_generate(plan, result):
    """Generation (+ inference, metrics, loss) in one worker process, caches warm across
    samples — the way one Tensorleap worker processes its share of the dataset."""
    import numpy as np
    integ = _load_worker(plan, result)
    result["handler_functions"] = handler_function_names(integ.root)
    recorder = Recorder()
    instrument_registry(recorder)
    selection = select_samples(integ, plan["samples_per_state"], plan["seed"], plan["mode"])
    result["selection_mode"] = plan["mode"]
    model_inputs, handlers = mapping_info()
    result["mapping"] = {"model_inputs": model_inputs,
                         "handlers": [[k, n, a] for k, n, a in handlers]}
    do_metrics = plan.get("metrics", True)
    lm = specs = feed = None
    if do_metrics:
        lm = load_model(integ)
        specs = model_input_specs(lm)
    all_rows = [row_key(st, rid) for st, sid in selection
                for rid in (sid if isinstance(sid, list) else [sid])]
    step = max(1, len(all_rows) // max(1, plan.get("vis_samples", 0) or 1))
    vis_keys = set(all_rows[::step][:plan.get("vis_samples", 0)]) if do_metrics else set()
    payload, payload_rows, unsupported = {}, [], {}
    gen_times, inference = [], []   # (seconds, rows, warm-up?)
    warm_key = None
    rss_start = current_rss_gb()
    t_start = time.perf_counter()
    for state, chunk in chunks(selection, plan["batch_size"]):
        rows = []
        for sid in chunk:
            n = entry_rows(sid)
            key = row_key(state, sid[0] if isinstance(sid, list) else sid)
            # The first unit pays process warm-up (lazy imports, first-call init): it is
            # reported apart, and its chunk's metric calls too, so means reflect steady state.
            warm = warm_key is None and len(selection) > 1
            if warm:
                warm_key = key
            recorder.set("generation", key, rows=n)
            t0 = time.perf_counter()
            sample = fetch(integ, state, sid)
            gen_times.append(((time.perf_counter() - t0), n, warm))
            rows.extend(sample_rows(sample, sid))
        warm_chunk = row_key(state, rows[0][0]) == warm_key
        if plan.get("max_seconds") and time.perf_counter() - t_start > plan["max_seconds"]:
            result["truncated"] = True
            break
        if not do_metrics:
            continue
        inputs, gts = _stack(rows, 1), _stack(rows, 2)
        if feed is None:
            feed = model_feed_names(specs, model_inputs, rows[0][1])
            result["model_feed"] = feed
        recorder.set("inference", row_key(state, rows[0][0]), rows=len(rows))
        t0 = time.perf_counter()
        preds = run_model(lm, specs, [inputs[name] for name in feed])
        inference.append((time.perf_counter() - t0, len(rows), warm_chunk))
        _run_scored_handlers(integ, handlers, state, rows, inputs, gts, preds, recorder, unsupported)
        for j, r in enumerate(rows):
            if row_key(state, r[0]) not in vis_keys:
                continue
            idx = len(payload_rows)
            payload_rows.append([state, r[0]])
            for name, arr in r[1].items():
                payload["input|%s|%d" % (name, idx)] = arr
            for name, arr in (r[2] or {}).items():
                payload["gt|%s|%d" % (name, idx)] = arr
            for k, p in enumerate(preds):
                payload["pred|%d|%d" % (k, idx)] = p[j]
    exclude = {warm_key} if warm_key else set()
    steady = [(t, n) for t, n, w in gen_times if not w]
    n_rows = sum(n for _, n in steady)
    result["generation"] = {
        "samples": n_rows,
        "warmup_first_sample_seconds": next((t for t, _, w in gen_times if w), None),
        "per_sample_seconds": percentiles([t / n for t, n in steady]),
        "handlers": handler_stats(recorder, ("generation",), n_rows, exclude),
    }
    if do_metrics:
        steady_inf = [(t, n) for t, n, w in inference if not w] or [(t, n) for t, n, _ in inference]
        m_rows = sum(n for _, n in steady_inf)
        result["inference"] = {
            "batch_size": plan["batch_size"],
            "per_batch_seconds": percentiles([t for t, _ in steady_inf]),
            "per_sample_mean_seconds": sum(t for t, _ in steady_inf) / m_rows if m_rows else None,
        }
        metric_exclude = exclude if len(inference) > 1 else set()
        result["metrics"] = {"samples": m_rows,
                             "handlers": handler_stats(recorder, ("metric", "loss"), m_rows,
                                                       metric_exclude)}
        if payload_rows and plan.get("payload_path"):
            np.savez(plan["payload_path"], **payload)
        result["vis_samples"] = payload_rows
    result["unsupported"] = unsupported
    result["memory"] = {"rss_start_gb": rss_start, "rss_end_gb": current_rss_gb(),
                        "peak_rss_gb": peak_rss_gb()}


def _payload_by_index(path):
    import numpy as np
    by_idx = {}
    with np.load(path, allow_pickle=False) as z:
        for k in z.files:
            kind, name, idx = k.split("|")
            by_idx.setdefault(int(idx), {"input": {}, "gt": {}, "pred": {}})[kind][name] = z[k]
    return by_idx


def worker_visualize(plan, result):
    """Visualizers in a fresh process, fed the sample's tensors the way the platform's
    visualization payload does — no encoder, metadata or metric has run here."""
    import numpy as np
    integ = _load_worker(plan, result)
    recorder = Recorder()
    instrument_registry(recorder)
    _, handlers = mapping_info()
    visualizers = [(n, a) for k, n, a in handlers if k == "visualizer"]
    by_idx = _payload_by_index(plan["payload_path"])
    totals, unsupported = [], {}
    rss_start = current_rss_gb()
    for idx, (state, rid) in enumerate(plan["vis_samples"]):
        p = by_idx.get(idx, {"input": {}, "gt": {}, "pred": {}})
        preds = [p["pred"][str(k)] for k in range(len(p["pred"]))]
        total = 0.0
        for name, args in visualizers:
            try:
                tensors = {arg: np.expand_dims(resolve_arg(t, s, p["input"], p["gt"] or None, preds), 0)
                           for arg, (t, s) in args.items()}
            except Unsupported as exc:
                if not exc.gt_missing:
                    unsupported["visualizer:%s" % name] = str(exc)
                continue
            recorder.set("visualizer", row_key(state, rid))
            t0 = time.perf_counter()
            with working_dir(integ.root), captured_stdout():
                integ.loader.run_visualizer(name, np.array([rid]), integ.states[state], tensors)
            total += time.perf_counter() - t0
        totals.append(total)
    # The first sample pays process warm-up; the others have different data, so their
    # caches are still cold, as on the platform.
    exclude = set()
    if len(totals) > 1:
        state, rid = plan["vis_samples"][0]
        exclude = {row_key(state, rid)}
    steady = totals[1:] if exclude else totals
    n = len(steady)
    result["visualizers"] = {"samples": n,
                             "warmup_first_sample_seconds": totals[0] if exclude else None,
                             "per_sample_seconds": percentiles(steady),
                             "handlers": handler_stats(recorder, ("visualizer",), n, exclude)}
    result["unsupported"] = unsupported
    result["memory"] = {"rss_start_gb": rss_start, "rss_end_gb": current_rss_gb(),
                        "peak_rss_gb": peak_rss_gb()}


def worker_diagnose(plan, result):
    """File reads per sample + repeated component calls (cProfile) — a separate process,
    so the profiler never distorts the timing workers."""
    integ = _load_worker(plan, result)
    names = handler_function_names(integ.root)
    selection = select_samples(integ, plan["samples"], plan["seed"], plan["mode"],
                               first_state_only=True)
    tracker = ReadTracker()
    tracker.install()
    prof = cProfile.Profile()
    n_rows = 0
    try:
        for state, sid in selection:
            tracker.sample = row_key(state, sid[0] if isinstance(sid, list) else sid)
            prof.enable()
            try:
                fetch(integ, state, sid)
            finally:
                prof.disable()
                tracker.sample = None
            n_rows += entry_rows(sid)
    finally:
        tracker.uninstall()
    result["mode"] = plan["mode"]
    result["reads"] = tracker.summary(n_rows)
    result["calls"] = profile_analysis(prof, integ.root, n_rows, names)


def worker_snapshot(plan, result):
    """Every handler's output for a fixed, sorted set of samples (equivalence baseline)."""
    import numpy as np
    integ = _load_worker(plan, result)
    model_inputs, handlers = mapping_info()
    lm = load_model(integ)
    specs = model_input_specs(lm)
    feed = None
    snap = Snapshot()
    unsupported = {}
    selection = snapshot_selection(integ, plan["samples_per_state"])
    for state, chunk in chunks(selection, plan["batch_size"]):
        rows = []
        for sid in chunk:
            rows.extend(sample_rows(fetch(integ, state, sid), sid))
        idx = [snap.add_sample(row_key(state, r[0])) for r in rows]
        for i, r in zip(idx, rows):
            for name, arr in r[1].items():
                snap.put("input", name, i, arr)
            for name, arr in (r[2] or {}).items():
                snap.put("gt", name, i, arr)
            for name, value in r[3].items():
                snap.put("metadata", name, i, value)
        inputs, gts = _stack(rows, 1), _stack(rows, 2)
        if feed is None:
            feed = model_feed_names(specs, model_inputs, rows[0][1])
        preds = run_model(lm, specs, [inputs[name] for name in feed])
        for k, p in enumerate(preds):
            for j, i in enumerate(idx):
                snap.put("prediction", str(k), i, p[j])

        def keep(kind, name, out, idx=idx):
            for j, i in enumerate(idx):
                snap.put(kind, name, i, split_batch(out, j))

        _run_scored_handlers(integ, handlers, state, rows, inputs, gts, preds, None, unsupported, keep)
        for kind, name, args in handlers:
            if kind != "visualizer":
                continue
            for j, (i, r) in enumerate(zip(idx, rows)):
                try:
                    tensors = {arg: np.expand_dims(resolve_arg(t, s, r[1], r[2], [p[j] for p in preds]), 0)
                               for arg, (t, s) in args.items()}
                except Unsupported:
                    continue
                with working_dir(integ.root), captured_stdout():
                    out = integ.loader.run_visualizer(name, np.array([r[0]]), integ.states[state], tensors)
                snap.put("visualizer", name, i, out)
    snap.save(plan["snapshot_dir"])
    result["samples"] = len(snap.samples)
    result["unsupported"] = unsupported


WORKERS = {"generate": worker_generate, "visualize": worker_visualize,
           "diagnose": worker_diagnose, "snapshot": worker_snapshot}


def cmd_worker(args):
    plan = _read_json(args.plan)
    result = {"task": args.task}
    try:
        WORKERS[args.task](plan, result)
        code = EXIT_OK
    except Exception:
        result["error"] = traceback.format_exc()[-6000:]
        code = EXIT_RUN_FAILED
    write_json(args.result, result)
    return code


def run_worker(task, plan, run_dir, label, timeout):
    plan_path = os.path.join(run_dir, "plan-%s.json" % label)
    result_path = os.path.join(run_dir, "worker-%s.json" % label)
    write_json(plan_path, plan)
    t0 = time.perf_counter()
    cmd = [sys.executable, os.path.abspath(__file__), "_worker", task,
           "--plan", plan_path, "--result", result_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        code, log = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        code, log = EXIT_RUN_FAILED, "worker timed out after %ss\n%s" % (timeout, exc)
    with open(os.path.join(run_dir, "worker-%s.log" % label), "w", encoding="utf-8") as fh:
        fh.write(log or "")
    result = _read_json(result_path) or {}
    if code != 0 and not result.get("error"):
        result["error"] = (log or "")[-4000:] or "worker exited %d" % code
    result["exit_code"] = code
    result["wall_seconds"] = time.perf_counter() - t0
    return result


def _read_json(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# profile
# --------------------------------------------------------------------------- #

def _next_run_dir(out):
    runs = os.path.join(out, "runs")
    os.makedirs(runs, exist_ok=True)
    existing = [int(d) for d in os.listdir(runs) if d.isdigit()]
    path = os.path.join(runs, "%03d" % (max(existing) + 1 if existing else 1))
    os.makedirs(path)
    return path


def cmd_profile(args):
    out = out_dir(args)
    run_dir = _next_run_dir(out)
    entry = args.entry or read_entry_file(args.root)
    floor = _read_json(os.path.join(out, "floor.json"))
    batch_size = args.batch_size or (floor or {}).get("recommended_batch_size") or 8
    base = {"root": args.root, "entry": entry, "seed": args.seed, "batch_size": batch_size}
    timeout = args.worker_timeout
    workers = {}
    args.load_start = load_info()

    print("tl_perf profile -> %s" % run_dir)
    if args.load_start and args.load_start["busy"]:
        print("  warning: machine busy (load %.1f on %d cores) — timings will be noisy" % (
            args.load_start["load1"], args.load_start["cores"]))
    print("  [1/5] generation + inference + metrics (server-default order)...", flush=True)
    gen = workers["generate"] = run_worker("generate", dict(
        base, mode="random", samples_per_state=args.samples, metrics=True,
        vis_samples=args.vis_samples, max_seconds=args.max_seconds,
        payload_path=os.path.join(run_dir, "vis_payload.npz")), run_dir, "generate", timeout)
    if gen.get("error"):
        print("tl_perf profile: generation worker failed:\n%s" % gen["error"], file=sys.stderr)
        return EXIT_RUN_FAILED

    if not args.no_what_if:
        print("  [2/5] generation, sorted-order what-if...", flush=True)
        workers["generate_sorted"] = run_worker("generate", dict(
            base, mode="contiguous", samples_per_state=args.samples, metrics=False,
            max_seconds=args.max_seconds), run_dir, "generate_sorted", timeout)
    if gen.get("vis_samples"):
        print("  [3/5] visualizers (fresh process, payload tensors)...", flush=True)
        workers["visualize"] = run_worker("visualize", dict(
            base, payload_path=os.path.join(run_dir, "vis_payload.npz"),
            vis_samples=gen["vis_samples"]), run_dir, "visualize", timeout)
    print("  [4/5] diagnostics (file reads, repeated calls)...", flush=True)
    workers["diagnose"] = run_worker("diagnose", dict(
        base, mode="random", samples=args.diagnose_samples), run_dir, "diagnose", timeout)
    workers["diagnose_sorted"] = run_worker("diagnose", dict(
        base, mode="contiguous", samples=args.diagnose_samples), run_dir, "diagnose_sorted", timeout)

    baseline_dir = os.path.join(out, "baseline")
    set_baseline = args.set_baseline or not os.path.isdir(baseline_dir)
    print("  [5/5] output snapshot%s..." % (" x2 (baseline + determinism)" if set_baseline else ""),
          flush=True)
    snap_plan = dict(base, samples_per_state=args.snapshot_samples,
                     snapshot_dir=os.path.join(run_dir, "snapshot"))
    workers["snapshot"] = run_worker("snapshot", snap_plan, run_dir, "snapshot", timeout)
    nondet = None
    if set_baseline and not workers["snapshot"].get("error"):
        workers["snapshot_repeat"] = run_worker("snapshot", dict(
            snap_plan, snapshot_dir=os.path.join(run_dir, "snapshot_repeat")),
            run_dir, "snapshot_repeat", timeout)
        if not workers["snapshot_repeat"].get("error"):
            nondet = determinism(load_snapshot(os.path.join(run_dir, "snapshot")),
                                 load_snapshot(os.path.join(run_dir, "snapshot_repeat")))
            write_json(os.path.join(run_dir, "determinism.json"), nondet)

    profile = _assemble_profile(args, run_dir, entry, batch_size, floor, workers, nondet)
    write_json(os.path.join(run_dir, "profile.json"), profile)
    write_json(os.path.join(out, "profile.json"), profile)
    if set_baseline and nondet is not None:
        if os.path.isdir(baseline_dir):
            shutil.rmtree(baseline_dir)
        if os.path.exists(os.path.join(out, "accepted.json")):
            os.remove(os.path.join(out, "accepted.json"))   # gains restart from the new baseline
        shutil.copytree(run_dir, baseline_dir,
                        ignore=shutil.ignore_patterns("vis_payload.npz", "snapshot_repeat"))
    _print_profile(profile, set_baseline and nondet is not None, baseline_dir)
    failed = [k for k, w in workers.items() if w.get("error")]
    return EXIT_RUN_FAILED if failed else EXIT_OK


def _assemble_profile(args, run_dir, entry, batch_size, floor, workers, nondet):
    gen = workers["generate"]
    vis = workers.get("visualize") or {}
    unsupported = dict(gen.get("unsupported") or {})
    unsupported.update(vis.get("unsupported") or {})

    def diag(name):
        w = workers.get(name) or {}
        return None if w.get("error") or not w else {"reads": w.get("reads"), "calls": w.get("calls")}

    startups = [w["startup_seconds"] for w in workers.values() if w.get("startup_seconds") is not None]
    return {
        "run": os.path.basename(run_dir),
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "root": args.root, "entry": entry,
        "config": {"samples_per_state": args.samples, "seed": args.seed, "batch_size": batch_size,
                   "vis_samples": args.vis_samples, "diagnose_samples": args.diagnose_samples,
                   "snapshot_samples_per_state": args.snapshot_samples,
                   "order": "server-default (random order)"},
        "code_loader": (code_loader_info() or {}).get("version"),
        "load": {"start": getattr(args, "load_start", None), "end": load_info()},
        "floor": None if not floor else {
            k: floor.get(k) for k in ("t_inf_per_sample_mean_seconds", "recommended_batch_size",
                                      "framework", "device", "knee_reached")},
        "dataset": {"state_lengths": gen.get("state_lengths"), "grouped": gen.get("grouped")},
        "mapping": gen.get("mapping"),
        "handler_functions": gen.get("handler_functions"),
        "startup": {"per_worker_seconds": startups, "stats": percentiles(startups)},
        "generation": gen.get("generation"),
        "generation_sorted_what_if": (workers.get("generate_sorted") or {}).get("generation"),
        "inference": gen.get("inference"),
        "metrics": gen.get("metrics"),
        "visualizers": vis.get("visualizers"),
        "diagnostics": {"server_order": diag("diagnose"), "sorted_what_if": diag("diagnose_sorted")},
        "determinism": None if nondet is None else {f: len(v["samples"]) for f, v in nondet.items()},
        "memory": {k: w.get("memory") for k, w in workers.items() if w.get("memory")},
        "unsupported": unsupported,
        "truncated": bool(gen.get("truncated")),
        "workers": {k: {"exit_code": w.get("exit_code"), "wall_seconds": w.get("wall_seconds"),
                        "error": (w.get("error") or "")[-600:] or None} for k, w in workers.items()},
    }


def _print_handler_table(handlers, per_label):
    rows = sorted(handlers.values(), key=lambda h: -(h.get("per_sample_mean_seconds") or 0))
    for h in rows:
        print("    %-12s %-34s %8.2f %12s %12s" % (
            h["kind"], h["name"][:34], h["calls_per_sample"] or 0,
            fmt_ms(h["per_sample_mean_seconds"] or 0), fmt_ms(h["per_call_seconds"].get("p95", 0))))


def _print_profile(p, baseline_set, baseline_dir):
    gen = p["generation"] or {}
    print("  dataset %s; profiled %s samples; batch %s" % (
        p["dataset"]["state_lengths"], gen.get("samples"), p["config"]["batch_size"]))
    st = p["startup"]["stats"]
    if st.get("n"):
        print("  startup (import + preprocess) per worker: mean %.2f s (n=%d)" % (st["mean"], st["n"]))
    g = gen.get("per_sample_seconds") or {}
    w = (p.get("generation_sorted_what_if") or {}).get("per_sample_seconds") or {}
    print("  generation per sample: mean %s  p95 %s%s" % (
        fmt_ms(g.get("mean", 0)), fmt_ms(g.get("p95", 0)),
        ("   (sorted what-if: mean %s)" % fmt_ms(w["mean"])) if w.get("n") else ""))
    print("    %-12s %-34s %8s %12s %12s" % ("kind", "handler", "calls/s", "mean/sample", "p95/call"))
    _print_handler_table(gen.get("handlers") or {}, "sample")
    inf = p.get("inference") or {}
    if inf.get("per_sample_mean_seconds") is not None:
        print("  inference per sample (batch %s): %s" % (inf["batch_size"], fmt_ms(inf["per_sample_mean_seconds"])))
    if (p.get("metrics") or {}).get("handlers"):
        print("  metrics + loss:")
        _print_handler_table(p["metrics"]["handlers"], "sample")
    if p.get("visualizers"):
        v = p["visualizers"]
        print("  visualizers per visualized sample: mean %s (n=%d, fresh process)" % (
            fmt_ms(v["per_sample_seconds"].get("mean", 0)), v["samples"]))
        _print_handler_table(v["handlers"], "sample")
    for label, key in (("server order", "server_order"), ("sorted what-if", "sorted_what_if")):
        d = (p["diagnostics"] or {}).get(key)
        if d and d.get("reads"):
            r = d["reads"]
            print("  reads (%s): %.2f file reads/sample, max %d reads of one file in one sample" % (
                label, r["reads_per_sample"] or 0, r["max_reads_of_one_file_in_one_sample"]))
    d = (p["diagnostics"] or {}).get("server_order") or {}
    for rc in (d.get("calls") or {}).get("repeated_calls", [])[:5]:
        print("  repeated: %s x%.1f/sample from %s (%s/sample)" % (
            rc["function"], rc["calls_per_sample"], ", ".join(rc["called_from"]),
            fmt_ms(rc["cumulative_seconds_per_sample"])))
    if p.get("determinism"):
        print("  nondeterministic outputs (differ between two runs of unchanged code): %s" %
              ", ".join("%s (%d samples)" % kv for kv in sorted(p["determinism"].items())))
    for k, reason in (p.get("unsupported") or {}).items():
        print("  not profiled: %s — %s" % (k, reason))
    for k, wk in p["workers"].items():
        if wk.get("error"):
            print("  WORKER FAILED %s: %s" % (k, wk["error"].strip().splitlines()[-1]))
    if baseline_set:
        print("  baseline set -> %s" % baseline_dir)


# --------------------------------------------------------------------------- #
# compare
# --------------------------------------------------------------------------- #

def _latest_run(out):
    runs = os.path.join(out, "runs")
    names = sorted(d for d in os.listdir(runs) if d.isdigit()) if os.path.isdir(runs) else []
    return os.path.join(runs, names[-1]) if names else None


def pipeline_costs(p):
    def total(block):
        return sum((h.get("per_sample_mean_seconds") or 0)
                   for h in ((p.get(block) or {}).get("handlers") or {}).values())
    return {
        "generation": ((p.get("generation") or {}).get("per_sample_seconds") or {}).get("mean", 0.0),
        "inference": (p.get("inference") or {}).get("per_sample_mean_seconds") or 0.0,
        "metrics": total("metrics"),
        "visualizers": ((p.get("visualizers") or {}).get("per_sample_seconds") or {}).get("mean", 0.0),
    }


def expected_total(p, costs, visualized):
    n = sum((p.get("dataset") or {}).get("state_lengths", {}).values())
    nv = visualized or n
    return n * (costs["generation"] + costs["inference"] + costs["metrics"]) + nv * costs["visualizers"]


def cmd_compare(args):
    """Equivalence against the ORIGINAL baseline (every step stays lossless relative to the
    start); gain and memory against the last ACCEPTED run (so each change must earn its own
    gain — after one big win, a useless or slower change must not ride on it). An exit 0
    makes the current run the new accepted reference."""
    out = out_dir(args)
    base_dir = args.baseline or os.path.join(out, "baseline")
    cur_dir = args.run or _latest_run(out)
    accepted = _read_json(os.path.join(out, "accepted.json")) or {}
    ref_dir = args.reference or accepted.get("run") or base_dir
    if cur_dir and ref_dir and os.path.realpath(ref_dir) == os.path.realpath(cur_dir):
        ref_dir = base_dir
    base_p = _read_json(os.path.join(base_dir, "profile.json"))
    ref_p = _read_json(os.path.join(ref_dir, "profile.json")) or base_p
    cur_p = _read_json(os.path.join(cur_dir, "profile.json")) if cur_dir else None
    if base_p is None or cur_p is None:
        print("tl_perf compare: need a baseline (%s) and a profiled run (%s)" % (base_dir, cur_dir),
              file=sys.stderr)
        return EXIT_BLOCKER
    base_snap = load_snapshot(os.path.join(base_dir, "snapshot"))
    cur_snap = load_snapshot(os.path.join(cur_dir, "snapshot"))
    nondet = _read_json(os.path.join(base_dir, "determinism.json")) or {}
    eq = compare_snapshots(base_snap, cur_snap, nondet, args.rtol, args.atol) \
        if base_snap is not None and cur_snap is not None else \
        {"equivalent": False, "error": "snapshot missing"}

    bc, rc, cc = pipeline_costs(base_p), pipeline_costs(ref_p), pipeline_costs(cur_p)
    bt = expected_total(base_p, bc, args.visualized_samples)
    rt = expected_total(ref_p, rc, args.visualized_samples)
    ct = expected_total(cur_p, cc, args.visualized_samples)
    gain = (rt - ct) / rt if rt else 0.0                  # this step
    cumulative = (bt - ct) / bt if bt else 0.0            # since the baseline
    rw = (ref_p.get("generation_sorted_what_if") or {}).get("per_sample_seconds") or {}
    cw = (cur_p.get("generation_sorted_what_if") or {}).get("per_sample_seconds") or {}
    r_mem = ((ref_p.get("memory") or {}).get("generate") or {}).get("peak_rss_gb")
    c_mem = ((cur_p.get("memory") or {}).get("generate") or {}).get("peak_rss_gb")
    mem_increase = (c_mem - r_mem) / r_mem if r_mem and c_mem else 0.0
    loads = [((p.get("load") or {}).get("start") or {}).get("load1") for p in (ref_p, cur_p)]
    load_warning = None
    if all(x is not None for x in loads) and max(loads) > 2.0 * max(min(loads), 1.0):
        load_warning = ("the two runs were taken under different machine load (%.1f vs %.1f); "
                        "re-measure before trusting the gain" % tuple(loads))

    if not eq.get("equivalent"):
        code = EXIT_NOT_EQUIVALENT
    elif mem_increase > args.max_mem_increase and (c_mem - r_mem) > 0.0625:
        code = EXIT_MEMORY_REGRESSION
    elif gain < args.min_gain:
        code = EXIT_NO_GAIN
    else:
        code = EXIT_OK
    report = {
        "baseline": base_dir, "reference": ref_dir, "run": cur_dir, "exit_code": code,
        "equivalence": eq,
        "per_sample_seconds": {"baseline": bc, "reference": rc, "current": cc},
        "sorted_what_if_generation_mean": {"reference": rw.get("mean"), "current": cw.get("mean")},
        "expected_total_seconds": {"baseline": bt, "reference": rt, "current": ct,
                                   "gain": gain, "cumulative_gain": cumulative},
        "peak_rss_gb": {"reference": r_mem, "current": c_mem, "increase": mem_increase},
        "load_warning": load_warning,
        "thresholds": {"min_gain": args.min_gain, "max_mem_increase": args.max_mem_increase,
                       "rtol": args.rtol, "atol": args.atol},
    }
    path = os.path.join(cur_dir, "compare.json")
    write_json(path, report)
    write_json(os.path.join(out, "compare.json"), report)
    if code == EXIT_OK and not args.no_accept:
        write_json(os.path.join(out, "accepted.json"), {"run": cur_dir})
    bw = rw  # printed as the reference column below

    print("tl_perf compare: equivalence vs %s; gain vs %s" % (base_dir, ref_dir))
    verdict = "EQUIVALENT" if eq.get("equivalent") else "NOT EQUIVALENT"
    print("  outputs: %s (%s values compared bit-for-bit%s)" % (
        verdict, eq.get("checked_values"),
        ", tolerance rtol=%g atol=%g" % (args.rtol, args.atol) if args.rtol or args.atol else ""))
    for field, info in (eq.get("mismatched_fields") or {}).items():
        print("    differs: %s in %d samples, e.g. %s" % (field, info["samples"], info["examples"][0]))
    for field, info in (eq.get("nondeterministic_fields") or {}).items():
        b, c = info["baseline"] or {}, info["current"] or {}
        print("    %s: %s (nondeterministic; distribution check mean %.4g->%.4g, std %.4g->%.4g)" % (
            "ok" if info["ok"] else "SHIFTED", field, b.get("mean", 0), c.get("mean", 0),
            b.get("std", 0), c.get("std", 0)))
    for m in eq.get("missing") or []:
        print("    missing: %s" % m)
    print("  %-12s %12s %12s %12s" % ("per sample", "baseline", "reference", "current"))
    for block in ("generation", "inference", "metrics", "visualizers"):
        print("  %-12s %12s %12s %12s" % (block, fmt_ms(bc[block]), fmt_ms(rc[block]), fmt_ms(cc[block])))
    if bw.get("mean") and cw.get("mean"):
        print("  %-12s %12s %12s %12s" % ("gen (sorted)", "", fmt_ms(bw["mean"]), fmt_ms(cw["mean"])))
    print("  expected total: %.1f s -> %.1f s (this change %+.1f%%; since baseline %+.1f%%)" % (
        rt, ct, -100.0 * gain, -100.0 * cumulative))
    if r_mem and c_mem:
        print("  peak RSS (generation worker): %.2f GB -> %.2f GB (%+.1f%%)" % (r_mem, c_mem, 100 * mem_increase))
    if load_warning:
        print("  warning: %s" % load_warning)
    if code == EXIT_OK and not args.no_accept:
        print("  accepted: this run is now the reference for the next change")
    print("  -> %s (exit %d)" % (path, code))
    return code


# --------------------------------------------------------------------------- #
# score
# --------------------------------------------------------------------------- #

def _evidence(p):
    """{"kind:name": [evidence, ...]} plus block-level notes, from the diagnostics."""
    names = p.get("handler_functions") or {}
    by_fn = {}
    for key, fn in names.items():
        by_fn.setdefault(fn, []).append(key)
    per_handler, notes = {}, []
    diag = (p.get("diagnostics") or {})
    server = (diag.get("server_order") or {})
    for rc in ((server.get("calls") or {}).get("repeated_calls") or []):
        text = "%s runs %.1fx per sample, called from %s" % (
            rc["function"], rc["calls_per_sample"], ", ".join(rc["called_from"]))
        for key in by_fn.get(rc["function"], []):
            per_handler.setdefault(key, []).append(text)
        for caller in rc["called_from"]:
            for key in by_fn.get(caller, []):
                per_handler.setdefault(key, []).append(
                    "re-invokes %s (%s per sample)" % (rc["function"], fmt_ms(rc["cumulative_seconds_per_sample"])))
    implicated = set(per_handler)
    reads = server.get("reads") or {}
    sorted_reads = ((diag.get("sorted_what_if") or {}).get("reads") or {})
    inputs = [k for k in ((p.get("generation") or {}).get("handlers") or {}) if k.startswith("input:")]
    if reads.get("max_reads_of_one_file_in_one_sample", 0) > 1:
        text = "the same file is read up to %dx within one sample (%d of %d samples)" % (
            reads["max_reads_of_one_file_in_one_sample"], reads["samples_with_repeated_reads"], reads["samples"])
        notes.append(text)
        for key in implicated:          # attributed only where the call analysis points
            per_handler[key].append(text)
    rs, rw = reads.get("reads_per_sample"), sorted_reads.get("reads_per_sample")
    if rs and rw is not None and rs >= 0.5 and rw < 0.5 * rs:
        text = ("%.2f file reads per sample in server order vs %.2f in sorted order: the storage "
                "layout does not match the order samples are processed in" % (rs, rw))
        notes.append(text)
        for key in inputs:
            per_handler.setdefault(key, []).append(text)
    return per_handler, notes


def cmd_score(args):
    out = out_dir(args)
    p = _read_json(args.profile or os.path.join(out, "profile.json"))
    if p is None:
        print("tl_perf score: no profile.json; run `tl_perf profile` first", file=sys.stderr)
        return EXIT_BLOCKER
    floor = p.get("floor") or _read_json(os.path.join(out, "floor.json")) or {}
    t_inf = floor.get("t_inf_per_sample_mean_seconds")
    t_inf_source = "floor"
    if not t_inf:
        t_inf = (p.get("inference") or {}).get("per_sample_mean_seconds")
        t_inf_source = "profile inference"
    n = sum((p.get("dataset") or {}).get("state_lengths", {}).values())
    nv = args.visualized_samples or n
    static = _read_json(args.static) if args.static else []
    evidence, notes = _evidence(p)
    costs = pipeline_costs(p)

    candidates = []

    def add(block, key, h, count):
        per = h.get("per_sample_mean_seconds") or 0.0
        ev = list(evidence.get(key, []))
        st = [s for s in static or [] if s.get("handler") in (key, h.get("name"))]
        confidence = min(1.0, 0.5 + (0.3 if ev else 0.0) + (0.2 if st else 0.0))
        expected = per * count
        candidates.append({
            "handler": key, "block": block, "kind": h.get("kind"),
            "per_sample_mean_seconds": per,
            "p95_per_call_seconds": (h.get("per_call_seconds") or {}).get("p95"),
            "ratio_to_inference": per / t_inf if t_inf else None,
            "samples_affected": count, "expected_seconds": expected,
            "confidence": confidence, "priority": expected * confidence,
            "evidence": ev, "static_signals": [s.get("code") or s.get("message") for s in st],
        })

    for key, h in ((p.get("generation") or {}).get("handlers") or {}).items():
        add("generation", key, h, n)
    for key, h in ((p.get("metrics") or {}).get("handlers") or {}).items():
        add("metrics" if h.get("kind") == "metric" else "loss", key, h, n)
    for key, h in ((p.get("visualizers") or {}).get("handlers") or {}).items():
        add("visualizers", key, h, nv)
    startup = (p.get("startup") or {}).get("stats") or {}
    if startup.get("n"):
        add("startup", "startup:preprocess", {"kind": "startup", "name": "import + preprocess",
                                              "per_sample_mean_seconds": startup["mean"]}, 1)
    candidates.sort(key=lambda c: -c["priority"])
    for i, c in enumerate(candidates, 1):
        c["rank"] = i
        c["minor"] = c["ratio_to_inference"] is not None and c["ratio_to_inference"] < args.min_ratio \
            and c["block"] != "startup"

    totals = {"generation": costs["generation"] * n, "inference": costs["inference"] * n
              if costs["inference"] else (t_inf or 0) * n,
              "metrics": costs["metrics"] * n, "visualizers": costs["visualizers"] * nv,
              "startup": startup.get("mean", 0.0)}
    grand = sum(totals.values()) or 1.0
    report = {
        "t_inf_per_sample_mean_seconds": t_inf, "t_inf_source": t_inf_source,
        "samples_total": n, "visualized_samples_assumed": nv,
        "producer_to_inference_ratio": costs["generation"] / t_inf if t_inf else None,
        "shares": {k: v / grand for k, v in totals.items()},
        "expected_seconds": totals,
        "notes": notes,
        "candidates": candidates,
    }
    path = os.path.join(out, "score.json")
    write_json(path, report)

    print("tl_perf score (unit: model inference = %s per sample, from %s)" % (
        fmt_ms(t_inf or 0), t_inf_source))
    if report["producer_to_inference_ratio"] is not None:
        print("  sample generation costs %.1fx the model's inference per sample" %
              report["producer_to_inference_ratio"])
    print("  share of expected runtime: %s" % ", ".join(
        "%s %.0f%%" % (k, 100 * v) for k, v in sorted(report["shares"].items(), key=lambda kv: -kv[1])))
    for note in notes:
        print("  evidence: %s" % note)
    print("  %4s %-11s %-34s %11s %8s %10s %5s" % ("rank", "block", "handler", "mean/sample", "x inf",
                                                  "expected", "conf"))
    for c in candidates[:args.top]:
        print("  %4d %-11s %-34s %11s %8s %9.1fs %5.2f%s" % (
            c["rank"], c["block"], c["handler"][:34], fmt_ms(c["per_sample_mean_seconds"]),
            "%.2f" % c["ratio_to_inference"] if c["ratio_to_inference"] is not None else "-",
            c["expected_seconds"], c["confidence"], "  (minor)" if c["minor"] else ""))
        for e in c["evidence"][:3]:
            print("       - %s" % e)
    print("  -> %s" % path)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# fit
# --------------------------------------------------------------------------- #

def _linear_fit(xs, ys):
    """Least-squares intercept and slope."""
    n = float(len(xs))
    if n < 2:
        return (ys[0] if ys else 0.0), 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var if var else 0.0
    return my - slope * mx, slope


def _instance_rows(integ):
    """Instance-level rows per state (0 when the integration has none)."""
    get = getattr(integ.loader, "get_instances_data", None)
    rows = {}
    for state, enum_state in integ.states.items():
        try:
            with working_dir(integ.root), captured_stdout():
                _sample_to_instances, instance_to_sample = get(enum_state) if get else ({}, {})
            rows[state] = len(instance_to_sample or {})
        except Exception:
            rows[state] = 0
    return rows


def cmd_fit(args):
    """Measured, not modeled: model memory is read from the process while running each
    batch size (ascending, so every step sets a new peak)."""
    import numpy as np
    out = out_dir(args)
    integ = Integration(args.root, args.entry or read_entry_file(args.root))
    try:
        integ.load()
    except Exception as exc:
        print("tl_perf fit: integration failed to load: %r" % (exc,), file=sys.stderr)
        return EXIT_INVALID_INTEGRATION
    if not integ.valid:
        print("tl_perf fit: check_dataset() failed; run preflight", file=sys.stderr)
        return EXIT_INVALID_INTEGRATION
    setup = integ.setup_summary()

    def tensor_bytes(entries):
        return sum(4 * int(np.prod(e["shape"])) for e in entries)

    per_sample_bytes = tensor_bytes(setup["inputs"]) + tensor_bytes(setup["ground_truths"])
    instances = _instance_rows(integ)
    rows = {s: n + instances.get(s, 0) for s, n in setup["state_lengths"].items()}
    n_total = sum(rows.values())

    rss_before = current_rss_gb()
    try:
        lm = load_model(integ)
    except Exception as exc:
        print("tl_perf fit: model could not be loaded: %r" % (exc,), file=sys.stderr)
        return EXIT_MODEL_LOAD
    specs = model_input_specs(lm)
    pool, _mapping = real_input_pool(integ, specs, 4)
    rng = np.random.default_rng(0)
    run_model(lm, specs, make_batch(specs, 1, pool, rng))   # warm the runtime first
    rss_loaded = max(current_rss_gb() or 0.0, peak_rss_gb() or 0.0)

    floor = _read_json(os.path.join(out, "floor.json")) or {}
    sizes = [lm.fixed_batch_size] if lm.fixed_batch_size else \
        [int(x) for x in args.batch_sizes.split(",") if x.strip()]
    measured = []
    for bs in sorted(sizes):
        try:
            run_model(lm, specs, make_batch(specs, bs, pool, rng))
        except Exception as exc:
            measured.append({"batch_size": bs, "error": repr(exc)[:300]})
            break
        peak = max(current_rss_gb() or 0.0, peak_rss_gb() or 0.0)
        measured.append({"batch_size": bs, "peak_rss_gb": peak,
                         "activation_gb": max(0.0, peak - rss_loaded)})
    ok = [m for m in measured if "error" not in m]
    base, per_sample_act = _linear_fit([m["batch_size"] for m in ok],
                                       [m["activation_gb"] for m in ok])
    per_sample_act = max(per_sample_act, 0.0)

    memory_gb = args.memory_gb or memory_info().get("total_gb") or 0.0
    budget = memory_gb * args.usable_fraction
    candidates = sorted(set(sizes) | {floor.get("recommended_batch_size") or 1})

    def predicted(bs):
        return rss_loaded + max(base, 0.0) + per_sample_act * bs + bs * per_sample_bytes / 2 ** 30

    table = [{"batch_size": bs, "predicted_peak_gb": predicted(bs),
              "fits": predicted(bs) <= budget} for bs in candidates]
    fitting = [t["batch_size"] for t in table if t["fits"]]
    knee = floor.get("recommended_batch_size")
    if not fitting:
        recommended = None
    elif knee:
        recommended = max([b for b in fitting if b <= knee] or [min(fitting)])
    else:
        recommended = max(fitting)

    batch_support = _batch_support(integ, lm, specs, pool)
    report = {
        "method": "measured: process memory while running each batch size (not a formula)",
        "per_sample_tensor_bytes": per_sample_bytes,
        "rows_per_state": rows, "instance_rows": instances, "rows_total": n_total,
        "dataset_tensor_gb": n_total * per_sample_bytes / 2 ** 30,
        "grouped": setup["grouped"],
        "model": {"framework": lm.framework, "fixed_batch_size": lm.fixed_batch_size,
                  "process_rss_before_model_gb": rss_before,
                  "process_rss_loaded_gb": rss_loaded,
                  "activation_per_sample_gb": per_sample_act, "activation_base_gb": base},
        "measured": measured,
        "memory_budget_gb": budget, "memory_total_gb": memory_gb,
        "usable_fraction": args.usable_fraction,
        "table": table,
        "floor_recommended_batch_size": knee,
        "recommended_batch_size": recommended,
        "batch_support": batch_support,
    }
    path = os.path.join(out, "fit.json")
    write_json(path, report)

    print("tl_perf fit (%s)" % report["method"])
    print("  rows %s (instances %s) = %d; %.1f KB of input+GT tensors per sample; %.2f GB total" % (
        rows, instances, n_total, per_sample_bytes / 1024.0, report["dataset_tensor_gb"]))
    print("  model resident %.2f GB; +%.1f MB activation per sample in a batch" % (
        rss_loaded, per_sample_act * 1024))
    print("  budget %.1f GB (%.0f%% of %.1f GB)" % (budget, 100 * args.usable_fraction, memory_gb))
    for t in table:
        print("    batch %4d  predicted peak %6.2f GB  %s" % (
            t["batch_size"], t["predicted_peak_gb"], "fits" if t["fits"] else "DOES NOT FIT"))
    for name, status in batch_support.items():
        if status != "ok":
            print("  batch support: %s -> %s" % (name, status))
    if recommended is None:
        print("  NOT PREDICTED TO FIT even at the smallest batch size")
        print("  -> %s (exit %d)" % (path, EXIT_NOT_FIT))
        return EXIT_NOT_FIT
    print("  recommended batch size %d%s" % (
        recommended, " (floor knee %d)" % knee if knee else ""))
    print("  -> %s" % path)
    return EXIT_OK


def _has_batch_dim(out, batch):
    """A per-sample result: every array (or the list itself) has `batch` rows."""
    import numpy as np
    if isinstance(out, (list, tuple)) and len(out) == batch:
        return True          # e.g. confusion-matrix elements, one list per sample
    arrays = [v for _f, v in flatten_output(out) if isinstance(v, np.ndarray) and v.ndim >= 1]
    return bool(arrays) and all(a.shape[0] == batch for a in arrays)


def _batch_support(integ, lm, specs, pool):
    """Run every wired metric/loss on a batch of 2 real samples: they must accept a batch."""
    import numpy as np
    model_inputs, handlers = mapping_info()
    status = {}
    for state, ids in integ.sample_ids.items():
        if len(ids) < 2:
            continue
        rows = []
        for sid in ids[:2]:
            rows.extend(sample_rows(integ.get_sample(state, sid), sid))
        inputs, gts = _stack(rows, 1), _stack(rows, 2)
        try:
            feed = model_feed_names(specs, model_inputs, rows[0][1])
            preds = run_model(lm, specs, [inputs[n] for n in feed])
        except Exception as exc:
            return {"model": "error: %r" % (exc,)}
        for kind, name, args in handlers:
            if kind not in ("metric", "loss"):
                continue
            key = "%s:%s" % (kind, name)
            try:
                tensors = {arg: resolve_arg(t, s, inputs, gts, preds) for arg, (t, s) in args.items()}
                call = integ.loader.run_metric if kind == "metric" else integ.loader.run_custom_loss
                with working_dir(integ.root), captured_stdout():
                    out = call(name, np.array([r[0] for r in rows]), integ.states[state], tensors)
                status[key] = "ok" if _has_batch_dim(out, 2) else "returns no per-sample batch dimension"
            except Unsupported as exc:
                status[key] = "skipped: %s" % exc
            except Exception as exc:
                status[key] = "error on a batch of 2: %r" % (exc,)
        break
    return status


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

REPORT_REQUIRED = {
    "title": str,
    "optimizations": list,
    "remaining_bottleneck": dict,
    "tensorleap_actions": list,
}
OPTIMIZATION_REQUIRED = ("problem", "change", "evidence", "equivalence")


def validate_report(doc):
    errors = []
    if not isinstance(doc, dict):
        return ["report.json must be a JSON object"]
    for key, typ in REPORT_REQUIRED.items():
        if key not in doc:
            errors.append("missing required field %r" % key)
        elif not isinstance(doc[key], typ):
            errors.append("%r must be a %s" % (key, typ.__name__))
    for i, opt in enumerate(doc.get("optimizations") or []):
        if not isinstance(opt, dict):
            errors.append("optimizations[%d] must be an object" % i)
            continue
        for key in OPTIMIZATION_REQUIRED:
            if not opt.get(key):
                errors.append("optimizations[%d] missing %r" % (i, key))
    rb = doc.get("remaining_bottleneck")
    if isinstance(rb, dict):
        for key in ("component", "evidence"):
            if not rb.get(key):
                errors.append("remaining_bottleneck missing %r" % key)
    for i, act in enumerate(doc.get("tensorleap_actions") or []):
        if not isinstance(act, dict) or not act.get("need"):
            errors.append("tensorleap_actions[%d] needs a 'need'" % i)
    return errors


def _md_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines)


def _share_rows(before, after, visualized):
    rows = []
    bc = pipeline_costs(before) if before else None
    ac = pipeline_costs(after) if after else None
    for block in ("generation", "inference", "metrics", "visualizers"):
        b = bc[block] if bc else None
        a = ac[block] if ac else None
        rows.append([block, fmt_ms(b) if b is not None else "-", fmt_ms(a) if a is not None else "-"])
    if before:
        rows.append(["expected total", "%.1f s" % expected_total(before, bc, visualized),
                     "%.1f s" % expected_total(after, ac, visualized) if after else "-"])
    return rows


def render_report(doc, out):
    lines = ["# %s" % doc["title"], ""]
    if doc.get("summary"):
        lines += [doc["summary"], ""]
    preflight = _read_json(os.path.join(out, "preflight.json")) or {}
    floor = _read_json(os.path.join(out, "floor.json")) or {}
    fit = _read_json(os.path.join(out, "fit.json")) or {}
    before = _read_json(os.path.join(out, "baseline", "profile.json"))
    latest_dir = _latest_run(out)
    after = _read_json(os.path.join(latest_dir, "profile.json")) if latest_dir else None
    if after and before and after.get("run") == before.get("run") and after.get("created") == before.get("created"):
        after = None

    lines += ["## Environment", ""]
    env = doc.get("environment") or {}
    if preflight:
        env.setdefault("platform", preflight.get("platform"))
        env.setdefault("cpu cores", (preflight.get("cpu") or {}).get("logical_cores"))
        env.setdefault("RAM (GB)", "%.1f" % ((preflight.get("memory") or {}).get("total_gb") or 0))
        env.setdefault("code-loader", (preflight.get("code_loader") or {}).get("version"))
        if preflight.get("model"):
            env.setdefault("model", "%s on %s" % (preflight["model"]["framework"],
                                                  preflight["model"]["device"]["label"]))
    lines += [_md_table(["", ""], sorted(env.items())) if env else "_not recorded_", ""]

    lines += ["## Performance floor and fit", ""]
    if floor:
        lines.append("- Model inference floor: **%s per sample** (mean, batch %s, %s on %s)." % (
            fmt_ms(floor["t_inf_per_sample_mean_seconds"]), floor["recommended_batch_size"],
            floor["framework"], floor["device"]["label"]))
    if fit:
        lines.append("- Recommended batch size: **%s**; model resident %.2f GB, +%.1f MB per sample "
                     "in a batch (measured)." % (
                         fit.get("recommended_batch_size"), fit["model"]["process_rss_loaded_gb"],
                         1024 * fit["model"]["activation_per_sample_gb"]))
        bad = {k: v for k, v in (fit.get("batch_support") or {}).items() if v != "ok"}
        for k, v in bad.items():
            lines.append("- Batch support: `%s` — %s." % (k, v))
    lines.append("")

    lines += ["## Runtime breakdown (per sample)", ""]
    visualized = doc.get("visualized_samples")
    lines += [_md_table(["block", "before", "after"], _share_rows(before, after, visualized)), ""]
    if before or after:
        src = after or before
        handlers = []
        for block in ("generation", "metrics", "visualizers"):
            for h in ((src.get(block) or {}).get("handlers") or {}).values():
                pc = h.get("per_call_seconds") or {}
                mean = h.get("per_sample_mean_seconds") or 0
                handlers.append((mean, [block, "`%s`" % h["name"], fmt_ms(mean), fmt_ms(pc.get("p50", 0)),
                                        fmt_ms(pc.get("p95", 0)), fmt_ms(pc.get("p99", 0))]))
        handlers.sort(key=lambda r: -r[0])
        lines += ["Components (%s):" % ("after" if after else "before"), "",
                  _md_table(["block", "component", "mean/sample", "P50/call", "P95/call", "P99/call"],
                            [row for _, row in handlers]), ""]

    lines += ["## Optimizations applied", ""]
    if not doc["optimizations"]:
        lines += ["_None._", ""]
    for i, opt in enumerate(doc["optimizations"], 1):
        lines += ["### %d. %s" % (i, opt["problem"]), "",
                  "- **Change:** %s" % opt["change"],
                  "- **Evidence:** %s" % opt["evidence"],
                  "- **Equivalence:** %s" % opt["equivalence"]]
        if opt.get("before") or opt.get("after"):
            lines.append("- **Runtime:** %s → %s%s" % (opt.get("before", "?"), opt.get("after", "?"),
                                                       " (%s)" % opt["gain"] if opt.get("gain") else ""))
        if opt.get("side_effects"):
            lines.append("- **Trade-offs:** %s" % opt["side_effects"])
        if opt.get("commit"):
            lines.append("- **Commit:** `%s`" % opt["commit"])
        lines.append("")

    rb = doc["remaining_bottleneck"]
    lines += ["## Remaining bottleneck", "",
              "**%s**%s" % (rb["component"], " — %s" % rb["share"] if rb.get("share") else ""),
              "", "- Evidence: %s" % rb["evidence"]]
    if rb.get("explanation"):
        lines.append("- Why: %s" % rb["explanation"])
    lines.append("")

    if doc.get("lossy_options"):
        lines += ["## Options that would change behavior (not applied without consent)", ""]
        lines += [_md_table(["option", "expected gain", "cost / behavior change", "decision"],
                            [[o.get("option", ""), o.get("gain", ""), o.get("cost", ""), o.get("decision", "pending")]
                             for o in doc["lossy_options"]]), ""]
    if doc.get("server_validation"):
        sv = doc["server_validation"]
        lines += ["## Server validation", "", "- Status: **%s**" % sv.get("status", "unknown")]
        for k in ("job", "duration", "notes"):
            if sv.get(k):
                lines.append("- %s: %s" % (k.capitalize(), sv[k]))
        lines.append("")
    if doc.get("remaining_integration_issues"):
        lines += ["## Remaining issues in the integration", ""] + \
            ["- %s" % x for x in doc["remaining_integration_issues"]] + [""]
    lines += ["## Recommended Tensorleap actions", ""]
    if not doc["tensorleap_actions"]:
        lines += ["_None._", ""]
    for act in doc["tensorleap_actions"]:
        lines.append("- **%s**%s%s" % (act["need"],
                                       " — evidence: %s" % act["evidence"] if act.get("evidence") else "",
                                       " — impact: %s" % act["impact"] if act.get("impact") else ""))
    lines.append("")
    if doc.get("coverage_caveats"):
        lines += ["## What was not verified", ""] + ["- %s" % x for x in doc["coverage_caveats"]] + [""]
    return "\n".join(lines).rstrip() + "\n"


def cmd_report(args):
    out = out_dir(args)
    src = args.input or os.path.join(out, "report.json")
    try:
        doc = _read_json(src)
    except ValueError as exc:
        print("tl_perf report: %s is not valid JSON: %s" % (src, exc), file=sys.stderr)
        return EXIT_BAD_REPORT
    if doc is None:
        print("tl_perf report: %s not found" % src, file=sys.stderr)
        return EXIT_BAD_REPORT
    errors = validate_report(doc)
    if errors:
        print("tl_perf report: invalid report.json:", file=sys.stderr)
        for e in errors:
            print("  - %s" % e, file=sys.stderr)
        return EXIT_BAD_REPORT
    md = render_report(doc, out)
    path = os.path.join(out, "report.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    print("tl_perf report -> %s" % path)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

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

    p = sub.add_parser("profile", parents=[common],
                       help="every integration component, run the way Tensorleap runs it")
    p.add_argument("--samples", type=int, default=200, help="samples per state")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--batch-size", type=int, default=None,
                   help="metric/loss batch (default: floor's recommendation, else 8)")
    p.add_argument("--vis-samples", type=int, default=50, help="samples to run visualizers on")
    p.add_argument("--diagnose-samples", type=int, default=30)
    p.add_argument("--snapshot-samples", type=int, default=16, help="snapshot samples per state")
    p.add_argument("--max-seconds", type=float, default=None,
                   help="stop generating after this long (per worker)")
    p.add_argument("--worker-timeout", type=float, default=3600)
    p.add_argument("--no-what-if", action="store_true", help="skip the sorted-order what-if")
    p.add_argument("--set-baseline", action="store_true",
                   help="make this run the equivalence baseline (automatic for the first run)")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("score", parents=[common], help="rank optimization candidates from a profile")
    p.add_argument("--profile", default=None, help="profile.json (default: latest)")
    p.add_argument("--static", default=None,
                   help="JSON list of static-read findings: [{handler, code, message}]")
    p.add_argument("--visualized-samples", type=int, default=None,
                   help="how many samples get visualized (default: all)")
    p.add_argument("--min-ratio", type=float, default=0.05,
                   help="below this cost ratio to inference a candidate is marked minor")
    p.add_argument("--top", type=int, default=15)
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("compare", parents=[common],
                       help="equivalence + timing/memory delta against the baseline")
    p.add_argument("--baseline", default=None, help="baseline dir (default: <out>/baseline)")
    p.add_argument("--run", default=None, help="run dir (default: latest run)")
    p.add_argument("--reference", default=None,
                   help="run to measure the gain against (default: last accepted run, else baseline)")
    p.add_argument("--no-accept", action="store_true",
                   help="don't make this run the new reference on exit 0")
    p.add_argument("--rtol", type=float, default=0.0, help="declared relative tolerance")
    p.add_argument("--atol", type=float, default=0.0, help="declared absolute tolerance")
    p.add_argument("--min-gain", type=float, default=0.05,
                   help="minimum expected-runtime gain to count (run-to-run noise is ~3%%)")
    p.add_argument("--max-mem-increase", type=float, default=0.10)
    p.add_argument("--visualized-samples", type=int, default=None)
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("fit", parents=[common],
                       help="per-sample size and measured memory headroom per batch size")
    p.add_argument("--batch-sizes", default=DEFAULT_BATCH_SIZES)
    p.add_argument("--memory-gb", type=float, default=None,
                   help="memory of the machine that will run the model (default: this one's)")
    p.add_argument("--usable-fraction", type=float, default=0.8)
    p.set_defaults(func=cmd_fit)

    p = sub.add_parser("report", parents=[common], help="render report.json into report.md")
    p.add_argument("--input", default=None, help="report.json (default: <out>/report.json)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("_worker")  # internal: one measurement pass in a fresh process
    p.add_argument("task", choices=sorted(WORKERS))
    p.add_argument("--plan", required=True)
    p.add_argument("--result", required=True)
    p.set_defaults(func=cmd_worker, root=".")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_BLOCKER
    args.root = os.path.abspath(args.root)
    if args.func is not cmd_worker:
        ensure_out(out_dir(args))
    return args.func(args)


if __name__ == "__main__":
    exit_code = main()
    # code-loader prints its integration-test status/warning table from an atexit hook;
    # it is about authoring, not runtime, so keep it out of our output.
    sys.stdout.flush()
    sys.stdout = open(os.devnull, "w")
    sys.exit(exit_code)
