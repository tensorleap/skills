"""Acceptance-mode structured check for a Tensorleap integration.

Wraps ``code_loader.leaploader.LeapLoader.check_dataset()`` and prints a JSON
summary. Use this (not the human-readable exit table) for "is it actually done?"
decisions: ``isValid`` / ``generalError`` / per-handler ``payloads[].passed`` are
authoritative; the exit table can show stale crosses.

The check runs the way the server job does: with an EMPTY home folder and temp
folder (the job runs as another user and starts empty on every push). Whatever
the dataset side fetches or creates there is reported under ``dataAccess``,
with an estimate of how much every push would fetch. Set
``TL_CHECK_EMPTY_HOME=0`` to run with your real home folder instead.

Must run inside the project's environment (pyenv + poetry by default, or whatever
the user chose) so ``code_loader`` imports:

    poetry run python scripts/tl_check.py [repo_root] [entry_file]
    # or, with the project's venv active:  python scripts/tl_check.py [repo_root]

    repo_root   defaults to "."
    entry_file  defaults to "leap_integration.py"

Exit code: 0 when the parse reports ``is_valid``, 1 when it doesn't, 2 when
code_loader is unavailable or too old, 3 when the check was stopped early because
the integration kept downloading into the empty home folder (see ``dataAccess``).
"""

import atexit
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

MIN_CODE_LOADER = (1, 0, 142)

# Empty-home settings.
STOP_AFTER_WRITING_S = 45      # stop when the integration keeps writing this long...
STOP_AFTER_MB = 200            # ...or has written this much, whichever comes first
MIN_REPORTED_FILE = 1000       # bytes; smaller files (config stubs) are not listed
EXPLAIN = (
    "The check ran with an empty home and temp folder, like the server job, which "
    "runs as a different user and starts empty on every push. Files listed under "
    "'written' were fetched or created by the dataset side during the check, so the "
    "job will fetch or create them again on every push. 'estimatedPerPushMB' = "
    "preprocessMB (every push) + sharedMB (fetched once, e.g. an asset) + perSampleMB "
    "x samples. Decide whether that is acceptable (e.g. a "
    "small asset) or whether the data should live in the data volume and be read "
    "from the path in project_config.yaml. Data read from the GENERIC_HOST_PATH path, "
    "written in full, is on the data volume, where it belongs.")


class _EmptyHome:
    """Point HOME and the temp folder at empty folders and measure what appears there."""

    def __init__(self):
        self.real_home = os.path.expanduser("~")
        self.root = tempfile.mkdtemp(prefix="tl_check_home_")
        self.home = os.path.join(self.root, "home")
        self.tmp = os.path.join(self.root, "tmp")
        self.preexisting = set()
        self.notes = {}
        self.started = time.time()
        self.phase = "import and preprocess"
        self.preprocess_bytes = None
        atexit.register(self.cleanup)

    def enter(self):
        os.makedirs(self.tmp)
        analytics = os.path.join(self.real_home, ".tensorleap")
        if os.path.isdir(analytics):
            # code_loader keeps its analytics ids here; without them every check is a new device.
            shutil.copytree(analytics, os.path.join(self.home, ".tensorleap"))
        else:
            os.makedirs(self.home)
        self.preexisting = set(self._files())
        dropped = sorted(k for k, v in os.environ.items()
                         if (k.startswith("XDG_") or k.endswith("_HOME") or "CACHE" in k)
                         and v.startswith(self.real_home))
        for key in dropped:
            del os.environ[key]
        self.notes["clearedEnv"] = dropped
        if "GENERIC_HOST_PATH" in os.environ:
            self.notes["GENERIC_HOST_PATH"] = "%s (already set in the environment)" % os.environ["GENERIC_HOST_PATH"]
        else:
            volumes = _local_data_volumes()
            if volumes:
                os.environ["GENERIC_HOST_PATH"] = ":".join(volumes)
                self.notes["GENERIC_HOST_PATH"] = ("%s (the local server's data volumes, where the job "
                                                   "can read data)" % ":".join(volumes))
            else:
                self.notes["GENERIC_HOST_PATH"] = "not set (no local server answered)"
        os.environ["HOME"] = self.home
        os.environ["TMPDIR"] = self.tmp
        tempfile.tempdir = None
        threading.Thread(target=self._watch, daemon=True).start()

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _files(self):
        out = {}
        for root, _, files in os.walk(self.root):
            for name in files:
                path = os.path.join(root, name)
                try:
                    out[path] = os.path.getsize(path)
                except OSError:
                    pass
        return out

    def written(self):
        return {p: s for p, s in self._files().items() if p not in self.preexisting}

    def written_bytes(self):
        return sum(self.written().values())

    def _label(self, path):
        for base, label in ((self.home, "~"), (self.tmp, "$TMPDIR")):
            if path.startswith(base):
                return label + path[len(base):]
        return path

    def folders(self, depth=3):
        groups = {}
        for path, size in self.written().items():
            if size < MIN_REPORTED_FILE:
                continue
            parts = self._label(path).split("/")
            key = "/".join(parts[:depth + 1]) if len(parts) > depth + 1 else self._label(path)
            files, total = groups.get(key, (0, 0))
            groups[key] = (files + 1, total + size)
        ranked = sorted(groups.items(), key=lambda kv: -kv[1][1])[:8]
        return [{"folder": k, "files": n, "MB": round(b / 1e6, 2)} for k, (n, b) in ranked]

    def _watch(self):
        # A "streak" is a stretch of continuous writing (no pause longer than 5 s), so a
        # tiny config file written at import doesn't count toward a later download.
        streak_start, streak_bytes0 = None, 0
        last_size, last_growth = 0, None
        while True:
            time.sleep(0.5)
            size = self.written_bytes()
            now = time.time()
            if size > last_size:
                if last_growth is None or now - last_growth > 5:
                    streak_start, streak_bytes0 = now, last_size
                last_growth = now
            last_size = size
            in_streak = last_growth is not None and now - last_growth <= 5
            too_long = in_streak and now - streak_start > STOP_AFTER_WRITING_S
            if too_long or size > STOP_AFTER_MB * 1e6:
                self._stop(size, streak_start, streak_bytes0, now)

    def _stop(self, size, streak_start, streak_bytes0, now):
        seconds = max(now - streak_start, 1e-6) if streak_start else None
        report = {
            "stoppedEarly": True,
            "stoppedDuring": self.phase,
            "writtenMB": round(size / 1e6, 2),
            "writingForSeconds": round(seconds, 1) if seconds else None,
            "rateMBps": round((size - streak_bytes0) / 1e6 / seconds, 3) if seconds else None,
            "written": self.folders(),
            "environment": self.notes,
            "explain": EXPLAIN + " The check was stopped early because the integration was "
                       "still downloading; the real amount per push is at least 'writtenMB'.",
        }
        print(json.dumps({"available": True, "dataAccess": report}, indent=2), file=sys.__stdout__, flush=True)
        print("STOPPED EARLY: the dataset side kept writing into the empty home/temp folder "
              "(%.1f MB so far). See dataAccess." % (size / 1e6), file=sys.__stderr__, flush=True)
        self.cleanup()
        os._exit(3)

    def report(self, setup, first_sample_bytes, second_sample):
        """first_sample_bytes: total after check_dataset (preprocess + sample 0 per split).
        second_sample: (bytes added by sample 1 per split, number of splits probed) or None."""
        total = self.written_bytes()
        out = {"stoppedEarly": False, "writtenMB": round(total / 1e6, 2), "written": self.folders(),
               "environment": self.notes}
        samples = 0
        if setup is not None:
            pp = setup.preprocess
            samples = sum([int(pp.training_length), int(pp.validation_length),
                           int(pp.test_length or 0), int(pp.unlabeled_length or 0)])
        if self.preprocess_bytes is not None and second_sample is not None and second_sample[1]:
            # Sample 0 also pays for anything fetched once and shared (an asset, a lookup
            # table); sample 1 pays only its own data, so it gives the per-sample cost.
            added, probed = second_sample
            per_sample = added / probed
            shared = max(first_sample_bytes - self.preprocess_bytes - per_sample * probed, 0)
            out["preprocessMB"] = round(self.preprocess_bytes / 1e6, 2)
            out["sharedMB"] = round(shared / 1e6, 2)
            out["perSampleMB"] = round(per_sample / 1e6, 4)
            out["samples"] = samples
            out["estimatedPerPushMB"] = round((self.preprocess_bytes + shared + per_sample * samples) / 1e6, 1)
        else:
            out["estimatedPerPushMB"] = None
            out["estimateNote"] = ("the per-sample cost could not be measured separately "
                                   "(preprocess not cached, or no second sample); only the total is known")
        if out["written"]:
            out["explain"] = EXPLAIN
        return out


def _run_second_sample(loader):
    """Call every input/GT/metadata handler on the second sample of each split, the way
    check_dataset() calls them on the first. Returns the number of splits probed."""
    from code_loader.inner_leap_binder import global_leap_binder
    from code_loader.contract.enums import DataStateEnum
    container = global_leap_binder.setup_container
    probed = 0
    for state, response in loader._preprocess_result().items():
        ids = response.sample_ids
        if ids is None or len(ids) < 2:
            continue
        handlers = list(container.inputs) + list(container.metadata)
        if state != DataStateEnum.unlabeled:
            handlers += list(container.ground_truths)
        for handler in handlers:
            try:
                handler.function(ids[1], response)
            except Exception:  # noqa: BLE001 - only measuring; check_dataset reports real errors
                pass
        probed += 1
    return probed


def _local_data_volumes():
    """Data volumes of a local Tensorleap server, from `leap server info`; [] if none answers."""
    try:
        proc = subprocess.run(["leap", "server", "info"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    volumes, inside = [], False
    for line in (proc.stdout + proc.stderr).splitlines():
        text = line.strip()
        if text.lower().startswith("datasetvolumes:"):
            inside = True
        elif inside and text.startswith("- "):
            volumes.append(text[2:].split(":")[-1])
        elif inside:
            break
    return volumes


def _code_loader_too_old():
    """Return an error message when the installed code-loader is below the minimum."""
    from importlib.metadata import PackageNotFoundError, version
    try:
        installed = version("code-loader")
    except PackageNotFoundError:
        return None
    release = tuple(int(n) for n in re.findall(r"\d+", installed)[:3])
    if release >= MIN_CODE_LOADER:
        return None
    return ("code-loader %s is below the required %s (this env runs Python %d.%d). "
            "Upgrade code-loader; if pip finds no matching version, this Python is not "
            "supported by code-loader: recreate the env with a supported interpreter."
            % (installed, ".".join(map(str, MIN_CODE_LOADER)), *sys.version_info[:2]))


def _as_int_list(values):
    if values is None:
        return None
    return [int(v) for v in values]


def _payload_entry(item):
    entry = {"name": str(item.name), "passed": bool(item.is_passed)}
    if getattr(item, "handler_type", None) is not None:
        entry["handlerType"] = str(item.handler_type)
    if getattr(item, "shape", None) is not None:
        entry["shape"] = _as_int_list(item.shape)
    if getattr(item, "display", None):
        entry["display"] = {str(k): str(v) for k, v in item.display.items()}
    return entry


def _shape_entry(item, include_channel_dim=False):
    entry = {"name": str(item.name)}
    shape = getattr(item, "shape", None)
    if shape is not None:
        entry["shape"] = _as_int_list(shape)
    if include_channel_dim:
        channel_dim = getattr(item, "channel_dim", None)
        if channel_dim is not None:
            entry["channelDim"] = int(channel_dim)
    return entry


def _prediction_type_entry(item):
    entry = {"name": str(item.name)}
    if getattr(item, "labels", None):
        entry["labels"] = [str(v) for v in item.labels]
    channel_dim = getattr(item, "channel_dim", None)
    if channel_dim is not None:
        entry["channelDim"] = int(channel_dim)
    return entry


def main():
    # Absolutize: code_loader's LeapLoader path walker infinite-recurses on a
    # relative root like ".", so always resolve to an absolute path.
    repo_root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    entry_name = sys.argv[2] if len(sys.argv) > 2 else "leap_integration.py"

    too_old = _code_loader_too_old()
    if too_old:
        print(json.dumps({"available": False, "error": too_old}, indent=2))
        return 2

    # Before code_loader and the integration are imported, so libraries that read
    # HOME at import time see the empty one.
    empty_home = None
    if os.environ.get("TL_CHECK_EMPTY_HOME", "1") != "0":
        empty_home = _EmptyHome()
        empty_home.enter()

    try:
        from code_loader.leaploader import LeapLoader
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({
            "available": False,
            "error": "could not import code_loader.leaploader.LeapLoader; "
                     "run inside the project's env (e.g. poetry run python …): %s" % exc,
        }, indent=2))
        return 2

    loader = LeapLoader(repo_root, entry_name)
    if empty_home is not None:
        # exec_script() imports the integration and runs preprocess once (cached), so
        # what it writes is the per-push preprocess cost; check_dataset() then runs the
        # encoders on one sample per split. A failure here is reported by check_dataset().
        try:
            loader.exec_script()
        except Exception:  # noqa: BLE001
            pass
        if getattr(loader, "_preprocess_result_cached", None) is not None:
            empty_home.preprocess_bytes = empty_home.written_bytes()
        empty_home.phase = "encoders and metadata (one sample per split)"
    result = loader.check_dataset()

    payload = {
        "available": True,
        "entryFile": entry_name,
        "isValid": bool(result.is_valid),
        "hasCustomLayers": bool(getattr(result, "is_valid_for_model", False)),
        "generalError": result.general_error or "",
        "printLog": result.print_log or "",
        "payloads": [_payload_entry(p) for p in (result.payloads or [])],
    }

    setup = getattr(result, "setup", None)
    if setup is not None:
        pp = setup.preprocess
        payload["setup"] = {
            "preprocess": {
                "trainingLength": int(pp.training_length),
                "validationLength": int(pp.validation_length),
                "testLength": int(pp.test_length or 0),
                "unlabeledLength": int(pp.unlabeled_length or 0),
            },
            "inputs": [_shape_entry(i, include_channel_dim=True) for i in (setup.inputs or [])],
            "outputs": [_shape_entry(o) for o in (setup.outputs or [])],
            "predictionTypes": [_prediction_type_entry(p) for p in (setup.prediction_types or [])],
        }

    if empty_home is not None:
        first_sample_bytes = empty_home.written_bytes()
        second_sample = None
        if result.is_valid and empty_home.preprocess_bytes is not None:
            empty_home.phase = "encoders and metadata (second sample per split)"
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    probed = _run_second_sample(loader)
                second_sample = (empty_home.written_bytes() - first_sample_bytes, probed)
            except Exception:  # noqa: BLE001
                second_sample = None
        payload["dataAccess"] = empty_home.report(setup, first_sample_bytes, second_sample)
        if not result.is_valid:
            payload["dataAccess"]["hint"] = (
                "This check ran with an empty home folder, like the server job. If the failure is a "
                "missing file under ~ or in a home-folder cache, the job will fail the same way: put "
                "the data in the data volume and read it from the path in project_config.yaml. "
                "TL_CHECK_EMPTY_HOME=0 reruns with your real home folder.")

    print(json.dumps(payload, indent=2))

    failed = [p["name"] for p in payload["payloads"] if not p["passed"]]
    if not payload["isValid"] or failed:
        if failed:
            print("FAILED handlers: %s" % ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
