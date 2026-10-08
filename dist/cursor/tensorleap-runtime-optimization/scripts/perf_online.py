"""perf_online — online diagnostics for the tensorleap-runtime-optimization skill.

Reached through `tl_perf.py online <action>`; never run directly. Reads ONE real
Tensorleap run through the public `leap` CLI only, and turns it into evidence:

  collect   follow a run while it executes: poll `leap run info` (progress) and
            `leap run logs` (each worker's most recent log lines), merge them, keep them
  analyze   find the warm-up / stable / tail periods per phase, compute stable-window
            statistics per component, attribute the bottleneck, compare with offline

Stdlib only, Python 3.8+. Outputs go under <out>/online/.

Exit codes (in addition to tl_perf's):
  0 ok            2 blocker (missing input, no `leap` CLI, run not found)
  20 still running: the run has not finished — run the same command again to continue
"""

import datetime
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time

EXIT_OK = 0
EXIT_BLOCKER = 2
EXIT_STILL_RUNNING = 20
EXIT_STEADY_VISUALIZATION = 21        # the visualization's pace is steady: ask the user to stop the run
# When the visualization has given the diagnostics what they need: a steady pace over enough samples
# (a fifth of the visualization, at least 200 for percentiles, at most 1,000), and stopping still
# saves a real part of it. A short or nearly finished visualization runs to the end.
STEADY_VISUALIZATION_MIN_SAMPLES = 200
STEADY_VISUALIZATION_MAX_SAMPLES = 1000
STEADY_VISUALIZATION_SHARE = 0.2
STOP_MIN_REMAINING_SHARE = 0.25
STOP_MIN_SAVING_SECONDS = 300.0

MODES = ("smoke", "diagnostics")
ONLINE_DIR = "online"

DEFAULT_POLL_SECONDS = 30.0
DEFAULT_WINDOW_SECONDS = 60.0
DEFAULT_STABLE_WINDOWS = 3
DEFAULT_TOLERANCE = 0.10


# --------------------------------------------------------------------------- #
# report.json: server_validation
# --------------------------------------------------------------------------- #

def validate_server_validation(sv):
    """Errors for report.json's `server_validation` (absent = no server validation)."""
    if sv is None:
        return []
    if not isinstance(sv, dict):
        return ["'server_validation' must be an object"]
    errors = []
    mode = sv.get("mode")
    if mode not in MODES:
        errors.append("server_validation.mode must be one of %s" % ", ".join(MODES))
    authorized = sv.get("authorized_by_user")
    if not isinstance(authorized, bool):
        errors.append("server_validation.authorized_by_user must be true or false")
    elif mode == "diagnostics" and not authorized:
        errors.append("server_validation.mode 'diagnostics' requires authorized_by_user: true "
                      "(an unattended or unconfirmed run uses mode 'smoke')")
    return errors


def render_server_validation(sv, out):
    """The '## Server validation' section lines."""
    mode = sv.get("mode")
    label = {"smoke": "smoke validation (small subset)",
             "diagnostics": "online diagnostics (authorized by the user)"}.get(mode, mode or "unknown")
    lines = ["## Server validation", "",
             "- Mode: **%s**" % label,
             "- Status: **%s**" % sv.get("status", "unknown")]
    for k in ("job", "duration", "notes"):
        if sv.get(k):
            lines.append("- %s: %s" % (k.capitalize(), sv[k]))
    lines.append("")
    if mode == "diagnostics":
        lines += render_online_section(sv, out)
    return lines


# --------------------------------------------------------------------------- #
# the offline profile of the code being pushed
# --------------------------------------------------------------------------- #

def _pushed_profile(out, tl):
    """The profile of the code being pushed: the last accepted run, else the latest run,
    else the baseline."""
    accepted = tl._read_json(os.path.join(out, "accepted.json")) or {}
    for run_dir in (accepted.get("run"), tl._latest_run(out), os.path.join(out, "baseline")):
        if run_dir:
            p = tl._read_json(os.path.join(run_dir, "profile.json"))
            if p:
                return p, run_dir
    return None, None


def _ceil(x):
    return int(math.ceil(x - 1e-9))


# --------------------------------------------------------------------------- #
# stability: warm-up / stable window / tail of one phase
# --------------------------------------------------------------------------- #

def _interp(points, t):
    """Progress at time t, linear between observations (points sorted by time)."""
    if t <= points[0][0]:
        return points[0][1]
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t <= t1:
            return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return points[-1][1]


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def phase_windows(points, pods=None, window=DEFAULT_WINDOW_SECONDS):
    """Cut one phase's progress [(t, done), ...] into fixed windows from its first progress
    to its last. Each window: rows/s, and the worker situation seen during it
    (pods = [(t, ready, pending), ...] from the polls)."""
    pts = sorted((float(t), float(v)) for t, v in points)
    moving = [i for i in range(1, len(pts)) if pts[i][1] > pts[i - 1][1]]
    if not moving:
        return []
    start = pts[moving[0] - 1][0]
    end = pts[moving[-1]][0]
    out, t = [], start
    while t < end - 1e-9:
        t1 = min(t + window, end)
        rate = (_interp(pts, t1) - _interp(pts, t)) / (t1 - t) if t1 > t else 0.0
        seen = [(r, pe) for (tp, r, pe) in (pods or []) if t - 1e-9 <= tp <= t1 + 1e-9]
        out.append({"start": t, "end": t1, "rate": rate,
                    "workers": max(r for r, _ in seen) if seen else None,
                    "pending": any(pe for _, pe in seen) if seen else None})
        t = t1
    return out


def detect_stability(points, pods=None, window=DEFAULT_WINDOW_SECONDS, k=DEFAULT_STABLE_WINDOWS,
                     tolerance=DEFAULT_TOLERANCE):
    """The stable window of one phase: the longest run of >= k consecutive full windows in
    which no worker is pending, the worker count does not change, every window's rate is
    within +-tolerance of the run's median, and the pace is not still rising — taken on the
    highest plateau (an early plateau before more workers arrive is warm-up, not steady
    state). Never assumed: if no such run exists the phase is reported as not stable."""
    wins = phase_windows(points, pods, window)
    full = [w for w in wins if w["end"] - w["start"] >= 0.999 * window]
    res = {"window_seconds": window, "k": k, "tolerance": tolerance, "windows": wins, "reached": False}
    if not wins:
        res["reason"] = "no progress observed"
        return res
    res["phase"] = {"start": wins[0]["start"], "end": wins[-1]["end"]}
    if len(full) < k:
        res["reason"] = "phase shorter than %d windows of %gs" % (k, window)
        return res

    def ok_pods(a, b):
        return not a["pending"] and (a["workers"] is None or b["workers"] is None or a["workers"] == b["workers"])

    def rise(seg):
        """Least-squares change of the rate across seg, relative to its median."""
        n = len(seg)
        xs = range(n)
        ys = [w["rate"] for w in seg]
        mx, my = (n - 1) / 2.0, sum(ys) / n
        den = sum((x - mx) ** 2 for x in xs) or 1.0
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        med = _median(ys)
        return slope * (n - 1) / med if med > 0 else float("inf")

    runs = []
    i = 0
    while i <= len(full) - k:
        seg = full[i:i + k]
        med = _median([w["rate"] for w in seg])
        steady = med > 0 and all(abs(w["rate"] - med) <= tolerance * med for w in seg) and \
            all(ok_pods(seg[j], seg[j - 1]) for j in range(1, k)) and not seg[0]["pending"]
        climbing = all(b["rate"] > a["rate"] for a, b in zip(seg, seg[1:])) and \
            seg[-1]["rate"] - seg[0]["rate"] > tolerance * med
        if not steady or climbing:
            i += 1
            continue
        j = i + k
        while j < len(full):
            m = _median([w["rate"] for w in full[i:j + 1]])
            if abs(full[j]["rate"] - m) > tolerance * m or not ok_pods(full[j], full[j - 1]):
                break
            j += 1
        a = i
        max_rise = 0.5 * tolerance                           # a slow climb is still warm-up
        while j - a > k and rise(full[a:j]) > max_rise:
            a += 1
        if rise(full[a:j]) <= max_rise:
            runs.append((a, j, _median([w["rate"] for w in full[a:j]])))
        i = j
    if not runs:
        rates = [w["rate"] for w in full]
        res["reason"] = ("the pace never held within +-%d%% for %d windows (rows/s from %.2f to %.2f)"
                         % (round(100 * tolerance), k, min(rates), max(rates)))
        return res
    top = max(r[2] for r in runs)
    plateau = [r for r in runs if r[2] >= (1.0 - tolerance) * top]
    i, j, med = max(plateau, key=lambda r: (r[1] - r[0], r[2]))
    res["plateaus"] = [{"start": full[a]["start"], "end": full[b - 1]["end"], "rate_median": m,
                        "seconds": full[b - 1]["end"] - full[a]["start"]} for a, b, m in runs]
    st, en = full[i]["start"], full[j - 1]["end"]
    samples = _interp(sorted(points), en) - _interp(sorted(points), st)
    res.update({
        "reached": True,
        "warmup": {"start": res["phase"]["start"], "end": st, "seconds": st - res["phase"]["start"]},
        "stable": {"start": st, "end": en, "seconds": en - st, "rows": samples, "rate_median": med,
                   "windows": j - i, "workers": full[i]["workers"],
                   "share_of_phase": (en - st) / max(res["phase"]["end"] - res["phase"]["start"], 1e-9)},
        "tail": {"start": en, "end": res["phase"]["end"], "seconds": res["phase"]["end"] - en},
    })
    return res


# --------------------------------------------------------------------------- #
# collect: follow one run through the public `leap` CLI and keep its logs
# --------------------------------------------------------------------------- #
#
# `leap run logs <id>` returns each pod's most recent lines (a fixed tail per container)
# plus a `kubectl describe` of the pod: the live pods while the run is going, an end-time
# snapshot after it. Workers removed during the run are in neither. So the run is polled:
# every poll's tail is merged onto what was kept (anchored on the last kept lines); no
# anchor = lines were lost between polls = a recorded gap.

TERMINAL = ("FINISHED", "FAILED", "STOPPED", "TERMINATED")
TAIL_LINES = 2000                       # the per-container tail the CLI returns today
NO_EVALUATION_SECONDS = 600             # a finished push with no evaluation after this long
ANCHOR_LINES = 8
IDENTITY_PREFIXES = ("user.", "team.")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
ROLE_RE = re.compile(r"^(evaluate|generic-process|streaming-handler|redis)-")


def leap_cmd():
    return os.environ.get("TL_LEAP", "leap")


def _leap(args, timeout=900):
    try:
        proc = subprocess.run([leap_cmd()] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def run_info(job):
    rc, out, _ = _leap(["run", "info", job, "-o", "json"], timeout=120)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


ACTIVE_STATUSES_RE = re.compile(r"\b(PENDING|INITIALIZING|STARTED|RUNNING|QUEUED|IN_PROGRESS)\b")


def other_jobs_in_flight(own):
    """Other runs in flight on the same server (they share its CPU / GPU and storage)."""
    rc, out, _ = _leap(["run", "list", "-s", "PENDING,INITIALIZING,STARTED,QUEUED"], timeout=120)
    if rc != 0:
        return None
    found = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and ACTIVE_STATUSES_RE.search(line) and parts[-1] not in own:
            found.append({"type": parts[0], "status": next((p for p in parts if ACTIVE_STATUSES_RE.match(p)), None),
                          "job": parts[-1]})
    return found


def read_logs_tar(path):
    """{name: text} from a `leap run logs --output` tar (members may have mode 0)."""
    found = {}
    with tarfile.open(path) as tar:
        for m in tar.getmembers():
            if m.isfile():
                fh = tar.extractfile(m)
                found[os.path.basename(m.name)] = fh.read().decode("utf-8", "replace") if fh else ""
    return found


def fetch_logs(job, scratch):
    base = os.path.join(scratch, "fetch")
    for p in (base, base + ".tar"):
        if os.path.exists(p):
            os.remove(p)
    rc, _, err = _leap(["run", "logs", job, "--output", base])
    tar_path = base + ".tar" if os.path.exists(base + ".tar") else base
    if rc != 0 or not os.path.exists(tar_path):
        return None, (err.strip() or "leap run logs failed")[-300:]
    try:
        return read_logs_tar(tar_path), None
    finally:
        os.remove(tar_path)


def pod_role(name):
    m = ROLE_RE.match(name)
    return m.group(1) if m else "other"


def redact_line(line):
    """Drop identity fields from a JSON log line; mask e-mail addresses anywhere."""
    s = line.rstrip("\n")
    if s.startswith("{"):
        try:
            d = json.loads(s)
        except ValueError:
            d = None
        if isinstance(d, dict):
            d = {k: v for k, v in d.items() if not k.startswith(IDENTITY_PREFIXES)}
            s = json.dumps(d, sort_keys=False)
    return EMAIL_RE.sub("<email>", s)


def redact_describe(text):
    """`kubectl describe pod` without its environment blocks (settings are not evidence the
    report may quote) and without e-mail addresses."""
    out, skip = [], None
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip(" "))
        if skip is not None:
            if line.strip() and indent <= skip:
                skip = None
            else:
                continue
        if line.strip().startswith(("Environment:", "Environment Variables from:")):
            skip = indent
            out.append(line.split(":")[0] + ": <removed>")
            continue
        out.append(line)
    return EMAIL_RE.sub("<email>", "\n".join(out))


def _k8s_time(s):
    try:
        return datetime.datetime.strptime(s.strip(), "%a, %d %b %Y %H:%M:%S %z").timestamp()
    except ValueError:
        return None


def parse_describe(text):
    """Pod facts from a `kubectl describe pod` text (main container section)."""
    d = {"status": None, "start": None, "ready": None, "state": None, "waiting_reason": None,
         "restarts": 0, "last_state_reason": None, "limit_memory": None, "request_memory": None,
         "oom": "OOMKilled" in text}
    m = re.search(r"^Status:\s+(\S+)", text, re.M)
    d["status"] = m.group(1) if m else None
    m = re.search(r"^Start Time:\s+(.+)$", text, re.M)
    d["start"] = _k8s_time(m.group(1)) if m else None
    body = text.split("\nContainers:", 1)[1] if "\nContainers:" in text else text
    body = body.split("\nConditions:", 1)[0]
    for key, rx in (("state", r"^\s+State:\s+(\S+)"), ("waiting_reason", r"^\s+State:\s+Waiting\n\s+Reason:\s+(\S+)"),
                    ("last_state_reason", r"^\s+Last State:\s+\S+\n\s+Reason:\s+(\S+)"),
                    ("limit_memory", r"Limits:\n(?:\s+\S+:.*\n)*?\s+memory:\s+(\S+)"),
                    ("request_memory", r"Requests:\n(?:\s+\S+:.*\n)*?\s+memory:\s+(\S+)")):
        m = re.search(rx, body, re.M)
        d[key] = m.group(1) if m else None
    m = re.search(r"Restart Count:\s+(\d+)", body)
    d["restarts"] = int(m.group(1)) if m else 0
    m = re.search(r"^\s+Image:\s+\S*/engine(?:-generic)?:(\d+\.\d+\.\d+)", body, re.M)
    d["engine_version"] = m.group(1) if m else None
    m = re.search(r"Limits:\n(?:\s+\S+:.*\n)*?\s+nvidia\.com/gpu:\s+(\d+)", body)
    d["gpus"] = int(m.group(1)) if m else 0
    m = re.search(r"^\s+Ready:\s+(\S+)", body, re.M)
    d["ready"] = (m.group(1) == "True") if m else None
    return d


def merge_tail(kept, new, anchor=ANCHOR_LINES):
    """Lines of `new` (a fresh tail) not yet in `kept`, and whether lines were lost between
    the two fetches (no overlap). The anchor is the last kept lines, searched anywhere in
    `new` (a pod's log may start with another container's lines)."""
    new = [l for l in new if l.strip()]
    if not kept:
        return new, False
    for size in (anchor, 3, 1):
        a = kept[-size:] if len(kept) >= size else kept
        n = len(a)
        for q in range(len(new) - n, -1, -1):
            if new[q:q + n] == a:
                return new[q + n:], False
    return new, True


class Collection(object):
    """On-disk state of one followed run: online/<job>/{logs,describe}/, polls.jsonl,
    collect.json. Every call resumes from what is there."""

    def __init__(self, out, job):
        self.dir = os.path.join(online_dir(out), job)
        self.logs = os.path.join(self.dir, "logs")
        self.desc = os.path.join(self.dir, "describe")
        for p in (self.logs, self.desc):
            if not os.path.isdir(p):
                os.makedirs(p)
        self.state_path = os.path.join(self.dir, "collect.json")
        self.state = _load_json(self.state_path) or {
            "job": job, "evaluate_job": None, "push_job": None, "status": None, "polls": 0,
            "first_poll": None, "last_poll": None, "terminal": False, "final_fetches": 0,
            "pods": {}, "gaps": [], "source": "poll"}

    def save(self):
        _write_json(self.state_path, self.state)

    def kept(self, pod):
        p = os.path.join(self.logs, pod + ".log")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as fh:
            return fh.read().splitlines()

    def ingest(self, files, t):
        """Merge one fetch. Returns {pod: describe facts} seen in it."""
        seen = {}
        for name, text in sorted(files.items()):
            if name.startswith("describe-"):
                pod = name[len("describe-"):]
                facts = parse_describe(text)
                facts["role"] = pod_role(pod)
                seen[pod] = facts
                with open(os.path.join(self.desc, pod + ".txt"), "w", encoding="utf-8") as fh:
                    fh.write(redact_describe(text))
                continue
            pod = name
            raw = text.splitlines()
            lines = [redact_line(l) for l in raw]
            kept = self.kept(pod)
            add, gap = merge_tail(kept, lines)
            info = self.state["pods"].setdefault(pod, {
                "role": pod_role(pod), "lines": 0, "gaps": 0, "first_fetch": t,
                "complete_from_start": len([l for l in raw if l.strip()]) < TAIL_LINES})
            if gap:
                info["gaps"] += 1
                self.state["gaps"].append({"pod": pod, "at": t})
            if add:
                with open(os.path.join(self.logs, pod + ".log"), "a", encoding="utf-8") as fh:
                    fh.write("\n".join(add) + "\n")
                info["lines"] += len(add)
            info["last_fetch"] = t
        for pod, facts in seen.items():
            self.state["pods"].setdefault(pod, {"role": facts["role"], "lines": 0, "gaps": 0,
                                                "first_fetch": t, "complete_from_start": True})
            self.state["pods"][pod]["describe"] = facts
        return seen

    def record_poll(self, t, info, ev_info, seen, others=None):
        steps = [{"id": s.get("id"), "status": s.get("status"), "current": s.get("current"),
                  "total": s.get("total")} for s in ((ev_info or {}).get("steps") or [])]
        line = {"t": t, "status": (info or {}).get("status"), "evaluate_status": (ev_info or {}).get("status"),
                "steps": steps,
                "pods": [{"pod": p, "role": f["role"], "status": f["status"], "state": f["state"],
                          "waiting_reason": f["waiting_reason"], "ready": f["ready"],
                          "restarts": f["restarts"], "oom": f["oom"]} for p, f in sorted(seen.items())],
                "other_jobs": others}
        with open(os.path.join(self.dir, "polls.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
        self.state["polls"] += 1
        self.state["first_poll"] = self.state["first_poll"] or t
        self.state["last_poll"] = t


def _load_json(path):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)
        fh.write("\n")
    os.replace(tmp, path)


def evaluation_for_version(version_id, after):
    """The evaluation the push client created itself (no server-side chaining): the newest
    Evaluate run of the same version created after `after` (ISO time)."""
    rc, out, _ = _leap(["run", "list", "-t", "Evaluate"], timeout=120)
    if rc != 0 or not version_id:
        return None
    for line in out.splitlines()[:8]:
        job = line.split()[-1] if line.split() else ""
        if not re.match(r"^[0-9a-f]{24}$", job):
            continue
        info = run_info(job) or {}
        if info.get("versionId") == version_id and (info.get("createdAt") or "") >= (after or ""):
            return job
    return None


def _resolve(job, info):
    """(evaluate run id or None, push status) for a push or an evaluation run id."""
    if not info:
        return None, None
    if (info.get("subType") or info.get("type") or "").lower() in ("evaluate", "training"):
        return job, None
    chained = info.get("chainedEvaluate") or {}
    return chained.get("jobId"), info.get("status")


def cmd_collect(args, out, tl):
    if args.from_tar:
        return _collect_from_tar(args, out)
    if not args.job:
        print("tl_perf online collect: --job <push or evaluation run id> is required", file=sys.stderr)
        return EXIT_BLOCKER
    if shutil.which(leap_cmd()) is None and not os.path.exists(leap_cmd()):
        print("tl_perf online collect: the `leap` CLI is not on PATH", file=sys.stderr)
        return EXIT_BLOCKER
    col = Collection(out, args.job)
    if not _take_lock(col.dir):
        print("tl_perf online collect: another collect is already following %s — run only one at a time"
              % args.job, file=sys.stderr)
        return EXIT_BLOCKER
    try:
        return _collect_loop(args, out, col)
    finally:
        _release_lock(col.dir)


def _lock_path(d):
    return os.path.join(d, ".collect.lock")


def _take_lock(d):
    path = _lock_path(d)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except OSError:
            try:
                with open(path) as fh:
                    pid = int(fh.read().strip() or 0)
                os.kill(pid, 0)                 # the holder is alive
                return False
            except (ValueError, OSError):
                try:
                    os.remove(path)             # a stale lock from a process that is gone
                except OSError:
                    return False
    return False


def _release_lock(d):
    try:
        os.remove(_lock_path(d))
    except OSError:
        pass


def steady_samples_needed(total):
    """Visualized samples the steady window must hold before the run may be stopped."""
    return max(STEADY_VISUALIZATION_MIN_SAMPLES,
               min(STEADY_VISUALIZATION_MAX_SAMPLES, _ceil(STEADY_VISUALIZATION_SHARE * total)))


def _progress_at(pts, t):
    """Samples done at time t, linearly between the polls around it."""
    before = [p for p in pts if p[0] <= t]
    after = [p for p in pts if p[0] >= t]
    if not before:
        return after[0][1] if after else 0.0
    if not after:
        return before[-1][1]
    (t0, v0), (t1, v1) = before[-1], after[0]
    return v0 if t1 <= t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)


def visualization_steady(polls):
    """The visualization's pace once the diagnostics have what they need from it: a steady pace
    whose window holds steady_samples_needed() samples, while stopping would still save at least
    STOP_MIN_REMAINING_SHARE of the samples and STOP_MIN_SAVING_SECONDS. Else None."""
    pts, total = [], None
    for p in polls:
        for st_ in p.get("steps") or []:
            if st_.get("id") == STEP_VISUALIZATION and st_.get("current") is not None:
                pts.append((p["t"], float(st_["current"])))
                total = st_.get("total") or total
    if len([1 for _, v in pts if v > 0]) < 3 or not total:
        return None
    st = detect_stability(pts, _pod_series(polls))
    if not st.get("reached"):
        return None
    total = int(total)
    steady_samples = _progress_at(pts, st["stable"]["end"]) - _progress_at(pts, st["stable"]["start"])
    needed = steady_samples_needed(total)
    if steady_samples < needed:
        return None                      # steady, but not over enough samples yet
    done, rate = int(pts[-1][1]), st["stable"]["rate_median"]
    remaining = total - done
    if remaining < STOP_MIN_REMAINING_SHARE * total or not rate or remaining / rate < STOP_MIN_SAVING_SECONDS:
        return None                      # little left: let it finish
    return {"at": pts[-1][0], "rate": rate, "done": done, "total": total,
            "steady_samples": int(steady_samples), "samples_needed": needed,
            "saving_seconds": remaining / rate,
            "warmup_seconds": st["warmup"]["seconds"], "stable_seconds": st["stable"]["seconds"]}


def _collect_loop(args, out, col):
    scratch = os.path.join(col.dir, "tmp")
    if not os.path.isdir(scratch):
        os.makedirs(scratch)
    began = time.time()
    misses = 0
    while True:
        t = time.time()
        info = run_info(args.job)
        if info is None:
            misses += 1
            if misses >= 3 and not col.state["polls"]:
                print("tl_perf online collect: run %s not found (leap run info failed)" % args.job,
                      file=sys.stderr)
                return EXIT_BLOCKER
        ev_id, push_status = _resolve(args.job, info)
        if not ev_id and push_status == "FINISHED":
            ev_id = col.state.get("evaluate_job") or evaluation_for_version((info or {}).get("versionId"),
                                                                            (info or {}).get("createdAt"))
        if ev_id and ev_id != args.job:
            col.state["push_job"], col.state["evaluate_job"] = args.job, ev_id
        elif ev_id:
            col.state["evaluate_job"] = ev_id
        if not ev_id and push_status == "FINISHED":
            col.state["polls_without_evaluation"] = col.state.get("polls_without_evaluation", 0) + 1
            if col.state["polls_without_evaluation"] * args.interval >= NO_EVALUATION_SECONDS:
                col.state["status"] = "PUSH FINISHED, NO EVALUATION"
                col.save()
                print("tl_perf online collect: push %s finished but no evaluation of its version appeared in %d s; "
                      "the push client may have stopped before creating it — re-push over the same version "
                      "(Phase 6A step 8)" % (args.job, NO_EVALUATION_SECONDS), file=sys.stderr)
                return EXIT_BLOCKER
        ev_info = run_info(ev_id) if ev_id and ev_id != args.job else (info if ev_id else None)
        seen = {}
        if ev_id:
            files, err = fetch_logs(ev_id, scratch)
            if files is None:
                col.state.setdefault("fetch_errors", []).append({"at": t, "error": err})
            else:
                seen = col.ingest(files, t)
        others = other_jobs_in_flight({args.job, ev_id})
        col.record_poll(t, info, ev_info, seen, others)
        ev_status = (ev_info or {}).get("status")
        col.state["status"] = ev_status or push_status
        for st_ in (ev_info or {}).get("steps") or []:
            if st_.get("id") == STEP_EVALUATION and st_.get("total"):
                col.state["rows_evaluated"] = st_["total"]
        for src in (ev_info, info):
            rep_ = (src or {}).get("errorReport")
            if rep_ and (src or {}).get("status") in TERMINAL:
                notes_ = ((rep_.get("notifications") or {}).get("notifications") or [])
                col.state["error_report"] = {
                    "job": src.get("jobId"), "status": src.get("status"),
                    "messages": [EMAIL_RE.sub("<email>", ("%s: %s %s" % (n.get("title", ""), n.get("message", ""),
                                                                        n.get("extraMessage", ""))).strip())[:300]
                                 for n in notes_][:5],
                    "levels": [n.get("level") for n in notes_][:5]}
        push_failed = push_status in TERMINAL and not ev_id and push_status != "FINISHED"
        if ev_status in TERMINAL or push_failed:
            col.state["terminal"] = True
            if ev_id and col.state["final_fetches"] < 2:   # the end-time snapshot can lag the status
                col.state["final_fetches"] += 1
                col.save()
                time.sleep(min(args.interval, 10.0))
                continue
            break
        col.save()
        if not getattr(args, "full_visualization", False) and not col.state.get("visualization_steady"):
            steady = visualization_steady(_polls(col.dir))
            if steady:
                # Diagnostics need the visualization only until its pace is steady; the rest repeats
                # the same measurements. The CLI cannot stop a run, so the user stops it.
                col.state["visualization_steady"] = steady
                col.save()
                print("tl_perf online collect: the visualization has what the diagnostics need — a steady %.2f "
                      "samples/s over %d samples (%d needed); %d of %d done, stopping now saves about %.0f min. "
                      "Ask the user to stop run %s in the Tensorleap UI now, then run the same command again: it "
                      "reads the last logs and ends when the run has stopped."
                      % (steady["rate"], steady["steady_samples"], steady["samples_needed"], steady["done"],
                         steady["total"], steady["saving_seconds"] / 60.0, ev_id or args.job))
                return EXIT_STEADY_VISUALIZATION
        if args.for_seconds is not None and time.time() - began + args.interval > args.for_seconds:
            print("tl_perf online collect: %s still %s after %d polls; run the same command again"
                  % (ev_id or args.job, col.state["status"] or "starting", col.state["polls"]))
            return EXIT_STILL_RUNNING
        time.sleep(max(0.0, args.interval - (time.time() - t)))
    col.save()
    shutil.rmtree(scratch, ignore_errors=True)
    tables = parse_collection(col.dir)
    _write_json(os.path.join(col.dir, "components.json"), tables)
    _print_collect(col, tables)
    return EXIT_OK


def _collect_from_tar(args, out):
    files = read_logs_tar(args.from_tar)
    job = args.job or os.path.splitext(os.path.basename(args.from_tar))[0].split(".")[0]
    col = Collection(out, job)
    polls = os.path.join(col.dir, "polls.jsonl")
    if os.path.exists(polls):                 # a re-import replaces the previous one
        os.remove(polls)
    col.state.update({"polls": 0, "first_poll": None, "last_poll": None})
    t = os.path.getmtime(args.from_tar)
    seen = col.ingest(files, t)
    col.record_poll(t, None, None, seen)
    col.state.update({"source": "tar", "terminal": True, "evaluate_job": job,
                      "note": "imported after the run: only each pod's last lines survive"})
    col.save()
    tables = parse_collection(col.dir)
    _write_json(os.path.join(col.dir, "components.json"), tables)
    _print_collect(col, tables)
    return EXIT_OK


def _print_collect(col, tables):
    st = col.state
    print("Online collection %s: %s, %d polls, %d pods, %d gaps -> %s" % (
        st.get("evaluate_job") or st["job"], st.get("status") or "imported", st["polls"],
        len(st["pods"]), len(st["gaps"]), col.dir))
    for w in tables.get("coverage", {}).get("warnings", []):
        print("  [WARN] %s" % w)


# --------------------------------------------------------------------------- #
# parse: one table per component from the kept logs
# --------------------------------------------------------------------------- #

def log_time(asctime):
    """The engine logs `YYYY-MM-DD HH:MM:SS,mmm` in UTC."""
    try:
        dt = datetime.datetime.strptime(asctime, "%Y-%m-%d %H:%M:%S,%f")
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=datetime.timezone.utc).timestamp()


PERCENTILE_KEYS = ("p50_seconds", "p90_seconds", "p95_seconds", "p99_seconds")
SERIES = (
    ("samples generator: generate single sample", "generation_per_sample",
     ("duration_seconds", "get_sample_secs", "queue_pop_secs", "redis_push_secs")),
    ("metrics_runner: process metrics batch end-to-end", "metrics_batch",
     ("duration_seconds", "metric_calc_secs", "wait_secs", "per_sample_secs")),
    ("vis calculator: process vis batch end-to-end", "visualizers_batch",
     ("duration_seconds", "per_sample_secs")),
    ("metrics_runner: starvation ended", "metrics_idle", ("duration_seconds",)),
    ("vis_calculator: starvation ended", "visualizers_idle", ("duration_seconds",)),
)
MARKERS = (
    ("eval generator path", "eval_state_start"),
    ("Starting post processing evaluation", "post_processing_start"),
    ("trainer.post_processing completed", "post_processing_end"),
    ("visualization.calculate_and_upload_visualizers completed", "visualization_end"),
    ("engine.job completed", "job_end"),
)


PARSER_VERSION = 11      # components.json written by an older parser is re-parsed


def parse_collection(job_dir):
    """Component tables from online/<job>/: spans, per-call series, memory, utilization,
    configuration, phase markers, pods, and a coverage summary."""
    logs_dir = os.path.join(job_dir, "logs")
    state = _load_json(os.path.join(job_dir, "collect.json")) or {}
    spans, series, memory, gpu, markers, events = [], {}, [], [], [], []
    config = {"children_per_pod": {}}
    ws1 = False
    per_pod = {}
    for fname in sorted(os.listdir(logs_dir)) if os.path.isdir(logs_dir) else []:
        pod = fname[:-4] if fname.endswith(".log") else fname
        role = pod_role(pod)
        first = last = None
        throttled = 0
        seen_lines = set()
        with open(os.path.join(logs_dir, fname), encoding="utf-8") as fh:
            for line in fh:
                if not line.startswith("{"):
                    continue
                if line in seen_lines:          # an exact repeat (millisecond timestamps): a collection artifact
                    continue
                seen_lines.add(line)
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                ts = log_time(d.get("asctime"))
                if ts is None:
                    continue
                first = ts if first is None else first
                last = ts
                msg = d.get("message") or ""
                if "throttle_interval_sec" in d:
                    throttled += 1
                if "span_name" in d:
                    agg = msg.endswith(".aggregate")
                    row = {"t": ts, "pod": pod, "role": role, "span": d["span_name"],
                           "aggregate": agg, "n": d.get("n", 1),
                           "sum": d.get("sum_seconds", d.get("duration_seconds")),
                           "mean": d.get("duration_seconds"),
                           "min": d.get("min_seconds", d.get("duration_seconds")),
                           "max": d.get("max_seconds", d.get("duration_seconds")),
                           "window": d.get("window_seconds"), "span_kind": d.get("span_kind"),
                           "parent": d.get("span_parent"),
                           "sampled": "throttle_interval_sec" in d}
                    for k in PERCENTILE_KEYS:
                        if k in d:
                            row[k[:3]] = d[k]
                            ws1 = True
                    if d.get("span_kind"):
                        ws1 = True
                    spans.append(row)
                for prefix, key, fields in SERIES:
                    if msg.startswith(prefix):
                        rec = {"t": ts, "pod": pod, "sampled": "throttle_interval_sec" in d}
                        rec.update({f: d[f] for f in fields if f in d})
                        if "state" in d:
                            rec["state"] = d["state"]
                        series.setdefault(key, []).append(rec)
                if "rss_gb" in d or "peak_rss_gb" in d or "rss_after_trim_gb" in d:
                    memory.append({"t": ts, "pod": pod, "role": role,
                                   "rss_gb": d.get("rss_gb", d.get("current_rss_gb", d.get("rss_after_trim_gb"))),
                                   "peak_rss_gb": d.get("peak_rss_gb"),
                                   "label": d.get("step") or d.get("stage") or d.get("label") or msg[:60]})
                if "gpu_util_proxy" in d:
                    gpu.append({"t": ts, "state": d.get("data_state"), "proxy": d["gpu_util_proxy"],
                                "infer_seconds": d.get("infer_seconds"), "loop_wall_seconds": d.get("loop_wall_seconds")})
                if "children_per_pod" in d:
                    config["children_per_pod"][pod] = d["children_per_pod"]
                if msg.startswith("Computed redis queue caps") and "batch_size" in d:
                    config["batch_size"] = d["batch_size"]
                if msg.startswith("eval generator path"):
                    config.update({k: d[k] for k in ("grouped", "minibatch_size", "eval_batch_reuse") if k in d})
                if msg.startswith("dataset ready-sample payload breakdown"):
                    config["payload_format"] = d.get("format")
                if "chose aggregate_every" in msg:
                    config["generation_aggregate_every"] = d.get("aggregate_every")
                    config["total_samples"] = d.get("total_samples")
                if "died; re-forking" in msg or "failing pod" in msg or "based on current memory" in msg:
                    events.append({"t": ts, "pod": pod, "message": msg[:160]})
                for prefix, name in MARKERS:
                    if msg.startswith(prefix):
                        mk = {"t": ts, "name": name, "pod": pod}
                        if "duration_seconds" in d:
                            mk["duration_seconds"] = d["duration_seconds"]
                        markers.append(mk)
        per_pod[pod] = {"role": role, "first": first, "last": last, "throttled_lines": throttled}
    pods = {}
    for pod, info in (state.get("pods") or {}).items():
        merged = dict(info)
        merged.update(per_pod.get(pod, {}))
        pods[pod] = merged
    for pod, info in per_pod.items():
        pods.setdefault(pod, info)
    coverage = _coverage(state, pods)
    return {"parser_version": PARSER_VERSION, "engine": parse_engine(logs_dir),
            "ws1_fields": ws1, "spans": spans, "series": series, "memory": memory, "gpu": gpu,
            "config": config, "markers": sorted(markers, key=lambda m: m["t"]), "events": events,
            "pods": pods, "coverage": coverage}


def _coverage(state, pods):
    warns = []
    if state.get("source") == "tar":
        warns.append("imported after the run: each pod keeps only its last lines; earlier evaluation "
                     "may be missing")
    for p, info in sorted(pods.items()):
        if info.get("gaps"):
            warns.append("%s: %d gap(s) between polls (more lines than the log tail in one interval)"
                         % (p, info["gaps"]))
        if info.get("complete_from_start") is False and state.get("source") != "tar":
            warns.append("%s: its first fetch was already a full tail; the pod's earliest lines are missing" % p)
        if info.get("throttled_lines"):
            warns.append("%s: %d per-call timing lines were sampled by the server's logger" % (p, info["throttled_lines"]))
    return {"source": state.get("source", "poll"), "polls": state.get("polls", 0),
            "gaps": len(state.get("gaps") or []), "warnings": warns}


# --------------------------------------------------------------------------- #
# window statistics (used by analyze on the stable window)
# --------------------------------------------------------------------------- #

def merged_quantile(windows, q):
    """Quantile q (0-1) of several windows known only by n, min, P50, P90, P95, P99, max:
    each window is a piecewise-linear distribution through those points; the windows are
    mixed by n and the mixture is inverted (approximate, labelled as such)."""
    def cdf(w, x):
        knots = [(w["min"], 0.0), (w["p50"], 0.5), (w["p90"], 0.9), (w["p95"], 0.95),
                 (w["p99"], 0.99), (w["max"], 1.0)]
        if x <= knots[0][0]:
            return 0.0
        for (x0, c0), (x1, c1) in zip(knots, knots[1:]):
            if x <= x1:
                return c1 if x1 <= x0 else c0 + (c1 - c0) * (x - x0) / (x1 - x0)
        return 1.0

    total = float(sum(w["n"] for w in windows))
    lo, hi = min(w["min"] for w in windows), max(w["max"] for w in windows)
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if sum(w["n"] * cdf(w, mid) for w in windows) / total < q:
            lo = mid
        else:
            hi = mid
    return hi


MIN_CALLS_FOR_PERCENTILES = 5
LOGGER_FULL_LINES = 100     # per-call lines a worker logs in full before sampling (K26)


def percentile(xs, q):
    """Nearest rank (the engine's convention)."""
    if not xs:
        return None
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, int(math.ceil(q / 100.0 * len(s))) - 1))]


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def _inside(t, spans):
    """t falls in one of the [start, end) intervals: a time on a boundary belongs to what follows."""
    return any(a <= t < b for a, b in spans or ())


LOOP_SPANS = ("trainer.validation_step", "trainer.validation_step.pull_batch", "trainer.infer",
              "trainer.validation_step.push_metrics", "trainer.validation_step.extract_ls",
              "trainer.validation_step.marshal_numpy", "trainer.update_working_state")


def window_stats(tables, t0, t1, loop_end=None, skip=None):
    """Per-component statistics inside [t0, t1], leaving out the `skip` intervals.

    - Aggregate span lines (complete, mean per window): windows at least half inside count;
      mean = sum/n over them. With per-window percentiles (newer servers) the windows are
      merged (`merged_quantile`).
    - Per-call lines (spans with n=1 and the per-sample/per-batch series): exact percentiles
      of the observed calls; `sampled` when the server's logger dropped some of them.
    - `loop_end`: the evaluation loop's end. An evaluation-loop window still open then is
      flushed only at process exit; its calls all happened inside the loop, so it is clipped."""
    skip = skip or []
    dur = max(t1 - t0 - sum(_overlap(a, b, t0, t1) for a, b in skip), 1e-9)
    comps = {}
    for r in tables.get("spans", []):
        weight = 1.0
        if r["aggregate"]:
            w = r.get("window") or 0.0
            end = r["t"]
            if loop_end is not None and r["span"] in LOOP_SPANS and end > loop_end:
                end = loop_end
            span = max(end - (r["t"] - w), 1e-9)
            ov = _overlap(r["t"] - w, end, t0, t1) - sum(
                _overlap(max(r["t"] - w, a), min(end, b), t0, t1) for a, b in skip)
            if ov <= 0:
                continue
            weight = min(1.0, ov / span)       # a window partly inside counts pro rata
        elif not (t0 <= r["t"] <= t1) or _inside(r["t"] - 0.5 * (r["sum"] or 0.0), skip):
            continue                             # a call is placed by its middle: it ends when it is logged
        key = "%s:%s" % (r["role"], r["span"])
        c = comps.setdefault(key, {"component": r["span"], "role": r["role"], "source": "span",
                                   "n": 0, "sum": 0.0, "min": None, "max": None, "calls": [],
                                   "w_p50": [],
                                   "span_kind": r.get("span_kind"), "sampled": False, "pods": set()})
        c["n"] += (r["n"] or 0) * weight
        c["sum"] += (r["sum"] or 0.0) * weight
        c["min"] = r["min"] if c["min"] is None else min(c["min"], r["min"])
        c["max"] = r["max"] if c["max"] is None else max(c["max"], r["max"])
        c["pods"].add(r["pod"])
        c["sampled"] = c["sampled"] or r.get("sampled", False)
        if r["aggregate"]:
            if "p50" in r:
                c["w_p50"].append({"n": r["n"] * weight, "min": r["min"], "max": r["max"], "p50": r["p50"],
                                   "p90": r["p90"], "p95": r["p95"], "p99": r["p99"]})
        else:
            c["calls"].append(r["mean"])
    out = {}
    for key, c in comps.items():
        row = {"component": c["component"], "role": c["role"], "source": "span", "calls": int(round(c["n"])),
               "mean": c["sum"] / c["n"] if c["n"] else None, "sum_seconds": c["sum"],
               "min": c["min"], "max": c["max"], "span_kind": c["span_kind"],
               "pods": len(c["pods"]), "share_of_window": min(1.0, c["sum"] / dur / max(len(c["pods"]), 1))}
        if c["w_p50"]:
            row.update({q: merged_quantile(c["w_p50"], int(q[1:]) / 100.0) for q in ("p50", "p90", "p95", "p99")})
            row["percentiles"] = "merged from the server's per-window percentiles (approximate)"
        elif len(c["calls"]) == int(round(c["n"])) and len(c["calls"]) >= MIN_CALLS_FOR_PERCENTILES:
            row.update({q: percentile(c["calls"], int(q[1:])) for q in ("p50", "p90", "p95", "p99")})
            row["percentiles"] = "sampled calls" if c["sampled"] else "every call"
        else:
            row["percentiles"] = None
        out[key] = row
    for name, recs in (tables.get("series") or {}).items():
        inside = [r for r in recs if t0 <= r["t"] <= t1 and not _inside(r["t"], skip)]
        if not inside:
            continue
        field = {"generation_per_sample": "get_sample_secs", "metrics_batch": "metric_calc_secs",
                 "visualizers_batch": "per_sample_secs"}.get(name, "duration_seconds")
        xs = [r[field] for r in inside if field in r]
        pods = len({r["pod"] for r in inside})
        per_pod = {}
        for r in recs:
            per_pod[r["pod"]] = per_pod.get(r["pod"], 0) + 1
        sampled = any(r["sampled"] for r in inside) or max(per_pod.values()) >= LOGGER_FULL_LINES
        out["series:" + name] = {
            "component": name, "role": "generic-process", "source": "per-call lines", "field": field,
            "calls": len(xs), "mean": sum(xs) / len(xs) if xs else None,
            "sum_seconds": sum(xs), "min": min(xs) if xs else None, "max": max(xs) if xs else None,
            "pods": pods,
            "percentiles": None if len(xs) < MIN_CALLS_FOR_PERCENTILES else
            ("possibly sampled: the server logs about the first %d per worker, then one per ~10 s"
             % LOGGER_FULL_LINES if sampled else "every call logged"),
            "share_of_window": min(1.0, sum(xs) / dur / max(pods, 1))}
        if len(xs) >= MIN_CALLS_FOR_PERCENTILES:
            out["series:" + name].update({q: percentile(xs, int(q[1:])) for q in ("p50", "p90", "p95", "p99")})
    return out


# --------------------------------------------------------------------------- #
# analyze: phases, critical path, bottleneck attribution, memory, offline vs online
# --------------------------------------------------------------------------- #
#
# Attribution reads who waits on whom (the run's own logs):
#   the evaluation waits for samples          -> sample generation limits it (producer-bound)
#   the evaluation waits to hand off metrics  -> the metrics limit it
#   inference fills the evaluation loop       -> the model is the floor
#   none of these, workers idle               -> work around inference on the platform side
#   work after the evaluation (visualization, analysis, drains) extends the job: critical
#   path via the tail. Expensive work that overlaps the evaluation without making it wait is
#   off the critical path.

WAIT_HIGH = 0.30           # share of the evaluation loop spent waiting for samples
NEAR_WAIT = 0.10           # ... from here on, sample generation is close to limiting
HANDOFF_HIGH = 0.20        # share spent waiting to hand off a batch's metrics
INFER_HIGH = 0.70          # share spent in inference: the model is the floor
IDLE_HIGH = 0.30           # share of worker time spent idle
TAIL_SHARE = 0.10          # a period this share of the job is worth a finding
MISMATCH = 1.5             # online/offline ratio outside [1/x, x] is a finding ...
MISMATCH_ABS = 0.001       # ... and at least this many seconds apart (sub-ms ratios are noise)
SMALL_BATCH = 0.5          # realised rows per inference call below this share of the configured batch
NEAR_LIMIT = 0.85          # memory use this close to the limit is pressure

STEP_EVALUATION, STEP_VISUALIZATION = "train_and_evaluate", "visualize_samples"
CONSUMER = "evaluate"
LOOP, PULL, INFER, HANDOFF = ("trainer.validation_step", "trainer.validation_step.pull_batch",
                              "trainer.infer", "trainer.validation_step.push_metrics")
OVERHEAD = ("trainer.validation_step.extract_ls", "trainer.validation_step.marshal_numpy")
DRAINS = ("trainer.wait_for_metrics", "trainer.wait_for_streaming_queue")
GENERIC = "generic-process"


def _polls(job_dir):
    p = os.path.join(job_dir, "polls.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def _pod_series(polls):
    out = []
    for p in polls:
        workers = [x for x in p.get("pods") or [] if x["role"] == GENERIC]
        ready = sum(1 for x in workers if x.get("ready"))
        pending = any(x.get("status") == "Pending" or x.get("waiting_reason") or x.get("ready") is False
                      for x in workers)
        out.append((p["t"], ready, pending))
    return out


def phase_progress(polls, tables):
    """{phase: (points, pods, unit, source)} — polled progress when the run was followed,
    else reconstructed from the logs (aggregate windows / per-batch lines)."""
    res = {}
    pods = _pod_series(polls)
    for phase, step in (("evaluation", STEP_EVALUATION), ("visualization", STEP_VISUALIZATION)):
        pts = []
        for p in polls:
            for s in p.get("steps") or []:
                if s.get("id") == step and s.get("current") is not None:
                    pts.append((p["t"], float(s["current"])))
        if len([1 for _, v in pts if v > 0]) >= 2:
            res[phase] = (pts, pods, "rows", "polled progress")
    if "evaluation" not in res:
        rows = sorted((r for r in tables.get("spans", []) if r["role"] == CONSUMER and r["span"] == LOOP
                       and r["aggregate"]), key=lambda r: r["t"])
        if rows:
            acc, pts = 0.0, [(rows[0]["t"] - (rows[0].get("window") or 0.0), 0.0)]
            for r in rows:
                acc += r["n"]
                pts.append((r["t"], acc))
            res["evaluation"] = (pts, _log_pods(tables), "batches", "log windows")
    if "visualization" not in res:
        recs = sorted((tables.get("series") or {}).get("visualizers_batch") or [], key=lambda r: r["t"])
        if len(recs) >= 2:
            pts = [(recs[0]["t"] - recs[0].get("duration_seconds", 0.0), 0.0)]
            pts += [(r["t"], float(i + 1)) for i, r in enumerate(recs)]
            res["visualization"] = (pts, _log_pods(tables), "batches", "log lines")
    return res


def _log_pods(tables):
    spans = [(p["first"], p["last"]) for p in (tables.get("pods") or {}).values()
             if p.get("role") == GENERIC and p.get("first") is not None]
    if not spans:
        return []
    times = sorted({t for s in spans for t in s})
    return [(t, sum(1 for a, b in spans if a <= t <= b), False) for t in times]


def _eval_bounds(phases, tables):
    """(start, end, source) of the evaluation loop: polled progress, else the per-state
    GPU-utilization lines (end of each state's loop; start = end - its wall time). The last
    aggregate window is flushed at process exit and spans post-processing: never used."""
    ph = (phases.get("evaluation") or {})
    if ph.get("phase") and ph.get("source") == "polled progress":
        return ph["phase"]["start"], ph["phase"]["end"], "polled progress"
    gpu = [g for g in tables.get("gpu") or [] if g.get("loop_wall_seconds") is not None]
    if gpu:
        return (min(g["t"] - g["loop_wall_seconds"] for g in gpu), max(g["t"] for g in gpu),
                "the evaluation loop's own per-state timing")
    rows = sorted((r for r in tables.get("spans") or [] if r["role"] == CONSUMER and r["span"] == LOOP
                   and r["aggregate"]), key=lambda r: r["t"])
    full = [r for r in rows if r["n"] >= max(x["n"] for x in rows)] if len(rows) > 1 else []
    if full:
        start = rows[0]["t"] - (rows[0].get("window") or 0.0)
        end = full[-1]["t"]
        pull = {r["t"]: r for r in tables.get("spans") or [] if r["role"] == CONSUMER and r["span"] == PULL}
        for r in rows[rows.index(full[-1]) + 1:]:      # the partial window flushed at exit
            per_batch = (r["mean"] or 0.0) + ((pull.get(r["t"]) or {}).get("mean") or 0.0)
            end += r["n"] * per_batch
        return start, end, "the evaluation loop's timing windows (approximate end)"
    return None, None, None


def _job_bounds(tables, polls, source):
    end = next((m for m in reversed(tables.get("markers") or []) if m["name"] == "job_end"), None)
    if end and end.get("duration_seconds"):
        return end["t"] - end["duration_seconds"], end["t"], "the run's own job span"
    pods = tables.get("pods") or {}
    lasts = [p["last"] for p in pods.values() if p.get("last") is not None]
    starts = [(p.get("describe") or {}).get("start") for p in pods.values() if p.get("role") == CONSUMER]
    starts = [s for s in starts if s] or [p["first"] for p in pods.values() if p.get("first") is not None]
    if source == "poll" and polls:
        starts.append(polls[0]["t"])
    if not starts or not lasts:
        return None, None, None
    return min(starts), max(lasts), "evaluation pod start to the last log line"


def _marker(tables, name):
    return next((m for m in tables.get("markers") or [] if m["name"] == name), None)


def _quote(job_dir, pod_prefix, needle, t0=None, t1=None):
    """One log line from the kept logs as evidence: the last matching line in [t0, t1]."""
    logs = os.path.join(job_dir, "logs")
    best = None
    for f in sorted(os.listdir(logs)) if os.path.isdir(logs) else []:
        if not f.startswith(pod_prefix):
            continue
        with open(os.path.join(logs, f), encoding="utf-8") as fh:
            for line in fh:
                if needle not in line:
                    continue
                try:
                    ts = log_time(json.loads(line).get("asctime"))
                except ValueError:
                    ts = None
                if t0 is not None and ts is not None and not (t0 <= ts <= t1 + 1):
                    continue
                best = line.strip()
    return best[:400] if best else None


def _finding(fid, title, btype, location, critical_path, owner, evidence, explanation, suggestion,
             seconds=None, share=None, lines=None):
    return {"id": fid, "title": title, "type": btype, "location": location,
            "critical_path": critical_path, "owner": owner, "seconds": seconds, "share_of_job": share,
            "evidence": [e for e in evidence if e], "log_lines": [l for l in (lines or []) if l],
            "explanation": explanation, "suggestion": suggestion}


def _num(st, key, field="sum_seconds"):
    row = st.get("%s:%s" % (CONSUMER, key))
    return (row or {}).get(field) or 0.0


def _offline(out, tl):
    floor = tl._read_json(os.path.join(out, "floor.json")) or {}
    profile, _ = _pushed_profile(out, tl)
    if not profile:
        return {}
    costs = tl.pipeline_costs(profile)
    mem = profile.get("memory") or {}
    um = profile.get("user_memory") or {}
    sweep = {int(e["batch_size"]): e.get("per_sample_mean_seconds") for e in floor.get("sweep") or []
             if isinstance(e, dict) and e.get("batch_size") and e.get("per_sample_mean_seconds")}
    return {"floor_sweep": sweep,
            "inference_s": floor.get("t_inf_per_sample_mean_seconds") or costs["inference"],
            "inference_batch": floor.get("recommended_batch_size"),
            "generation_s": costs["generation"], "metrics_s": costs["metrics"],
            "visualizer_s": costs["visualizers"], "startup_s": tl.startup_seconds(profile),
            "worker_peak_rss_gb": max([v.get("peak_rss_gb") or 0 for v in mem.values() if isinstance(v, dict)] or [0]) or None,
            "generation_peak_rss_gb": (mem.get("generate") or {}).get("peak_rss_gb") if isinstance(mem.get("generate"), dict) else None,
            "user_footprint_gb": um.get("footprint_gb"), "user_peak_stage": um.get("peak_stage"),
            "device": (floor.get("device") or {}).get("label"),
            "memory_status": ((tl._read_json(os.path.join(out, "score.json")) or {}).get("memory") or {}).get("status"),
            "memory_reasons": ((tl._read_json(os.path.join(out, "score.json")) or {}).get("memory") or {}).get("reasons")}


def _rows_per_call(phases, w0, w1, infer, state, tables):
    """Realised rows per inference call: polled rows in the window / inference calls in it;
    else the run's row total / all inference calls when the evaluation pod's log is complete."""
    if not infer or not infer.get("calls"):
        return None, None
    ev = phases.get("evaluation") or {}
    pts = ev.get("points")
    if pts and ev.get("unit") == "rows":
        rows = _interp(pts, w1) - _interp(pts, w0)
        if rows > 0:
            return rows / infer["calls"], "polled rows / inference calls in the window"
    total = state.get("rows_evaluated")
    pods = tables.get("pods") or {}
    complete = all(p.get("complete_from_start") and not p.get("gaps")
                   for p in pods.values() if p.get("role") == CONSUMER)
    calls = sum(r["n"] for r in tables.get("spans") or [] if r["role"] == CONSUMER and r["span"] == INFER)
    if total and calls and complete and state.get("source") != "tar":
        return total / float(calls), "rows evaluated / inference calls"
    return None, None


def _user_visualizer_share(stats, tables, offline, view):
    """Evidence line: how much of the online per-sample visualization time the user's own
    visualizers explain (their offline cost scaled by the server's measured slowdown)."""
    vis = window_stats(tables, 0, 1e12).get("series:visualizers_batch")
    gen_on = (stats.get("series:generation_per_sample") or {}).get("mean")
    if not vis or not vis.get("mean") or not offline.get("visualizer_s"):
        return None
    slow = max(1.0, gen_on / offline["generation_s"]) if gen_on and offline.get("generation_s") else 1.0
    user = min(1.0, offline["visualizer_s"] * slow / vis["mean"])
    return ("your visualizers explain about %.0f%% of the %.0f ms per visualized sample on the workers "
            "(offline %.0f ms × %.1f for this server's measured slowdown)" % (
                100 * user, 1000 * vis["mean"], 1000 * offline["visualizer_s"], slow))


def _gb(s):
    """'10Gi' / '512Mi' / '2G' -> GiB."""
    m = re.match(r"^([\d.]+)\s*([KMGT]i?)?", s or "")
    if not m:
        return None
    mult = {"K": 1e3 / 2 ** 30, "Ki": 2 ** -20, "M": 1e6 / 2 ** 30, "Mi": 2 ** -10, "G": 1e9 / 2 ** 30,
            "Gi": 1.0, "T": 1e12 / 2 ** 30, "Ti": 1024.0}.get(m.group(2) or "", 1.0 / 2 ** 30)
    return float(m.group(1)) * mult


# --------------------------------------------------------------------------- #
# root causes (K29): for each slow phase, who waited on whom, what the side that set the pace
# spent its time on, the mechanism, the engine part to change, what is ruled out and how much of
# the phase is explained. From the run's own lines only; links not directly measured are marked.
# --------------------------------------------------------------------------- #

RC_MIN_SHARE = TAIL_SHARE      # a phase this share of the run gets a root-cause chain
RC_RULED_OUT = 0.05            # a candidate under this share of the phase is ruled out
RC_PACED_BUSY = 0.5            # the evaluation pod busy this share of a phase sets its pace
RC_PACED_WAIT = 0.5            # ... waiting this share of it: the workers set the pace


def _rc_row(ws, role, name):
    return ws.get("%s:%s" % (role, name)) or {}


def _rc_sum(ws, role, name):
    return _rc_row(ws, role, name).get("sum_seconds") or 0.0


def _rc_mean(ws, role, name):
    return _rc_row(ws, role, name).get("mean")


def _rc_prefixed(ws, role, prefix):
    return sorted(((r["component"][len(prefix):], r) for r in ws.values()
                   if r["role"] == role and r["component"].startswith(prefix)),
                  key=lambda kv: -(kv[1].get("sum_seconds") or 0.0))


def _rc_part(name, seconds, how="measured"):
    return {"name": name, "seconds": float(seconds or 0.0), "how": how}


def _rc_t(x):
    if x is None:
        return "-"
    return "%.0f ms" % (1000 * x) if x < 1 else ("%.1f s" % x if x < 100 else "%.0f s" % x)


def _rc_chain(phase, wall, pacing, parts, driver, mechanism, optimize, ruled_out, to_confirm, evidence,
              link="measured", notes=None):
    """One root-cause chain. Confidence: measured when measured parts explain most of the phase
    and the mechanism link is measured; inferred when an inferred part or link is needed;
    candidate when less than half of the phase is explained."""
    parts = [p for p in parts if p["seconds"] > 0]
    explained = min(wall, sum(p["seconds"] for p in parts)) if wall else 0.0
    measured = sum(p["seconds"] for p in parts if p["how"] == "measured")
    if not wall or explained < 0.5 * wall:
        conf = "candidate"
    elif link == "measured" and measured >= 0.5 * wall and \
            all(p["how"] == "measured" for p in parts if p["seconds"] >= 0.2 * wall):
        conf = "measured"
    else:
        conf = "inferred"
    for p in parts:
        p["share_of_phase"] = p["seconds"] / wall if wall else None
    return {"phase": phase, "wall_seconds": wall, "pacing": pacing,
            "parts": sorted(parts, key=lambda p: -p["seconds"]),
            "explained_share": explained / wall if wall else None,
            "unexplained_seconds": max(0.0, (wall or 0.0) - explained), "driver": driver, "mechanism": mechanism,
            "link": link, "optimize": [o for o in optimize if o], "ruled_out": [r for r in ruled_out if r],
            "to_confirm": [c for c in to_confirm if c], "confidence": conf,
            "evidence": [e for e in evidence if e], "notes": [n for n in (notes or []) if n]}


def _rc_visualization_render(ws, n_renders):
    """Where a worker's visualization batch goes: heatmaps, your visualizers, encoding and
    writing the results. Per rendered batch."""
    per = (lambda x: x / n_renders) if n_renders else (lambda x: None)
    user = sum(r.get("sum_seconds") or 0.0 for _, r in _rc_prefixed(ws, GENERIC, "vis_calculator.user_visualizer."))
    user += sum(r.get("sum_seconds") or 0.0
                for _, r in _rc_prefixed(ws, GENERIC, "vis_calculator.user_heatmap_visualizer."))
    items = [("heatmaps", _rc_sum(ws, GENERIC, "heatmap.generate")),
             ("your visualizers", user),
             ("image encoding", _rc_sum(ws, GENERIC, "storage.image_encode")),
             ("writing result files", _rc_sum(ws, GENERIC, "vis_calculator.upload.payload_json") +
              _rc_sum(ws, GENERIC, "storage.flatbuffer_upload_sync")),
             ("heatmap overlays", _rc_sum(ws, GENERIC, "vis_calculator.overlay")),
             ("restoring shared parent data", _rc_sum(ws, GENERIC, "redis.vis_queue.restore_parent_sections"))]
    return [(name, total, per(total)) for name, total in sorted(items, key=lambda x: -x[1]) if total > 0]


def _rc_visualization(tables, stage, children, gpus):
    t0, t1 = stage["start"], stage["end"]
    wall = max(t1 - t0, 1e-9)
    ws = window_stats(tables, t0, t1 + 1)
    batch = _rc_row(ws, CONSUMER, "visualization.batch")
    iters = dict(_rc_prefixed(ws, GENERIC, "generic_processor.iteration."))
    if not batch and not iters:
        return None                      # a server without the visualization lines
    ev_busy = batch.get("sum_seconds") or 0.0
    wait = _rc_sum(ws, CONSUMER, "visualization.next_batch_wait")
    fwd = _rc_row(ws, CONSUMER, "visualization.forward")
    prep = _rc_sum(ws, CONSUMER, "visualization.prepare_payload")
    push = _rc_sum(ws, CONSUMER, "visualization.push_submit")
    rows = _rc_mean(ws, CONSUMER, "visualization.batch_rows_NOT_SECONDS")
    shared = _rc_mean(ws, CONSUMER, "visualization.shared_parent_batch_NOT_SECONDS")
    sync = _rc_mean(ws, CONSUMER, "redis.vis_queue.sync_push_NOT_SECONDS")
    backpressure = _rc_sum(ws, CONSUMER, "redis.vis_queue.backpressure_wait")
    n_batches = batch.get("calls") or 0
    pods = max([r.get("pods") or 0 for r in iters.values()] or [0])
    procs = max(1, pods) * max(1, children)
    vis_w = (iters.get("vis") or {}).get("sum_seconds") or 0.0
    gen_w = (iters.get("generate") or {}).get("sum_seconds") or 0.0
    empty_w = (iters.get("empty") or {}).get("sum_seconds") or 0.0
    other_w = sum(r.get("sum_seconds") or 0.0 for k, r in iters.items() if k not in ("vis", "generate", "empty"))
    worker_util = vis_w / (procs * wall)
    n_renders = (iters.get("vis") or {}).get("calls") or _rc_row(ws, GENERIC, "vis_calculator.upload").get("calls")
    render = _rc_visualization_render(ws, n_renders)
    busy_share, wait_share = ev_busy / wall, wait / wall
    fwd_sum, fwd_n, fwd_mean = fwd.get("sum_seconds") or 0.0, fwd.get("calls") or 0, fwd.get("mean")
    full = stage.get("projected_full_seconds")
    evidence = ["stopped after a steady pace; the full visualization would take ≈ %s at that pace" % _rc_t(full)
                if full else "",
                "evaluation pod: %s in its own visualization work over %d batches (%s), %s waiting for samples (%s)" % (
                    _rc_t(ev_busy), n_batches, _pct(busy_share), _rc_t(wait), _pct(wait_share)),
                "forward passes: %d, mean %s, P95 %s" % (fwd_n, _rc_t(fwd_mean), _rc_t(fwd.get("p95"))) if fwd_n else "",
                "%s of batches reused a published parent (no forward pass)" % _pct(shared) if shared is not None else "",
                "rows per batch: %.1f" % rows if rows else "",
                "workers: %d process(es); rendering %s, generating samples %s, polling empty queues %s of their time" % (
                    procs, _pct(vis_w / (procs * wall)), _pct(gen_w / (procs * wall)), _pct(empty_w / (procs * wall)))
                if iters else "",
                ("per rendered batch on a worker: " + ", ".join("%s %s" % (n, _rc_t(p)) for n, _, p in render[:5]))
                if render and n_renders else ""]
    ruled, notes, sigs = [], [], []
    if backpressure / wall < RC_RULED_OUT:
        ruled.append("visualization-queue back-pressure (%s)" % _rc_t(backpressure))
    if sync is not None and sync < RC_RULED_OUT:
        ruled.append("pushes on the evaluation pod's own thread (%s of pushes)" % _pct(sync))
    twice_note = None
    if n_renders:
        twice = [(k, r) for k, r in _rc_prefixed(ws, GENERIC, "vis_calculator.user_visualizer.")
                 if (r.get("calls") or 0) >= 1.8 * n_renders and (r.get("sum_seconds") or 0) >= 0.05 * vis_w]
        if twice:
            extra = sum((r.get("sum_seconds") or 0.0) / 2 for _, r in twice)
            names = ", ".join("'%s'" % k for k, _ in twice)
            sigs.append(_sig(
                "G4", "your visualizer%s on the workers" % ("s" if len(twice) > 1 else ""),
                "Your visualizer%s %s ran about twice per rendered batch (%d calls each over %d batches): the heatmap "
                "overlay runs %s a second time on the same input. The second run is %s of worker rendering time." % (
                    "s" if len(twice) > 1 else "", names, twice[0][1]["calls"], n_renders,
                    "each" if len(twice) > 1 else "it", _pct(extra / vis_w) if vis_w else "-"),
                cost=extra / procs, side_seconds=wall, pacing=None,
                evidence=["%s: %d calls, %d rendered batches" % (k, r["calls"], n_renders) for k, r in twice],
                change="the heatmap overlay: reuse the visualizer output it already has instead of running it again",
                confirm="visualizer calls per rendered batch stay near 2"))
            twice_note = "the heatmap overlay re-running your visualizer%s (%s of worker rendering time)" % (
                "s" if len(twice) > 1 else "", _pct(extra / vis_w) if vis_w else "-")
    mask = _rc_mean(ws, GENERIC, "heatmap.mask_applied_NOT_SECONDS")
    fetch = _rc_row(ws, GENERIC, "heatmap.instance_mask_fetch")
    if mask is not None and mask < 0.5 and fetch.get("calls"):
        sigs.append(_sig(
            "G4", "instance heatmap masks on the workers",
            "Each instance row regenerated its source sample to fetch its mask (%d instance rows, %s each, %s of worker "
            "time), yet the mask applied to the heatmap only %s of the time; the rest show the whole-sample heatmap."
            % (fetch["calls"], _rc_t(fetch.get("mean")), _rc_t(fetch.get("sum_seconds")), _pct(mask)),
            cost=(fetch.get("sum_seconds") or 0.0) / procs, side_seconds=wall, pacing=None,
            evidence=["`heatmap.instance_mask_fetch`: %d calls; mask applied %s" % (fetch["calls"], _pct(mask))],
            change="instance heatmap masks: take the mask from the already-generated source sample, and skip the "
                   "fetch when its resolution cannot match the heatmap",
            confirm="mask fetches track instance rows while masks rarely apply"))
    if sync is not None and sync >= 0.5:
        sigs.append(_sig(
            "G6", "visualization pushes on the evaluation pod",
            "%s of visualization pushes ran on the evaluation pod's own thread (%s in pushes)." % (
                _pct(sync), _rc_t(push)),
            cost=push, side_seconds=wall, pacing=None,
            evidence=["synchronous pushes: %s" % _pct(sync)],
            change="push visualization payloads off the evaluation pod's main thread",
            confirm="synchronous pushes stay above half"))
    if busy_share >= RC_PACED_BUSY and wait_share < 0.3:
        pacing = "the evaluation pod"
        parts = [_rc_part("forward passes on the evaluation pod", fwd_sum),
                 _rc_part("preparing visualization payloads", prep), _rc_part("handing batches to the workers", push),
                 _rc_part("waiting for samples", wait)]
        driver = "%d forward passes × %s%s" % (fwd_n, _rc_t(fwd_mean), " on CPU (no GPU)" if gpus == 0 else "")
        mechanism = ("The evaluation pod handles visualization batches one at a time. Each batch that does not reuse a "
                     "published parent runs a forward pass (%d of %d batches, %.1f row(s) per batch); the workers were "
                     "busy %s of their time on visualization, so they waited for the evaluation pod."
                     % (fwd_n, n_batches, rows or 0, _pct(worker_util)))
        optimize = ["the visualization forward pass on the evaluation pod: more rows per pass, a GPU%s, or overlapping "
                    "passes with the hand-off to the workers" % (" (none on this run)" if gpus == 0 else "")]
        if wait_share < RC_RULED_OUT:
            ruled.append("sample production for visualization (the evaluation pod waited %s)" % _pct(wait_share))
        if worker_util < 0.5:
            ruled.append("the workers' rendering (busy %s)" % _pct(worker_util))
        link, confirm = "measured", "forward passes stay most of the phase while worker rendering stays low"
    elif wait_share >= RC_PACED_WAIT:
        pacing = "the generic workers"
        parts = [_rc_part("rendering visualizations (per worker process)", vis_w / procs),
                 _rc_part("generating samples (per worker process)", gen_w / procs),
                 _rc_part("polling empty queues (per worker process)", empty_w / procs),
                 _rc_part("other worker work (per worker process)", other_w / procs)]
        gen_share = gen_w / (procs * wall)
        driver = "%d worker process(es) × %s per rendered batch" % (procs, _rc_t(vis_w / n_renders) if n_renders else "-")
        if gen_share < 0.05 and worker_util >= 0.5:
            mechanism = ("Each visualized sample is produced on demand: the evaluation pod waits %s per batch while the "
                         "workers spend %s of their time rendering and %s generating samples. Samples are not produced "
                         "ahead of rendering, so each one costs a render plus the hand-off."
                         % (_rc_t(wait / n_batches) if n_batches else "-", _pct(worker_util), _pct(gen_share)))
            link = "inferred"
        elif empty_w / (procs * wall) >= 0.5:
            mechanism = ("The workers mostly poll empty queues while the evaluation pod waits for samples: the hand-off "
                         "between them, not either side's work, sets the pace.")
            link = "inferred"
        else:
            mechanism = "The workers are busy rendering (%s of their time) and the evaluation pod waits for them." % _pct(worker_util)
            link = "measured"
        optimize = ["render in parallel during visualization (%d worker process(es) on this run)" % procs,
                    "produce visualization samples ahead of rendering instead of on demand"]
        if render:
            name, total, per = render[0]
            optimize.append("%s on the workers (%s per rendered batch, %s of their visualization time)"
                            % (name, _rc_t(per), _pct(total / vis_w) if vis_w else "-"))
        if fwd_sum / wall < RC_RULED_OUT:
            ruled.append("the evaluation pod's forward passes (%s of the phase)" % _pct(fwd_sum / wall))
        confirm = "the evaluation pod's wait stays most of the phase while sample generation stays near zero"
    else:
        pacing = "neither side alone"
        parts = [_rc_part("the evaluation pod's own work", ev_busy), _rc_part("waiting for samples", wait)]
        driver, link = "-", "inferred"
        mechanism = ("The evaluation pod is busy %s of the phase and waits %s of it; neither side alone sets the pace."
                     % (_pct(busy_share), _pct(wait_share)))
        optimize, confirm = [], ""
    role = _pacing_role(pacing)
    for s_ in sigs:
        s_["on_pacing_side"] = (role == GENERIC) if s_["subject"].endswith("on the workers") else (role == CONSUMER)
    rc = _rc_chain(stage["stage"], wall, pacing, parts, driver, mechanism, optimize, ruled, [confirm], evidence,
                   link=link, notes=notes)
    rc["mechanisms"] = sigs
    return rc


def _rc_startup(tables, stage, eng):
    wall = stage["seconds"]
    ev = eng.get("events") or {}
    fb = ((ev.get("first_batch") or {}).get("records") or [None])[0]
    calls = (ev.get("remote_call") or {}).get("records") or []
    served = (ev.get("command_served") or {}).get("records") or []
    if not fb and not calls:
        return None
    spans = {r["span"]: r for r in tables.get("spans") or [] if r["role"] == CONSUMER and not r["aggregate"]}
    by_cmd = {}
    for c in calls:
        by_cmd[c.get("command") or "?"] = by_cmd.get(c.get("command") or "?", 0.0) + (c.get("duration_seconds") or 0.0)
    since_eval = (fb or {}).get("seconds_since_evaluate_start")
    parts = []
    if since_eval is not None and wall > since_eval:
        parts.append(_rc_part("before the evaluation began (pod start, engine start, job payload)", wall - since_eval))
    for name, label in (("trainer.load_model", "loading the model"), ("trainer.inspect_model", "inspecting the model")):
        if spans.get(name):
            parts.append(_rc_part(label, spans[name]["sum"]))
    for cmd, secs in sorted(by_cmd.items(), key=lambda kv: -kv[1]):
        parts.append(_rc_part("waiting on remote call '%s'" % cmd, secs))
    if fb:
        parts.append(_rc_part("the first batch (first sample and first step)",
                              (fb.get("first_pull_seconds") or 0.0) + (fb.get("first_step_seconds") or 0.0)))
    top = max(parts, key=lambda p: p["seconds"]) if parts else None
    firsts = sorted((s for s in served if s.get("first_in_process")), key=lambda s: -(s.get("duration_seconds") or 0))
    loads = sorted(((ev.get("worker_code_load") or {}).get("records") or []),
                   key=lambda r: -(r.get("preprocess_call_seconds") or 0))
    evidence = ["first evaluated batch %s after the evaluation started" % _rc_t(since_eval) if since_eval is not None else "",
                "remote calls: " + ", ".join("%s %s" % (c, _rc_t(s)) for c, s in sorted(by_cmd.items(), key=lambda kv: -kv[1])[:4])
                if by_cmd else "",
                "a worker's first command: '%s' took %s (peak memory %s GB)" % (
                    firsts[0].get("dataset_command"), _rc_t(firsts[0].get("duration_seconds")), firsts[0].get("peak_rss_gb"))
                if firsts and (firsts[0].get("duration_seconds") or 0) >= 1 else "",
                "workers loading your integration (module import and preprocess): slowest %s over %d process(es), "
                "peak memory %s GB" % (_rc_t(loads[0].get("preprocess_call_seconds")), len(loads),
                                       max(r.get("peak_rss_gb") or 0 for r in loads))
                if loads and (loads[0].get("preprocess_call_seconds") or 0) >= 1 else ""]
    optimize, integration, link = [], [], "measured"
    if top and top["name"].startswith("waiting on remote call"):
        cmd = top["name"].split("'")[1]
        srv = next((s for s in firsts if s.get("dataset_command") == cmd), None)
        mechanism = ("The evaluation pod waited %s on the remote call '%s'%s." % (
            _rc_t(top["seconds"]), cmd,
            "; the worker that served it took %s — its first command, which also loads your integration (module "
            "import and preprocess), peak memory %s GB" % (_rc_t(srv.get("duration_seconds")), srv.get("peak_rss_gb"))
            if srv else ""))
        optimize = ["start-up waits on one worker's first command; every new worker loads the integration again"]
        integration = ["make module import and preprocess cheaper (move heavy work out of import time)"] if srv else []
        if not srv:
            link = "inferred"
    elif top and top["name"].startswith("before the evaluation began"):
        mechanism = ("Most of the start-up passed before the evaluation began (%s): pod start, engine start, the "
                     "job's own services and payload. The engine's logs don't break this part down further."
                     % _rc_t(top["seconds"]))
        optimize = ["the job's start before evaluation (pod scheduling and image availability, engine start-up)"]
        link = "inferred"
    elif top:
        mechanism = "Most of the start-up went to %s (%s)." % (top["name"], _rc_t(top["seconds"]))
        optimize = [top["name"]]
    else:
        mechanism = "-"
    return _rc_chain(stage["stage"], wall, "the evaluation pod's start-up", parts, top["name"] if top else "-",
                     mechanism, optimize + ["integration: " + i for i in integration], [], [], evidence, link=link)


def _rc_leaf_spans(tables, t0, t1, role=CONSUMER):
    """Per-call spans that ended inside [t0, t1] and contain no other span there: the stage's
    own steps, without double counting a parent and its children."""
    rows = [r for r in tables.get("spans") or [] if r["role"] == role and not r["aggregate"] and t0 <= r["t"] <= t1 + 1]
    parents = {r.get("parent") for r in rows if r.get("parent")}
    out = {}
    for r in rows:
        if r["span"] in parents or r["span"] in STRUCTURE_SPANS:
            continue
        o = out.setdefault(r["span"], {"span": r["span"], "seconds": 0.0, "calls": 0})
        o["seconds"] += r["sum"] or 0.0
        o["calls"] += 1
    return sorted(out.values(), key=lambda o: -o["seconds"])


def _rc_evaluation(tables, e0, e1, limiter, children):
    if e0 is None or e1 is None or not limiter:
        return None
    wall = max(e1 - e0, 1e-9)
    ws = window_stats(tables, e0, e1 + 1, loop_end=e1)
    metrics = [("metric '%s'" % k, r) for k, r in _rc_prefixed(ws, GENERIC, "metrics.user_metric.")] + \
              [("instance metric '%s'" % k, r) for k, r in _rc_prefixed(ws, GENERIC, "metrics.user_instance_metric.")]
    loss = _rc_row(ws, GENERIC, "metrics.loss")
    if loss.get("sum_seconds"):
        metrics.append(("the loss", loss))
    pods = max([r.get("pods") or 0 for _, r in metrics] or [0])
    procs = max(1, pods) * max(1, children)
    fwd = [(k, r) for k, r in _rc_prefixed(ws, CONSUMER, "trainer.validation_step.forward.")]
    evidence = list(limiter.get("evidence") or [])[:3]
    if limiter["id"] == "metrics-bound" and metrics:
        total = sum(r.get("sum_seconds") or 0.0 for _, r in metrics)
        parts = [_rc_part("%s (per worker process)" % name, (r.get("sum_seconds") or 0.0) / procs) for name, r in metrics]
        name, r = max(metrics, key=lambda m: m[1].get("sum_seconds") or 0.0)
        mechanism = ("The evaluation waits to hand off metrics; on the workers %s takes %s per batch, %s of the metrics "
                     "and loss time." % (name, _rc_t(r.get("mean")), _pct((r.get("sum_seconds") or 0.0) / total) if total else "-"))
        return _rc_chain("evaluation loop", wall, "the metrics on the generic workers", parts, name, mechanism,
                         ["integration: %s" % name], [], [], evidence, link="inferred")
    if limiter["id"] == "producer-bound":
        pull = _rc_row(ws, CONSUMER, "trainer.validation_step.pull_batch")
        gen_rows = [(_label(r["component"]), r) for k, r in ws.items()
                    if r["role"] == GENERIC and not k.startswith("series:") and not is_counter(r["component"]) and
                    not _is_wait(r["component"]) and r["component"] not in STRUCTURE_SPANS and
                    not r["component"].startswith(("generic_processor.", "metrics_runner.", "vis_calculator."))]
        gen_rows.sort(key=lambda kv: -(kv[1].get("sum_seconds") or 0.0))
        parts = [_rc_part("%s (per worker process)" % n, (r.get("sum_seconds") or 0.0) / procs) for n, r in gen_rows[:6]]
        parts.append(_rc_part("the evaluation pod waiting for samples", pull.get("sum_seconds")))
        top = gen_rows[0] if gen_rows else None
        mechanism = ("The evaluation waits for samples %s of the loop; on the workers the largest step is %s (%s per "
                     "call, %d calls over %d worker process(es))." % (
                         _pct((pull.get("sum_seconds") or 0.0) / wall), top[0], _rc_t(top[1].get("mean")),
                         top[1].get("calls") or 0, procs) if top else
                     "The evaluation waits for samples %s of the loop." % _pct((pull.get("sum_seconds") or 0.0) / wall))
        return _rc_chain("evaluation loop", wall, "the generic workers (sample production)", parts,
                         top[0] if top else "-", mechanism, [top[0]] if top else [], [], [], evidence,
                         link="inferred")
    if limiter["id"] in ("inference-floor", "consumer-bound"):
        loop = _loop_parts(ws)
        if not loop:
            return None
        parts = [_rc_part("forward pass" if k == "forward" else _label(LOOP_STEP + "." + k), r.get("sum_seconds"))
                 for k, r in loop]
        k, r = loop[0]
        name = "the forward pass" if k == "forward" else "'%s'" % _label(LOOP_STEP + "." + k)
        fwd_r = dict(loop).get("forward") or {}
        mechanism = ("The evaluation is limited on the evaluation pod; its largest per-step part is %s (%s per step, %s "
                     "of the loop)%s." % (name, _rc_t(r.get("mean")), _pct((r.get("sum_seconds") or 0.0) / wall),
                                         "" if k == "forward" else "; the forward pass is %s per step" % _rc_t(fwd_r.get("mean"))))
        optimize = ["the evaluation forward pass (device, rows per pass)"] if k == "forward" else \
            ["'%s' on the evaluation pod" % _label(LOOP_STEP + "." + k)]
        return _rc_chain("evaluation loop", wall, "the evaluation pod", parts, name, mechanism, optimize, [], [],
                         evidence)
    return None


def _stage_at(view, t):
    for s_ in view.get("timeline") or []:
        if s_["start"] <= t <= s_["end"] and s_["stage"] != "evaluation loop":
            return s_["stage"]
    for s_ in view.get("timeline") or []:
        if s_["start"] <= t <= s_["end"]:
            return s_["stage"]
    return None


def memory_root_cause(eng, view, memory, offline, cfg, server_warnings=None):
    """With memory as the priority: the pods that hold and reserve the most memory, where
    their peak falls, how much of it is the integration's own code (offline measurement), and
    the part to change. From the run's resource readings, else from the pods' limits and peaks."""
    recs = ((eng.get("events") or {}).get("resources") or {}).get("records") or []
    pods = memory.get("pods") or {}
    roles = {}
    for role, label in ((GENERIC, "worker pod"), (CONSUMER, "evaluation pod")):
        rs = [r for r in recs if pod_role(r.get("pod") or "") == role and r.get("container_memory_gb") is not None]
        ps = [p for p in pods.values() if p.get("role") == role]
        limit = max([r.get("container_memory_limit_gb") or 0 for r in rs] + [p.get("limit_gb") or 0 for p in ps] or [0]) or None
        if rs:
            top = max(rs, key=lambda r: r["container_memory_gb"])
            peak, peak_t, src = top["container_memory_gb"], top["t"], "resource readings"
        else:
            peak = max([p.get("peak_rss_gb") or 0 for p in ps] or [0]) or None
            peak_t, src = None, "logged peaks"
        n = len(ps) or len({r.get("pod") for r in rs}) or 0
        if peak:
            roles[role] = {"label": label, "limit": limit, "peak": peak, "peak_at": peak_t, "pods": n, "source": src}
    if not roles:
        return None
    # the subject: the role reserving the most memory across its pods
    role, d = max(roles.items(), key=lambda kv: (kv[1]["limit"] or kv[1]["peak"]) * max(kv[1]["pods"], 1))
    procs = max(list((cfg.get("children_per_pod") or {}).values()) or [1])
    user = offline.get("user_footprint_gb")
    user_src = "your code's footprint per worker process (offline memory pass)"
    if not user and offline.get("generation_peak_rss_gb"):
        user = offline["generation_peak_rss_gb"]
        user_src = "your integration's sample-generation process (offline peak %.1f GB)" % user
    parts = []
    if role == GENERIC and user:
        mine = min(d["peak"], user * procs)
        parts.append({"name": "%s × %d process(es)" % (user_src, procs), "gb": mine})
        parts.append({"name": "the rest of the pod (engine, model, buffers)", "gb": max(0.0, d["peak"] - mine)})
    else:
        parts.append({"name": "the pod's peak", "gb": d["peak"]})
    phase = _stage_at(view, d["peak_at"]) if d["peak_at"] else None
    reserved = sum((v["limit"] or v["peak"]) * max(v["pods"], 1) for v in roles.values())
    share = (d["peak"] / d["limit"]) if d["limit"] else None
    mech = "Each %s reserves %s and peaks at %.1f GB%s%s." % (
        d["label"], "%.1f GB" % d["limit"] if d["limit"] else "an unknown amount", d["peak"],
        " (%s of it)" % _pct(share) if share else "", (" during %s" % phase) if phase else "")
    if role == GENERIC and user:
        mech += " About %.1f GB of that is your integration's own memory, held in every worker process for the " \
                "whole run; the server sizes worker memory to it." % min(d["peak"], user * procs)
    mech += " The job reserves about %.0f GB across its pods." % reserved
    optimize = []
    if role == GENERIC and user and user * procs >= 0.4 * d["peak"]:
        optimize.append("integration: your integration's memory per worker process (%.1f GB%s) — the memory loop's "
                        "candidates" % (user, ", largest at %s" % offline["user_peak_stage"]
                                         if offline.get("user_peak_stage") else ""))
    if share is not None and share < 0.5:
        optimize.append("the %s memory reservation (%.1f GB for a %.1f GB peak)" % (d["label"], d["limit"], d["peak"]))
    elif share is not None and share >= NEAR_LIMIT:
        optimize.append("the %s memory: it peaked at %s of its limit (out-of-memory risk)" % (d["label"], _pct(share)))
    evidence = ["%s: limit %s, peak %.1f GB (%s), %d pod(s)" % (
                    v["label"], "%.1f GB" % v["limit"] if v["limit"] else "-", v["peak"], v["source"], v["pods"])
                for v in roles.values()]
    evidence += ["server: %s" % w for w in server_warnings or [] if "memory" in w.lower()]
    return {"kind": "memory", "phase": "memory: %s" % d["label"], "subject": d["label"], "peak_gb": d["peak"],
            "limit_gb": d["limit"], "peak_phase": phase, "reserved_gb": reserved, "parts": parts,
            "mechanism": mech, "optimize": optimize, "evidence": evidence,
            "confidence": "measured" if d["source"] == "resource readings" else "inferred",
            "pacing": "memory"}


# --------------------------------------------------------------------------- #
# mechanism rules (K29): six generic shapes read from the run's own measurements.
#   G1 who waits on whom          G4 repeated work
#   G2 which step dominates       G5 growth over time
#   G3 count × unit cost          G6 serial work beside idle capacity
# A rule keys on a shape and the engine's logged step names, never on the name of a platform
# feature or a setting. Each signal states a mechanism with its cost, the engine part where
# it happens, and what would confirm it. G1/G2 are the chains' pacing side and breakdown.
# --------------------------------------------------------------------------- #

RULE_NAMES = {"G1": "who waits on whom", "G2": "which step dominates", "G3": "count × unit cost",
              "G4": "repeated work", "G5": "growth over time", "G6": "serial work beside idle capacity"}
SIG_MIN_SHARE = 0.05       # a mechanism costing less than this share of its side's time is not reported
PER_ROW = 0.8              # calls at least this share of the rows: the step runs once per row
REPEAT = 1.3               # an operation this many times its natural unit repeats work
GROWTH_MIN_GB = 0.5        # memory growth worth a signal: at least this much ...
GROWTH_MIN_SHARE = 0.10    # ... and this share of the pod's limit ...
GROWTH_MIN_R = 0.8         # ... rising steadily (correlation with time)
WAIT_GROWTH = 2.0          # a wait whose mean per call rises this much from the first third to the last
SERIAL_CORES = 1.3         # a busy side using at most this many cores ...
SERIAL_LIMIT = 2.5         # ... of a limit of at least this many works serially
IMBALANCE = 0.7            # one pod doing this share of its role's work while others exist
HANDOFF_SHARE = 0.15       # the evaluation pod handing data over this share of a phase ...
RECEIVER_IDLE = 0.5        # ... while the receiving workers are idle this share of it
LIKE_FORWARD = 0.5         # a metric costing this share of a forward pass per batch
DATA_MOVEMENT = ("redis.", "blob_store.", "storage.", "etl.")
WAIT_WORDS = ("wait", "pull_batch", "idle", "drain")
HANDOFF_SPANS = ("trainer.validation_step.push_metrics", "visualization.push_submit")   # on the loop thread
SIDE_LABEL = {CONSUMER: "the evaluation pod", GENERIC: "the generic workers", "streaming-handler": "the result writers"}


def _is_wait(span):
    return any(w in span for w in WAIT_WORDS)


def _sig(rule, subject, statement, cost=None, side_seconds=None, pacing=None, evidence=None, change=None,
         confirm=None, how="measured"):
    return {"rule": rule, "rule_name": RULE_NAMES[rule], "subject": subject, "statement": statement,
            "cost_seconds": cost, "share": (cost / side_seconds) if cost and side_seconds else None,
            "on_pacing_side": pacing, "evidence": [e for e in evidence or [] if e], "change": change,
            "confirm": confirm, "how": how}


def _records(eng, key, t0=None, t1=None):
    recs = ((eng.get("events") or {}).get(key) or {}).get("records") or []
    return [r for r in recs if t0 is None or t0 <= r["t"] <= t1 + 1]


def _fit(xs, ys):
    """Least-squares slope and correlation."""
    n = len(xs)
    if n < 3:
        return None, None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if not sxx or not syy:
        return (sxy / sxx if sxx else None), None
    return sxy / sxx, sxy / (sxx * syy) ** 0.5


def _phase_ctx(tables, eng, view, stage, kind, pacing_role, children):
    t0, t1 = stage["start"], stage["end"]
    wall = max(t1 - t0, 1e-9)
    ws = window_stats(tables, t0, t1 + 1, loop_end=t1 if kind == "evaluation" else None)
    gen_pods = {r["pod"] for r in tables.get("spans") or [] if r["role"] == GENERIC and t0 <= r["t"] <= t1 + 1}
    procs = max(1, len(gen_pods)) * max(1, children)
    cfg = tables.get("config") or {}
    if kind == "evaluation":
        batches = _rc_row(ws, CONSUMER, "trainer.validation_step").get("calls") or 0
        rpb = ((view.get("measurements") or {}).get("rows_per_batch") or {}).get("median") or cfg.get("batch_size") or 1
    elif kind == "visualization":
        batches = _rc_row(ws, CONSUMER, "visualization.batch").get("calls") or 0
        rpb = _rc_mean(ws, CONSUMER, "visualization.batch_rows_NOT_SECONDS") or 1
    else:
        batches, rpb = 0, 1
    return {"stage": stage, "kind": kind, "t0": t0, "t1": t1, "wall": wall, "ws": ws, "pacing": pacing_role,
            "procs": procs, "gen_pods": gen_pods, "batches": batches, "rows": batches * rpb, "rows_per_batch": rpb,
            "sources": cfg.get("total_samples"), "source_samples": _source_samples(eng),
            "eng": eng, "tables": tables, "view": view}


def _source_samples(eng):
    """Source samples behind the evaluated rows when rows are derived from them (element
    instances): one instance-metrics batch per source batch, from the engine's own line."""
    e = (eng.get("events") or {}).get("instance_metrics") or {}
    if not e.get("count"):
        return None
    sizes = [r.get("batch_size") for r in e.get("records") or [] if r.get("batch_size")]
    return int(round(e["count"] * (_median(sizes) if sizes else 1)))


def _side_seconds(ctx, role):
    return ctx["wall"] * (ctx["procs"] if role == GENERIC else 1)


def _label(span):
    return engine_label(span) or span


def g3_per_row(ctx):
    """G3: a data-movement step that runs once per row although the phase works in batches."""
    out = []
    if ctx["batches"] < 3 or ctx["rows_per_batch"] < 2:
        return out
    for key, r in ctx["ws"].items():
        span, role = r["component"], r["role"]
        if key.startswith("series:") or not span.startswith(DATA_MOVEMENT) or _is_wait(span) or is_counter(span):
            continue
        total, calls = r.get("sum_seconds") or 0.0, r.get("calls") or 0
        side = _side_seconds(ctx, role)
        if calls < PER_ROW * ctx["rows"] or total < SIG_MIN_SHARE * side:
            continue
        out.append(_sig(
            "G3", "%s (%s)" % (_label(span), SIDE_LABEL.get(role, role)),
            "'%s' runs once per row, not once per batch: %d calls × %s (P95 %s) = %s, %s of %s's time in this phase "
            "(%d rows in %d batches)." % (_label(span), calls, _rc_t(r.get("p50") or r.get("mean")), _rc_t(r.get("p95")),
                                          _rc_t(total), _pct(total / side), SIDE_LABEL.get(role, role), ctx["rows"],
                                          ctx["batches"]),
            cost=total / (ctx["procs"] if role == GENERIC else 1), side_seconds=ctx["wall"],
            pacing=role == ctx["pacing"], evidence=["`%s`: %d calls, mean %s" % (span, calls, _rc_t(r.get("mean")))],
            change="'%s' per batch instead of per row" % _label(span),
            confirm="the call count tracks rows, not batches"))
    return out


def g4_regeneration(ctx):
    """G4: source samples generated more than once in a phase."""
    gen = _rc_row(ctx["ws"], GENERIC, "samples_generator.generate_sample")
    calls, src = gen.get("calls") or 0, ctx["sources"]
    if ctx["kind"] != "evaluation" or not calls or not src:
        return []
    ratio = calls / float(src)
    if ratio < REPEAT:
        return []
    cost = (gen.get("sum_seconds") or 0.0) / ctx["procs"]
    return [_sig(
        "G4", "sample generation on the workers",
        "Rows were generated %.1f times each during evaluation: %d generations for %d rows (%s each, %s per worker "
        "process)." % (ratio, calls, src, _rc_t(gen.get("mean")), _rc_t(cost)),
        cost=cost, side_seconds=ctx["wall"], pacing=ctx["pacing"] == GENERIC,
        evidence=["`samples_generator.generate_sample`: %d calls; rows to evaluate: %d" % (calls, src)],
        change="generate each row once",
        confirm="generations per source sample stay above 1")]


def g4_rows_per_source(ctx):
    """G4/G3: the model runs once per derived row although the rows share a source sample."""
    src, rows = ctx["source_samples"], ctx["sources"]
    if not src or not rows or rows < REPEAT * src:
        return []
    ws = ctx["ws"]
    if ctx["kind"] == "evaluation":
        fwd = _rc_prefixed(ws, CONSUMER, "trainer.validation_step.forward.")
        calls = sum(r.get("calls") or 0 for _, r in fwd)
        total = sum(r.get("sum_seconds") or 0.0 for _, r in fwd)
        where, change = "evaluation forward pass", ("run the model once per source sample and derive its instance "
                                                     "rows from that pass")
    elif ctx["kind"] == "visualization":
        r = _rc_row(ws, CONSUMER, "visualization.forward")
        calls, total = r.get("calls") or 0, r.get("sum_seconds") or 0.0
        where, change = "visualization forward pass", ("run the model once per visualized source sample and reuse "
                                                        "it for the rows derived from it")
    else:
        return []
    per = calls / float(src)
    if not calls or per < REPEAT:
        return []
    mean = total / calls
    extra = max(0, calls - src)
    derived = ("the %d rows derived from those samples" % (rows - src)) if ctx["kind"] == "evaluation" else \
        "rows derived from the same source sample"
    return [_sig(
        "G4", "%s on the evaluation pod" % where,
        "The model ran %d times for %d source samples (%.1f per source sample) in this phase: %s ran their own "
        "forward pass (%s each) instead of sharing their source's. The extra passes cost %d × %s = %s, %s of the "
        "phase." % (calls, src, per, derived, _rc_t(mean), extra, _rc_t(mean), _rc_t(extra * mean),
                    _pct(extra * mean / ctx["wall"])),
        cost=extra * mean, side_seconds=ctx["wall"], pacing=ctx["pacing"] == CONSUMER,
        evidence=["forward passes %d (%s); source samples %d (one instance-metrics batch each); rows %d" % (
            calls, _rc_t(total), src, rows)],
        change=change, confirm="forward passes per source sample stay above 1")]


def g4_store_loads(ctx):
    """G4: the same stored latent space loaded again inside one phase."""
    recs = [r for r in _records(ctx["eng"], "ls_reload", ctx["t0"], ctx["t1"]) if r.get("duration_seconds") is not None]
    if len(recs) < 3:
        return []
    by = {}
    for r in recs:
        by.setdefault(r.get("ls_type") or "?", []).append(r)
    repeats = sum(len(v) - 1 for v in by.values())
    if repeats < 2:
        return []
    total = sum(r["duration_seconds"] for r in recs)
    extra = sum(sum(x["duration_seconds"] for x in v[1:]) for v in by.values())
    callers = {}
    for r in recs:
        k = r.get("caller_span") or "outside any timed step"
        callers[k] = callers.get(k, 0) + 1
    return [_sig(
        "G4", "latent-space store loads on the evaluation pod",
        "The stored latent space was loaded %d times for %d distinct latent space(s) in this phase (%s in total, "
        "%s of it repeated loads): each caller reads the same data again." % (len(recs), len(by), _rc_t(total),
                                                                           _rc_t(extra)),
        cost=extra, side_seconds=ctx["wall"], pacing=ctx["pacing"] == CONSUMER,
        evidence=["loads by caller: " + ", ".join("%s ×%d" % (_label(k), v) for k, v in
                                                  sorted(callers.items(), key=lambda kv: -kv[1])[:4])],
        change="load each latent space once per phase and share it across its callers",
        confirm="loads per latent space stay above 1")]


STARTUP_SECONDS = 5.0      # a worker's own load this long, or ...
STARTUP_GB = 2.0           # ... this much memory after it, repeated per process is worth a signal


def g4_startup(ctx):
    """G4/G3: every worker process loads the integration on its own."""
    ready = _records(ctx["eng"], "worker_ready")
    loads = _records(ctx["eng"], "worker_code_load")
    costs = [r["seconds"] for r in ready]
    peaks = [r["peak_rss_gb"] for r in loads if r.get("peak_rss_gb")]
    n = max(len(costs), len(peaks))
    slow = bool(costs) and percentile(costs, 50) >= STARTUP_SECONDS
    heavy = bool(peaks) and _median(peaks) >= STARTUP_GB
    if n < 2 or not (slow or heavy):
        return []
    late = [r for r in ready if ctx.get("e0") and r["t"] > ctx["e0"] + 60]
    raw = _median([r.get("raw_lines") or 0 for r in ready]) if ready else 0
    parts = []
    if costs:
        parts.append("%s each to load (P50; slowest %s; %s in all)" % (
            _rc_t(percentile(costs, 50)), _rc_t(max(costs)), _rc_t(sum(costs))))
    if peaks:
        parts.append("%.1f GB each once loaded" % _median(peaks))
    return [_sig(
        "G4", "worker start-up (your integration's import and preprocess)",
        "%d worker processes each loaded your integration on their own: %s%s." % (
            n, ", ".join(parts), "; %d of them started after the evaluation began and repeated it mid-run" % len(late)
            if late else ""),
        cost=min(max(costs), ctx["wall"]) if costs else None, side_seconds=ctx["wall"], pacing=True,
        evidence=["per-process load: " + ", ".join(_rc_t(c) for c in sorted(costs, reverse=True)[:6]) if costs else None,
                  "%d line(s) of the integration's own output during each load (median)" % raw if raw else None],
        change="load the integration once per machine and share it across worker processes",
        confirm="one full load per new worker process", how="measured" if costs else "inferred")]


LOOP_STEP = "trainer.validation_step"


def _loop_parts(ws):
    """The evaluation step's own parts on the evaluation pod: (name, row), forward paths summed."""
    parts = [(k, r) for k, r in _rc_prefixed(ws, CONSUMER, LOOP_STEP + ".") if not k.startswith("forward.")
             and not is_counter(LOOP_STEP + "." + k)]
    fwd = _rc_prefixed(ws, CONSUMER, LOOP_STEP + ".forward.")
    if fwd:
        parts.append(("forward", {"sum_seconds": sum(r.get("sum_seconds") or 0.0 for _, r in fwd),
                                  "calls": sum(r.get("calls") or 0 for _, r in fwd)}))
    elif _rc_row(ws, CONSUMER, "trainer.infer"):
        parts.append(("forward", _rc_row(ws, CONSUMER, "trainer.infer")))
    for _, r in parts:
        if r.get("calls") and r.get("mean") is None:
            r["mean"] = (r.get("sum_seconds") or 0.0) / r["calls"]
    return sorted(parts, key=lambda kv: -(kv[1].get("sum_seconds") or 0.0))


def g3_step_overhead(ctx):
    """G3: per-step work around the model dominates when each step carries few rows."""
    ws, steps = ctx["ws"], ctx["batches"]
    if ctx["kind"] != "evaluation" or steps < 50:
        return []
    parts = dict(_loop_parts(ws))
    fwd = (parts.get("forward") or {}).get("sum_seconds") or 0.0
    around = {k: r for k, r in parts.items() if k != "forward" and not _is_wait(k)}
    other = sum(r.get("sum_seconds") or 0.0 for r in around.values())
    if not around or other < 2 * fwd or other < 0.3 * ctx["wall"]:
        return []
    top, r = max(around.items(), key=lambda kv: kv[1].get("sum_seconds") or 0.0)
    return [_sig(
        "G3", "per-step work around the model on the evaluation pod",
        "Each evaluation step carries %.1f row(s); the work around the model costs %s per step against %s for the "
        "forward pass: %d steps × %s = %s, %s of the phase. The largest part is '%s' (%s per step)." % (
            ctx["rows_per_batch"], _rc_t(other / steps), _rc_t(fwd / steps), steps, _rc_t(other / steps), _rc_t(other),
            _pct(other / ctx["wall"]), _label(LOOP_STEP + "." + top), _rc_t(r.get("mean"))),
        cost=other, side_seconds=ctx["wall"], pacing=ctx["pacing"] == CONSUMER,
        evidence=["%s: %s" % (_label(LOOP_STEP + "." + k), _rc_t(v.get("sum_seconds"))) for k, v in
                  sorted(around.items(), key=lambda kv: -(kv[1].get("sum_seconds") or 0.0))[:4]],
        change="the per-step work around the model (largest: '%s'): more rows per step, or cheaper per-step "
               "handling" % _label(LOOP_STEP + "." + top),
        confirm="per-step work stays several times the forward pass")]


def _pod_presence(ctx):
    """First and last line of each worker pod (whole run)."""
    seen = {}
    for r in ctx["tables"].get("spans") or []:
        if r["role"] == GENERIC:
            a, b = seen.get(r["pod"], (r["t"], r["t"]))
            seen[r["pod"]] = (min(a, r["t"]), max(b, r["t"]))
    return seen


def g4_replaced_workers(ctx):
    """G4: a worker pod replaced during the evaluation; the replacement repeats the start-up."""
    if ctx["kind"] != "evaluation":
        return []
    seen = _pod_presence(ctx)
    gone = sorted((b, p) for p, (a, b) in seen.items() if ctx["t0"] < b < ctx["t1"] - 30)
    ready = {r["pod"]: r for r in _records(ctx["eng"], "worker_ready")}
    new = sorted((a, p) for p, (a, b) in seen.items() if ctx["t0"] + 30 < a < ctx["t1"])
    if not gone or not new:
        return []
    out, taken = [], set()
    loads = {r["pod"]: r for r in _records(ctx["eng"], "worker_code_load")}
    resizes = _records(ctx["eng"], "worker_memory_resize")
    for end, old in gone:
        # its replacement: the next pod that appears after it stops
        nxt = next(((a, p) for a, p in new if p != old and p not in taken and a >= end - 60), None)
        if nxt is None:
            continue
        start, pod = nxt
        taken.add(pod)
        rd = ready.get(pod)
        load = rd["seconds"] if rd else None
        back = (rd["t"] if rd else start)
        lost = max(0.0, back - end)
        waits = sum(r["sum"] or 0.0 for r in ctx["tables"].get("spans") or []
                    if r["role"] == CONSUMER and end <= r["t"] <= back + 60 and
                    r["span"] in ("trainer.validation_step.pull_batch", "etl.wait_for_sample"))
        rz = next((r for r in reversed(resizes) if r["t"] <= end + 5), None)
        out.append(_sig(
            "G4", "worker pods replaced during the evaluation",
            "A worker pod stopped %s into the evaluation and its replacement loaded your integration again%s%s: no "
            "replacement was producing for %s, and the evaluation waited %s for samples meanwhile%s." % (
                _rc_t(end - ctx["t0"]), " (%s" % _rc_t(load) if load else "",
                ", %.1f GB)" % loads[pod]["peak_rss_gb"] if load and pod in loads and loads[pod].get("peak_rss_gb")
                else (")" if load else ""), _rc_t(lost), _rc_t(waits),
                "; just before, the engine raised the workers' memory setting (%s → %s GB, after a worker reached "
                "%.1f GB)" % (rz.get("current_stored_memory_limit_gb"), rz.get("calculated_memory_limit_gb"),
                              rz.get("current_memory_gb") or 0) if rz else ""),
            cost=waits or lost, side_seconds=ctx["wall"], pacing=True,
            evidence=["%s: last line at +%s; %s: first line at +%s%s" % (
                old[-5:], _rc_t(end - ctx["t0"]), pod[-5:], _rc_t(start - ctx["t0"]),
                ", ready after %s" % _rc_t(load) if load else "")],
            change="resizing workers without restarting them, or without repeating the integration's load",
            confirm="pods replaced mid-run, each followed by a full load"))
    return out


def g1_metrics_handoff(ctx):
    """G1/G6: the evaluation waits on the metrics hand-off — the hand-off threads, or the metrics
    workers behind a full queue."""
    if ctx["kind"] != "evaluation":
        return []
    ws = ctx["ws"]
    loop = _rc_sum(ws, CONSUMER, "trainer.validation_step.push_metrics")
    if loop < 0.1 * ctx["wall"]:
        return []
    full = _rc_sum(ws, CONSUMER, "redis.metrics_queue.queue_full_wait")
    work = _rc_sum(ws, CONSUMER, "redis.metrics_queue.push") + _rc_sum(ws, CONSUMER, "blob_store.metrics.write")
    seen = _pod_presence(ctx)
    present = sum(max(0.0, min(b, ctx["t1"]) - max(a, ctx["t0"])) for a, b in seen.values())
    per_proc = ctx["procs"] / max(1, len(ctx["gen_pods"]))
    busy_m = _rc_sum(ws, GENERIC, "generic_processor.iteration.metrics")
    busy_all = sum(r.get("sum_seconds") or 0.0 for k, r in ws.items() if r["role"] == GENERIC and
                   r["component"].startswith("generic_processor.iteration.") and not r["component"].endswith(".empty"))
    avail = present * per_proc or ctx["procs"] * ctx["wall"]
    if full >= work:
        st = ("The evaluation spent %s of the loop handing off metrics (%s per step) because the metrics queue was "
              "full: the hand-off threads waited %s for room. The workers that drain it were busy %s of their time "
              "(metrics %s), so they took metrics off the queue slower than the evaluation produced them." % (
                  _pct(loop / ctx["wall"]), _rc_t(loop / max(1, ctx["batches"])), _rc_t(full),
                  _pct(busy_all / avail), _pct(busy_m / avail)))
        change = "the rate the workers take metrics off the queue: %s" % (
            "they are mostly not busy, so their scheduling between samples and metrics"
            if busy_all / avail < 0.5 else "their metrics work")
        rule = "G1" if busy_all / avail >= 0.5 else "G6"
        pacing = busy_all / avail >= 0.5
    else:
        st = ("The evaluation spent %s of the loop handing off metrics (%s per step); the hand-off threads were busy "
              "serializing and storing (%s) while the queue had room and the workers were busy %s of their time." % (
                  _pct(loop / ctx["wall"]), _rc_t(loop / max(1, ctx["batches"])), _rc_t(work), _pct(busy_all / avail)))
        change = "the metrics hand-off on the evaluation pod: serialize and store in parallel, off the loop"
        rule, pacing = "G6", True
    return [_sig(rule, "metrics hand-off on the evaluation pod", st, cost=loop, side_seconds=ctx["wall"],
                 pacing=pacing or ctx["pacing"] == CONSUMER,
                 evidence=["hand-off on the loop %s; queue-full waits %s; serialize + store %s" % (
                     _rc_t(loop), _rc_t(full), _rc_t(work))],
                 change=change, confirm="the hand-off share of the loop stays high")]


def g4_metric_like_forward(ctx):
    """G4 (inferred): a metric or the loss costs about a forward pass per batch."""
    if ctx["kind"] != "evaluation":
        return []
    ws = ctx["ws"]
    fwd = [r for _, r in _rc_prefixed(ws, CONSUMER, "trainer.validation_step.forward.")]
    fwd_mean = (sum(r.get("sum_seconds") or 0 for r in fwd) / max(1, sum(r.get("calls") or 0 for r in fwd))
                if fwd else _rc_mean(ws, CONSUMER, "trainer.infer"))
    if not fwd_mean:
        return []
    out = []
    items = [("metric '%s'" % k, r) for k, r in _rc_prefixed(ws, GENERIC, "metrics.user_metric.")] + \
            [("instance metric '%s'" % k, r) for k, r in _rc_prefixed(ws, GENERIC, "metrics.user_instance_metric.")]
    loss = _rc_row(ws, GENERIC, "metrics.loss")
    if loss.get("calls"):
        items.append(("the loss", loss))
    for name, r in items:
        mean, total = r.get("mean") or 0.0, r.get("sum_seconds") or 0.0
        if mean >= LIKE_FORWARD * fwd_mean and total >= SIG_MIN_SHARE * ctx["wall"]:
            out.append(_sig(
                "G4", "%s on the generic workers" % name,
                "%s takes %s per batch on the workers, %.1f× the model's forward pass on the evaluation pod (%s): it "
                "likely runs the model again on inputs the evaluation already ran." % (
                    name[0].upper() + name[1:], _rc_t(mean), mean / fwd_mean, _rc_t(fwd_mean)),
                cost=total / ctx["procs"], side_seconds=ctx["wall"], pacing=ctx["pacing"] == GENERIC,
                evidence=["%s: %d calls × %s" % (name, r.get("calls") or 0, _rc_t(mean))],
                change="integration: compute %s from the predictions the evaluation already has" % name,
                confirm="%s per batch stays close to a forward pass" % name, how="inferred"))
    return out


def g4_payload_twice(ctx):
    """G4: the metrics hand-off carries the inputs again."""
    if ctx["kind"] != "evaluation":
        return []
    rec = (_records(ctx["eng"], "metrics_payload") or [None])[0]
    if not rec or not rec.get("total_bytes"):
        return []
    total = rec["total_bytes"]
    inputs = max(rec.get("field.inputs_bytes") or 0, rec.get("field.batch") or 0)
    if inputs < 0.5 * total or total < 1e6:
        return []
    push = _rc_row(ctx["ws"], CONSUMER, "trainer.validation_step.push_metrics")
    return [_sig(
        "G4", "metrics hand-off on the evaluation pod",
        "Each batch's metrics hand-off carries its inputs again: %.1f of %.1f MB per batch are the inputs the "
        "workers already produced, serialized and stored a second time (%s of hand-off time in this phase)." % (
            inputs / 1e6, total / 1e6, _rc_t(push.get("sum_seconds"))),
        cost=push.get("sum_seconds"), side_seconds=ctx["wall"], pacing=ctx["pacing"] == CONSUMER,
        evidence=["metrics element: %d bytes, inputs %d bytes" % (total, inputs)],
        change="pass the workers a reference to the inputs they already stored instead of the inputs",
        confirm="the hand-off's size tracks the input size")]


def g5_memory(ctx):
    """G5: a pod's memory keeps rising through the phase."""
    out = []
    recs = _records(ctx["eng"], "resources", ctx["t0"], ctx["t1"])
    by = {}
    for r in recs:
        gb = r.get("container_memory_gb") if r.get("container_memory_gb") is not None else r.get("rss_gb")
        if gb is not None:
            by.setdefault(r["pod"], []).append((r["t"], gb, r.get("container_memory_limit_gb")))
    if not any(pod_role(p) == CONSUMER for p in by):
        probes = [r for r in _records(ctx["eng"], "rss_probe", ctx["t0"], ctx["t1"]) if r.get("rss_gb") is not None]
        for r in probes:
            by.setdefault(r["pod"], []).append((r["t"], r["rss_gb"], None))
    probes = [r for r in _records(ctx["eng"], "rss_probe", ctx["t0"], ctx["t1"]) if "evaluated_samples" in r]
    for pod, pts in by.items():
        pts.sort()
        if len(pts) < 4:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        slope, r = _fit(xs, ys)
        limit = max([p[2] or 0 for p in pts]) or None
        rise = ys[-1] - ys[0]
        if slope is None or r is None or r < GROWTH_MIN_R or rise < GROWTH_MIN_GB or \
                (limit and rise < GROWTH_MIN_SHARE * limit):
            continue
        per_rows = None
        pr = [p for p in probes if p.get("rss_gb") is not None]
        if len(pr) >= 3 and pod_role(pod) == CONSUMER:
            rs, _ = _fit([p["evaluated_samples"] for p in pr], [p["rss_gb"] for p in pr])
            per_rows = 1000 * rs if rs and rs > 0 else None
        left = (limit - ys[-1]) / slope if limit and slope > 0 else None
        out.append(_sig(
            "G5", "memory of %s" % ("the evaluation pod" if pod_role(pod) == CONSUMER else "a %s pod" % pod_role(pod)),
            "Memory rose steadily from %.1f to %.1f GB over %s (%.2f GB/min%s)%s." % (
                ys[0], ys[-1], _rc_t(xs[-1] - xs[0]), 60 * slope,
                ", %.2f GB per 1,000 rows" % per_rows if per_rows else "",
                "; at this slope it reaches its %.0f GB limit in about %s" % (limit, _rc_t(left)) if left else ""),
            cost=None, side_seconds=ctx["wall"], pacing=pod_role(pod) == ctx["pacing"],
            evidence=["%s: %d readings, correlation with time %.2f" % (pod, len(pts), r)],
            change="what the %s keeps per row (memory that is not returned after each batch)" % (
                "evaluation pod" if pod_role(pod) == CONSUMER else "worker"),
            confirm="memory keeps rising with rows while the pace stays flat"))
    return out


def g5_wait_growth(ctx):
    """G5: a wait whose cost per call keeps rising through the phase."""
    out = []
    rows = [r for r in ctx["tables"].get("spans") or [] if r["aggregate"] and ctx["t0"] <= r["t"] <= ctx["t1"] + 1
            and _is_wait(r["span"]) and r["n"]]
    by = {}
    for r in rows:
        by.setdefault((r["role"], r["span"]), []).append(r)
    for (role, span), rs in by.items():
        if len(rs) < 6:
            continue
        rs.sort(key=lambda r: r["t"])
        k = len(rs) // 3
        mean = lambda xs: sum(x["sum"] or 0 for x in xs) / max(1, sum(x["n"] for x in xs))
        first, last = mean(rs[:k]), mean(rs[-k:])
        total = sum(x["sum"] or 0 for x in rs)
        if first > 0 and last >= WAIT_GROWTH * first and total >= SIG_MIN_SHARE * _side_seconds(ctx, role):
            out.append(_sig(
                "G5", "%s (%s)" % (_label(span), SIDE_LABEL.get(role, role)),
                "'%s' grew from %s to %s per call through the phase (%s in total): what it waits on falls further "
                "behind as the run goes on." % (_label(span), _rc_t(first), _rc_t(last), _rc_t(total)),
                cost=total / (ctx["procs"] if role == GENERIC else 1), side_seconds=ctx["wall"],
                pacing=role == ctx["pacing"], evidence=["`%s`: %d windows" % (span, len(rs))],
                change="what '%s' waits on" % _label(span), confirm="the wait per call keeps rising"))
    return out


def g6_cores(ctx):
    """G6: the pacing side works on about one core of a larger limit."""
    role = ctx["pacing"]
    if role not in (CONSUMER, GENERIC):
        return []
    recs = [r for r in _records(ctx["eng"], "resources", ctx["t0"], ctx["t1"])
            if pod_role(r["pod"]) == role and r.get("cores_used") is not None and r.get("cpu_limit_cores")]
    if len(recs) < 2:
        return []
    cores = _median([r.get("container_cores_used") or r["cores_used"] for r in recs])
    limit = _median([r["cpu_limit_cores"] for r in recs])
    if cores > SERIAL_CORES or limit < SERIAL_LIMIT:
        return []
    return [_sig(
        "G6", "%s's CPU" % SIDE_LABEL[role],
        "%s set the pace while each pod used %.1f of its %.0f cores (median over %d readings): the work runs one "
        "step at a time while the rest of the CPU sits idle." % (SIDE_LABEL[role][0].upper() + SIDE_LABEL[role][1:],
                                                                 cores, limit, len(recs)),
        cost=None, side_seconds=ctx["wall"], pacing=True,
        evidence=["cores used: median %.2f; CPU limit %.0f" % (cores, limit)],
        change="run this side's per-batch work in parallel (more processes or threads per pod)",
        confirm="cores used stay near one while that side sets the pace")]


def g6_imbalance(ctx):
    """G6: one worker pod is saturated while other pods present in the phase have little to do."""
    busy, seen = {}, {}
    for r in ctx["tables"].get("spans") or []:
        if r["role"] != GENERIC or not (ctx["t0"] <= r["t"] <= ctx["t1"] + 1):
            continue
        a, b = seen.get(r["pod"], (r["t"], r["t"]))
        seen[r["pod"]] = (min(a, r["t"] - (r.get("window") or 0.0)), max(b, r["t"]))
        if r["span"].startswith("generic_processor.iteration.") and not r["span"].endswith((".empty", "_NOT_SECONDS")):
            busy[r["pod"]] = busy.get(r["pod"], 0.0) + (r["sum"] or 0.0)
    present = [p for p, (a, b) in seen.items() if min(b, ctx["t1"]) - max(a, ctx["t0"]) >= 0.5 * ctx["wall"]]
    if len(present) < 2 or not busy:
        return []
    top, secs = max(((p, busy.get(p, 0.0)) for p in present), key=lambda kv: kv[1])
    others = [busy.get(p, 0.0) for p in present if p != top]
    total = secs + sum(others)
    per_proc = ctx["procs"] / max(1, len(ctx["gen_pods"]))
    if secs < IMBALANCE * total or secs < IMBALANCE * ctx["wall"] * per_proc:
        return []
    return [_sig(
        "G6", "work spread across the worker pods",
        "One worker pod was busy %s of the phase (%s) while %d other pod(s) present for most of it were busy %s on "
        "average." % (_pct(secs / (ctx["wall"] * per_proc)), _rc_t(secs), len(others),
                      _pct(sum(others) / len(others) / (ctx["wall"] * per_proc))),
        cost=(secs - total / len(present)) / per_proc, side_seconds=ctx["wall"], pacing=ctx["pacing"] == GENERIC,
        evidence=["busy time by pod: " + ", ".join("%s %s" % (p[-5:], _rc_t(busy.get(p, 0.0))) for p in present[:6])],
        change="how this phase's work is handed out to worker pods",
        confirm="one pod stays saturated while the others idle")]


def _worker_idle_share(ctx):
    """Share of the workers' time spent idle in the phase. The idle wait spans the empty polls
    between productive iterations, so it is used alone when present."""
    ws = ctx["ws"]
    idle = _rc_sum(ws, GENERIC, "generic_processor.idle_wait") or _rc_sum(ws, GENERIC, "generic_processor.iteration.empty")
    return min(1.0, idle / (ctx["procs"] * ctx["wall"]))


def g6_handoff(ctx):
    """G6: the evaluation pod hands data over on its own thread while the receivers are idle."""
    ws = ctx["ws"]
    spans = [s for s in HANDOFF_SPANS if _rc_row(ws, CONSUMER, s)]
    if not spans:
        return []
    span = max(spans, key=lambda s: _rc_sum(ws, CONSUMER, s))
    secs = _rc_sum(ws, CONSUMER, span)
    idle_share = _worker_idle_share(ctx)
    sync = _rc_mean(ws, CONSUMER, "redis.vis_queue.sync_push_NOT_SECONDS")
    if secs < HANDOFF_SHARE * ctx["wall"] or idle_share < RECEIVER_IDLE:
        return []
    return [_sig(
        "G6", "%s on the evaluation pod" % _label(span),
        "The evaluation pod spent %s of the phase handing data to the workers ('%s', %s per call) on its own "
        "thread, while the workers were idle %s of the time%s." % (
            _pct(secs / ctx["wall"]), _label(span), _rc_t(_rc_mean(ws, CONSUMER, span)), _pct(idle_share),
            "; %s of the pushes were synchronous" % _pct(sync) if sync is not None else ""),
        cost=secs, side_seconds=ctx["wall"], pacing=ctx["pacing"] == CONSUMER,
        evidence=["`%s`: %s in the phase; workers idle %s" % (span, _rc_t(secs), _pct(idle_share))],
        change="serialize and push off the evaluation pod's main thread, or more of it in parallel",
        confirm="the hand-off share stays high while the workers idle")]


DETECTORS = {
    "evaluation": (g1_metrics_handoff, g3_step_overhead, g3_per_row, g4_replaced_workers, g4_regeneration, g4_rows_per_source, g4_metric_like_forward, g4_payload_twice,
                   g5_memory, g5_wait_growth, g6_cores, g6_imbalance, g6_handoff),
    "visualization": (g4_rows_per_source, g3_per_row, g4_store_loads, g5_memory, g5_wait_growth, g6_cores, g6_imbalance, g6_handoff),
    "start-up": (g4_startup,),
}


def mechanism_signals(tables, eng, view, stage, kind, pacing_role, children, e0=None, e1=None):
    """Every rule that fires on one phase, costliest first."""
    ctx = _phase_ctx(tables, eng, view, stage, kind, pacing_role, children)
    ctx["e0"], ctx["e1"] = e0, e1
    out = []
    for det in DETECTORS.get(kind, ()):
        try:
            out += det(ctx)
        except Exception as exc:          # a rule must never take the analysis down
            out.append({"rule": "-", "rule_name": det.__name__, "error": str(exc)})
    out = [s for s in out if s.get("error") or s.get("cost_seconds") is None or
           (s.get("share") or 0) >= SIG_MIN_SHARE or (s["rule"] == "G4" and s["cost_seconds"] >= 1.0)]
    if pacing_role is None:              # the pace-setting side is not known for this phase
        for s in out:
            if s.get("on_pacing_side") is False:
                s["on_pacing_side"] = None
    return sorted(out, key=lambda s: (not s.get("on_pacing_side"), -(s.get("cost_seconds") or 0)))


PACING_ROLE = (("the evaluation pod", CONSUMER), ("the generic workers", GENERIC), ("the metrics on the generic", GENERIC),
               ("the result writers", "streaming-handler"))
LIMITER_ROLE = {"inference-floor": CONSUMER, "consumer-bound": CONSUMER, "producer-bound": GENERIC,
                "metrics-bound": GENERIC}
SIGNAL_LEADS = 0.2       # a pacing-side mechanism costing this share of its phase names the part to change


def _pacing_role(pacing):
    for prefix, role in PACING_ROLE:
        if (pacing or "").startswith(prefix):
            return role
    return None


def _phase_kind(stage):
    name = stage["stage"]
    if stage.get("step") == "after_visualizations":
        return "visualization"
    if name.startswith("start-up"):
        return "start-up"
    if name == "evaluation loop":
        return "evaluation"
    if name.startswith(("post-processing", "end of job")):
        return "post-processing"
    return None


def _merged(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        elif b > a:
            out.append((a, b))
    return out


def _post_processing_spans(view, tables=None):
    """The intervals of the platform's analysis after evaluation (embeddings, insights and the
    other post-processing steps; the visualization is not one of them). The online analysis
    leaves them out: it covers start-up, evaluation and visualization. The post-processing span
    gives the exact start (the first step markers can be missing from a log's tail); the step
    markers place the visualization and the end-of-job waits."""
    tl = view.get("timeline") or []
    spans = [(s_["start"], s_["end"]) for s_ in tl if _phase_kind(s_) == "post-processing"]
    rows = (tables or {}).get("spans") or []
    pp = next((r for r in rows if r["span"] == "trainer.post_processing" and r.get("sum")), None)
    if not pp:
        return _merged(spans)
    a, b = pp["t"] - pp["sum"], pp["t"]
    vis = next(((s_["start"], s_["end"]) for s_ in tl if _phase_kind(s_) == "visualization"), None)
    if vis is None:
        v = next((r for r in rows if r["span"] == "visualization.calculate_and_upload_visualizers" and r.get("sum")), None)
        vis = (v["t"] - v["sum"], v["t"]) if v else None
    whole = [(a, b)] if vis is None else [(a, min(b, vis[0])), (max(a, vis[1]), b)]
    return _merged(whole + spans)


def _scoped_engine(eng, skip):
    """The engine's warnings, events and uploads outside the `skip` intervals."""
    if not skip:
        return eng
    out = dict(eng)
    warnings = []
    for w in eng.get("warnings") or []:
        times = w.get("times")
        if times:
            kept = [tw for tw in times if not _inside(tw[0], skip)]
            if not kept:
                continue
            total = sum(n for _, n in times)
            n_kept = sum(n for _, n in kept)
            count = n_kept if total == w["count"] else max(1, int(round(w["count"] * n_kept / float(total))))
            warnings.append(dict(w, times=kept, count=count, first=kept[0][0], last=kept[-1][0]))
        elif not (_inside(w["first"], skip) and _inside(w["last"], skip)):
            warnings.append(w)
    out["warnings"] = warnings
    events = {}
    for key, e in (eng.get("events") or {}).items():
        recs = e.get("records") or []
        if not recs:
            events[key] = e
            continue
        kept = [r for r in recs if not _inside(r["t"], skip)]
        if not kept:
            continue
        count = len(kept) if len(recs) == e["count"] else max(1, int(round(e["count"] * len(kept) / float(len(recs)))))
        pods = sorted({r["pod"] for r in kept if r.get("pod")}) or e.get("pods") or []
        events[key] = dict(e, records=kept, count=count, pods=pods, first=kept[0]["t"], last=kept[-1]["t"])
    out["events"] = events
    out["uploads"] = [u for u in eng.get("uploads") or [] if not _inside(u["t"], skip)]
    return out


def _rc_from_signals(stage, sigs):
    """A phase no specific chain covers: the costliest mechanism the rules found in it."""
    top = next((s_ for s_ in sigs if s_.get("cost_seconds")), None)
    if not top:
        return None
    wall = stage["seconds"]
    return _rc_chain(stage["stage"], wall, "not measured for this phase", [_rc_part(top["subject"], top["cost_seconds"])],
                     top["subject"], top["statement"], [top.get("change")], [], [top.get("confirm")], top["evidence"],
                     link="inferred")


def _attach_signals(rc, sigs):
    """The rules' mechanisms join the chain: pacing-side ones that cost enough name the part
    to change; the rest are listed with their cost."""
    sigs = [s_ for s_ in sigs if not s_.get("error")]
    rc["mechanisms"] = (rc.get("mechanisms") or []) + sigs
    wall = rc.get("wall_seconds") or 0.0
    lead = [s_ for s_ in rc["mechanisms"] if s_.get("on_pacing_side") and s_.get("change") and
            (s_.get("cost_seconds") is None or (wall and s_["cost_seconds"] >= SIGNAL_LEADS * wall))]
    for s_ in lead:
        if s_["change"] not in rc["optimize"]:
            rc["optimize"].append(s_["change"])
        if s_.get("confirm") and s_["confirm"] not in rc["to_confirm"]:
            rc["to_confirm"].append(s_["confirm"])
    return rc


MEMORY_WARNINGS = ("memory",)       # server or engine warnings about memory on this run


def memory_pressure(memory, eng, server_warnings, offline):
    """Is memory a problem on this run? Any of: a pod killed for memory, a pod's peak at
    NEAR_LIMIT of its limit, a memory warning from the server or the engine, or the offline
    memory check at AMBER or RED. Memory leads the analysis only then."""
    reasons = []

    def label(pod):
        return "the evaluation pod" if pod_role(pod) == CONSUMER else "worker pod %s" % pod[-5:]

    peaks = {}
    for pod, v in (memory.get("pods") or {}).items():
        if v.get("oom") or v.get("last_state_reason") == "OOMKilled":
            reasons.append("%s was killed for exceeding its memory" % label(pod))
        if v.get("peak_rss_gb") and v.get("limit_gb"):
            peaks[pod] = (v["peak_rss_gb"], v["limit_gb"])
    for r in ((eng.get("events") or {}).get("resources") or {}).get("records") or []:
        gb, lim = r.get("container_memory_gb"), r.get("container_memory_limit_gb")
        if gb and lim and gb > peaks.get(r["pod"], (0, 0))[0]:
            peaks[r["pod"]] = (gb, lim)
    for pod, (gb, lim) in sorted(peaks.items()):
        if gb >= NEAR_LIMIT * lim:
            reasons.append("%s peaked at %.1f of %.1f GB (%s)" % (label(pod), gb, lim, _pct(gb / lim)))
    for w in server_warnings or []:
        if any(k in w.lower() for k in MEMORY_WARNINGS):
            reasons.append("the server warned: %s" % _clip(w, 160))
    for w in eng.get("warnings") or []:
        if "memory leak" in w["message"].lower() or "out of memory" in w["message"].lower():
            reasons.append("the engine warned: %s (×%d)" % (_clip(w["message"], 120), w["count"]))
    st = offline.get("memory_status")
    if st in ("AMBER", "RED"):
        reasons.append("the offline memory check is %s%s" % (st, (": " + offline["memory_reasons"][0])
                                                              if offline.get("memory_reasons") else ""))
    reasons = list(dict.fromkeys(reasons))
    return {"pressure": bool(reasons), "reasons": reasons}


def build_root_causes(tables, view, eng, job_s, limiter, e0, e1, children, gpus):
    """One chain per phase that took at least RC_MIN_SHARE of the run, slowest first, each with
    the mechanisms the generic rules found in it. Mechanisms in shorter phases are kept in a
    run-wide list. Only start-up, evaluation and visualization: the platform's analysis after
    evaluation (post-processing) is not analyzed."""
    out, elsewhere = [], []
    for stage in view.get("timeline") or []:
        kind = _phase_kind(stage)
        if kind == "post-processing":
            continue
        name = stage["stage"]
        big = job_s and stage["seconds"] >= RC_MIN_SHARE * job_s
        rc = None
        if big:
            try:
                if kind == "visualization":
                    rc = _rc_visualization(tables, stage, children, gpus)
                elif kind == "start-up":
                    rc = _rc_startup(tables, stage, eng)
                elif kind == "evaluation":
                    rc = _rc_evaluation(tables, e0, e1, limiter, children)
            except Exception as exc:          # a chain must never take the analysis down
                rc = {"phase": name, "error": str(exc)}
        role = _pacing_role((rc or {}).get("pacing"))
        if role is None and kind == "evaluation" and limiter:
            role = LIMITER_ROLE.get(limiter["id"])
        sigs = mechanism_signals(tables, eng, view, stage, kind, role, children, e0, e1) \
            if kind and stage["seconds"] > 0 else []
        if rc and not rc.get("error"):
            _attach_signals(rc, sigs)
        elif big and not rc:
            rc = _rc_from_signals(stage, sigs)
            if rc:
                _attach_signals(rc, sigs[1:])
        if not big:
            elsewhere += [dict(s_, phase=name) for s_ in sigs if not s_.get("error")]
        if rc:
            rc["share_of_run"] = stage["seconds"] / job_s
            rc["step"] = stage.get("step")
            out.append(rc)
    out.sort(key=lambda r: -(r.get("wall_seconds") or 0.0))
    if elsewhere:
        out.append({"kind": "other-mechanisms", "phase": "shorter phases", "mechanisms": elsewhere})
    return out


# --------------------------------------------------------------------------- #
# server settings (K30): what each pod got against what the run used, with a verdict and a
# recommendation for automatic or manual settings. Recommends only; never changes settings.
# --------------------------------------------------------------------------- #

SETTINGS_MODES = ("automatic", "manual")
CPU_BOUND = 0.9          # P95 cores used at this share of the limit, or ...
CPU_THROTTLED = 0.10     # ... CPU throttled in this share of periods: the CPU setting limited the pod
UNUSED = 0.3             # peak use under this share of what was reserved: mostly unused


def _quant(xs, q):
    return percentile(xs, q) if xs else None


def server_settings(eng, tables, view, memory, cfg, gpus, limiter, roots, settings_mode, priority="runtime", skip=None):
    recs = ((eng.get("events") or {}).get("resources") or {}).get("records") or []
    rows = []
    by_role = {}
    seen = set()
    for r in recs:
        role = pod_role(r.get("pod") or "")
        key = (r.get("pod"), int(r["t"] // 30))           # one container reading per pod per half-minute
        if key in seen:
            continue
        seen.add(key)
        by_role.setdefault(role, []).append(r)
    mode = settings_mode or "not stated"
    paced = {rc.get("pacing") for rc in roots or []}

    def advise(auto_text, manual_text):
        if mode == "automatic":
            return "Automatic settings: %s." % auto_text
        if mode == "manual":
            return "Manual settings: %s." % manual_text
        return "Automatic settings: %s. Manual settings: %s." % (auto_text, manual_text)

    for role, label in ((CONSUMER, "evaluation pod"), (GENERIC, "worker pods")):
        rs = by_role.get(role) or []
        if not rs:
            # an engine without resource readings: memory from the pods' limits and peak RSS only
            pods = [p for p in (memory.get("pods") or {}).values() if p.get("role") == role]
            lim = max([p["limit_gb"] for p in pods if p.get("limit_gb")] or [0]) or None
            peak = max([p["peak_rss_gb"] for p in pods if p.get("peak_rss_gb")] or [0]) or None
            if lim and peak:
                rs = [{"container_memory_limit_gb": lim, "container_memory_gb": peak}]
            else:
                continue
        limit = max([r["cpu_limit_cores"] for r in rs if r.get("cpu_limit_cores")] or [0]) or None
        # CPU over start-up, evaluation and visualization; memory over the whole run, since a
        # limit must hold every step of the job
        in_scope = [r for r in rs if "t" not in r or not _inside(r["t"], skip)]
        cores = [r["container_cores_used"] for r in in_scope if r.get("container_cores_used") is not None]
        thr = [r["cpu_throttled_share"] for r in in_scope if r.get("cpu_throttled_share") is not None]
        p95, peak_cores = _quant(cores, 95), max(cores) if cores else None
        thr_max = max(thr) if thr else None
        used = "P50 %s, P95 %s cores%s" % (
            "%.2f" % _quant(cores, 50) if cores else "-", "%.2f" % p95 if p95 is not None else "-",
            "; throttled up to %s of CPU periods" % _pct(thr_max) if thr_max else "")
        if limit and ((p95 is not None and p95 >= CPU_BOUND * limit) or (thr_max or 0) >= CPU_THROTTLED):
            verdict = "limited the run" if (label == "worker pods" and "the generic workers" in paced) or \
                (label == "evaluation pod" and "the evaluation pod" in paced) else "at its limit"
            rec = advise("the CPU sized for the %s was below what it needed" % label,
                         "raise the %s CPU limit above %.0f cores" % (label, limit))
        elif limit and p95 is not None and p95 <= UNUSED * limit:
            verdict = "mostly unused"
            rec = advise("%.0f cores were sized for the %s and it used %.2f at P95" % (limit, label, p95),
                         "lower the %s CPU toward %.1f cores, or give the cores to the side that sets the pace" % (
                             label, max(1.0, 1.5 * p95)))
        else:
            verdict, rec = "fits", ""
        if cores or limit:
            rows.append({"setting": "%s CPU" % label, "got": "%.1f cores" % limit if limit else "no limit",
                         "used": used, "verdict": verdict, "recommendation": rec})
        mem_lim = max([r["container_memory_limit_gb"] for r in rs if r.get("container_memory_limit_gb")] or [0]) or None
        mem_peak = max([r["container_memory_gb"] for r in rs if r.get("container_memory_gb") is not None] or [0]) or None
        if mem_lim and mem_peak:
            if mem_peak >= NEAR_LIMIT * mem_lim:
                verdict, rec = "near its limit", advise("the memory sized for the %s left little headroom" % label,
                                                         "raise the %s memory above %.1f GB" % (label, mem_peak * 1.25))
            elif mem_peak <= UNUSED * mem_lim:
                verdict = "mostly unused"
                rec = advise("%.1f GB were sized for the %s and it peaked at %.1f GB" % (mem_lim, label, mem_peak),
                             "lower the %s memory toward %.1f GB (reserved memory blocks other jobs)" % (label, mem_peak * 1.5))
            else:
                verdict, rec = "fits", ""
            rows.append({"setting": "%s memory" % label, "got": "%.1f GB" % mem_lim,
                         "used": "peak %.1f GB" % mem_peak, "verdict": verdict, "recommendation": rec})
    workers = [p for p in (memory.get("pods") or {}).values() if p.get("role") == GENERIC]
    children = sorted(set((cfg.get("children_per_pod") or {}).values())) or [1]
    wv, wr = "fits", ""
    if "the generic workers" in paced:
        wv = "limited the run"
        wr = advise("the worker count did not grow for the phase the workers paced",
                    "more worker pods or processes for that phase")
    rows.append({"setting": "worker pods × processes", "got": "%d × %s" % (len(workers), "/".join(map(str, children))),
                 "used": "the workers set the pace of a phase" if "the generic workers" in paced else
                 "the workers did not set the pace of any phase",
                 "verdict": wv, "recommendation": wr})
    gv, gr = "fits", ""
    if gpus == 0 and ("the evaluation pod" in paced or (limiter or {}).get("id") == "inference-floor"):
        gv, gr = "limited the run", "a GPU for the evaluation pod: model inference sets the pace and runs on CPU"
    rows.append({"setting": "GPUs", "got": "-" if gpus is None else str(gpus),
                 "used": "inference on CPU" if gpus == 0 else "-", "verdict": gv, "recommendation": gr})
    rows.append({"setting": "batch size", "got": str(cfg.get("batch_size", "-")), "used": "-",
                 "verdict": "see the batch-size fit", "recommendation": ""})
    for r in rows:
        r["effect"] = {"limited the run": "runtime", "at its limit": "runtime (not on the critical path now)",
                       "near its limit": "stability (out-of-memory risk)",
                       "mostly unused": "capacity for other jobs, not runtime"}.get(r["verdict"], "-")
        if priority == "memory" and "memory" in r["setting"] and r["verdict"] in ("mostly unused", "near its limit"):
            r["effect"] = "memory (the priority): " + ("frees room for more workers or jobs"
                                                      if r["verdict"] == "mostly unused" else "out-of-memory risk")
    if priority == "memory":                      # memory rows first
        rows.sort(key=lambda r: 0 if "memory" in r["setting"] else 1)
    return {"mode": mode, "rows": rows, "readings": len(seen), "priority": priority,
            "note": "" if recs else "this server's logs carry no resource readings; settings are judged from limits only"}


def _split_server_messages(rep):
    """The run's server notifications as (errors, warnings). A notification without a level
    counts as an error."""
    msgs = rep.get("messages") or []
    levels = rep.get("levels") or [None] * len(msgs)
    errors = [m for m, lv in zip(msgs, levels) if (lv or "error").lower() not in ("warning", "info")]
    warns = [m for m, lv in zip(msgs, levels) if (lv or "").lower() == "warning"]
    return errors, warns


def _running_steps(polls):
    """The steps still running at the last poll, e.g. 'visualize_samples at 1825 of 10000'."""
    for p in reversed(polls or []):
        run = [s for s in p.get("steps") or [] if s.get("status") == "RUNNING"]
        if run:
            return ", ".join("%s at %s of %s" % (s["id"], s.get("current"), s.get("total"))
                             if s.get("total") else s["id"] for s in run)
    return ""


def analyze_collection(job_dir, tables, polls, offline, window=DEFAULT_WINDOW_SECONDS,
                       k=DEFAULT_STABLE_WINDOWS, tolerance=DEFAULT_TOLERANCE, settings_mode=None,
                       priority="runtime"):
    state = _load_json(os.path.join(job_dir, "collect.json")) or {}
    findings, notes = [], []
    j0, j1, job_src = _job_bounds(tables, polls, (tables.get("coverage") or {}).get("source", "poll"))
    job_s = (j1 - j0) if j0 is not None else None
    whole = window_stats(tables, j0, j1 + 1) if j0 is not None else {}

    # ---- phases: warm-up / stable / tail, each measured
    phases = {}
    for phase, (pts, pods, unit, src) in phase_progress(polls, tables).items():
        st = detect_stability(pts, pods, window, k, tolerance)
        st.pop("windows", None)
        st.update({"unit": unit, "source": src, "points": sorted(pts)})
        phases[phase] = st
    ev = phases.get("evaluation") or {}
    e0, e1, eval_src = _eval_bounds(phases, tables)
    if ev.get("reached"):
        w0, w1, basis = ev["stable"]["start"], ev["stable"]["end"], "stable window"
    elif e0 is not None:
        w0, w1, basis = e0, e1, "whole evaluation (no stable window)"
    else:
        w0, w1, basis = j0, j1, "whole run (evaluation phase not found)"
    stats = window_stats(tables, w0, w1, loop_end=e1) if w0 is not None else {}

    # ---- the evaluation loop: who waits on whom. One iteration = pulling the batch (a
    # separate span) + the step (inference, extraction, metrics hand-off)
    loop = _num(stats, LOOP) + _num(stats, PULL) if _num(stats, LOOP) > 0 else 0.0
    shares = {}
    if loop > 0:
        for name, key in (("waiting for samples", PULL), ("inference", INFER),
                          ("metrics hand-off", HANDOFF)) + tuple((n.split(".")[-1], n) for n in OVERHEAD):
            shares[name] = _num(stats, key) / loop
        shares["other"] = max(0.0, 1.0 - sum(shares.values()))
    gen_rows = [r for k2, r in stats.items() if r["role"] == GENERIC]
    # the worker process's own idle wait; the metrics / visualizer threads' idle periods overlap
    # it (and each other), so they are only a fallback, never summed
    proc_idle = [r["sum_seconds"] for r in gen_rows if r["component"] == "generic_processor.idle_wait"]
    idle = sum(proc_idle) if proc_idle else max([r["sum_seconds"] for r in gen_rows if "idle" in r["component"]] or [0.0])
    queue_full = sum(r["sum_seconds"] for r in gen_rows if "queue_full_wait" in r["component"])
    pods_in_window = [p for p in (tables.get("pods") or {}).values() if p.get("role") == GENERIC and
                      p.get("first") is not None and p["first"] <= w1 and (p.get("last") or w1) >= w0]
    children = list((tables.get("config") or {}).get("children_per_pod", {}).values()) or [1]
    worker_procs = max(1, len(pods_in_window)) * max(1, int(_median(children)))
    idle_share = min(1.0, idle / max((w1 - w0) * worker_procs, 1e-9)) if w0 is not None else 0.0
    queue_full_share = queue_full / max((w1 - w0) * worker_procs, 1e-9) if w0 is not None else 0.0
    gpu = tables.get("gpu") or []
    proxy = _median([g["proxy"] for g in gpu]) if gpu else None
    limiter = None
    if loop > 0:
        q = lambda key: _quote(job_dir, CONSUMER, '"span_name": "%s"' % key, w0, w1)
        common = ["evaluation loop over the %s: %.1f s of %.1f s" % (basis, loop, w1 - w0)]
        if shares["waiting for samples"] >= WAIT_HIGH:
            gen = stats.get("series:generation_per_sample") or stats.get("%s:samples_generator.generate_sample" % GENERIC)
            limiter = _finding(
                "producer-bound", "The evaluation waits for samples: sample generation limits throughput",
                "compute", "user code (sample generation: encoders, metadata)", "on", "integration",
                common + ["waiting for samples %.0f%% of the loop" % (100 * shares["waiting for samples"]),
                          "inference %.0f%% of the loop" % (100 * shares["inference"]),
                          "workers idle %.0f%% of their time" % (100 * idle_share),
                          ("generation per sample online: mean %.1f ms, P95 %s" % (
                              1000 * gen["mean"], "%.1f ms" % (1000 * gen["p95"]) if gen.get("p95") else "n/a"))
                          if gen and gen.get("mean") else None],
                "Workers produce samples slower than the model consumes them, so the model waits.",
                "Make sample generation cheaper (see the offline profile's top generation costs); a "
                "smaller per-sample cost needs fewer workers to keep the model busy.",
                lines=[q(PULL), q(LOOP)])
        elif shares["metrics hand-off"] >= HANDOFF_HIGH:
            # Per-metric lines (newer engines) say whether the user's metrics and loss are the cost.
            user_rows = [r for k, r in stats.items() if r.get("role") == GENERIC and
                         r["component"].startswith(("metrics.user_metric.", "metrics.user_instance_metric.",
                                                    "metrics.loss"))]
            n_batches = (stats.get("%s:%s" % (CONSUMER, LOOP)) or {}).get("calls") or 0
            # one call per metric (and the loss) per metrics batch: the sum of their mean call times
            user_per_batch = sum(r["mean"] or 0.0 for r in user_rows) if user_rows else None
            handoff_per_batch = (_num(stats, HANDOFF) / n_batches) if n_batches else None
            cheap = user_per_batch is not None and handoff_per_batch and user_per_batch < 0.2 * handoff_per_batch
            limiter = _finding(
                "metrics-bound", "The evaluation waits to hand off metrics: %s" % (
                    "the metrics pipeline limits throughput, not your metrics" if cheap else
                    "the metrics limit throughput"),
                "compute", "metrics pipeline (workers)" if cheap else "user code (metrics / loss)", "on",
                "tensorleap" if cheap else "integration",
                common + ["metrics hand-off %.0f%% of the loop" % (100 * shares["metrics hand-off"])] +
                (["your metrics and loss: %.3g ms per metrics batch on the workers vs %.3g ms of hand-off wait per "
                  "evaluation batch" % (1000 * user_per_batch, 1000 * handoff_per_batch)]
                 if user_per_batch is not None and handoff_per_batch else []),
                "Batches wait for the metrics workers to accept them." + (
                    " Your metrics and loss are a small part of that: the workers' metrics throughput is the limit."
                    if cheap else ""),
                "Tensorleap: metrics throughput on the workers (how many process metrics, and their share of "
                "iterations vs sample generation)." if cheap else
                "Make the custom metrics and loss cheaper per batch (vectorize; avoid per-sample loops).",
                lines=[q(HANDOFF)])
        elif shares["inference"] >= INFER_HIGH:
            close = shares["waiting for samples"] >= NEAR_WAIT
            limiter = _finding(
                "inference-floor", "Model inference is the floor: the evaluation is limited by the model" +
                (" (sample generation close behind)" if close else ""),
                "compute", "model", "on", "expected",
                common + ["inference %.0f%% of the loop" % (100 * shares["inference"]),
                          "waiting for samples %.0f%%" % (100 * shares["waiting for samples"]),
                          "workers idle %.0f%% of their time" % (100 * idle_share),
                          "GPU-utilization proxy (median over states) %.2f" % proxy if proxy is not None else None],
                "The model runs most of the time and nothing else makes it wait." + (
                    " The evaluation still waited for samples %.0f%% of the time: making the model faster would soon "
                    "hand the limit to sample generation." % (100 * shares["waiting for samples"]) if close else ""),
                "Only a faster model or hardware moves this: batch size, model export, GPU.",
                lines=[q(INFER)])
        else:
            top = max(((n, v) for n, v in shares.items() if n not in ("inference", "waiting for samples")),
                      key=lambda kv: kv[1])
            limiter = _finding(
                "consumer-bound", "The evaluation side limits throughput, outside model inference",
                "compute", "consumer", "on", "tensorleap",
                common + ["inference %.0f%%, waiting for samples %.0f%%, largest other part: %s %.0f%% of the loop" % (
                    100 * shares["inference"], 100 * shares["waiting for samples"], top[0], 100 * top[1]),
                    "workers idle %.0f%% of their time" % (100 * idle_share),
                    "workers blocked on a full queue %.0f%% of their time" % (100 * queue_full_share)
                    if queue_full_share else None],
                "The evaluation is neither waiting for samples nor running the model most of the time.",
                "Ask Tensorleap to look at the evaluation loop's non-inference work for this run.",
                lines=[q(LOOP), q(INFER)])
    elif gpu:
        notes.append("the evaluation loop's timing lines were not in the collected logs; only the "
                     "GPU-utilization proxy per state is available (reduced confidence)")
    # producer work that does not make the evaluation wait is off the critical path
    if limiter and limiter["id"] != "producer-bound":
        gen = stats.get("series:generation_per_sample")
        if gen and gen.get("mean"):
            findings.append(_finding(
                "generation-off-critical-path", "Sample generation keeps up (off the critical path)",
                "compute", "user code (sample generation)", "off", "integration",
                ["waiting for samples %.0f%% of the loop" % (100 * shares.get("waiting for samples", 0)),
                 "generation per sample mean %.1f ms over %d logged calls" % (1000 * gen["mean"], gen["calls"])],
                "Workers produce samples fast enough; making generation faster would not shorten the run "
                "while this holds.", "No action for runtime; worker count and memory may still benefit."))

    # ---- the engine's own view of the run (K28)
    eng = tables.get("engine") or parse_engine(os.path.join(job_dir, "logs"))
    ended_early = state.get("status") in ("FAILED", "TERMINATED", "STOPPED")
    view = engine_view(tables, eng, j0, j1, e0, e1, _running_ids(polls) if ended_early else None)
    # post-processing (the platform's analysis after evaluation) is not analyzed: the engine's
    # statistics, warnings and events below cover start-up, evaluation and visualization only
    eng_all, skip = eng, _post_processing_spans(view, tables)
    if skip:
        eng = _scoped_engine(eng, skip)
        view = engine_view(tables, eng, j0, j1, e0, e1, _running_ids(polls) if ended_early else None, skip=skip)
    exact = bool(view["memory_steps"])            # post-processing step markers: an exact timeline

    # ---- the job's wall time, period by period
    periods = []
    if job_s:
        if e0 is not None:
            warm = (ev.get("warmup") or {}).get("seconds") if ev.get("reached") else None
            periods.append(("start-up before evaluation", max(0.0, e0 - j0), "scheduling"))
            if warm:
                periods.append(("evaluation warm-up (workers starting)", warm, "scheduling"))
            periods.append(("evaluation", max(0.0, (e1 - e0) - (warm or 0.0)),
                            limiter["id"] if limiter else "evaluation"))
        vis_end = _marker(tables, "visualization_end")
        pp_end = _marker(tables, "post_processing_end")
        vis_s = vis_end["duration_seconds"] if vis_end else None
        if vis_s:
            periods.append(("visualization", vis_s, "visualization"))
        if pp_end:
            periods.append(("post-processing (not analyzed)", max(0.0, pp_end["duration_seconds"] - (vis_s or 0.0)),
                            "post-processing"))
        drains = sum(_num(whole, d) for d in DRAINS)
        if drains:
            periods.append(("waiting for workers to finish (end of job)", drains, "drain"))
    accounted = sum(s for _, s, _ in periods)

    def share(s):
        return s / job_s if job_s else None

    if limiter:
        body = next((s for n, s, _ in periods if n == "evaluation"), None)
        limiter["seconds"], limiter["share_of_job"] = body, share(body) if body else None
        findings.insert(0, limiter)
    for name, secs, kind in periods:
        if not secs or (share(secs) or 0) < TAIL_SHARE:
            continue
        if kind == "post-processing" or (exact and kind in ("visualization", "scheduling")):
            continue                              # not analyzed / the engine timeline below covers these exactly
        if kind == "scheduling":
            off = offline.get("startup_s")
            findings.append(_finding(
                "start-up", "Start-up: %s takes %.0f s (%.0f%% of the job)" % (name, secs, 100 * share(secs)),
                "scheduling", "scheduling", "on", "integration" if off and off >= 30 else "tensorleap",
                ["%s: %.0f s" % (name, secs),
                 "offline preprocess + import per worker: %.1f s" % off if off is not None else None,
                 "evaluation stable after %s" % ("%.0f s of warm-up" % ev["warmup"]["seconds"]
                                                 if ev.get("reached") else "— never stable")],
                "Workers are added while the run starts and each runs preprocess before it produces samples.",
                "Keep preprocess fast (it runs again on every worker)" if off and off >= 30 else
                "Ask Tensorleap why start-up takes this long for this run.",
                seconds=secs, share=share(secs)))
        elif kind == "visualization":
            in_vis = window_stats(tables, vis_end["t"] - secs, vis_end["t"] + 1)
            vis = in_vis.get("series:visualizers_batch")
            # how much of the per-sample visualization time is the user's visualizers: their
            # offline cost, scaled by how much slower this server ran the user's generation code
            gen_on = (stats.get("series:generation_per_sample") or {}).get("mean")
            slow = max(1.0, gen_on / offline["generation_s"]) if gen_on and offline.get("generation_s") else 1.0
            user = None
            if vis and vis.get("mean") and offline.get("visualizer_s"):
                user = min(1.0, offline["visualizer_s"] * slow / vis["mean"])
            platform = user is not None and user < 0.5
            findings.append(_finding(
                "visualization-tail", "Visualization after evaluation takes %.0f s (%.0f%% of the job)" % (
                    secs, 100 * share(secs)),
                "compute", "producer (platform work around your visualizers: rendering, upload)" if platform
                else "user code (visualizers)", "on (tail)", "tensorleap" if platform else "integration",
                ["visualization phase %.0f s" % secs,
                 "per visualized sample online: mean %.0f ms%s" % (1000 * vis["mean"], ", P95 %.0f ms" % (
                     1000 * vis["p95"]) if vis.get("p95") else "") if vis and vis.get("mean") else None,
                 "your visualizers offline: %.0f ms per sample (x%.1f for this server's measured slowdown = "
                 "about %.0f%% of the online time)" % (1000 * offline["visualizer_s"], slow, 100 * user)
                 if user is not None else None],
                "Visualizations are produced per sample after evaluation; results are ready only when they finish."
                + (" Most of that time is spent around your visualizers, not in them." if platform else ""),
                ("Ask Tensorleap to look at the per-sample visualization handling (rendering, upload) for this run."
                 if platform else "Make the slowest visualizers cheaper (they run once per visualized sample)."),
                seconds=secs, share=share(secs),
                lines=[_quote(job_dir, CONSUMER, "visualization.calculate_and_upload_visualizers completed")]))
        elif kind == "drain":
            findings.append(_finding(
                "end-drain", "The job waits %.0f s at the end for workers to finish" % secs,
                "scheduling", "producer", "on (tail)", "integration",
                ["end-of-job waits: %.1f s" % secs],
                "Asynchronous work (metrics or visualization hand-off) was still running when evaluation ended.",
                "Look at the metrics and visualizer cost per sample.", seconds=secs, share=share(secs),
                lines=[_quote(job_dir, CONSUMER, d) for d in DRAINS]))

    view["measurements"] = engine_measurements(eng, e0, e1)
    rep = state.get("error_report") or {}
    msgs, server_warnings = _split_server_messages(rep)
    status = state.get("status")
    stopped = status in ("TERMINATED", "STOPPED") and not msgs
    vis_done = _visualization_progress(polls)
    vis_ph = phases.get("visualization") or {}
    # Diagnostics need the visualization only until its pace is steady: a stop during the
    # visualization after that is the plan, not a lost run.
    planned_stop = bool(stopped and vis_done and vis_ph.get("reached"))
    if server_warnings:
        notes.append("server warning(s) on this run: " + "; ".join(server_warnings))
    if planned_stop:
        cur, total = vis_done
        rate = vis_ph["stable"]["rate_median"]
        vis_stage = next((s_ for s_ in view["timeline"] if s_.get("step") == "after_visualizations"), None)
        so_far = vis_stage["seconds"] if vis_stage else None
        full = (so_far + (total - cur) / rate) if (so_far and rate) else None
        findings.append(_finding(
            "visualization-stopped-steady",
            "Visualization stopped after it reached a steady pace: %d of %d samples at %.2f samples/s" % (cur, total, rate),
            "scheduling", "visualization pipeline", "off", "expected",
            ["visualization reached a steady pace after %s and held it for %s" % (
                _s(vis_ph["warmup"]["seconds"]), _s(vis_ph["stable"]["seconds"])),
             "stopped at %d of %d samples (%s)" % (cur, total, status),
             "at the steady pace, all %d samples would take ≈ %s (measured %s so far)" % (total, _s(full), _s(so_far))
             if full else ""],
            "Diagnostics need the visualization only until its pace is steady; the rest of it would repeat the "
            "same measurements.", "None: the projection above gives the full visualization time.",
            seconds=None))
        if vis_stage is not None:
            vis_stage["projected_full_seconds"] = full
            vis_stage["stage"] = "visualization (stopped after a steady pace)"
    elif stopped:
        # stopped from outside (a person or the server) with no failure logged: the timings are
        # partial, not wrong, and the measured limiter still leads
        at = _running_steps(polls)
        findings.append(_finding(
            "run-stopped", "The run was stopped before it finished (%s)%s; no failure was logged" % (
                status, (" while " + at) if at else ""),
            "scheduling", "other infrastructure", "off", "user",
            ["run status %s" % status] + (["last progress: %s" % at] if at else []) +
            ["server warning: %s" % w for w in server_warnings] +
            ["no ERROR-level notification on the run, and its pods were not out of memory"],
            "Everything measured describes the run up to the stop; phases after it were not observed.",
            "Ask who stopped the run; re-run to completion when the remaining phases matter."))
    elif msgs or status in ("FAILED", "TERMINATED", "STOPPED"):
        findings.insert(0, _finding(
            "run-failed", "The run ended %s%s" % (status, (": " + msgs[0]) if msgs else ""),
            "scheduling" if any("model_id" in m or "missing required" in m for m in msgs) else "compute",
            "other infrastructure", "on", "tensorleap" if msgs else "integration",
            ["run status %s" % status] + ["server report: %s" % m for m in msgs] +
            ["server warning: %s" % w for w in server_warnings] +
            ["the evaluation processed %d row(s) before it ended" % int(ev["points"][-1][1])
             if ev.get("points") else "no evaluation progress was recorded"],
            "A failed run's timings describe only what ran before the failure.",
            "Retry once if the server rejected the run at creation; otherwise read the error in the evaluation pod's log.",
            seconds=job_s or 0.0, share=1.0 if job_s else None,
            lines=[_quote(job_dir, CONSUMER, '"levelname": "ERROR"')]))
    efind = engine_findings(view, job_s, worker_procs, job_dir) + \
        measurement_findings(view["measurements"], view, job_dir, (tables.get("config") or {}).get("batch_size"),
                             shares.get("waiting for samples"))
    vis_share = _user_visualizer_share(stats, tables, offline, view)
    for f in efind:
        if f["id"] == "engine-visualization" and vis_share:
            f["evidence"].append(vis_share)
    findings = efind + findings

    # ---- memory
    memory = {"pods": {}}
    for pod, info in sorted((tables.get("pods") or {}).items()):
        d = info.get("describe") or {}
        peak = max([m["rss_gb"] or 0 for m in tables.get("memory") or [] if m["pod"] == pod] or [0]) or None
        lim = _gb(d.get("limit_memory"))
        memory["pods"][pod] = {"role": info.get("role"), "limit_gb": lim, "request_gb": _gb(d.get("request_memory")),
                               "peak_rss_gb": peak, "restarts": d.get("restarts"), "oom": d.get("oom"),
                               "last_state_reason": d.get("last_state_reason")}
        if d.get("oom") or d.get("last_state_reason") == "OOMKilled":
            findings.insert(0, _finding(
                "oom", "Out of memory: %s was killed for exceeding its memory" % pod, "memory",
                "user code" if info.get("role") == GENERIC else "consumer", "on", "integration"
                if info.get("role") == GENERIC else "tensorleap",
                ["%s: last state OOMKilled, %s restart(s), limit %s" % (pod, d.get("restarts"), d.get("limit_memory")),
                 "offline worker footprint %.2f GB (peak during %s)" % (offline["user_footprint_gb"], offline.get("user_peak_stage"))
                 if offline.get("user_footprint_gb") else None],
                "The worker needed more memory than it was given and was restarted or failed.",
                "Reduce the integration's memory (see the memory section of the offline report).",
                lines=[_quote(job_dir, pod, "died; re-forking"), _quote(job_dir, pod, "failing pod")]))
        elif peak and lim and peak >= NEAR_LIMIT * lim:
            findings.append(_finding(
                "memory-pressure", "%s used %.1f of %.1f GB (%.0f%%)" % (pod, peak, lim, 100 * peak / lim),
                "memory", "consumer" if info.get("role") == CONSUMER else "user code", "on", "tensorleap"
                if info.get("role") == CONSUMER else "integration",
                ["peak RSS %.2f GB vs limit %.2f GB" % (peak, lim)],
                "Memory use close to the limit risks a restart.", "Reduce memory or raise the limit.",
                lines=[_quote(job_dir, pod, '"rss_gb": %s' % ("%.3f" % peak).rstrip("0").rstrip("."))]))
        elif d.get("restarts"):
            findings.append(_finding(
                "restarts", "%s restarted %d time(s)" % (pod, d["restarts"]), "memory" if d.get("last_state_reason")
                == "OOMKilled" else "scheduling", "other infrastructure", "on", "tensorleap",
                ["%s: %d restart(s), last state %s" % (pod, d["restarts"], d.get("last_state_reason"))],
                "A restarted worker loses its work in progress.", "Check the previous container's log."))
    gen_limits = [v["limit_gb"] for v in memory["pods"].values() if v["role"] == GENERIC and v["limit_gb"]]
    if gen_limits:
        memory["worker_limit_gb"] = max(gen_limits)
    memory["events"] = tables.get("events") or []
    if memory.get("worker_limit_gb") and offline.get("worker_peak_rss_gb"):
        memory["offline_worker_peak_gb"] = offline["worker_peak_rss_gb"]

    # ---- offline vs online
    comparison = []

    def compare(name, online, off, unit_note, location, comparable=True):
        if not online or not off:
            return
        ratio = online / off
        row = {"component": name, "online": online, "offline": off, "ratio": ratio, "note": unit_note,
               "like_for_like": comparable}
        comparison.append(row)
        if comparable and (ratio > MISMATCH or ratio < 1.0 / MISMATCH) and abs(online - off) >= MISMATCH_ABS:
            findings.append(_finding(
                "mismatch-" + name.replace(" ", "-"), "%s: online %.1f ms vs offline %.1f ms (×%.2f)" % (
                    name.capitalize(), 1000 * online, 1000 * off, ratio),
                "compute", location, "see the limiter", "tensorleap" if location in ("model", "consumer") else "integration",
                ["online %.2f ms per %s; offline %.2f ms" % (1000 * online, unit_note, 1000 * off)],
                "The platform runs this component %s than this machine did." % ("slower" if ratio > 1 else "faster"),
                "Check the server's hardware and load (CPU vs GPU, contention) before trusting offline ranks."))

    infer = stats.get("%s:%s" % (CONSUMER, INFER))
    configured = (tables.get("config") or {}).get("batch_size")
    rows_per_call, rows_src = _rows_per_call(phases, w0, w1, infer, state, tables)
    batch = rows_per_call or configured or 1
    if infer and infer.get("mean"):
        sweep = offline.get("floor_sweep") or {}
        near = max([b for b in sweep if b <= round(batch)] or [None]) if sweep else None
        off = sweep.get(near) if near else offline.get("inference_s")
        compare("inference", infer["mean"] / batch, off,
                "row (%.1f rows per call, %s; offline at batch %s)" % (batch, rows_src or "configured batch",
                                                                      near or offline.get("inference_batch")),
                "model")
    if rows_per_call and configured and rows_per_call < SMALL_BATCH * configured:
        findings.append(_finding(
            "small-realised-batch", "Observed rows per batch (%.1f) far below the configured batch size (%d)" % (
                rows_per_call, configured), "compute", "consumer", "on" if limiter and limiter["id"] ==
            "inference-floor" else "see the limiter", "tensorleap",
            ["%.1f rows per inference call (%s) vs configured batch size %d" % (rows_per_call, rows_src, configured),
             "offline floor at batch %s: %.2f ms per row" % (offline.get("inference_batch"),
                                                            1000 * offline["inference_s"]) if offline.get("inference_s") else None],
            "Inference ran on much smaller batches than configured, which costs more per row.",
            "Ask Tensorleap why the evaluation runs smaller batches than configured for this run.",
            lines=[_quote(job_dir, CONSUMER, '"span_name": "%s"' % INFER, w0, w1)]))
    gen = stats.get("series:generation_per_sample")
    if gen and gen.get("mean"):
        compare("generation", gen["mean"], offline.get("generation_s"), "sample", "user code")
    vis = stats.get("series:visualizers_batch") or whole.get("series:visualizers_batch")
    if vis and vis.get("mean"):
        compare("visualizers", vis["mean"], offline.get("visualizer_s"),
                "visualized sample — online includes the image upload", "user code", comparable=False)
    met = stats.get("series:metrics_batch")
    if met and met.get("mean"):
        compare("metrics", met["mean"] / batch, offline.get("metrics_s"),
                "row — online excludes the loss", "user code", comparable=False)
    balance = None
    if offline.get("inference_s"):
        predicted = (offline.get("generation_s", 0) + offline.get("metrics_s", 0)) / offline["inference_s"]
        balance = {"offline_generation_to_inference": predicted,
                   "online_waiting_for_samples_share": shares.get("waiting for samples")}

    # ---- gap to the floor
    gap = None
    if offline.get("inference_s") and ev.get("unit") == "rows":
        if ev.get("reached"):
            per_row, basis_g = 1.0 / ev["stable"]["rate_median"], "stable evaluation pace vs the model floor"
        elif ev.get("phase") and ev.get("points"):
            rows = ev["points"][-1][1] - ev["points"][0][1]
            span = ev["phase"]["end"] - ev["phase"]["start"]
            per_row = span / rows if rows > 0 else None
            basis_g = "whole evaluation (no stable window: includes its warm-up) vs the model floor"
        else:
            per_row = None
        if per_row:
            gap = {"floor_s_per_row": offline["inference_s"], "online_s_per_row": per_row,
                   "ratio": per_row / offline["inference_s"], "basis": basis_g}

    # ---- every finding has evidence; rank by time on the critical path
    findings = [f for f in findings if f["evidence"]]
    order = {"on": 0, "on (tail)": 1, "see the limiter": 2, "off": 3}
    findings.sort(key=lambda f: (f["id"] not in ("run-failed", "oom"), order.get(f["critical_path"], 4), -(f["seconds"] or 0)))
    critical = [f for f in findings if f["critical_path"] in ("on", "on (tail)") and f["seconds"]]
    critical.sort(key=lambda f: -f["seconds"])
    oom = [f for f in findings if f["id"] in ("run-failed", "oom")]
    primary = (oom or critical or [None])[0]          # no measured critical-path time: no claim
    if primary is None:
        notes.append("no primary bottleneck: the collected logs hold no measured critical-path time")
    secondary = [f["id"] for f in critical if f is not primary and (f["share_of_job"] or 0) >= TAIL_SHARE]

    overlap = {}
    for p in polls:
        if e0 is not None and j1 is not None and j0 is not None and j0 <= p["t"] <= j1:
            for o in p.get("other_jobs") or []:
                overlap.setdefault(o["job"], {"type": o["type"], "polls": 0})["polls"] += 1
    # a slower steady pace that starts while another run is in flight on the same server
    pod_series = _pod_series(polls)

    def workers_in(t0, t1):
        xs = [r for t, r, _ in pod_series if t0 < t <= t1]      # a poll reports the interval before it
        return (min(xs), max(xs)) if xs else (None, None)

    for ph_name, ph in phases.items():
        if not ph.get("reached"):
            continue
        slow = [pl for pl in ph.get("plateaus") or [] if pl["start"] > ph["stable"]["start"]
                and pl["rate_median"] < (1 - 2 * tolerance) * ph["stable"]["rate_median"]]
        merged = []
        for pl in slow:                          # adjacent slow plateaus at the same pace are one period
            if merged and abs(pl["rate_median"] - merged[-1]["rate_median"]) <= tolerance * merged[-1]["rate_median"]:
                merged[-1]["end"] = pl["end"]
                merged[-1]["seconds"] = merged[-1]["end"] - merged[-1]["start"]
            else:
                merged.append(dict(pl))
        for pl in merged:
            during = sorted({o["job"]: o["type"] for p in polls if pl["start"] - 300 <= p["t"] <= pl["end"]
                             for o in (p.get("other_jobs") or [])}.items())
            if not during:
                continue
            w_stable = workers_in(ph["stable"]["start"], ph["stable"]["end"])
            w_slow = workers_in(pl["start"], pl["end"])
            findings.append(_finding(
                "slowdown-%s" % ph_name, "%s slowed from %.1f to %.1f %s/s for %.0f s while another run shared the server" % (
                    ph_name.capitalize(), ph["stable"]["rate_median"], pl["rate_median"], ph.get("unit"), pl["seconds"]),
                "scheduling", "other infrastructure (shared server)", "on", "expected",
                ["steady %.1f %s/s, then steady %.1f %s/s from +%.0f s for %.0f s" % (
                    ph["stable"]["rate_median"], ph.get("unit"), pl["rate_median"], ph.get("unit"),
                    pl["start"] - ph["phase"]["start"], pl["seconds"]),
                 "ready worker pods: %s during the steady pace, %s during the slower one" % (
                     "%s-%s" % w_stable if w_stable[0] != w_stable[1] else w_stable[0],
                     "%s-%s" % w_slow if w_slow[0] != w_slow[1] else w_slow[0]) if w_stable[0] is not None else None,
                 "runs in flight then: %s" % ", ".join("%s %s" % (t, k[:8]) for k, t in during)],
                "The server's compute was shared with another run (and this run had fewer workers), so this phase "
                "ran slower than it does alone.",
                "Read the faster steady pace as this run's own; run diagnostics when the server is otherwise idle.",
                seconds=pl["seconds"], share=pl["seconds"] / job_s if job_s else None))
    if overlap:
        interval = (polls[-1]["t"] - polls[0]["t"]) / max(len(polls) - 1, 1) if len(polls) > 1 else 0.0
        notes.append("other runs were in flight on the same server during this run (%s): they share its compute, "
                     "so the timings are higher than on a quiet server" % ", ".join(
                         "%s %s for ~%.0f s" % (v["type"], k[:8], v["polls"] * interval) for k, v in sorted(overlap.items())))
    cov = tables.get("coverage") or {}
    reduced, medium = [], []
    if overlap:
        medium.append("other runs shared the server during this run")
    if cov.get("source") == "tar":
        reduced.append("imported after the run (tail of each log only)")
    if cov.get("gaps"):
        reduced.append("%d collection gap(s)" % cov["gaps"])
    if stopped and not planned_stop:
        reduced.append("the run was stopped before it finished")
    if loop <= 0:
        reduced.append("no evaluation-loop timing in the collected logs")
    if not tables.get("ws1_fields"):
        medium.append("the server's timing lines carry no percentiles; percentiles come from per-call lines")
    if not ev.get("reached"):
        medium.append("the evaluation never reached a steady pace")
    confidence = "reduced" if reduced else ("medium" if medium else "high")
    reasons = reduced + medium

    ev_desc = next((p.get("describe") or {} for p in (tables.get("pods") or {}).values()
                    if p.get("role") == CONSUMER and p.get("describe")), {})
    server = {"version": ev_desc.get("engine_version"), "gpus": ev_desc.get("gpus"),
              "source": "the evaluation pod of this run"} if ev_desc else {}
    children_n = max(list((tables.get("config") or {}).get("children_per_pod", {}).values()) or [1])
    roots = build_root_causes(tables, view, eng, job_s, limiter, e0, e1, children_n, server.get("gpus"))
    runtime_primary = primary["id"] if primary else None
    pressure = memory_pressure(memory, eng, server_warnings, offline)
    memory_leads = priority == "memory" and pressure["pressure"]
    if priority == "memory":
        mem_rc = memory_root_cause(eng, view, memory, offline, tables.get("config") or {}, server_warnings)
        if mem_rc and not memory_leads:
            # memory is the priority but not under pressure on this run: the runtime leads, and
            # the memory root follows the runtime chains
            at = next((i for i, r in enumerate(roots) if r.get("kind") == "other-mechanisms"), len(roots))
            roots = roots[:at] + [mem_rc] + roots[at:]
        elif mem_rc:
            mem_rc["pressure"] = pressure["reasons"]
            roots = [mem_rc] + roots
            if not primary or primary["id"] not in ("run-failed", "oom"):
                f = _finding("memory-priority", "Memory: %s peaks at %.1f GB of %s" % (
                    mem_rc["subject"], mem_rc["peak_gb"], "%.1f GB" % mem_rc["limit_gb"] if mem_rc["limit_gb"] else "-"),
                    "memory", mem_rc["subject"], "on", "integration" if any(o.startswith("integration:")
                                                                           for o in mem_rc["optimize"]) else "tensorleap",
                    mem_rc["evidence"], mem_rc["mechanism"],
                    "; ".join(o.replace("integration: ", "") for o in mem_rc["optimize"]) or "-")
                findings.insert(0, f)
                primary = f
    settings = server_settings(eng_all, tables, view, memory, tables.get("config") or {}, server.get("gpus"),
                               limiter, roots, settings_mode, priority, skip=skip)
    return {"job": state.get("evaluate_job") or state.get("job"), "push_job": state.get("push_job"),
            "server": server, "root_causes": roots, "server_settings": settings,
            "status": state.get("status"), "job_seconds": job_s, "job_bounds_source": job_src,
            "phases": phases, "evaluation_bounds": {"start": e0, "end": e1, "source": eval_src},
            "statistics_basis": basis, "window": {"start": w0, "end": w1},
            "components": stats, "loop_shares": shares, "worker_idle_share": idle_share,
            "worker_queue_full_share": queue_full_share,
            "worker_processes": worker_procs, "gpu_utilization_proxy": proxy,
            "periods": [{"name": n, "seconds": s, "share_of_job": share(s), "kind": kd} for n, s, kd in periods],
            "unaccounted_seconds": (job_s - accounted) if job_s else None,
            "primary": primary["id"] if primary else None, "secondary": secondary, "priority": priority,
            "runtime_primary": runtime_primary, "memory_pressure": pressure, "memory_leads": memory_leads,
            "findings": findings, "memory": memory, "comparison": comparison, "balance": balance,
            "gap_to_floor": gap, "offline": offline, "config": tables.get("config"), "engine": view,
            "confidence": confidence, "confidence_reasons": reasons, "notes": notes,
            "coverage": cov, "ws1_fields": tables.get("ws1_fields"), "overlapping_runs": overlap}


def cmd_analyze(args, out, tl):
    base = online_dir(out)
    job = args.job
    if not job:
        cands = [d for d in os.listdir(base) if os.path.isfile(os.path.join(base, d, "collect.json"))] \
            if os.path.isdir(base) else []
        job = max(cands, key=lambda d: os.path.getmtime(os.path.join(base, d, "collect.json"))) if cands else None
    job_dir = os.path.join(base, job) if job else None
    if not job_dir or not os.path.isfile(os.path.join(job_dir, "collect.json")):
        print("tl_perf online analyze: nothing collected under %s — run `online collect` first" % base,
              file=sys.stderr)
        return EXIT_BLOCKER
    tables = _load_json(os.path.join(job_dir, "components.json"))
    if not tables or tables.get("parser_version") != PARSER_VERSION:
        tables = parse_collection(job_dir)
        _write_json(os.path.join(job_dir, "components.json"), tables)
    priority = args.priority or (tl._read_json(os.path.join(out, "score.json")) or {}).get("priority") or "runtime"
    analysis = analyze_collection(job_dir, tables, _polls(job_dir), _offline(out, tl), args.window,
                                  args.stable_windows, args.tolerance, settings_mode=args.settings_mode,
                                  priority=priority)
    path = os.path.join(base, "analysis.json")
    _write_json(path, analysis)
    _write_json(os.path.join(job_dir, "analysis.json"), analysis)
    print("Online diagnostics %s (%s, confidence %s):" % (analysis["job"], analysis["status"], analysis["confidence"]))
    for ph, st in analysis["phases"].items():
        if st.get("reached"):
            print("  %s: warm-up %.0f s, stable %.0f s at %.2f %s/s (%.0f%% of the phase), tail %.0f s" % (
                ph, st["warmup"]["seconds"], st["stable"]["seconds"], st["stable"]["rate_median"], st["unit"],
                100 * st["stable"]["share_of_phase"], st["tail"]["seconds"]))
        else:
            print("  %s: no steady pace (%s)" % (ph, st.get("reason")))
    for f in analysis["findings"][:8]:
        print("  [%s] %s (%s; %s; critical path: %s)" % (
            "PRIMARY" if f["id"] == analysis["primary"] else f["owner"], f["title"], f["type"], f["location"],
            f["critical_path"]))
    print("  -> %s" % path)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# engine view: what the platform itself spent the run on (K28: the engine's own stage,
# data-movement and warning names as they appear in the run's logs, each with a label)
# --------------------------------------------------------------------------- #

ENGINE_CATEGORIES = (            # (span-name prefix, category) — first match wins
    ("trainer.post_processing.drain", "post-processing: queue drains"), ("trainer.load_model", "start-up"),
    ("trainer.inspect_model", "start-up"), ("remote_command.", "remote calls"),
    ("generic_processor.iteration", "workers: time by activity"), ("generic_processor.ratio", "workers: time by activity"),
    ("heatmap.", "visualization: heatmaps"), ("storage.", "storage: uploads"),
    ("metrics.user", "metrics: your metrics"), ("metrics.loss", "metrics: your metrics"),
    ("trainer.validation_step", "evaluation loop"), ("trainer.infer", "evaluation loop"),
    ("trainer.update_working_state", "evaluation loop"), ("etl.", "evaluation loop: batch assembly"),
    ("redis.dataset", "data movement: sample queue"), ("redis.metrics", "data movement: metrics queue"),
    ("redis.vis", "data movement: visualization queue"), ("redis.streaming", "data movement: results stream"),
    ("redis.", "data movement: other queues"), ("blob_store.dataset", "storage: samples"),
    ("blob_store.metrics", "storage: metrics"), ("blob_store.vis", "storage: visualizations"),
    ("blob_store.streaming", "storage: results stream"), ("blob_store.", "storage: other"),
    ("auto_pca", "embeddings: PCA"), ("latent_space", "embeddings: latent space"),
    ("nn_index", "embeddings: nearest-neighbour index"), ("insights.", "insights"),
    ("multi_ls_insights.", "insights"), ("visualization.", "visualization"),
    ("vis_calculator", "visualization"), ("analyzer.", "heatmaps / analysis"),
    ("metrics_runner", "metrics"), ("instance_metric_router", "metrics"),
    ("samples_generator", "sample generation"), ("samples.", "sample generation"),
    ("generic_processor.idle_wait", "workers idle"), ("trainer.wait_for", "end-of-job waits"),
    ("trainer.post_processing", "post-processing (total)"), ("metadata_revision", "metadata"),
    ("trainer.new_metadata", "metadata"),
)
STRUCTURE_SPANS = ("engine.job", "worker.run", "worker.execute_job", "trainer.evaluate", "worker.post_running",
                   "worker.pre_running", "trainer.post_processing")
ENGINE_LABELS = {
    "trainer.infer": "model inference", "trainer.validation_step": "evaluation step",
    "trainer.validation_step.pull_batch": "waiting for the next batch of samples",
    "trainer.validation_step.extract_ls": "embedding (feature) extraction",
    "trainer.validation_step.marshal_numpy": "output conversion",
    "trainer.validation_step.push_metrics": "metrics hand-off",
    "etl.assemble_batch": "batch assembly", "etl.wait_for_sample": "waiting for a sample",
    "redis.metrics_queue.push": "push to the metrics queue",
    "redis.vis_queue.serialize_and_push": "serialize + push to the visualization queue",
    "blob_store.vis.write": "write visualization payloads", "blob_store.metrics.write": "write metrics payloads",
    "auto_pca.fit": "PCA fit", "latent_space.persist": "latent-space persist", "nn_index.fit": "nearest-neighbour index build",
    "nn_index.query_with_timeout": "nearest-neighbour query",
    "insights.calculate_insights_all_latent_spaces": "insights over all latent spaces",
    "insights.detect_mislabeled_samples": "mislabeled-sample detection",
    "multi_ls_insights.display_insight_populations": "insight populations for display",
    "visualization.calculate_and_upload_visualizers": "visualizations (whole phase)",
    "vis_calculator.batch": "visualizer batch on a worker", "metrics_runner.batch": "metrics batch on a worker",
    "samples_generator.generate_sample": "sample generation on a worker",
    "generic_processor.idle_wait": "worker idle wait", "trainer.wait_for_streaming_queue": "wait for the results stream to drain",
    "multi_ls_insights.gather_per_ls_insights": "insights per latent space",
    "insights.single_calculate_low_perf_insights": "low-performance insights (per latent space and metric)",
    "insights.precompute_instance_populations": "instance populations (precompute)",
    "insights.single_calculate_insights": "insights for one latent space",
    "multi_ls_insights.build_embedding_store": "embedding store for insights",
    "multi_ls_insights.compute_sub_insights": "sub-population insights", "multi_ls_insights.compute_insights_mi": "insight mutual information",
    "multi_ls_insights.compute_failure_modes": "failure modes", "multi_ls_insights.extend_clusters": "cluster extension",
    "multi_ls_insights.build_and_publish_top_panels": "top insight panels", "multi_ls_insights.build_insights_with_blobs": "insight payloads",
    "instance_metric_router.compute_for_batch": "instance-level metrics routing (per batch)",
    "samples.mask_sidecar": "instance mask side-car (per sample)", "redis.metrics_queue.async_push_wait": "metrics queue async push wait",
    "trainer.update_working_state": "evaluation state update",
    "trainer.wait_for_metrics": "wait for metrics to finish", "trainer.post_processing": "post-processing (total)",
    "trainer.load_model": "loading the model", "trainer.inspect_model": "inspecting the model",
    "trainer.post_processing.drain_metrics": "waiting for the metrics queue to drain",
    "trainer.post_processing.drain_streaming": "waiting for the results stream to drain",
    "trainer.post_processing.raw_ls_load": "loading the raw latent spaces",
    "trainer.validation_step.forward.originals": "forward pass", "trainer.validation_step.forward.unlabeled":
        "forward pass (unlabeled samples)", "trainer.validation_step.forward.instance_synthesized":
        "instance rows from their parent (no forward pass)",
    "visualization.load_latent_space": "visualization: loading the latent space",
    "visualization.build_model": "visualization: building the model",
    "visualization.drain_wait": "visualization: waiting for the workers to finish",
    "visualization.next_batch_wait": "visualization: waiting for the next batch of samples",
    "visualization.forward": "visualization: forward pass", "visualization.prepare_payload": "visualization: preparing payloads",
    "visualization.push_submit": "visualization: handing a batch to the workers",
    "visualization.batch": "visualization: one batch on the evaluation pod",
    "redis.vis_queue.backpressure_wait": "visualization queue back-pressure",
    "redis.vis_queue.quantize_inputs": "compressing visualization inputs",
    "redis.vis_queue.push_slot_wait": "waiting for a free push slot",
    "redis.vis_queue.push_headroom_wait": "waiting for memory headroom to push",
    "redis.vis_queue.push_pool_queue_wait": "push queued behind others", "redis.vis_queue.parent_write": "writing shared parent data",
    "redis.vis_queue.pickle": "serializing a visualization element",
    "redis.vis_queue.blob_write": "writing a visualization element to shared storage",
    "redis.vis_queue.rpush": "queueing a visualization element",
    "redis.vis_queue.restore_parent_sections": "restoring shared parent data",
    "redis.vis_queue.restore_inputs": "restoring visualization inputs",
    "generic_processor.iteration.generate": "workers: generating samples", "generic_processor.iteration.metrics":
        "workers: computing metrics", "generic_processor.iteration.vis": "workers: rendering visualizations",
    "generic_processor.iteration.empty": "workers: polling empty queues", "generic_processor.iteration.command":
        "workers: serving remote calls", "generic_processor.iteration.hook": "workers: rollout hooks",
    "generic_processor.iteration.other": "workers: other work",
    "vis_calculator.starvation_wait": "workers: waiting for visualization work",
    "vis_calculator.prepare": "workers: preparing a visualization batch (incl. heatmaps)",
    "vis_calculator.render": "workers: running visualizers", "vis_calculator.upload": "workers: writing visualization results",
    "vis_calculator.overlay": "heatmap overlays", "vis_calculator.prepare_visualization": "formatting visualizer output",
    "vis_calculator.upload.payload_json": "writing visualization result files",
    "heatmap.generate": "heatmap (per input)", "heatmap.instance_mask_fetch": "instance mask (regenerates the parent sample)",
    "heatmap.cache_load": "heatmap cache read", "heatmap.cache_store": "heatmap cache write",
    "heatmap.accumulate": "heatmap accumulation over feature maps", "heatmap.zoom": "heatmap resize to the input",
    "storage.image_encode": "image encoding", "storage.flatbuffer_upload_sync": "heatmap file upload",
    "storage.async_upload.queue_wait": "upload queued", "storage.async_upload.run": "background upload",
    "metrics.loss": "your loss", "latent_space.store_load": "latent-space store load",
    "latent_space.store_load.with_metadata": "latent-space store load (with metadata)",
}
LABEL_FAMILIES = (      # (span-name prefix, label prefix) for names that carry a user-defined part
    ("metrics.user_metric.", "your metric "), ("metrics.user_instance_metric.", "your instance metric "),
    ("vis_calculator.user_visualizer.", "your visualizer "),
    ("vis_calculator.user_heatmap_visualizer.", "your heatmap visualizer "),
    ("remote_command.wait.", "waiting on remote call "), ("remote_command.run.", "serving remote call "),
)


COUNTER_LABELS = {      # counters: a value per call (count, size, share), named by what the value is
    "redis.vis_queue.payload_mib_NOT_SECONDS": "visualization element size (MiB)",
    "redis.vis_queue.sync_push_NOT_SECONDS": "share of visualization pushes on the evaluation pod's own thread",
    "visualization.batch_rows_NOT_SECONDS": "rows per visualization batch",
    "visualization.shared_parent_batch_NOT_SECONDS": "share of visualization batches reusing a parent (no forward pass)",
    "vis_calculator.renders_per_batch_NOT_SECONDS": "visualizer renders per batch on a worker",
    "heatmap.feature_maps_NOT_SECONDS": "feature maps per heatmap", "heatmap.mask_applied_NOT_SECONDS":
        "share of instance masks applied to heatmaps", "heatmap.cache_hit_NOT_SECONDS": "heatmap cache hit rate",
    "storage.async_upload.pending_NOT_SECONDS": "uploads still in flight when a new one is submitted",
    "generic_processor.ratio_NOT_SECONDS": "share of worker iterations offered to sample generation",
    "samples.mask_sidecar_mib_NOT_SECONDS": "instance mask side-car size (MiB)",
    "etl.batch_rows_NOT_SECONDS": "rows per assembled batch", "trainer.infer_batch_rows_NOT_SECONDS": "rows per model call",
    "samples.minibatch_rows_NOT_SECONDS": "rows per worker mini-batch",
}


def is_counter(span, kind=None):
    return kind == "counter" or span.endswith("_NOT_SECONDS")


def engine_label(span):
    if span in ENGINE_LABELS:
        return ENGINE_LABELS[span]
    for prefix, label in LABEL_FAMILIES:
        if span.startswith(prefix):
            return label + span[len(prefix):]
    return None
STEP_LABELS = {          # post-processing memory step reached -> the work that ended there
    "after_model_cleanup": "model cleanup", "after_upload_ls_pca": "latent-space upload",
    "after_nn_indexers": "nearest-neighbour indexes", "after_duplications": "duplicate detection",
    "after_pca_cleanup": "PCA cleanup", "after_mislabeled": "mislabeled detection",
    "after_first_analysis": "insights (first analysis)", "after_visualizations": "visualizations",
    "before_failure_aligned_training": "before failure-aligned training",
    "after_failure_aligned_training": "failure-aligned training",
}
EVENTS = (   # (message prefix, key) — INFO lines that count or time engine behaviour
    ("max number of samples in queue reached", "sample_queue_full"),
    ("redis metrics queue: async push backpressure wait", "metrics_backpressure"),
    ("Pushing  elements to redis key: streaming", "results_stream_push"),
    ("StreamingHandlerScaler tick", "results_stream_scaler"),
    ("eval_loop: heap trim returned", "heap_trim"),
    ("smart_read_file_streaming called", "blob_read"),
    ("starting visualize batch", "visualize_batch"),
    ("Publishing msg back to node", "progress_publish"),
    ("rss @ eval_loop.batch", "rss_probe"), ("calculated max_samples_per_batch", "sample_claim"),
    ("streaming bulk write split", "stream_bulk_write"), ("Shared-volume disk guard", "disk_guard"),
    ("waiting for vis calculation", "vis_drain_wait"), ("error occured in evaluate batch", "eval_batch_error"),
    ("Got timeout before vis done", "vis_timeout"), ("using ls after pca", "ls_reload"),
    ("max number of metrics objects in queue reached", "metrics_queue_full"),
    ("max number of streaming objects in queue reached", "stream_queue_full"),
    ("generic_processor | Starting generic processor service", "worker_start"),
    ("Failed to publish to RabbitMQ", "publish_retry"),
    ("sparse custom-LS stash over capacity", "stash_eviction"),
    ("process resources", "resources"), ("remote command completed", "remote_call"),
    ("dataset_command served", "command_served"), ("first evaluated batch", "first_batch"),
    ("chose aggregate_every for generate_sample", "worker_code_load"),
    ("metrics element byte breakdown", "metrics_payload"), ("dataset ready-sample payload breakdown", "sample_payload"),
    ("Updating existing generic pods settings based on current memory", "worker_memory_resize"),
    ("instance_metrics computed for batch", "instance_metrics"),
)
SPECIFIC_WARNINGS = ("Failed to apply mask on heatmap", "sparse custom-LS stash over capacity",
                     "error occured in evaluate batch")     # reported by their own findings
MESSAGE_INDEX_MAX = 5000
LARGE_SAMPLE_BYTES = 10 * 1000 * 1000    # a sample this large is claimed alone
UPLOAD_START, UPLOAD_DONE = "Trying to upload ", "Done upload "
WORKER_CONNECTED = "connected to rabbitmq server"


def engine_category(span):
    for prefix, cat in ENGINE_CATEGORIES:
        if span.startswith(prefix):
            return cat
    return "job structure" if span in STRUCTURE_SPANS else "other"


def _norm(msg):
    msg = re.sub(r"\b[0-9a-f]{12,}\b", "<id>", msg)          # hashed ids: one row per message kind
    return re.sub(r"\d+(\.\d+)?", "#", msg)[:120]


def parse_engine(logs_dir):
    """Engine-side lines of every kept pod log: post-processing step markers, warnings and
    errors, engine events, and upload start/done pairs (paired in order per pod)."""
    steps, warnings, events, messages, phases = [], {}, {}, {}, []
    uploads = []
    for fname in sorted(os.listdir(logs_dir)) if os.path.isdir(logs_dir) else []:
        pod = fname[:-4] if fname.endswith(".log") else fname
        role = pod_role(pod)
        pending = {}
        seen_lines = set()
        connected, raw_after = None, 0
        with open(os.path.join(logs_dir, fname), encoding="utf-8") as fh:
            for line in fh:
                if not line.startswith("{"):
                    raw_after += 1 if connected is not None else 0
                    continue
                if line in seen_lines:
                    continue
                seen_lines.add(line)
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                ts = log_time(d.get("asctime"))
                if ts is None:
                    continue
                msg = d.get("message") or ""
                if connected is not None:
                    # the first engine line after the worker connected: the gap is the integration's
                    # load (module import, preprocess) — its own prints land in between
                    e = events.setdefault("worker_ready", {"count": 0, "pods": [], "first": ts, "last": ts,
                                                           "records": [], "per_pod": {}})
                    e["count"] += 1
                    e["last"] = ts
                    e["pods"].append(pod)
                    e["per_pod"][pod] = e["per_pod"].get(pod, 0) + 1
                    e["records"].append({"pod": pod, "t": ts, "seconds": ts - connected, "raw_lines": raw_after})
                    connected = None
                if role == GENERIC and msg.startswith(WORKER_CONNECTED):
                    connected, raw_after = ts, 0
                mk = msg[:100]
                m = messages.get(mk)
                if m is None and len(messages) < MESSAGE_INDEX_MAX:
                    m = messages[mk] = {"message": mk, "count": 0, "throttled": False, "pods": []}
                # a throttled line says how many lines the logger dropped before it
                dropped = d.get("throttle_suppressed") if isinstance(d.get("throttle_suppressed"), int) else 0
                if m is not None:
                    m["count"] += 1 + dropped
                    m["throttled"] = m["throttled"] or "throttle_interval_sec" in d
                    if pod not in m["pods"]:
                        m["pods"].append(pod)
                if msg == "post_processing memory":
                    steps.append({"t": ts, "step": d.get("step"), "rss_gb": d.get("rss_gb"), "data_gb": d.get("data_gb")})
                pm = INSIGHTS_PHASE_RE.match(msg)
                if pm:
                    phases.append({"t": ts, "phase": pm.group(1), "label": pm.group(2), "seconds": float(pm.group(3))})
                lvl = d.get("levelname")
                if lvl in ("WARNING", "ERROR", "CRITICAL"):
                    key = "%s|%s|%s" % (lvl, role, _norm(msg))
                    w = warnings.setdefault(key, {"level": lvl, "role": role, "message": _norm(msg), "count": 0,
                                                  "pods": [], "first": ts, "last": ts, "example": line.strip()[:300],
                                                  "times": []})
                    w["count"] += 1 + dropped
                    w["last"] = ts
                    if len(w["times"]) < 2000:
                        w["times"].append([ts, 1 + dropped])
                    if pod not in w["pods"]:
                        w["pods"].append(pod)
                for prefix, key in EVENTS:
                    if msg.startswith(prefix) or (prefix in SUBSTRING_EVENTS and prefix in msg):
                        e = events.setdefault(key, {"count": 0, "pods": [], "first": ts, "last": ts, "records": [],
                                                    "per_pod": {}})
                        e["count"] += 1 + dropped
                        e["per_pod"][pod] = e["per_pod"].get(pod, 0) + 1 + dropped
                        e["last"] = ts
                        if pod not in e["pods"]:
                            e["pods"].append(pod)
                        rec = {k: v for k, v in d.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
                        rec.update({k: d[k] for k in EVENT_TEXT_FIELDS if k in d})
                        rec["pod"] = pod
                        if len(e["records"]) < 2000:
                            rec["t"] = ts
                            e["records"].append(rec)
                if msg.startswith(UPLOAD_START):
                    kind = msg[len(UPLOAD_START):].split(" ")[0]
                    pending.setdefault(kind, []).append(ts)
                elif msg.startswith(UPLOAD_DONE):
                    kind = msg[len(UPLOAD_DONE):].split(" ")[0]
                    if pending.get(kind):
                        uploads.append({"pod": pod, "role": role, "kind": kind, "t": ts,
                                        "seconds": ts - pending[kind].pop(0)})
    return {"steps": sorted(steps, key=lambda s: s["t"]), "warnings": sorted(warnings.values(), key=lambda w: -w["count"]),
            "events": events, "uploads": uploads, "messages": messages, "insights_phases": phases}


SUBSTRING_EVENTS = ("dataset_command served", "chose aggregate_every for generate_sample",
                    "Updating existing generic pods settings based on current memory")   # matched anywhere
INSIGHTS_PHASE_RE = re.compile(r"\[multi-LS-insights-timing\] Phase (\w+): (.+?) done in ([0-9.]+)s")
EVENT_TEXT_FIELDS = ("label", "command", "dataset_command", "first_in_process", "caller_span", "ls_type",
                     "load_metadata")


POLL_STEP_MARKER = {     # a platform progress step -> the post-processing step marker that ends it
    "visualize_samples": "after_visualizations", "insights_analysis": "after_first_analysis",
    "explore_populations": "after_first_analysis", "explore_sub_populations": "after_first_analysis",
    "prepare_displays": "after_first_analysis", "mislabeling": "after_mislabeled",
    "explore_duplications": "after_duplications", "indexing_neighbors": "after_nn_indexers",
}


def _visualization_progress(polls):
    """(done, total) of the visualization step at the last poll that reports it, else None."""
    for p in reversed(polls or []):
        for st_ in p.get("steps") or []:
            if st_.get("id") == "visualize_samples" and st_.get("total") and st_.get("current") is not None:
                return int(st_["current"]), int(st_["total"])
    return None


def _running_ids(polls):
    for p in reversed(polls or []):
        ids = [s["id"] for s in p.get("steps") or [] if s.get("status") == "RUNNING"]
        if ids:
            return ids
    return []


def engine_view(tables, eng, j0, j1, e0, e1, running=None, skip=None):
    """The run as the engine saw it: an exact timeline, every engine span by stage, data
    movement, engine events and warnings, memory per post-processing step."""
    out = {"timeline": [], "stages": [], "events": {}, "warnings": eng.get("warnings") or [], "uploads": {},
           "memory_steps": eng.get("steps") or [], "not_analyzed": skip or []}
    tl = out["timeline"]
    if j0 is not None and e0 is not None:
        tl.append({"stage": "start-up (job start → first evaluated batch)", "start": j0, "end": e0})
    if e0 is not None:
        tl.append({"stage": "evaluation loop", "start": e0, "end": e1})
    steps = eng.get("steps") or []
    for a, b in zip(steps, steps[1:]):
        name = b["step"] or "?"
        label = STEP_LABELS.get(name) or (("PCA: %s" % name[len("after_pca_ls_type_"):]) if name.startswith("after_pca_ls_type_") else name)
        tl.append({"stage": "visualization" if name == "after_visualizations" else "post-processing: " + label,
                   "start": a["t"], "end": b["t"],
                   "rss_gb": b.get("rss_gb"), "step": name})
    pp = next((r for r in tables.get("spans") or [] if r["span"] == "trainer.post_processing"), None)
    if pp and steps and pp["t"] > steps[-1]["t"] + 1:
        tl.append({"stage": "post-processing: after the last step (metadata, publishing)", "start": steps[-1]["t"], "end": pp["t"]})
    elif running is not None and steps and j1 is not None and j1 > steps[-1]["t"] + 1:
        # the run ended inside a step: the open stage runs from the last marker to the end
        marker = next((POLL_STEP_MARKER[i] for i in running if i in POLL_STEP_MARKER), None)
        tl.append({"stage": "post-processing: %s (the run ended inside it)" % (
                       STEP_LABELS.get(marker) or ", ".join(running) or "after the last step"),
                   "start": steps[-1]["t"], "end": j1, "step": marker, "open": True})
    for r in tables.get("spans") or []:
        if r["span"] in ("trainer.wait_for_metrics", "trainer.wait_for_streaming_queue") and (r["sum"] or 0) > 0.5:
            tl.append({"stage": "end of job: " + ENGINE_LABELS.get(r["span"], r["span"]), "start": r["t"] - r["sum"], "end": r["t"]})
    for s in tl:
        s["seconds"] = max(0.0, s["end"] - s["start"])
        s["share_of_run"] = s["seconds"] / (j1 - j0) if j0 is not None and j1 > j0 else None
    tl.sort(key=lambda s: s["start"])
    stats = window_stats(tables, j0, j1 + 1, loop_end=e1, skip=skip) if j0 is not None else {}
    out["counters"] = []
    for key, r in stats.items():
        if key.startswith("series:"):
            continue
        span = r["component"]
        if span in STRUCTURE_SPANS:
            continue
        if is_counter(span, r.get("span_kind")):
            # a count or size per call, not a duration: never summed as time
            out["counters"].append({"span": span, "label": COUNTER_LABELS.get(span) or engine_label(span),
                                    "role": r["role"], "samples": r["calls"], "mean": r["mean"], "max": r["max"],
                                    "pods": r["pods"]})
            continue
        out["stages"].append({"category": engine_category(span), "span": span, "label": engine_label(span),
                              "role": r["role"], "calls": r["calls"], "seconds": r["sum_seconds"], "mean": r["mean"],
                              "max": r["max"], "p50": r.get("p50"), "p95": r.get("p95"), "p99": r.get("p99"),
                              "pods": r["pods"], "span_kind": r.get("span_kind")})
    out["stages"].sort(key=lambda s: (s["category"], -(s["seconds"] or 0)))
    for key, e in (eng.get("events") or {}).items():
        recs = e.get("records") or []
        summ = {"count": e["count"], "pods": len(e["pods"]), "first": e["first"], "last": e["last"]}
        for field in ("duration_seconds", "doc_count", "pushed_docs", "pulled_docs", "current_replicas",
                      "desired_replicas", "heap_returned_gb", "rss_before_trim_gb", "pending_futures"):
            xs = [r[field] for r in recs if field in r]
            if xs:
                summ[field] = {"sum": sum(xs), "max": max(xs), "mean": sum(xs) / len(xs)}
        out["events"][key] = summ
    by_kind = {}
    for u in eng.get("uploads") or []:
        by_kind.setdefault(u["kind"], []).append(u["seconds"])
    for kind, xs in by_kind.items():
        out["uploads"][kind] = {"count": len(xs), "seconds": sum(xs), "mean": sum(xs) / len(xs),
                                "p50": percentile(xs, 50), "p95": percentile(xs, 95), "max": max(xs)}
    return out


def engine_findings(view, job_s, worker_procs, job_dir):
    """Findings about the engine itself, each with its evidence."""
    fs = []
    tl = [s for s in view["timeline"] if s["stage"] != "evaluation loop"]
    stages = view["stages"]

    def total(prefixes, role=None):
        return sum(s["seconds"] or 0 for s in stages if s["span"].startswith(prefixes) and (role is None or s["role"] == role))

    def exact(name, role):
        return sum(s["seconds"] or 0 for s in stages if s["span"] == name and s["role"] == role)

    # the visualization phase, decomposed into the engine's parts
    vis = next((s for s in view["timeline"] if s.get("step") == "after_visualizations"), None)
    if vis and job_s and vis["seconds"] / job_s >= TAIL_SHARE:
        parts = [("workers computing visualizers (all workers, busy time)", exact("vis_calculator.batch", GENERIC)),
                 ("serialize + push to the visualization queue (evaluation pod)",
                  exact("redis.vis_queue.serialize_and_push", CONSUMER)),
                 ("writing visualization payloads to storage (evaluation pod)", exact("blob_store.vis.write", CONSUMER))]
        up = view["uploads"].get("image")
        if up:
            parts.append(("workers encoding images and handing them to the upload pool (%d, P50 %.0f ms, "
                          "P95 %.0f ms; the upload itself runs in the background, untimed)" % (
                              up["count"], 1000 * up["p50"], 1000 * up["p95"]), up["seconds"]))
        fs.append(_finding(
            "engine-visualization", "Visualization phase: %.0f s (%.0f%% of the run)" % (vis["seconds"], 100 * vis["seconds"] / job_s),
            "compute", "visualization pipeline", "on (tail)", "tensorleap",
            ["%s: %.1f s" % (n, v) for n, v in parts if v] +
            ["the evaluation pod's own share: %.0f s of %.0f s wall%s" % (
                exact("visualization.batch", CONSUMER) or parts[1][1] + parts[2][1], vis["seconds"],
                " (its visualization loop, measured)" if exact("visualization.batch", CONSUMER) else
                " (serialize + write only; this engine does not time its visualization loop)")],
            "After evaluation, every visualized sample is regenerated, rendered on a worker, handed back through the "
            "visualization queue and written to storage; the run is not done until this finishes.",
            "Tensorleap: compare the evaluation pod's serialize/write time with the workers' compute and the uploads "
            "to see which side paces this phase.", seconds=vis["seconds"], share=vis["seconds"] / job_s,
            lines=[_quote(job_dir, CONSUMER, '"span_name": "redis.vis_queue.serialize_and_push"'),
                   _quote(job_dir, CONSUMER, '"span_name": "blob_store.vis.write"')]))
    # start-up (post-processing is not analyzed)
    for s in tl:
        if s["stage"].startswith("start-up") and job_s and s["seconds"] / job_s >= TAIL_SHARE:
            fs.append(_finding(
                "engine-start-up", "Start-up before the first evaluated batch: %.0f s (%.0f%% of the run)" % (
                    s["seconds"], 100 * s["seconds"] / job_s), "scheduling", "scheduling", "on", "tensorleap",
                ["job start to the first evaluated batch: %.1f s" % s["seconds"]],
                "Pods start, the model is loaded and workers run preprocess before the first batch.",
                "Tensorleap: pod start and model load times for this run.", seconds=s["seconds"], share=s["seconds"] / job_s))
    # engine events that signal pressure or degradation
    ev = view["events"]
    q = ev.get("sample_queue_full")
    if q:
        fs.append(_finding(
            "engine-sample-queue-full", "Workers paused %d times on a full sample queue" % q["count"],
            "scheduling", "data movement: sample queue", "off", "expected",
            ["'max number of samples in queue reached' ×%d on %d worker pod(s)" % (q["count"], q["pods"])],
            "Workers produced samples faster than the evaluation consumed them and waited for room.",
            "None needed while the model is the limit; it confirms the workers are ahead.",
            lines=[_quote(job_dir, GENERIC, "max number of samples in queue reached")]))
    bp = ev.get("metrics_backpressure")
    if bp:
        d = bp.get("duration_seconds") or {}
        fs.append(_finding(
            "engine-metrics-backpressure", "Metrics queue back-pressure: %d waits, %.2f s in total" % (bp["count"], d.get("sum", 0.0)),
            "scheduling", "data movement: metrics queue", "on" if d.get("sum", 0) >= 1.0 else "off", "tensorleap",
            ["'redis metrics queue: async push backpressure wait' ×%d, total %.3f s, max %.3f s" % (
                bp["count"], d.get("sum", 0.0), d.get("max", 0.0))],
            "The evaluation waited for earlier metric hand-offs to finish before pushing more.",
            "Tensorleap: only relevant if the total is a visible share of the evaluation.",
            lines=[_quote(job_dir, CONSUMER, "async push backpressure wait")]))
    for w in view["warnings"]:
        if w["count"] < 1 or w["message"].startswith(SPECIFIC_WARNINGS):
            continue
        fs.append(_finding(
            "engine-warning-%d" % len(fs), "%s on the %s: \"%s\" ×%d" % (
                "Error" if w["level"] == "ERROR" else "Warning", "evaluation pod" if w["role"] == CONSUMER else
                ("workers" if w["role"] == GENERIC else w["role"]), w["message"][:90], w["count"]),
            "compute", w["role"], "-", "tensorleap",
            ["%d line(s) on %d pod(s) between %s and %s" % (w["count"], len(w["pods"]),
                                                          _clock(w["first"]), _clock(w["last"]))],
            "Logged by the engine while processing this run.", "Tensorleap: confirm whether this degrades the results or the runtime.",
            lines=[w["example"]]))
    return fs


def _clock(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%H:%M:%S")


# --------------------------------------------------------------------------- #
# engine measurements: neutral numbers read from the engine's own lines — rows per batch,
# payload size per sample, results pushed vs drained, waits, memory left after each trim.
# A finding built on them states a mechanism and its cost; none of them checks whether a
# particular platform feature was on.
# --------------------------------------------------------------------------- #

def _msg_hits(messages, prefixes):
    count, lower_bound, pods = 0, False, set()
    for m in messages.values():
        if m["message"].startswith(prefixes):
            count += m["count"]
            lower_bound = lower_bound or m["throttled"]
            pods.update(m["pods"])
    return count, lower_bound, pods


STREAM_PRESSURE = ("max number of streaming objects in queue reached", "redis streaming queue: backpressure wait")


def engine_measurements(eng, e0, e1):
    ev = eng.get("events") or {}
    messages = eng.get("messages") or {}
    derived = {}
    probes = [r for r in (ev.get("rss_probe") or {}).get("records", []) if "step" in r and "evaluated_samples" in r]
    pairs = [(b["evaluated_samples"] - a["evaluated_samples"]) / float(b["step"] - a["step"])
             for a, b in zip(probes, probes[1:]) if b["step"] > a["step"] and b["evaluated_samples"] >= a["evaluated_samples"]]
    if pairs:
        derived["rows_per_batch"] = {"median": _median(pairs), "min": min(pairs), "max": max(pairs), "n": len(pairs)}
    claims = [r.get("max_samples_per_batch") for r in (ev.get("sample_claim") or {}).get("records", [])
              if r.get("max_samples_per_batch") is not None]
    if claims:
        derived["sample_claim"] = {"min": min(claims), "max": max(claims),
                                   "sample_bytes": max(r.get("sample_bytes", 0) for r in ev["sample_claim"]["records"])}
    ticks = (ev.get("results_stream_scaler") or {}).get("records", [])
    if ticks:
        pushed = sum(r.get("pushed_docs", 0) for r in ticks)
        pulled = sum(r.get("pulled_docs", 0) for r in ticks)
        n, _, _ = _msg_hits(messages, STREAM_PRESSURE)
        derived["results_stream"] = {"pushed": pushed, "pulled": pulled, "ticks": len(ticks),
                                     "max_replicas": max(r.get("desired_replicas", 0) for r in ticks),
                                     "pressure_lines": n}
    bulk = (ev.get("stream_bulk_write") or {}).get("records", [])
    for f in ("minio_ls_secs", "es_secs"):
        xs = [r[f] for r in bulk if f in r]
        if xs:
            derived.setdefault("stream_bulk_write", {})[f] = {"n": len(xs), "sum": sum(xs), "p50": percentile(xs, 50),
                                                              "p95": percentile(xs, 95), "max": max(xs)}
    guard = [r.get("waited_s", 0.0) for r in (ev.get("disk_guard") or {}).get("records", [])]
    if guard:
        derived["disk_guard_wait_s"] = sum(guard)
    trims = [r for r in (ev.get("heap_trim") or {}).get("records", []) if "rss_after_trim_gb" in r]
    if len(trims) >= 3:
        xs = [r.get("evaluated_samples", i) for i, r in enumerate(trims)]
        ys = [r["rss_after_trim_gb"] for r in trims]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        den = sum((x - mx) ** 2 for x in xs) or 1.0
        derived["memory_floor"] = {"first_gb": ys[0], "last_gb": ys[-1], "trims": len(ys),
                                   "gb_per_1000_rows": 1000.0 * sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den}
    restarts = {p: n - 1 for p, n in ((ev.get("worker_start") or {}).get("per_pod") or {}).items() if n > 1}
    if restarts:
        derived["worker_restarts"] = restarts
    for key in ("vis_drain_wait", "eval_batch_error", "vis_timeout", "publish_retry"):
        if ev.get(key):
            derived[key] = ev[key]["count"]
    evic = (ev.get("stash_eviction") or {}).get("records", [])
    if evic and e1 is not None:
        # each kept line also stands for the lines the logger dropped before it
        w = lambda r: 1 + int(r.get("throttle_suppressed") or 0)
        derived["stash_evictions"] = {"during_evaluation": sum(w(r) for r in evic if e0 <= r["t"] <= e1),
                                      "after_evaluation": sum(w(r) for r in evic if r["t"] > e1),
                                      "total": sum(w(r) for r in evic)}
    for key, prefixes in (("heatmap_non_spatial", ("Every feature map was rejected as non-spatial",)),
                          ("heatmap_mask_fallback", ("Failed to apply mask on heatmap",))):
        n, lb, pods = _msg_hits(messages, prefixes)
        if n:
            derived[key] = {"count": n, "lower_bound": lb, "pods": len(pods)}
    return derived


def _stage_seconds(view, span, role):
    return sum(s["seconds"] or 0.0 for s in view.get("stages") or [] if s["span"] == span and s["role"] == role)


def measurement_findings(d, view, job_dir, configured_batch, wait_share=None):
    fs = []
    if d.get("eval_batch_error"):
        fs.append(_finding(
            "engine-eval-batch-errors", "%d evaluation batch(es) failed and were skipped" % d["eval_batch_error"],
            "compute", "evaluation loop", "on", "tensorleap",
            ["'error occured in evaluate batch' ×%d" % d["eval_batch_error"]],
            "A failed batch is skipped: its samples are missing from the results.",
            "Tensorleap: the evaluation pod's log around these lines names the error.",
            lines=[_quote(job_dir, CONSUMER, "error occured in evaluate batch")]))
    se = d.get("stash_evictions")
    if se:
        during = se["during_evaluation"]
        fs.append(_finding(
            "engine-stash-eviction", "Custom latent-space stash evicted %d entries (%d during evaluation)" % (se["total"], during),
            "memory", "evaluation loop (custom latent spaces)", "-", "tensorleap",
            ["'sparse custom-LS stash over capacity; evicting the oldest entry' ×%d: %d during evaluation, %d after it"
             % (se["total"], during, se["after_evaluation"]),
             "evaluation-batch errors: %d" % d.get("eval_batch_error", 0)],
            ("The engine's message says an evicted batch is missing its sparse custom latent space. " +
             ("None happened during evaluation and no evaluation batch failed, so the evaluated results look "
              "unaffected; the evictions come from the work after it." if during == 0 and not d.get("eval_batch_error")
              else "Evictions during evaluation mean some evaluated batches lack their custom latent space.")),
            "Tensorleap: confirm which batches lost their custom latent space and why the stash was not drained.",
            lines=[_quote(job_dir, CONSUMER, "sparse custom-LS stash over capacity")]))
    hs = d.get("heatmap_non_spatial")
    if hs:
        overlay = _stage_seconds(view, "vis_calculator.overlay", GENERIC)
        fs.append(_finding(
            "engine-heatmap-non-spatial", "Heatmaps were computed from non-spatial feature maps %s%d times%s" % (
                "at least " if hs["lower_bound"] else "", hs["count"],
                "; their overlays cost %s of worker time" % _rc_t(overlay) if overlay else ""),
            "compute", "heatmaps", "-", "tensorleap",
            ["'Every feature map was rejected as non-spatial, falling back to the unfiltered set' ×%d on %d "
             "worker pod(s)" % (hs["count"], hs["pods"]),
             "heatmap overlays on the workers: %s" % _rc_t(overlay) if overlay else None],
            "No feature map of this input has spatial structure (e.g. a tabular input), so its heatmap is built "
            "from the unfiltered set and carries little meaning — yet the heatmap and its overlay are still computed "
            "for every visualized sample.",
            "Tensorleap: the heatmap path for inputs with no spatial feature maps.", seconds=overlay or None,
            lines=[_quote(job_dir, GENERIC, "Every feature map was rejected as non-spatial")]))
    hm = d.get("heatmap_mask_fallback")
    if hm:
        fetch = _stage_seconds(view, "heatmap.instance_mask_fetch", GENERIC)
        fs.append(_finding(
            "engine-heatmap-masks", "Instance heatmaps fell back to the whole-sample heatmap %s%d times%s" % (
                "at least " if hm["lower_bound"] else "", hm["count"],
                "; fetching the masks cost %s of worker time" % _rc_t(fetch) if fetch else ""),
            "compute", "heatmaps", "-", "tensorleap",
            ["'Failed to apply mask on heatmap, using regular heatmap' ×%d on %d worker pod(s)" % (
                hm["count"], hm["pods"]),
             "fetching instance masks on the workers: %s" % _rc_t(fetch) if fetch else None],
            "The instance mask could not be applied to the heatmap (their resolutions differ), so each instance's "
            "heatmap shows the whole sample. Fetching each instance's mask still costs a sample regeneration.",
            "Tensorleap: heatmap resolution vs instance-mask resolution for this model.", seconds=fetch or None,
            lines=[_quote(job_dir, GENERIC, "Failed to apply mask on heatmap")]))
    rpb = d.get("rows_per_batch")
    if rpb and configured_batch and rpb["median"] < SMALL_BATCH * configured_batch:
        fs.append(_finding(
            "engine-small-batches", "Evaluation ran %.1f rows per batch vs %d configured" % (rpb["median"], configured_batch),
            "compute", "evaluation loop", "see the limiter", "tensorleap",
            ["evaluated rows per evaluation step (memory probes): median %.1f, range %.1f-%.1f over %d intervals" % (
                rpb["median"], rpb["min"], rpb["max"], rpb["n"])],
            "Smaller batches than configured cost more per row on the model.",
            "Tensorleap: why the evaluation batches are smaller than configured for this run."))
    sc = d.get("sample_claim")
    if sc and sc["min"] == 1 and sc["sample_bytes"] >= LARGE_SAMPLE_BYTES:
        matters = wait_share is not None and wait_share >= NEAR_WAIT
        fs.append(_finding(
            "engine-claim-one", "Each sample moves %.1f MB, so workers claim and push one sample at a time" % (
                sc["sample_bytes"] / 1e6),
            "I/O", "data movement: sample queue", "on" if matters else ("off" if wait_share is not None else
                                                                      "see the limiter"), "integration",
            ["'calculated max_samples_per_batch' = %d with sample_bytes %.1f MB" % (sc["min"], sc["sample_bytes"] / 1e6),
             "the evaluation waited for samples %s of each step" % _pct(wait_share) if wait_share is not None else None],
            "Every sample is written, transferred and read back at this size, and one at a time: a sample larger than "
            "a worker's per-claim budget is never grouped with others. " +
            ("The evaluation waits for samples, so this round trip per sample is on its critical path." if matters
             else "It costs throughput only when the evaluation waits for samples; here it barely does."
             if wait_share is not None else ""),
            "Shrink what each sample carries (input dtype and resolution, ground-truth and mask size) if data movement "
            "or the sample queue shows up as a cost."))
    rs = d.get("results_stream")
    if rs and rs["pushed"] > rs["pulled"] * 1.2:
        fs.append(_finding(
            "engine-stream-backlog", "Results stream fell behind: %d docs pushed, %d drained" % (rs["pushed"], rs["pulled"]),
            "I/O", "streaming", "on (tail)", "tensorleap",
            ["scaler ticks: pushed %d, pulled %d, up to %d replicas" % (rs["pushed"], rs["pulled"], rs["max_replicas"]),
             "stream back-pressure lines: %d" % rs["pressure_lines"] if rs.get("pressure_lines") else None],
            "Results arrived faster than the index writers drained them: the backlog grows during evaluation and "
            "is paid for after it.", "Tensorleap: storage / index write latency for this run."))
    if d.get("disk_guard_wait_s"):
        fs.append(_finding(
            "engine-disk-guard", "Workers waited %.0f s for shared-disk space" % d["disk_guard_wait_s"],
            "I/O", "storage", "on", "tensorleap", ["shared-disk guard waits: %.1f s" % d["disk_guard_wait_s"]],
            "Producers were throttled because the shared disk was nearly full.", "Free shared-disk space on the server.",
            seconds=d["disk_guard_wait_s"]))
    mf = d.get("memory_floor")
    if mf and mf["gb_per_1000_rows"] > 0.05:
        fs.append(_finding(
            "engine-memory-floor", "Evaluation-pod memory floor rose %.2f GB per 1,000 rows" % mf["gb_per_1000_rows"],
            "memory", "consumer", "-", "tensorleap",
            ["memory after each trim: %.2f → %.2f GB over %d trims" % (mf["first_gb"], mf["last_gb"], mf["trims"])],
            "Memory that survives the periodic trim keeps growing: something is retained per row.",
            "Tensorleap: the evaluation pod's retained memory per row."))
    if d.get("worker_restarts"):
        n = sum(d["worker_restarts"].values())
        fs.append(_finding(
            "engine-worker-restarts", "Worker service restarted %d time(s) inside running pods" % n, "memory", "workers",
            "on", "tensorleap", ["%s: %d restart(s)" % (p, k) for p, k in sorted(d["worker_restarts"].items())],
            "A worker that starts again re-runs its preprocess and loses the work in progress.",
            "Tensorleap: the pod's previous container log (out of memory or a crash).",
            lines=[_quote(job_dir, GENERIC, "Starting generic processor service")]))
    if d.get("publish_retry"):
        n = d["publish_retry"]
        fs.append(_finding(
            "engine-publish-retry", "Progress publishing to the platform failed and was retried %d time(s), "
            "at least %d s of the evaluation pod's time" % (n, 5 * n), "scheduling", "other infrastructure (messaging)",
            "on", "tensorleap", ["'Failed to publish to RabbitMQ, retrying in 5 seconds.' ×%d (ERROR)" % n],
            "Publishing runs on the evaluation pod's main thread, so every retry pauses the run.",
            "Tensorleap: the message broker's availability during this run.", seconds=5.0 * n,
            lines=[_quote(job_dir, CONSUMER, "Failed to publish to RabbitMQ")]))
    if d.get("vis_timeout"):
        fs.append(_finding("engine-vis-timeout", "The visualization phase timed out waiting for workers (%d)" % d["vis_timeout"],
                           "scheduling", "visualization pipeline", "on (tail)", "tensorleap",
                           ["'Got timeout before vis done.' ×%d" % d["vis_timeout"]],
                           "Visualization stopped waiting for workers that made no progress.", "Tensorleap: see the log lines.",
                           lines=[_quote(job_dir, CONSUMER, "Got timeout before vis done")]))
    return fs


# --------------------------------------------------------------------------- #
# report: the "Online diagnostics" section, rendered from analysis.json
# --------------------------------------------------------------------------- #

PUBLIC_NAMES = {
    "evaluate:trainer.infer": "model inference (per call)",
    "evaluate:trainer.validation_step": "evaluation step: inference + hand-off (per call)",
    "evaluate:trainer.validation_step.pull_batch": "waiting for samples (per call)",
    "evaluate:trainer.validation_step.push_metrics": "metrics hand-off (per call)",
    "evaluate:trainer.validation_step.extract_ls": "embedding extraction (per call)",
    "evaluate:trainer.validation_step.marshal_numpy": "output conversion (per call)",
    "series:generation_per_sample": "sample generation: your encoders + metadata (per sample)",
    "series:metrics_batch": "custom metrics (per batch)",
    "series:visualizers_batch": "visualizers + upload (per sample)",
    "series:metrics_idle": "metrics workers idle (per idle period)",
    "series:visualizers_idle": "visualizer workers idle (per idle period)",
    "generic-process:generic_processor.idle_wait": "workers idle (per wait)",
}
CP_LABEL = {"on": "on", "on (tail)": "on (after evaluation)", "off": "off", "see the limiter": "-"}
ACTIVE_STATUSES = ("SUBMITTED", "IN PROGRESS", "STARTED", "RUNNING", "PENDING", "QUEUED")


def _analysis_path(sv, out):
    return os.path.join(out, sv.get("analysis") or os.path.join(ONLINE_DIR, "analysis.json"))


def validate_online(doc, out):
    """A diagnostics report with a finished run needs a readable analysis."""
    sv = doc.get("server_validation") if isinstance(doc, dict) else None
    if not isinstance(sv, dict) or sv.get("mode") != "diagnostics":
        return []
    if (sv.get("status") or "").upper() in ACTIVE_STATUSES:
        return []
    path = _analysis_path(sv, out)
    try:
        a = _load_json(path)
    except ValueError as exc:
        return ["%s is not valid JSON (%s): re-run `tl_perf online analyze`" % (path, exc)]
    if a is None:
        return ["server_validation.mode is 'diagnostics' but %s is missing: run `tl_perf online collect` "
                "until it exits 0, then `tl_perf online analyze`" % path]
    missing = [k for k in ("findings", "phases", "components", "confidence") if k not in a]
    return ["%s lacks %s: re-run `tl_perf online analyze`" % (path, ", ".join(missing))] if missing else []


def _clip(line, n=200):
    return line if len(line) <= n else line[:n] + "…"


def _s(x, unit="s"):
    if x is None:
        return "-"
    if unit == "s":
        return "%.0f s" % x if x >= 100 else ("%.1f s" % x if x >= 1 else "%.1f ms" % (1000 * x))
    return "%.2f %s" % (x, unit)


def _pct(x):
    return "-" if x is None else "%.0f%%" % (100 * x)


def _md(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return lines


def _component_rows(a):
    loop_cp = {"waiting for samples": "producer-bound", "model inference": "inference-floor",
               "metrics hand-off": "metrics-bound"}
    limiter = next((f for f in a.get("findings") or [] if f["id"] in
                    ("producer-bound", "metrics-bound", "inference-floor", "consumer-bound")), None)
    rows, other = [], 0
    for key, r in sorted((a.get("components") or {}).items(), key=lambda kv: -(kv[1].get("sum_seconds") or 0)):
        name = PUBLIC_NAMES.get(key)
        if not name:
            other += 1
            continue
        base = name.split(" (")[0]
        cp = "-"
        if limiter:
            if loop_cp.get(base) == limiter["id"] or (limiter["id"] == "producer-bound" and base.startswith("sample generation")):
                cp = "**on — the limiter**"
            elif key.startswith("evaluate:"):
                cp = "on"
            elif base.startswith("sample generation") and limiter["id"] != "producer-bound":
                cp = "off"
        rows.append([name, r.get("calls"), _s(r.get("mean")), _s(r.get("p50")), _s(r.get("p90")),
                     _s(r.get("p95")), _s(r.get("p99")), _pct(r.get("share_of_window")), cp])
    return rows, other


def _rel(t, t0):
    return "+%.0f s" % (t - t0) if t is not None and t0 is not None else "-"


CONFIDENCE_LABEL = {"measured": "measured", "inferred": "inferred (a link is not directly measured)",
                    "candidate": "candidate (less than half of the phase is explained)"}


def render_root_causes(roots):
    """K29: one chain per slow phase — who set the pace, where its time went, the mechanism,
    the engine part to change, what is ruled out, and how much of the phase is explained."""
    L = ["### Root causes", ""]
    roots = [r for r in roots or [] if not r.get("error")]
    if not roots:
        return L + ["_The collected logs carry no step-level engine timing for the slow phases (an engine without "
                    "these log lines, or lines lost in collection); the timeline and stages above are the finest "
                    "resolution available._", ""]
    L += ["For each phase that took at least %s of the run: who set the pace, where that side's time went, the "
          "mechanism, and the part to change. Derived from this run's own logs; inferred links are marked." % _pct(RC_MIN_SHARE), ""]
    other = [r for r in roots if r.get("kind") == "other-mechanisms"]
    roots = [r for r in roots if r.get("kind") != "other-mechanisms"]
    for i, r in enumerate(roots, 1):
        if r.get("kind") == "memory":
            L += _render_memory_root_cause(i, r)
            continue
        L += ["**%d. %s — %s (%s of the run)**" % (i, r["phase"], _s(r["wall_seconds"]), _pct(r.get("share_of_run"))), "",
              "- **Pace set by:** %s" % r["pacing"],
              "- **Mechanism:** %s%s" % (r["mechanism"], " _(inferred)_" if r.get("link") == "inferred" else "")]
        if r.get("driver") and r["driver"] != "-":
            L.append("- **Driver:** %s" % r["driver"])
        if r.get("parts"):
            L += ["- **Where the time went:**", ""]
            L += ["  " + l for l in _md(["part", "time", "share of the phase", ""],
                                         [[p["name"], _s(p["seconds"]), _pct(p.get("share_of_phase")),
                                           "" if p["how"] == "measured" else p["how"]] for p in r["parts"][:8]])]
            L += [""]
        L.append("- **Explained:** %s of the phase%s; confidence: %s" % (
            _pct(r.get("explained_share")), " (%s unexplained)" % _s(r["unexplained_seconds"])
            if r.get("unexplained_seconds", 0) >= 1 else "", CONFIDENCE_LABEL.get(r["confidence"], r["confidence"])))
        if r.get("optimize"):
            L.append("- **Part to change:** " + "; ".join(r["optimize"]))
        if r.get("ruled_out"):
            L.append("- **Ruled out:** " + "; ".join(r["ruled_out"]))
        for e in r.get("evidence") or []:
            L.append("- Evidence: %s" % e)
        for n in r.get("notes") or []:
            L.append("- Also seen: %s" % n)
        if r.get("mechanisms"):
            L.append("- **Mechanisms found** (generic rules over this phase's measurements):")
            L += ["  - %s" % _render_signal(m, r.get("wall_seconds")) for m in r["mechanisms"]]
        if r.get("to_confirm"):
            L.append("- To confirm on the next run: " + "; ".join(r["to_confirm"]))
        L.append("")
    for r in other:
        L += ["**Mechanisms in shorter phases**", ""]
        L += ["- %s: %s" % (m["phase"], _render_signal(m, None)) for m in r["mechanisms"]]
        L.append("")
    return L


def _render_signal(m, wall):
    cost = m.get("cost_seconds")
    return "_%s_ — %s%s%s%s" % (
        m["rule_name"], m["statement"],
        " Cost: %s%s." % (_rc_t(cost), " (%s of the phase)" % _pct(cost / wall) if wall else "") if cost else "",
        " Not on the side that set the pace." if m.get("on_pacing_side") is False else "",
        " Part to change: %s." % m["change"] if m.get("change") else "") + (
        " _(inferred)_" if m.get("how") == "inferred" else "")


def _covered_ids(roots, primary):
    """Findings a root-cause chain already explains in more detail."""
    ids = set()
    for r in roots:
        if r.get("error") or r.get("kind") == "other-mechanisms":
            continue
        if r.get("kind") == "memory":
            ids.add("memory-priority")
            continue
        if r.get("step") == "after_visualizations":
            ids |= {"engine-visualization", "visualization-tail"}
        elif r["phase"].startswith("start-up"):
            ids |= {"engine-start-up", "start-up"}
        elif r["phase"] == "evaluation loop":
            ids |= {"inference-floor", "consumer-bound", "producer-bound", "metrics-bound"}
        elif r.get("step"):
            ids.add("engine-" + r["step"])
    return ids


def render_tensorleap_actions(fs, roots, primary):
    L = ["### Recommended Tensorleap actions (from this run)", ""]
    roots = [r for r in roots if not r.get("error")]
    covered = _covered_ids(roots, primary)
    integration = []
    n = 0
    for r in roots:
        if r.get("kind") == "other-mechanisms":
            for m in r["mechanisms"]:
                if m.get("change") and m.get("cost_seconds"):
                    if m["change"].startswith("integration:"):
                        integration.append(m["change"][len("integration:"):].strip())
                        continue
                    n += 1
                    L.append("- **%s** (%s, %s): %s" % (m["phase"], m["rule_name"], _rc_t(m["cost_seconds"]), m["change"]))
            continue
        if r.get("kind") == "memory":
            opts = [o for o in r.get("optimize") or [] if not o.startswith("integration:")]
            integration += [o[len("integration:"):].strip() for o in r.get("optimize") or [] if o.startswith("integration:")]
            if opts:
                n += 1
                L.append("- **Memory%s: %s** — peak %.1f GB of %s: %s." % (
                    " (the priority)" if r.get("pressure") else "", r["subject"], r["peak_gb"],
                    "%.1f GB" % r["limit_gb"] if r.get("limit_gb") else "-", "; ".join(opts)))
            continue
        opts = [o for o in r.get("optimize") or [] if not o.startswith("integration:")]
        integration += [o[len("integration:"):].strip() for o in r.get("optimize") or [] if o.startswith("integration:")]
        if not opts:
            continue
        n += 1
        L.append("- **%s** — %s of the run, pace set by %s: %s. _(%s of the phase explained; %s)_" % (
            r["phase"], _pct(r.get("share_of_run")), r["pacing"], "; ".join(opts),
            _pct(r.get("explained_share")), r["confidence"]))
    rest = [f for f in fs.values() if f["owner"] == "tensorleap" and f["id"] not in covered]
    warnings = [f for f in rest if f["id"].startswith("engine-warning")]
    on_path = [f for f in rest if f not in warnings and f["critical_path"] in ("on", "on (tail)")]
    off_path = [f for f in rest if f not in warnings and f not in on_path]
    for f in on_path:
        n += 1
        L += ["- **%s**" % f["title"], "  - Where: %s" % f["location"],
              "  - Measurements: %s" % "; ".join(f["evidence"]),
              "  - Critical-path impact: %s%s" % (CP_LABEL.get(f["critical_path"], f["critical_path"]),
                                                  ", %s of the run" % _pct(f["share_of_job"]) if f.get("share_of_job") else ""),
              "  - Investigate: %s" % f["suggestion"]]
        L += ["  - Log: `%s`" % _clip(l) for l in f.get("log_lines", [])[:1]]
    if off_path:
        L += ["", "Engine behaviour to check (no measured runtime cost in this run):"]
        L += ["- %s — %s" % (f["title"], f["suggestion"]) for f in off_path]
        n += len(off_path)
    if warnings:
        L += ["", "Engine warnings to review (%d kinds; full list under Engine warnings and errors): %s." % (
            len(warnings), "; ".join(f["title"].split(": ", 1)[-1] for f in warnings[:5]) +
            ("; …" if len(warnings) > 5 else ""))]
        n += 1
    if not n:
        L += ["_None._"]
    other = [f for f in fs.values() if f["owner"] == "integration" and f["critical_path"] != "off"
             and f["id"] not in covered]
    items = list(dict.fromkeys(integration))          # once each, in order
    if items or other:
        L += ["", "In the integration:"] + ["- %s" % i for i in items] + \
             ["- %s — %s" % (f["title"], f["suggestion"]) for f in other]
    return L


def _render_memory_root_cause(i, r):
    L = ["**%d. Memory%s — %s: peak %.1f GB of %s%s**" % (
             i, " (the priority)" if r.get("pressure") else "", r["subject"], r["peak_gb"],
             "%.1f GB" % r["limit_gb"] if r.get("limit_gb") else "an unknown limit",
             ", during %s" % r["peak_phase"] if r.get("peak_phase") else ""), "",
         "- **Mechanism:** %s" % r["mechanism"], "- **Where the memory went (one pod):**", ""]
    L += ["  " + l for l in _md(["part", "GB"], [[p["name"], "%.1f" % p["gb"]] for p in r["parts"]])]
    L += ["", "- **Confidence:** %s" % ("measured (the pods' own resource readings)" if r["confidence"] == "measured"
                                        else "inferred (limits and logged peaks only)")]
    if r.get("optimize"):
        L.append("- **Part to change:** " + "; ".join(o.replace("integration: ", "your integration: ")
                                                     for o in r["optimize"]))
    L += ["- Evidence: %s" % e for e in r.get("evidence") or []] + [""]
    return L


def render_server_settings(st):
    """K30: what each pod got against what the run used. Recommends only."""
    if not st:
        return []
    L = ["### Server settings", "",
         "What each Tensorleap pod got, what this run used, and whether the setting limited the run or sat mostly "
         "unused. Resource settings: **%s**. This report recommends; it never changes server settings." % st["mode"], ""]
    if st.get("note"):
        L += ["_%s._" % st["note"], ""]
    L += _md(["setting", "got", "used", "verdict", "effect of a change"],
             [[r["setting"], r["got"], r["used"], r["verdict"], r.get("effect", "-")] for r in st.get("rows") or []])
    recs = [r for r in st.get("rows") or [] if r.get("recommendation")]
    if recs:
        L += ["", "Recommendations:"] + ["- **%s:** %s" % (r["setting"], r["recommendation"]) for r in recs]
    return L + [""]


def render_online_section(sv, out):
    path = _analysis_path(sv, out)
    a = _load_json(path)
    if a is None:
        return ["## Online diagnostics", "", "_Collection in progress — run `tl_perf online collect` until it "
                "exits 0, then `tl_perf online analyze` and re-render._", ""]
    eng = a.get("engine") or {}
    fs = {f["id"]: f for f in a.get("findings") or []}
    cfg = a.get("config") or {}
    mem = a.get("memory") or {}
    pods = mem.get("pods") or {}
    L = ["## Online diagnostics", "",
         "One authorized run on the Tensorleap server, read from the platform's own logs (`leap run logs`). "
         "Engine stages, transfers and warnings are named as they appear in those logs. Confidence: **%s**%s." % (
             a.get("confidence"), " — " + "; ".join(a.get("confidence_reasons") or []) if a.get("confidence_reasons") else ""), ""]

    # Environment
    srv = a.get("server") or {}
    workers = [p for p in pods.values() if p.get("role") == GENERIC]
    ev_pod = next((p for p in pods.values() if p.get("role") == CONSUMER), {})
    children = sorted(set((cfg.get("children_per_pod") or {}).values())) or ["?"]
    rows = [["run", "%s — %s, %s" % (a.get("job"), a.get("status") or "imported", _s(a.get("job_seconds")))],
            ["engine version", srv.get("version") or "-"],
            ["GPUs", srv.get("gpus") if srv.get("gpus") is not None else "-"],
            ["worker pods", "%d (× %s process each)" % (len(workers), "/".join(map(str, children)))],
            ["memory limits", "evaluation pod %s; workers %s" % (
                "%.1f GiB" % ev_pod["limit_gb"] if ev_pod.get("limit_gb") else "-",
                "%.1f GiB" % mem["worker_limit_gb"] if mem.get("worker_limit_gb") else "-")],
            ["batch size (configured)", cfg.get("batch_size", "-")],
            ["sample payloads", cfg.get("payload_format") or "-"]]
    L += ["### Environment", ""] + _md(["", ""], rows) + [""]

    # Engine timeline
    tl = eng.get("timeline") or []
    t0 = tl[0]["start"] if tl else None
    L += ["### Engine timeline", ""]
    if tl:
        # post-processing (the platform's analysis after evaluation) is shown as one row of time
        # and not analyzed
        pp = [s for s in tl if _phase_kind(s) == "post-processing"]
        items = [(s["start"], [s["stage"], _rel(s["start"], t0), _s(s["seconds"]), _pct(s.get("share_of_run"))])
                 for s in tl if _phase_kind(s) != "post-processing" and
                 (s["seconds"] >= 0.05 or s["stage"] == "evaluation loop")]
        if pp:
            secs = sum(s["seconds"] for s in pp)
            job = a.get("job_seconds")
            items.append((pp[0]["start"], ["post-processing (not analyzed)", _rel(pp[0]["start"], t0), _s(secs),
                                           _pct(secs / job) if job else "-"]))
        L += _md(["stage", "starts", "duration", "share of the run"], [r for _, r in sorted(items, key=lambda kv: kv[0])])
        st = (a.get("phases") or {})
        notes = []
        for ph in ("evaluation", "visualization"):
            p = st.get(ph) or {}
            if p.get("reached"):
                notes.append("%s reached a steady pace after %s (%.2f %s/s for %s)" % (
                    ph, _s(p["warmup"]["seconds"]), p["stable"]["rate_median"], p.get("unit"), _s(p["stable"]["seconds"])))
            elif p:
                notes.append("%s never reached a steady pace (%s)" % (ph, p.get("reason")))
        if notes:
            L += ["", "Pace: " + "; ".join(notes) + "."]
    else:
        L += ["_No engine step markers in the collected logs._"]
    L += [""]

    # Primary engine bottleneck
    prim = fs.get(a.get("primary"))
    L += ["### Primary bottleneck", ""]
    if prim:
        L += ["**%s** — %s" % (prim["title"], prim["explanation"]), "",
              "- Where: %s · %s · critical path: %s · owner: %s" % (
                  prim["location"], prim["type"], CP_LABEL.get(prim["critical_path"], prim["critical_path"]), prim["owner"])]
        L += ["- %s" % e for e in prim["evidence"]]
        L += ["- Log: `%s`" % _clip(l) for l in prim.get("log_lines", [])[:2]]
    else:
        L += ["**No primary bottleneck claimed:** no measured critical-path time in the collected logs."]
    sec = [fs[i] for i in a.get("secondary") or [] if i in fs]
    if sec:
        L += ["", "Next on the critical path:"] + ["- %s (%s)" % (f["title"], f["location"]) for f in sec]
    L += [""]
    L += render_root_causes(a.get("root_causes")) + render_server_settings(a.get("server_settings"))

    # Engine measurements: neutral numbers from the engine's lines
    d = eng.get("measurements") or {}
    if d:
        meas = []
        if d.get("rows_per_batch"):
            meas.append("rows per evaluation batch: median %.1f (configured %s)" % (
                d["rows_per_batch"]["median"], cfg.get("batch_size", "-")))
        if d.get("sample_claim"):
            c = d["sample_claim"]
            meas.append("per-sample payload %.1f MB; workers claim %s sample(s) at a time" % (
                c["sample_bytes"] / 1e6, c["min"] if c["min"] == c["max"] else "%d-%d" % (c["min"], c["max"])))
        if d.get("results_stream"):
            rs = d["results_stream"]
            meas.append("results stream: %d docs pushed, %d drained, up to %d writer replicas" % (
                rs["pushed"], rs["pulled"], rs["max_replicas"]))
        for f, lab in (("minio_ls_secs", "latent-space bulk writes"), ("es_secs", "index bulk writes")):
            b = (d.get("stream_bulk_write") or {}).get(f)
            if b:
                meas.append("%s: %d, P50 %s, P95 %s, total %s" % (lab, b["n"], _s(b["p50"]), _s(b["p95"]), _s(b["sum"])))
        if d.get("stash_evictions"):
            se = d["stash_evictions"]
            meas.append("custom latent-space stash evictions: %d (%d during evaluation, %d after)" % (
                se["total"], se["during_evaluation"], se["after_evaluation"]))
        if d.get("memory_floor"):
            mf = d["memory_floor"]
            meas.append("evaluation-pod memory after each trim: %.2f → %.2f GB (%.3f GB per 1,000 rows)" % (
                mf["first_gb"], mf["last_gb"], mf["gb_per_1000_rows"]))
        for key, lab in (("disk_guard_wait_s", "shared-disk guard waits (s)"), ("eval_batch_error", "failed evaluation batches"),
                         ("vis_drain_wait", "waits for visualization workers to finish"), ("vis_timeout", "visualization timeouts"),
                         ("publish_retry", "progress publish retries")):
            if d.get(key):
                meas.append("%s: %s" % (lab, ("%.1f" % d[key]) if isinstance(d[key], float) else d[key]))
        if d.get("worker_restarts"):
            meas.append("worker service restarts: %s" % ", ".join("%s ×%d" % kv for kv in sorted(d["worker_restarts"].items())))
        if meas:
            L += ["Engine measurements:", ""] + ["- %s" % m for m in meas] + [""]

    # Engine stages by component
    stages = [s for s in eng.get("stages") or [] if (s.get("seconds") or 0) >= 0.05]
    if stages:
        L += ["### Engine stages by component", "",
              "Every timed engine operation during start-up, evaluation and visualization (busy time; work on "
              "several pods overlaps). Post-processing is left out.", ""]
        rows = []
        for s in sorted(stages, key=lambda s: (s["category"], -(s["seconds"] or 0))):
            rows.append([s["category"], "`%s`" % s["span"], s.get("label") or "-", s["role"], s["calls"],
                         _s(s["seconds"]), _s(s["mean"]), _s(s["max"]), _s(s.get("p95"))])
        L += _md(["stage", "engine name", "what it is", "pod", "calls", "total", "mean", "max", "P95"], rows) + [""]
        cnt = eng.get("counters") or []
        if cnt:
            L += ["Engine counters (a value per call — a count, size or share — not time):", ""]
            L += _md(["what it counts", "engine name", "pod", "values", "mean", "max"],
                     [[c.get("label") or "-", "`%s`" % c["span"], c["role"], c["samples"],
                       "%.3g" % c["mean"] if c.get("mean") is not None else "-",
                       "%.3g" % c["max"] if c.get("max") is not None else "-"]
                      for c in sorted(cnt, key=lambda c: c["span"])]) + [""]

    # Data movement
    evs = eng.get("events") or {}
    dm = [s for s in stages if s["category"].startswith(("data movement", "storage"))]
    L += ["### Data movement: Redis queues, storage, uploads", ""]
    if dm:
        L += _md(["operation", "engine name", "pod", "calls", "total", "mean", "max"],
                 [[s.get("label") or s["category"], "`%s`" % s["span"], s["role"], s["calls"], _s(s["seconds"]),
                   _s(s["mean"]), _s(s["max"])] for s in sorted(dm, key=lambda s: -(s["seconds"] or 0))]) + [""]
    up = eng.get("uploads") or {}
    if up:
        L += ["Image and file hand-offs to storage (encode + hand-off to the upload pool; the transfer itself runs "
              "in the background and is not timed):", ""]
        L += _md(["kind", "count", "total (all pods)", "P50", "P95", "max"],
                 [[k, v["count"], _s(v["seconds"]), _s(v["p50"]), _s(v["p95"]), _s(v["max"])] for k, v in sorted(up.items())]) + [""]
    ev_rows = []
    labels = {"sample_queue_full": "sample queue full: workers paused (`max number of samples in queue reached`)",
              "metrics_backpressure": "metrics queue back-pressure waits",
              "results_stream_push": "results-stream pushes from workers", "results_stream_scaler": "results-stream scaler ticks",
              "blob_read": "payload reads from storage on workers", "heap_trim": "evaluation-loop heap trims",
              "visualize_batch": "visualization batches started", "progress_publish": "progress messages to the platform",
              "stash_eviction": "custom latent-space stash evictions", "ls_reload": "latent-space store loads",
              "remote_call": "remote calls from the evaluation pod", "command_served": "remote calls served by workers",
              "worker_code_load": "workers loading the integration", "first_batch": "first evaluated batch",
              "resources": "resource readings (CPU, memory)", "rss_probe": "evaluation-loop memory probes",
              "sample_claim": "worker sample-claim sizing", "worker_start": "worker processes started",
              "stream_bulk_write": "results-stream bulk writes", "vis_drain_wait": "waits for visualization workers",
              "metrics_queue_full": "metrics queue full: pushes waited", "publish_retry": "progress publish retries"}
    for k, v in sorted(evs.items(), key=lambda kv: -kv[1]["count"]):
        extra = []
        for f, lab in (("duration_seconds", "waited"), ("doc_count", "docs"), ("pushed_docs", "docs pushed"),
                       ("heap_returned_gb", "GB returned"), ("desired_replicas", "max replicas")):
            if f in v:
                val = v[f]["max"] if f == "desired_replicas" else v[f]["sum"]
                extra.append("%s %s" % (lab, ("%d" % val) if f in ("doc_count", "pushed_docs", "desired_replicas")
                                        else ("%.2f s" % val if f == "duration_seconds" else "%.2f" % val)))
        ev_rows.append([labels.get(k, k), v["count"], v["pods"], "; ".join(extra) or "-"])
    if ev_rows:
        L += _md(["engine event", "count", "pods", "amount"], ev_rows) + [""]

    # Evaluation loop
    sh = a.get("loop_shares") or {}
    L += ["### Evaluation loop: model and feature extraction", ""]
    if sh:
        L += ["Per evaluation iteration (%s): inference %s, waiting for samples %s, metrics hand-off %s, embedding "
              "(feature) extraction %s, output conversion %s, other %s. Workers idle %s of their time%s." % (
                  a.get("statistics_basis"), _pct(sh.get("inference")), _pct(sh.get("waiting for samples")),
                  _pct(sh.get("metrics hand-off")), _pct(sh.get("extract_ls")), _pct(sh.get("marshal_numpy")),
                  _pct(sh.get("other")), _pct(a.get("worker_idle_share")),
                  "; GPU-utilization proxy %.2f" % a["gpu_utilization_proxy"] if a.get("gpu_utilization_proxy") is not None else "")]
        lim = next((f for f in fs.values() if f["id"] in ("producer-bound", "metrics-bound", "inference-floor", "consumer-bound")), None)
        if lim:
            L += ["", "Inside evaluation: **%s**." % lim["title"]]
    else:
        L += ["_No evaluation-loop timing in the collected logs._"]
    gap = a.get("gap_to_floor")
    if gap:
        L += ["", "Gap to the model floor: %s per row online vs %s per row offline (×%.1f) — %s." % (
            _s(gap["online_s_per_row"]), _s(gap["floor_s_per_row"]), gap["ratio"], gap.get("basis", ""))]
    L += [""]

    # Engine warnings and errors
    ws = eng.get("warnings") or []
    L += ["### Engine warnings and errors", ""]
    L += _md(["level", "pod", "message", "count", "pods", "first", "last"],
             [[w["level"], w["role"], "`%s`" % w["message"][:110], w["count"], len(w["pods"]), _rel(w["first"], t0),
               _rel(w["last"], t0)] for w in ws]) if ws else ["_None logged._"]
    L += [""]

    # Memory
    L += ["### Memory", ""]
    mrows = [[p, v.get("role"), "%.1f GiB" % v["limit_gb"] if v.get("limit_gb") else "-",
              "%.2f GB" % v["peak_rss_gb"] if v.get("peak_rss_gb") else "-", v.get("restarts") or 0,
              "**yes**" if v.get("oom") else "no"]
             for p, v in sorted(pods.items()) if v.get("role") in (CONSUMER, GENERIC)]
    if mrows:
        L += _md(["pod", "role", "limit", "peak (logged)", "restarts", "out of memory"], mrows) + [""]

    # User code, briefly
    comp = a.get("comparison") or []
    crow, _ = _component_rows(a)
    user = [r for r in crow if r[0].startswith(("sample generation", "custom metrics", "visualizers"))]
    L += ["### User code (summary)", ""]
    if user:
        L += _md(["component", "calls", "mean", "P50", "P90", "P95", "P99"], [r[:7] for r in user]) + [""]
    if comp:
        L += _md(["vs offline", "online", "offline", "×", "note"],
                 [[c["component"], _s(c["online"]), _s(c["offline"]), "%.2f" % c["ratio"],
                   c["note"] + ("" if c.get("like_for_like") else " (not like-for-like)")] for c in comp]) + [""]

    # Recommended Tensorleap actions: root causes first, then what they don't cover
    L += render_tensorleap_actions(fs, a.get("root_causes") or [], a.get("primary"))
    cov = a.get("coverage") or {}
    L += ["", "Coverage: %s, %d poll(s), %d gap(s)%s." % (
        "followed during the run" if cov.get("source") == "poll" else "imported after the run",
        cov.get("polls") or 0, cov.get("gaps") or 0,
        "; " + "; ".join(a.get("notes")) if a.get("notes") else ""), ""]
    return L


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def online_dir(out):
    return os.path.join(out, ONLINE_DIR)


ACTIONS = {
    "collect": cmd_collect,
    "analyze": cmd_analyze,
}


def add_parser(sub, common, default_out):
    """Register `online` and its actions on tl_perf's subparsers. The shared options
    (--root, --out, ...) belong to each action: `online collect --out X`."""
    p = sub.add_parser("online",
                       help="online diagnostics of one Tensorleap run (collect / analyze)")
    p.set_defaults(root=".", entry=None, out=default_out)
    acts = p.add_subparsers(dest="online_action")

    a = acts.add_parser("collect", parents=[common],
                        help="follow a run while it executes and keep its logs")
    a.add_argument("--job", required=False, default=None,
                   help="the push run id (its chained evaluation is followed) or the evaluation run id")
    a.add_argument("--interval", type=float, default=DEFAULT_POLL_SECONDS,
                   help="seconds between polls")
    a.add_argument("--for", dest="for_seconds", type=float, default=None,
                   help="return after this many seconds (exit 20 while the run is still going)")
    a.add_argument("--full-visualization", action="store_true",
                   help="follow the whole visualization (by default collect exits 21 once its pace is steady, "
                        "so the user can stop the run)")
    a.add_argument("--from-tar", default=None,
                   help="import a finished run from a `leap run logs --output` tar instead of polling "
                        "(only each worker's last lines survive: reduced coverage)")

    a = acts.add_parser("analyze", parents=[common],
                        help="stable-window statistics, bottleneck attribution, offline comparison")
    a.add_argument("--job", default=None, help="collected run id (default: the latest collected)")
    a.add_argument("--window", type=float, default=DEFAULT_WINDOW_SECONDS,
                   help="measurement window length, in seconds")
    a.add_argument("--stable-windows", type=int, default=DEFAULT_STABLE_WINDOWS,
                   help="consecutive steady windows that start the stable period")
    a.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                   help="steady = throughput within this fraction of the rolling median")
    a.add_argument("--priority", choices=("runtime", "memory"), default=None,
                   help="what leads the diagnostics: runtime or memory (default: the priority `score` ran with)")
    a.add_argument("--settings-mode", choices=SETTINGS_MODES, default=None,
                   help="whether the server used its automatic resource settings or manual ones "
                        "(as the user answered at gate 6.0); omitted: not stated")
    return p


def run(args, out, tl):
    """`tl` is the tl_perf module: its artifact readers and cost model are reused."""
    action = getattr(args, "online_action", None)
    if action not in ACTIONS:
        print("tl_perf online: choose an action: %s" % ", ".join(sorted(ACTIONS)), file=sys.stderr)
        return EXIT_BLOCKER
    return ACTIONS[action](args, out, tl)
