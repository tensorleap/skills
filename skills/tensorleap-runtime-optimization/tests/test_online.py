"""Tests for scripts/perf_online.py (online diagnostics), reached through `tl_perf online`.

    python -m unittest discover -s tests -v
"""
import io
import json
import os
import random
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_fit_report import VALID_REPORT  # noqa: E402
from test_tl_perf import TL_PERF, tl_perf  # noqa: E402

import perf_online  # noqa: E402  (tests/test_tl_perf.py puts scripts/ on sys.path)


def _jload(path):
    with open(path) as fh:
        return json.load(fh)


def _report(**sv):
    doc = json.loads(json.dumps(VALID_REPORT))
    doc["server_validation"] = dict(doc["server_validation"], **sv)
    return doc


class ServerValidationModeTest(unittest.TestCase):
    """Phase 6.0: the report records the mode and whether the user authorized diagnostics;
    an unauthorized diagnostics run is invalid."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_report(self, doc):
        with open(os.path.join(self.out, "report.json"), "w") as fh:
            json.dump(doc, fh)
        return tl_perf.main(["report", "--root", self.out, "--out", self.out])

    def report_md(self):
        with open(os.path.join(self.out, "report.md")) as fh:
            return fh.read()

    def test_smoke_mode_renders(self):
        self.assertEqual(self.run_report(_report()), tl_perf.EXIT_OK)
        self.assertIn("- Mode: **smoke validation (small subset)**", self.report_md())

    def test_authorized_diagnostics_is_valid(self):
        doc = _report(mode="diagnostics", authorized_by_user=True, status="SUBMITTED")
        self.assertEqual(self.run_report(doc), tl_perf.EXIT_OK)
        self.assertIn("online diagnostics (authorized by the user)", self.report_md())

    def test_unauthorized_diagnostics_exits_11(self):
        doc = _report(mode="diagnostics", authorized_by_user=False)
        self.assertEqual(self.run_report(doc), tl_perf.EXIT_BAD_REPORT)

    def test_missing_mode_or_authorization_exits_11(self):
        for key in ("mode", "authorized_by_user"):
            doc = _report()
            del doc["server_validation"][key]
            self.assertEqual(self.run_report(doc), tl_perf.EXIT_BAD_REPORT, key)

    def test_unknown_mode_and_non_bool_authorization_exit_11(self):
        self.assertEqual(self.run_report(_report(mode="full")), tl_perf.EXIT_BAD_REPORT)
        self.assertEqual(self.run_report(_report(authorized_by_user="yes")), tl_perf.EXIT_BAD_REPORT)

    def test_no_server_validation_is_still_valid(self):
        doc = _report()
        del doc["server_validation"]
        self.assertEqual(self.run_report(doc), tl_perf.EXIT_OK)
        self.assertNotIn("## Server validation", self.report_md())


class OnlineCliTest(unittest.TestCase):
    def test_actions_and_flags_are_registered(self):
        parser = tl_perf.build_parser()
        cases = {
            "collect": ["--job", "abc", "--interval", "15", "--for", "60"],
            "analyze": ["--job", "abc", "--window", "30", "--stable-windows", "4", "--tolerance", "0.2"],
        }
        for action, flags in cases.items():
            args = parser.parse_args(["online", action, "--out", "x"] + flags)
            self.assertEqual(args.online_action, action)
            self.assertEqual(args.out, "x")
            self.assertIs(args.func, tl_perf.cmd_online)

    def test_online_without_action_is_a_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = tl_perf.build_parser().parse_args(["online"])
            self.assertEqual(perf_online.run(args, tmp, tl_perf), perf_online.EXIT_BLOCKER)


def synth_phase(segments, t0=0.0, poll=30.0, noise=0.03, seed=0):
    """Polled progress of one phase. segments: [(seconds, rows_per_s, workers, pending)].
    Returns points [(t, done)] and pods [(t, ready, pending)], one per poll."""
    rng = random.Random(seed)
    points, pods = [(t0, 0.0)], [(t0, 0, True)]
    done, t = 0.0, t0
    for seconds, rate, workers, pending in segments:
        end = t + seconds
        while t + poll <= end + 1e-9:
            t += poll
            done += max(0.0, rate * poll * (1.0 + rng.uniform(-noise, noise)))
            points.append((t, done))
            pods.append((t, workers, pending))
    return points, pods


RAMP_STABLE_TAIL = [(60, 0, 0, True), (120, 20, 1, False), (60, 50, 3, True), (60, 50, 3, False),
                    (900, 100, 6, False), (90, 30, 6, False)]


class StabilityTest(unittest.TestCase):
    """Warm-up / stable / tail per phase; stability is measured, never assumed."""

    def test_ramp_stable_tail(self):
        points, pods = synth_phase(RAMP_STABLE_TAIL)
        r = perf_online.detect_stability(points, pods)
        self.assertTrue(r["reached"], r.get("reason"))
        self.assertAlmostEqual(r["stable"]["rate_median"], 100, delta=5)
        self.assertAlmostEqual(r["warmup"]["end"], 300, delta=60)     # ramp ends at 300 s
        self.assertGreaterEqual(r["stable"]["seconds"], 780)
        self.assertLessEqual(r["stable"]["end"], 1200 + 1)            # tail starts at 1200 s
        self.assertGreater(r["tail"]["seconds"], 0)
        self.assertEqual(r["stable"]["workers"], 6)

    def test_never_stable_rising(self):
        segs = [(60, 10 * (1.05 ** i), 1 + i // 2, i % 2 == 0) for i in range(25)]
        points, pods = synth_phase(segs, noise=0.0)
        r = perf_online.detect_stability(points, pods)
        self.assertFalse(r["reached"])
        self.assertIn("never held", r["reason"])

    def test_noisy_phase_is_never_presented_as_steady(self):
        """Heavy noise: either no stable window, or a brief one that says how brief it is."""
        for seed in range(10):
            points, pods = synth_phase([(1200, 100, 4, False)], noise=0.45, seed=seed)
            r = perf_online.detect_stability(points, pods)
            if r["reached"]:
                self.assertLess(r["stable"]["share_of_phase"], 0.35, (seed, r["stable"]))

    def test_steady_phase_covers_most_of_the_phase(self):
        points, pods = synth_phase(RAMP_STABLE_TAIL)
        self.assertGreater(perf_online.detect_stability(points, pods)["stable"]["share_of_phase"], 0.6)

    def test_slow_climb_is_not_stable(self):
        segs = [(60, 100 * (1.04 ** i), 4, False) for i in range(20)]
        points, pods = synth_phase(segs, noise=0.0)
        self.assertFalse(perf_online.detect_stability(points, pods)["reached"])

    def test_early_plateau_before_more_workers_is_warmup(self):
        segs = [(240, 50, 3, False), (60, 70, 6, True), (600, 100, 6, False)]
        points, pods = synth_phase(segs)
        r = perf_online.detect_stability(points, pods)
        self.assertTrue(r["reached"])
        self.assertAlmostEqual(r["stable"]["rate_median"], 100, delta=5)
        self.assertGreaterEqual(r["warmup"]["seconds"], 240)

    def test_short_phase_is_reported_not_stable(self):
        points, pods = synth_phase([(120, 100, 2, False)])
        r = perf_online.detect_stability(points, pods)
        self.assertFalse(r["reached"])
        self.assertIn("shorter than", r["reason"])

    def test_two_phases_are_independent(self):
        ev_pts, ev_pods = synth_phase(RAMP_STABLE_TAIL)
        vis_segs = [(300, 5, 2, False), (60, 8, 4, True), (720, 12, 4, False), (60, 4, 4, False)]
        vis_pts, vis_pods = synth_phase(vis_segs, t0=2000.0, seed=1)
        ev = perf_online.detect_stability(ev_pts, ev_pods)
        vis = perf_online.detect_stability(vis_pts, vis_pods)
        self.assertTrue(ev["reached"] and vis["reached"])
        self.assertAlmostEqual(ev["stable"]["rate_median"], 100, delta=5)
        self.assertAlmostEqual(vis["stable"]["rate_median"], 12, delta=1)
        self.assertGreaterEqual(vis["stable"]["start"], 2000 + 360 - 60)
        self.assertLess(ev["stable"]["end"], vis["phase"]["start"])

    def test_no_progress(self):
        r = perf_online.detect_stability([(0, 0), (30, 0), (60, 0)])
        self.assertFalse(r["reached"])
        self.assertEqual(r["reason"], "no progress observed")


def _artifacts(t_inf, t_gen, t_met, t_vis, startup, lengths, instance_factor=1.0):
    floor = {"t_inf_per_sample_mean_seconds": t_inf}
    profile = {"dataset": {"state_lengths": lengths},
               "generation": {"per_sample_seconds": {"mean": t_gen}},
               "inference": {"per_sample_mean_seconds": t_inf},
               "metrics": {"handlers": {"metric:m": {"per_sample_mean_seconds": t_met}}},
               "visualizers": {"per_sample_seconds": {"mean": t_vis}},
               "startup": {"stats": {"mean": startup}}}
    fit = {"rows_per_state": {k: int(v * instance_factor) for k, v in lengths.items()}}
    return floor, fit, profile


def _log(asctime, message, **extra):
    d = {"asctime": asctime, "levelname": "INFO", "message": message,
         "user.email": "someone@example.com", "user.full_name": "A Person", "team.name": "t",
         "job.uid": "j"}
    d.update(extra)
    return json.dumps(d)


DESCRIBE = textwrap.dedent("""\
    Name:         generic-process-j-1
    Start Time:   Thu, 24 Sep 2026 15:19:00 +0000
    Status:       Running
    Init Containers:
      code-extractor:
        State:          Terminated
          Reason:       Completed
    Containers:
      main:
        State:          Running
        Last State:     Terminated
          Reason:       OOMKilled
        Ready:          True
        Restart Count:  2
        Limits:
          cpu:     4
          memory:  2Gi
        Requests:
          cpu:     1
          memory:  1Gi
        Environment:
          SOME_SETTING:  1
          OTHER:  owner@example.com
    Conditions:
      Type  Status
    """)


class CollectPiecesTest(unittest.TestCase):
    def test_merge_appends_only_new_lines(self):
        kept = ["a%d" % i for i in range(10)]
        add, gap = perf_online.merge_tail(kept, ["a%d" % i for i in range(5, 14)])
        self.assertEqual((add, gap), (["a10", "a11", "a12", "a13"], False))

    def test_merge_finds_the_anchor_after_another_container(self):
        kept = ["", "x1", "x2", "x3"]
        add, gap = perf_online.merge_tail(kept, ["", "x2", "x3", "x4"])
        self.assertEqual((add, gap), (["x4"], False))

    def test_merge_without_overlap_is_a_gap(self):
        add, gap = perf_online.merge_tail(["a1", "a2"], ["b1", "b2"])
        self.assertEqual((add, gap), (["b1", "b2"], True))

    def test_redaction_drops_identity_and_emails(self):
        line = perf_online.redact_line(_log("2026-09-24 14:48:12,942", "hi owner@example.com", n=1))
        self.assertNotIn("user.", line)
        self.assertNotIn("team.", line)
        self.assertNotIn("example.com", line)
        self.assertIn('"n": 1', line)

    def test_describe_is_parsed_and_its_environment_removed(self):
        d = perf_online.parse_describe(DESCRIBE)
        self.assertEqual((d["limit_memory"], d["request_memory"], d["restarts"]), ("2Gi", "1Gi", 2))
        self.assertEqual((d["last_state_reason"], d["oom"], d["ready"]), ("OOMKilled", True, True))
        self.assertEqual(d["state"], "Running")
        self.assertAlmostEqual(d["start"], 1790262340.0, delta=86400 * 400)  # parsed, UTC
        red = perf_online.redact_describe(DESCRIBE)
        self.assertNotIn("SOME_SETTING", red)
        self.assertNotIn("example.com", red)
        self.assertIn("Restart Count:  2", red)

    def test_log_time_is_utc(self):
        self.assertEqual(perf_online.log_time("1970-01-01 00:01:00,500"), 60.5)


def _ts(seconds):
    return "1970-01-01 %02d:%02d:%02d,000" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def _ws1_window(ts, name, n, mean, p, kind="work", window=30.0):
    return _log(ts, name + ".aggregate", span_name=name, resource=name, span_kind=kind,
                duration_seconds=mean, n=n, sum_seconds=mean * n, min_seconds=p[0] * 0.5,
                max_seconds=p[3] * 1.5, std_seconds=0.0, window_seconds=window,
                p50_seconds=p[0], p90_seconds=p[1], p95_seconds=p[2], p99_seconds=p[3])


class ParseAndWindowStatsTest(unittest.TestCase):
    def tables(self, lines, pod="evaluate-j-x"):
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "logs"))
        with open(os.path.join(tmp, "logs", pod + ".log"), "w") as fh:
            fh.write("\n".join(perf_online.redact_line(l) for l in lines) + "\n")
        return perf_online.parse_collection(tmp)

    def test_ws1_windows_give_merged_percentiles_and_span_kind(self):
        lines = [_ws1_window(_ts(30 * i), "trainer.infer", 100, 0.05,
                             (0.05, 0.07, 0.08, 0.12)) for i in range(1, 3)]
        lines += [_ws1_window(_ts(30 * i), "trainer.validation_step.pull_batch",
                              100, 0.01, (0.01, 0.02, 0.02, 0.03), kind="wait") for i in range(1, 3)]
        t = self.tables(lines)
        self.assertTrue(t["ws1_fields"])
        st = perf_online.window_stats(t, 0, 60)
        infer = st["evaluate:trainer.infer"]
        self.assertEqual(infer["calls"], 200)
        self.assertAlmostEqual(infer["mean"], 0.05)
        self.assertAlmostEqual(infer["p50"], 0.05, places=3)
        self.assertAlmostEqual(infer["p99"], 0.12, places=3)
        self.assertIn("merged", infer["percentiles"])
        self.assertEqual(st["evaluate:trainer.validation_step.pull_batch"]["span_kind"], "wait")

    def test_without_ws1_fields_means_only_and_per_call_percentiles(self):
        lines = [_log("1970-01-01 00:01:00,000", "trainer.infer.aggregate", span_name="trainer.infer",
                      duration_seconds=0.05, n=100, sum_seconds=5.0, min_seconds=0.01,
                      max_seconds=0.2, std_seconds=0.01, window_seconds=60.0)]
        lines += [_log("1970-01-01 00:00:%02d,000" % i,
                       "samples generator: generate single sample (queue_pop + get_sample + redis_push)",
                       duration_seconds=0.02, queue_pop_secs=0.0, get_sample_secs=0.01 * (1 + i % 5),
                       redis_push_secs=0.0, state=0, index="s%d" % i) for i in range(50)]
        t = self.tables(lines, pod="generic-process-j-1")
        self.assertFalse(t["ws1_fields"])
        st = perf_online.window_stats(t, 0, 61)
        infer = st["generic-process:trainer.infer"]
        self.assertEqual((infer["calls"], infer["percentiles"]), (100, None))
        gen = st["series:generation_per_sample"]
        self.assertEqual(gen["calls"], 50)
        self.assertEqual(gen["p50"], 0.03)
        self.assertEqual(gen["percentiles"], "every call logged")

    def test_a_hundred_lines_from_one_worker_are_flagged_possibly_sampled(self):
        lines = [_log("1970-01-01 00:%02d:%02d,000" % (i // 60, i % 60), "vis calculator: process vis batch end-to-end",
                      duration_seconds=0.3, per_sample_secs=0.3) for i in range(100)]
        st = perf_online.window_stats(self.tables(lines, pod="generic-process-j-1"), 0, 200)
        self.assertIn("possibly sampled", st["series:visualizers_batch"]["percentiles"])


STUB_LEAP = textwrap.dedent("""\
    #!%(python)s
    import io, json, os, sys, tarfile
    STATE = %(state)r
    TAIL = 50

    def load():
        return json.load(open(STATE)) if os.path.exists(STATE) else {"n": 0}

    st = load()
    args = sys.argv[1:]
    if args[:2] == ["run", "info"]:
        job = args[2]
        if job == "PUSH1":
            st["n"] += 1
            json.dump(st, open(STATE, "w"))
            out = {"jobId": "PUSH1", "type": "PUSH", "subType": "Push", "status": "FINISHED"}
            if st["n"] >= 2:
                out["chainedEvaluate"] = {"status": "dispatched", "jobId": "EVAL1"}
        else:
            n = st["n"]
            out = {"jobId": "EVAL1", "type": "TRAINING", "subType": "Evaluate",
                   "status": "FINISHED" if n >= 6 else "STARTED",
                   "steps": [{"id": "train_and_evaluate", "status": "STARTED",
                              "current": min(100, 20 * n), "total": 100}]}
        print(json.dumps(out))
    elif args[:2] == ["run", "list"]:
        print("Evaluate   Wed, 07 Oct 2026 18:53   STARTED    OTHER1")
    elif args[:2] == ["run", "logs"]:
        n = st["n"]
        dest = args[args.index("--output") + 1] + ".tar"
        def lines(count):
            return "\\n".join(json.dumps({"asctime": "2026-09-24 14:%%02d:%%02d,000" %% (i // 60, i %% 60),
                                          "message": "line %%d" %% i, "user.email": "a@b.co"})
                                for i in range(count))
        ev = n * 30 + (120 if n >= 4 else 0)             # a burst at poll 4: more than TAIL
        pods = {"evaluate-EVAL1-aaaaa": lines(ev)}
        if n <= 3:                                       # this worker is removed after poll 3
            pods["generic-process-EVAL1-b-1"] = lines(10 * n)
        with tarfile.open(dest, "w") as tar:
            for name, text in pods.items():
                body = "\\n".join(text.split("\\n")[-TAIL:]).encode()
                for nm, data in ((name, body), ("describe-" + name, b"Name: x\\nStatus: Running\\n")):
                    ti = tarfile.TarInfo(nm)
                    ti.size = len(data)
                    tar.addfile(ti, io.BytesIO(data))
    else:
        sys.exit(2)
    """)


class CollectEndToEndTest(unittest.TestCase):
    """Polling a running job through a stub `leap`: push -> chained evaluation, tail merging,
    a burst larger than the tail (a recorded gap), a worker removed mid-run, exit 20 + resume."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        stub = os.path.join(self.tmp.name, "leap")
        with open(stub, "w") as fh:
            fh.write(STUB_LEAP % {"python": sys.executable, "state": os.path.join(self.tmp.name, "st.json")})
        os.chmod(stub, 0o755)
        self.env = dict(os.environ, TL_LEAP=stub)
        self.out = os.path.join(self.tmp.name, "out")

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, *extra):
        return subprocess.run([sys.executable, TL_PERF, "online", "collect", "--root", self.out,
                               "--out", self.out, "--job", "PUSH1", "--interval", "0.05"] + list(extra),
                              env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)

    def test_follow_push_to_finish(self):
        first = self.collect("--for", "0.12")
        self.assertEqual(first.returncode, perf_online.EXIT_STILL_RUNNING, first.stdout.decode())
        done = self.collect()
        self.assertEqual(done.returncode, perf_online.EXIT_OK, done.stdout.decode())
        col = os.path.join(self.out, "online", "PUSH1")
        state = _jload(os.path.join(col, "collect.json"))
        self.assertEqual((state["push_job"], state["evaluate_job"], state["status"]), ("PUSH1", "EVAL1", "FINISHED"))
        self.assertTrue(state["terminal"])
        with open(os.path.join(col, "logs", "evaluate-EVAL1-aaaaa.log")) as fh:
            ev = fh.read().splitlines()
        self.assertEqual(len(ev), len(set(ev)))                       # no duplicates from overlaps
        self.assertTrue(all("a@b.co" not in l and "user." not in l for l in ev))
        self.assertGreaterEqual(state["pods"]["evaluate-EVAL1-aaaaa"]["gaps"], 1)   # the burst
        self.assertTrue(os.path.exists(os.path.join(col, "logs", "generic-process-EVAL1-b-1.log")))
        with open(os.path.join(col, "polls.jsonl")) as fh:
            polls = [json.loads(l) for l in fh]
        progress = [p["steps"][0]["current"] for p in polls if p["steps"]]
        self.assertEqual(progress, sorted(progress))
        self.assertTrue(all(p["other_jobs"] == [{"type": "Evaluate", "status": "STARTED", "job": "OTHER1"}]
                            for p in polls))
        self.assertTrue(os.path.exists(os.path.join(col, "components.json")))

    def test_from_tar_imports_a_finished_run(self):
        tar_path = os.path.join(self.tmp.name, "logs.tar")
        with tarfile.open(tar_path, "w") as tar:
            for name, text in (("evaluate-E-x", _log("1970-01-01 00:00:01,000", "engine.job completed",
                                                     span_name="engine.job", duration_seconds=5.0, n=1,
                                                     sum_seconds=5.0)),
                               ("describe-evaluate-E-x", DESCRIBE)):
                data = text.encode()
                ti = tarfile.TarInfo(name)
                ti.size, ti.mode = len(data), 0
                tar.addfile(ti, io.BytesIO(data))
        args = tl_perf.build_parser().parse_args(["online", "collect", "--from-tar", tar_path,
                                                  "--job", "E", "--out", self.out])
        self.assertEqual(perf_online.run(args, self.out, tl_perf), perf_online.EXIT_OK)
        t = _jload(os.path.join(self.out, "online", "E", "components.json"))
        self.assertEqual(t["markers"][0]["name"], "job_end")
        self.assertEqual(t["pods"]["evaluate-E-x"]["describe"]["limit_memory"], "2Gi")
        self.assertIn("imported after the run", t["coverage"]["warnings"][0])

    def test_missing_job_is_a_blocker(self):
        args = tl_perf.build_parser().parse_args(["online", "collect", "--out", self.out])
        self.assertEqual(perf_online.run(args, self.out, tl_perf), perf_online.EXIT_BLOCKER)


def _agg(t, name, n, mean, window=60.0, kind=None):
    extra = {"span_name": name, "resource": name, "duration_seconds": mean, "n": n,
             "sum_seconds": mean * n, "min_seconds": mean * 0.5, "max_seconds": mean * 2,
             "std_seconds": 0.0, "window_seconds": window}
    return _log(_ts(int(t)), name + ".aggregate", **extra)


def _call(t, name, seconds):
    return _log(_ts(int(t)), name + " completed", span_name=name, resource=name,
                duration_seconds=seconds, n=1, sum_seconds=seconds)


def build_run(tmp, loop, pull, infer, handoff=0.002, overhead=0.0, gen=0.02, idle_per_window=0.0,
              queue_full=0.0, vis_seconds=60.0, vis_per_sample=0.05, oom=False, eval_rss=1.0,
              eval_limit="10Gi", ramp=120.0, eval_seconds=900.0, rows_per_s=50.0, proxy=None, engine=False,
              stopped=False, priority="runtime"):
    """A followed run on disk: polls (ramp then steady progress, 4 workers), evaluate +
    generic pod logs with per-window spans, per-sample lines, markers, describes."""
    job = os.path.join(tmp, "online", "J")
    os.makedirs(os.path.join(job, "logs"))
    j0 = 1000.0
    e0 = j0 + 60
    e1 = e0 + ramp + eval_seconds
    polls, done, t = [], 0.0, j0
    while t <= e1 + 600:
        rate = 0.0 if t < e0 else (rows_per_s * 0.3 if t < e0 + ramp else (rows_per_s if t <= e1 else 0.0))
        done += rate * 30
        ready = 0 if t < e0 else (2 if t < e0 + ramp else 4)
        pods = [{"pod": "generic-process-J-%d" % i, "role": "generic-process", "status": "Running",
                 "state": "Running", "waiting_reason": None, "ready": True, "restarts": 0, "oom": False}
                for i in range(ready)]
        polls.append({"t": t, "status": "STARTED", "evaluate_status": "STARTED",
                      "steps": [{"id": "train_and_evaluate", "status": "STARTED", "current": done, "total": 1}],
                      "pods": pods})
        t += 30
    if stopped == "steady":  # visualization progress polled at a steady pace, then the planned stop
        vt = e1 + 5 + 60
        while vt <= e1 + 5 + 60 + vis_seconds:
            polls.append({"t": vt, "status": "FINISHED", "evaluate_status": "STARTED",
                          "steps": [{"id": "train_and_evaluate", "status": "DONE", "current": done, "total": done},
                                    {"id": "visualize_samples", "status": "RUNNING",
                                     "current": int((vt - e1 - 65) * 0.5), "total": 1000}],
                          "pods": [{"pod": "generic-process-J-%d" % i, "role": "generic-process", "status": "Running",
                                    "state": "Running", "waiting_reason": None, "ready": True, "restarts": 0,
                                    "oom": False} for i in range(4)]})
            vt += 30
    if stopped:      # stopped from outside while visualizing (no failure logged)
        polls.append({"t": e1 + 5 + 60 + vis_seconds, "status": "FINISHED", "evaluate_status": "TERMINATED",
                      "steps": [{"id": "train_and_evaluate", "status": "DONE", "current": done, "total": done},
                                {"id": "visualize_samples", "status": "RUNNING",
                                 "current": 180 if stopped is True else int(vis_seconds * 0.5), "total": 1000}],
                      "pods": []})
    with open(os.path.join(job, "polls.jsonl"), "w") as fh:
        fh.write("\n".join(json.dumps(p) for p in polls) + "\n")
    ev = []
    t = e0 + 60
    while t <= e1:
        ev.append(_agg(t, "trainer.validation_step", 100, loop))
        ev.append(_agg(t, "trainer.validation_step.pull_batch", 100, pull))
        ev.append(_agg(t, "trainer.infer", 100, infer))
        ev.append(_agg(t, "trainer.validation_step.push_metrics", 100, handoff))
        if overhead:
            ev.append(_agg(t, "trainer.validation_step.extract_ls", 100, overhead))
        ev.append(_log(_ts(int(t)), "rss @ eval_loop.batch: x", stage="eval_loop.batch", rss_gb=eval_rss))
        t += 60
    if proxy is not None:
        ev.append(_log(_ts(int(e1)), "evaluate inference loop: GPU-utilization proxy", gpu_util_proxy=proxy,
                       infer_seconds=proxy * (e1 - e0), loop_wall_seconds=e1 - e0, data_state="training"))
    pp0 = e1 + 5
    ev.append(_log(_ts(int(pp0)), "Starting post processing evaluation..."))
    if engine:
        for dt, step, rss in ((0, "start", 2.0), (1, "after_model_cleanup", 2.0), (6, "after_nn_indexers", 2.1),
                              (60, "after_first_analysis", 1.5), (60 + vis_seconds, "after_visualizations", 3.0)):
            if stopped and step == "after_visualizations":
                continue
            ev.append(_log(_ts(int(pp0 + dt)), "post_processing memory", step=step, rss_gb=rss, data_gb=1.0))
        for i in range(3):
            ev.append(json.dumps({"asctime": _ts(int(pp0 + 70 + i)), "levelname": "WARNING",
                                  "message": "sparse custom-LS stash over capacity; evicting the oldest entry",
                                  "capacity": 256}))
        ev.append(json.dumps({"asctime": _ts(int(pp0 + 75)), "levelname": "WARNING",
                              "message": "Failed to display populations"}))
    if stopped:
        j1 = pp0 + 60 + vis_seconds
        ev.append(_log(_ts(int(j1)), "starting visualize batch..."))
    else:
        ev.append(_call(pp0 + 60 + vis_seconds, "visualization.calculate_and_upload_visualizers", vis_seconds))
        ev.append(_call(pp0 + 70 + vis_seconds, "trainer.post_processing", 70 + vis_seconds))
        j1 = pp0 + 75 + vis_seconds
        ev.append(_call(j1, "engine.job", j1 - j0))
    with open(os.path.join(job, "logs", "evaluate-J-x.log"), "w") as fh:
        fh.write("\n".join(perf_online.redact_line(l) for l in ev) + "\n")
    for i in range(4):
        g = [_log(_ts(int(e0)), "generic_processor | resolved child worker count", children_per_pod=1)]
        t = e0 + ramp
        while t <= e1:
            g.append(_log(_ts(int(t)), "samples generator: generate single sample (queue_pop + get_sample + redis_push)",
                          duration_seconds=gen, queue_pop_secs=0.0, get_sample_secs=gen, redis_push_secs=0.0))
            if idle_per_window:
                g.append(_agg(t, "generic_processor.idle_wait", 10, idle_per_window / 10.0, kind="wait"))
            if queue_full:
                g.append(_agg(t, "redis.dataset_queue.queue_full_wait", 10, queue_full / 10.0, kind="wait"))
            t += 15
        t = pp0 + 60
        while t <= pp0 + 60 + vis_seconds:
            g.append(_log(_ts(int(t)), "vis calculator: process vis batch end-to-end",
                          duration_seconds=vis_per_sample, per_sample_secs=vis_per_sample))
            t += 5
        if oom and i == 0:
            g.append(_log(_ts(int(e0 + 300)), "generic_processor | child 3 died; re-forking"))
        if engine:
            for k in range(5):
                t = pp0 + 60 + 10 * k
                g.append(_log(_ts(int(t)), "Trying to upload image", file_path="x.png"))
                g.append(_log(_ts(int(t + (4 if k == 4 else 0))), "Done upload image", file_name="x.png"))
                g.append(_log(_ts(int(t + 1)), "max number of samples in queue reached. continuing"))
        with open(os.path.join(job, "logs", "generic-process-J-%d.log" % i), "w") as fh:
            fh.write("\n".join(perf_online.redact_line(l) for l in g) + "\n")
    tables = perf_online.parse_collection(job)
    tables["pods"]["evaluate-J-x"]["describe"] = perf_online.parse_describe(
        DESCRIBE.replace("2Gi", eval_limit).replace("OOMKilled", "Completed").replace("Restart Count:  2", "Restart Count:  0"))
    gdesc = perf_online.parse_describe(DESCRIBE if oom else DESCRIBE.replace("OOMKilled", "Completed")
                                       .replace("Restart Count:  2", "Restart Count:  0"))
    for i in range(4):
        tables["pods"]["generic-process-J-%d" % i]["describe"] = dict(gdesc) if (oom and i == 0) else \
            perf_online.parse_describe(DESCRIBE.replace("OOMKilled", "Completed").replace("Restart Count:  2", "Restart Count:  0"))
    with open(os.path.join(job, "collect.json"), "w") as fh:
        state = {"job": "J", "evaluate_job": "J", "status": "FINISHED", "source": "poll", "polls": len(polls),
                 "gaps": [], "pods": {}}
        if stopped:
            state.update(status="TERMINATED", error_report={
                "job": "J", "status": "TERMINATED", "levels": ["Warning"],
                "messages": ["Evaluate: This job needs more memory than the installation can guarantee"]})
        json.dump(state, fh)
    offline = {"inference_s": infer / 1.0, "generation_s": gen, "metrics_s": 0.001, "visualizer_s": vis_per_sample,
               "startup_s": 1.0, "worker_peak_rss_gb": 1.5, "user_footprint_gb": 1.2, "user_peak_stage": "preprocess"}
    return perf_online.analyze_collection(job, tables, perf_online._polls(job), offline, priority=priority)


class AnalyzeScenarioTest(unittest.TestCase):
    """S4: synthetic runs classify correctly, and every finding carries evidence."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def check(self, a):
        self.assertTrue(a["findings"])
        for f in a["findings"]:
            self.assertTrue(f["evidence"], f["id"])
            self.assertIn(f["type"], ("compute", "memory", "I/O", "scheduling"))
            self.assertTrue(f["location"] and f["critical_path"] and f["owner"])
        return {f["id"]: f for f in a["findings"]}

    def test_producer_bound(self):
        a = build_run(self.tmp, loop=0.20, pull=0.12, infer=0.06, gen=0.5)
        f = self.check(a)
        self.assertTrue(a["phases"]["evaluation"]["reached"])
        self.assertEqual(a["statistics_basis"], "stable window")
        self.assertEqual(a["primary"], "producer-bound")
        self.assertEqual(f["producer-bound"]["critical_path"], "on")
        self.assertEqual(f["producer-bound"]["owner"], "integration")
        self.assertTrue(f["producer-bound"]["log_lines"])

    def test_consumer_bound(self):
        a = build_run(self.tmp, loop=0.10, pull=0.002, infer=0.03, overhead=0.06, idle_per_window=9.0,
                      queue_full=3.0)
        f = self.check(a)
        self.assertEqual(a["primary"], "consumer-bound")
        self.assertEqual(f["consumer-bound"]["owner"], "tensorleap")
        self.assertTrue(any("full queue" in e for e in f["consumer-bound"]["evidence"]))
        self.assertIn("generation-off-critical-path", f)
        self.assertEqual(f["generation-off-critical-path"]["critical_path"], "off")

    def test_tail_visualizer_bound(self):
        a = build_run(self.tmp, loop=0.10, pull=0.002, infer=0.09, eval_seconds=300.0, vis_seconds=1500.0,
                      vis_per_sample=0.8)
        f = self.check(a)
        self.assertEqual(a["primary"], "visualization-tail")
        self.assertEqual(f["visualization-tail"]["critical_path"], "on (tail)")
        self.assertEqual(f["visualization-tail"]["location"], "user code (visualizers)")

    def test_inference_floor(self):
        a = build_run(self.tmp, loop=0.10, pull=0.003, infer=0.085, idle_per_window=8.0, proxy=0.9)
        f = self.check(a)
        self.assertEqual(a["primary"], "inference-floor")
        self.assertEqual(f["inference-floor"]["owner"], "expected")
        self.assertTrue(any("GPU-utilization proxy" in e for e in f["inference-floor"]["evidence"]))
        self.assertIsNotNone(a["gap_to_floor"])

    def test_memory_oom(self):
        a = build_run(self.tmp, loop=0.10, pull=0.003, infer=0.085, oom=True)
        f = self.check(a)
        self.assertEqual(a["primary"], "oom")
        self.assertEqual(f["oom"]["type"], "memory")
        self.assertTrue(any("offline worker footprint" in e for e in f["oom"]["evidence"]))
        self.assertTrue(f["oom"]["log_lines"])

    def test_memory_pressure_near_limit(self):
        a = build_run(self.tmp, loop=0.10, pull=0.003, infer=0.085, eval_rss=9.5, eval_limit="10Gi")
        f = self.check(a)
        self.assertIn("memory-pressure", f)
        self.assertEqual(a["memory"]["pods"]["evaluate-J-x"]["peak_rss_gb"], 9.5)

    def test_metrics_bound(self):
        a = build_run(self.tmp, loop=0.10, pull=0.003, infer=0.05, handoff=0.035)
        self.assertEqual(a["primary"], "metrics-bound")
        self.check(a)

    def test_offline_mismatch_is_a_finding_not_the_primary(self):
        a = build_run(self.tmp, loop=0.10, pull=0.003, infer=0.085, gen=0.02)
        a2 = dict(a)
        self.assertNotEqual(a2["primary"], None)
        self.assertFalse(a2["primary"].startswith("mismatch"))

    def test_findings_without_evidence_are_dropped(self):
        f = perf_online._finding("x", "t", "compute", "model", "on", "expected", [None, None], "e", "s")
        self.assertEqual(f["evidence"], [])


class OnlineReportTest(unittest.TestCase):
    """S5: the Online diagnostics section renders from analysis.json; invalid input -> exit 11."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = self.tmp

    def write(self, analysis=None, raw=None, status="FINISHED"):
        os.makedirs(os.path.join(self.out, "online"), exist_ok=True)
        if raw is not None or analysis is not None:
            with open(os.path.join(self.out, "online", "analysis.json"), "w") as fh:
                fh.write(raw if raw is not None else json.dumps(analysis))
        doc = _report(mode="diagnostics", authorized_by_user=True, status=status)
        with open(os.path.join(self.out, "report.json"), "w") as fh:
            json.dump(doc, fh)
        code = tl_perf.main(["report", "--root", self.out, "--out", self.out])
        md = ""
        if code == 0:
            with open(os.path.join(self.out, "report.md")) as fh:
                md = fh.read()
        return code, md

    def test_section_renders_from_an_analysis(self):
        a = build_run(self.tmp, loop=0.20, pull=0.12, infer=0.06, gen=0.5)
        code, md = self.write(a)
        self.assertEqual(code, tl_perf.EXIT_OK)
        for heading in ("## Online diagnostics", "### Environment", "### Engine timeline", "### Primary bottleneck",
                        "### Engine stages by component", "### Data movement: Redis queues, storage, uploads",
                        "### Evaluation loop: model and feature extraction", "### Engine warnings and errors",
                        "### Memory", "### User code (summary)",
                        "### Recommended Tensorleap actions (from this run)"):
            self.assertIn(heading, md)
        self.assertIn("**The evaluation waits for samples", md)
        self.assertIn("`trainer.validation_step.pull_batch` | waiting for the next batch of samples", md)
        self.assertIn("Inside evaluation: **The evaluation waits for samples", md)
        self.assertNotIn("`trainer.post_processing`", md)          # post-processing is not analyzed

    def test_engine_names_are_labelled_and_settings_never_shown(self):
        """K28: engine names appear as logged (in code spans) with a label; settings, formulas
        and source paths never appear."""
        a = build_run(self.tmp, loop=0.10, pull=0.003, infer=0.085, proxy=0.9)
        code, md = self.write(a)
        self.assertEqual(code, tl_perf.EXIT_OK)
        section = md[md.index("## Online diagnostics"):]
        self.assertIn("| `trainer.infer` | model inference |", section)
        for line in section.splitlines():
            self.assertNotRegex(line, r"\b(EVAL|VIS|FEATURE_FLAG|LEAP|REDIS|BLOB|STREAMING)_[A-Z_]+\b")
            self.assertNotRegex(line, r"\.py:\d+")
            if "Log: `" not in line:
                self.assertNotIn("gpu_util", line)
        self.assertIn("GPU-utilization proxy", section)

    def test_finished_run_without_analysis_exits_11(self):
        self.assertEqual(self.write()[0], tl_perf.EXIT_BAD_REPORT)

    def test_broken_or_incomplete_analysis_exits_11(self):
        self.assertEqual(self.write(raw="{nope")[0], tl_perf.EXIT_BAD_REPORT)
        self.assertEqual(self.write({"findings": []})[0], tl_perf.EXIT_BAD_REPORT)

    def test_run_in_progress_renders_a_placeholder(self):
        code, md = self.write(status="SUBMITTED")
        self.assertEqual(code, tl_perf.EXIT_OK)
        self.assertIn("Collection in progress", md)

    def test_smoke_mode_has_no_online_section(self):
        doc = _report()
        with open(os.path.join(self.out, "report.json"), "w") as fh:
            json.dump(doc, fh)
        self.assertEqual(tl_perf.main(["report", "--root", self.out, "--out", self.out]), tl_perf.EXIT_OK)
        with open(os.path.join(self.out, "report.md")) as fh:
            self.assertNotIn("## Online diagnostics", fh.read())


class EngineViewTest(unittest.TestCase):
    """K28: the engine's own timeline, stages, data movement, events and warnings."""

    def test_engine_timeline_events_and_warnings(self):
        a = build_run(tempfile.mkdtemp(), loop=0.10, pull=0.003, infer=0.085, eval_seconds=300.0,
                      vis_seconds=900.0, engine=True)
        eng = a["engine"]
        stages = [s["stage"] for s in eng["timeline"]]
        for want in ("evaluation loop", "post-processing: model cleanup", "post-processing: nearest-neighbour indexes",
                     "post-processing: insights (first analysis)", "visualization"):
            self.assertIn(want, stages)
        vis = next(s for s in eng["timeline"] if s["stage"] == "visualization")
        self.assertEqual(round(vis["seconds"]), 900)
        self.assertEqual(a["primary"], "engine-visualization")
        self.assertEqual(eng["events"]["sample_queue_full"]["count"], 20)
        self.assertEqual(eng["uploads"]["image"]["count"], 20)
        self.assertEqual(eng["uploads"]["image"]["max"], 4.0)
        w = next(w for w in eng["warnings"] if w["message"].startswith("sparse custom-LS stash"))
        self.assertEqual((w["level"], w["count"]), ("WARNING", 3))
        ids = {f["id"] for f in a["findings"]}
        self.assertIn("engine-sample-queue-full", ids)
        self.assertTrue(any(i.startswith("engine-warning") for i in ids))
        self.assertNotIn("visualization-tail", ids)      # the exact engine timeline replaces it
        self.assertNotIn("checks", eng)                  # no checklist of platform features
        self.assertFalse(any(f["title"].startswith(("Degraded", "Active")) for f in a["findings"]))
        se = eng["measurements"]["stash_evictions"]
        self.assertEqual((se["total"], se["during_evaluation"]), (3, 0))
        self.assertIn("engine-stash-eviction", ids)
        self.assertFalse(any(f["title"].startswith("Warning") and "stash" in f["title"] for f in a["findings"]))
        for f in a["findings"]:
            self.assertTrue(f["evidence"], f["id"])


    def test_a_run_stopped_while_visualizing_is_partial_not_failed(self):
        # the visualization is still warming up at the stop: no steady pace, so the stop is reported
        a = build_run(tempfile.mkdtemp(), loop=0.10, pull=0.003, infer=0.085, eval_seconds=300.0,
                      vis_seconds=500.0, engine=True, stopped=True)
        ids = [f["id"] for f in a["findings"]]
        self.assertNotIn("run-failed", ids)
        if "visualization-stopped-steady" in ids:          # steady from the workers' own lines
            self.assertNotIn("run-stopped", ids)
            return
        self.assertIn("run-stopped", ids)
        self.assertEqual(a["primary"], "engine-visualization")      # the measured limiter still leads
        stop = next(f for f in a["findings"] if f["id"] == "run-stopped")
        self.assertIn("visualize_samples at 180 of 1000", stop["title"])
        self.assertTrue(any(e.startswith("server warning: ") for e in stop["evidence"]))
        vis = next(s for s in a["engine"]["timeline"] if s.get("open"))
        self.assertEqual((vis["step"], round(vis["seconds"])), ("after_visualizations", 500))
        self.assertIn("the run was stopped before it finished", a["confidence_reasons"])
        self.assertTrue(any("needs more memory" in n for n in a["notes"]))

    def test_a_stop_after_a_steady_visualization_is_planned(self):
        a = build_run(tempfile.mkdtemp(), loop=0.10, pull=0.003, infer=0.085, eval_seconds=300.0,
                      vis_seconds=900.0, engine=True, stopped="steady")
        ids = [f["id"] for f in a["findings"]]
        self.assertIn("visualization-stopped-steady", ids)
        self.assertNotIn("run-stopped", ids)
        self.assertNotIn("the run was stopped before it finished", a["confidence_reasons"])
        f = next(f for f in a["findings"] if f["id"] == "visualization-stopped-steady")
        self.assertIn("450 of 1000 samples", f["title"])
        self.assertTrue(any("all 1000 samples would take" in e for e in f["evidence"]))
        vis = next(s for s in a["engine"]["timeline"] if s.get("open"))
        self.assertGreater(vis["projected_full_seconds"], vis["seconds"])

    def test_a_stop_before_a_steady_visualization_is_reported(self):
        a = build_run(tempfile.mkdtemp(), loop=0.10, pull=0.003, infer=0.085, eval_seconds=300.0,
                      vis_seconds=60.0, engine=True, stopped=True)
        ids = [f["id"] for f in a["findings"]]
        self.assertIn("run-stopped", ids)
        self.assertNotIn("visualization-stopped-steady", ids)
        self.assertIn("the run was stopped before it finished", a["confidence_reasons"])

    def test_server_warnings_are_not_failure_reasons(self):
        errs, warns = perf_online._split_server_messages(
            {"messages": ["a: failed", "b: needs more memory", "c: no level"], "levels": ["Error", "Warning", None]})
        self.assertEqual((errs, warns), (["a: failed", "c: no level"], ["b: needs more memory"]))


STUB_UNCHAINED = textwrap.dedent("""\
    #!%(python)s
    import io, json, sys, tarfile
    args = sys.argv[1:]
    if args[:2] == ["run", "info"]:
        job = args[2]
        infos = {
            "PUSH2": {"jobId": "PUSH2", "type": "PUSH", "subType": "Push", "status": "FINISHED",
                      "versionId": "V2", "createdAt": "2026-10-07T10:00:00Z"},
            "eeeeeeeeeeeeeeeeeeeeeee1": {"jobId": "eeeeeeeeeeeeeeeeeeeeeee1", "type": "TRAINING", "subType": "Evaluate", "status": "FINISHED",
                      "versionId": "OTHER", "createdAt": "2026-10-07T10:05:00Z", "steps": []},
            "eeeeeeeeeeeeeeeeeeeeeee2": {"jobId": "eeeeeeeeeeeeeeeeeeeeeee2", "type": "TRAINING", "subType": "Evaluate", "status": "FAILED",
                      "versionId": "V2", "createdAt": "2026-10-07T10:02:00Z", "steps": [],
                      "errorReport": {"notifications": {"notifications": [
                          {"title": "Evaluate", "message": "An unexpected error occurred",
                           "extraMessage": "__init__ missing required arguments: model_id"}]}}},
        }
        print(json.dumps(infos[job]))
    elif args[:2] == ["run", "list"]:
        if "-t" in args:
            print("Evaluate   Wed, 07 Oct 2026 10:05   FINISHED   eeeeeeeeeeeeeeeeeeeeeee1")
            print("Evaluate   Wed, 07 Oct 2026 10:02   FAILED     eeeeeeeeeeeeeeeeeeeeeee2")
    elif args[:2] == ["run", "logs"]:
        dest = args[args.index("--output") + 1] + ".tar"
        with tarfile.open(dest, "w") as tar:
            data = json.dumps({"asctime": "2026-10-07 10:02:05,000", "levelname": "ERROR",
                               "message": "Exception caught in manager"}).encode()
            ti = tarfile.TarInfo("evaluate-eeeeeeeeeeeeeeeeeeeeeee2-x")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
    else:
        sys.exit(2)
    """)


class UnchainedAndFailedTest(unittest.TestCase):
    # (run ids are 24 hex characters, as on the platform)
    """A push whose evaluation the client created (no server-side chaining) is followed by
    version; a run rejected at creation leads the analysis with its reason."""

    def test_follow_by_version_and_report_the_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = os.path.join(tmp, "leap")
            with open(stub, "w") as fh:
                fh.write(STUB_UNCHAINED % {"python": sys.executable})
            os.chmod(stub, 0o755)
            out = os.path.join(tmp, "out")
            env = dict(os.environ, TL_LEAP=stub)
            proc = subprocess.run([sys.executable, TL_PERF, "online", "collect", "--root", out, "--out", out,
                                   "--job", "PUSH2", "--interval", "0.05"], env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=120)
            self.assertEqual(proc.returncode, perf_online.EXIT_OK, proc.stdout.decode())
            state = _jload(os.path.join(out, "online", "PUSH2", "collect.json"))
            self.assertEqual((state["evaluate_job"], state["status"]), ("eeeeeeeeeeeeeeeeeeeeeee2", "FAILED"))
            self.assertIn("model_id", state["error_report"]["messages"][0])
            proc = subprocess.run([sys.executable, TL_PERF, "online", "analyze", "--root", out, "--out", out,
                                   "--job", "PUSH2"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=120)
            self.assertEqual(proc.returncode, perf_online.EXIT_OK, proc.stdout.decode())
            a = _jload(os.path.join(out, "online", "analysis.json"))
            self.assertEqual(a["primary"], "run-failed")
            self.assertIn("model_id", a["findings"][0]["title"])


class CollectionSafetyTest(unittest.TestCase):
    def test_one_collector_per_run_and_stale_locks_are_taken_over(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(perf_online._take_lock(d))
            self.assertFalse(perf_online._take_lock(d))          # this process holds it
            perf_online._release_lock(d)
            with open(os.path.join(d, ".collect.lock"), "w") as fh:
                fh.write("999999")                              # a process that is gone
            self.assertTrue(perf_online._take_lock(d))
            perf_online._release_lock(d)

    def test_duplicated_lines_are_counted_once(self):
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "logs"))
        line = perf_online.redact_line(_log(_ts(60), "trainer.infer.aggregate", span_name="trainer.infer",
                                            duration_seconds=0.05, n=100, sum_seconds=5.0, window_seconds=60.0))
        with open(os.path.join(tmp, "logs", "evaluate-j-x.log"), "w") as fh:
            fh.write("\n".join([line, line, line]) + "\n")
        t = perf_online.parse_collection(tmp)
        self.assertEqual(len(t["spans"]), 1)


class SharedServerSlowdownTest(unittest.TestCase):
    def test_a_slower_plateau_during_another_run_is_reported(self):
        tmp = tempfile.mkdtemp()
        build_run(tmp, loop=0.10, pull=0.003, infer=0.085)
        job = os.path.join(tmp, "online", "J")
        segs = [(60, 0, 0, True), (120, 20, 2, False), (900, 100, 4, False), (900, 50, 1, False), (60, 10, 1, False)]
        points, pods = synth_phase(segs, t0=1000.0)
        with open(os.path.join(job, "polls.jsonl"), "w") as fh:
            for (t, done), (_, ready, pend) in zip(points, pods):
                fh.write(json.dumps({"t": t, "steps": [{"id": "train_and_evaluate", "current": done, "total": 1}],
                                     "pods": [{"pod": "generic-process-J-%d" % i, "role": "generic-process",
                                               "status": "Running", "state": "Running", "waiting_reason": None,
                                               "ready": True} for i in range(ready)],
                                     "other_jobs": [{"type": "Push", "status": "STARTED", "job": "ffffffffffffffffffffffff"}]
                                     if t > 1000 + 60 + 120 + 900 else []}) + "\n")
        a = perf_online.analyze_collection(job, perf_online.parse_collection(job), perf_online._polls(job), {})
        f = [x for x in a["findings"] if x["id"] == "slowdown-evaluation"]
        self.assertEqual(len(f), 1, [x["id"] for x in a["findings"]])
        self.assertIn("ready worker pods: 4 during the steady pace, 1 during the slower one", f[0]["evidence"])
        self.assertIn("Push ffffffff", f[0]["evidence"][-1])


if __name__ == "__main__":
    unittest.main()


def _span(t, pod, name, n, total, window=60.0, aggregate=True, parent=None):
    role = perf_online.pod_role(pod)
    return {"t": t, "pod": pod, "role": role, "span": name, "aggregate": aggregate, "n": n, "sum": total,
            "mean": total / n if n else None, "min": 0.0, "max": total, "window": window if aggregate else None,
            "span_kind": None, "parent": parent, "sampled": False}


class RootCauseTest(unittest.TestCase):
    """K29: each slow phase gets who set the pace, where that side's time went, the mechanism and
    the part to change, from the run's own step-level lines."""

    EV, W1 = "evaluate-J-x", "generic-process-J-1"

    def _vis_tables(self, fwd_total, wait_total, vis_w, gen_w, empty_w):
        t = 1000.0 + 600.0                       # one window covering the 600 s phase
        spans = [_span(t, self.EV, "visualization.batch", 600, fwd_total + 6.0, window=600),
                 _span(t, self.EV, "visualization.forward", 300, fwd_total, window=600),
                 _span(t, self.EV, "visualization.prepare_payload", 600, 4.0, window=600),
                 _span(t, self.EV, "visualization.push_submit", 600, 2.0, window=600),
                 _span(t, self.EV, "visualization.next_batch_wait", 600, wait_total, window=600),
                 _span(t, self.EV, "visualization.batch_rows_NOT_SECONDS", 600, 600.0, window=600),
                 _span(t, self.EV, "visualization.shared_parent_batch_NOT_SECONDS", 600, 300.0, window=600),
                 _span(t, self.EV, "redis.vis_queue.sync_push_NOT_SECONDS", 600, 0.0, window=600),
                 _span(t, self.W1, "generic_processor.iteration.vis", 600, vis_w, window=600),
                 _span(t, self.W1, "generic_processor.iteration.generate", 600, gen_w, window=600),
                 _span(t, self.W1, "generic_processor.iteration.empty", 6000, empty_w, window=600),
                 _span(t, self.W1, "storage.image_encode", 600, 0.6 * vis_w, window=600),
                 _span(t, self.W1, "heatmap.generate", 600, 0.2 * vis_w, window=600)]
        stage = {"stage": "post-processing: visualizations", "step": "after_visualizations",
                 "start": 1000.0, "end": 1600.0, "seconds": 600.0}
        return {"spans": spans}, stage

    def test_evaluation_pod_paced_visualization(self):
        tables, stage = self._vis_tables(fwd_total=500.0, wait_total=10.0, vis_w=60.0, gen_w=5.0, empty_w=500.0)
        rc = perf_online._rc_visualization(tables, stage, children=1, gpus=0)
        self.assertEqual(rc["pacing"], "the evaluation pod")
        self.assertEqual(rc["parts"][0]["name"], "forward passes on the evaluation pod")
        self.assertEqual(rc["confidence"], "measured")
        self.assertIn("300 forward passes", rc["driver"])
        self.assertIn("on CPU (no GPU)", rc["driver"])
        self.assertTrue(any("the workers' rendering" in r for r in rc["ruled_out"]))
        self.assertGreater(rc["explained_share"], 0.85)

    def test_worker_paced_visualization_marks_the_link_inferred(self):
        tables, stage = self._vis_tables(fwd_total=5.0, wait_total=560.0, vis_w=520.0, gen_w=2.0, empty_w=70.0)
        rc = perf_online._rc_visualization(tables, stage, children=1, gpus=0)
        self.assertEqual(rc["pacing"], "the generic workers")
        self.assertEqual(rc["link"], "inferred")
        self.assertEqual(rc["confidence"], "inferred")
        self.assertIn("not produced ahead of rendering", rc["mechanism"])
        self.assertTrue(any(o.startswith("render in parallel") for o in rc["optimize"]))
        self.assertTrue(any(o.startswith("image encoding") for o in rc["optimize"]))
        self.assertTrue(any("forward passes" in r for r in rc["ruled_out"]))

    def test_a_visualizer_run_twice_per_batch_is_named(self):
        tables, stage = self._vis_tables(fwd_total=5.0, wait_total=560.0, vis_w=520.0, gen_w=2.0, empty_w=70.0)
        tables["spans"].append(_span(1600.0, self.W1, "vis_calculator.user_visualizer.split_plot", 1200, 400.0,
                                     window=600))
        rc = perf_online._rc_visualization(tables, stage, children=1, gpus=0)
        m = next(m for m in rc["mechanisms"] if m["rule"] == "G4")
        self.assertIn("'split_plot' ran about twice per rendered batch", m["statement"])
        self.assertTrue(m["on_pacing_side"])                    # the workers set the pace here
        self.assertTrue(m["change"].startswith("the heatmap overlay"))

    def test_no_step_level_lines_gives_no_chain(self):
        stage = {"stage": "post-processing: visualizations", "step": "after_visualizations",
                 "start": 0.0, "end": 100.0, "seconds": 100.0}
        self.assertIsNone(perf_online._rc_visualization({"spans": []}, stage, 1, 0))

    def test_start_up_waiting_on_a_workers_first_command(self):
        eng = {"events": {
            "first_batch": {"records": [{"seconds_since_evaluate_start": 2700.0, "first_pull_seconds": 1.0,
                                         "first_step_seconds": 2.0}]},
            "remote_call": {"records": [{"command": "get_instances_data", "duration_seconds": 2650.0},
                                        {"command": "get_sample_id_type", "duration_seconds": 0.01}]},
            "command_served": {"records": [{"dataset_command": "get_instances_data", "duration_seconds": 2648.0,
                                            "first_in_process": True, "peak_rss_gb": 2.9}]}}}
        stage = {"stage": "start-up (job start → first evaluated batch)", "start": 0.0, "end": 2760.0, "seconds": 2760.0}
        rc = perf_online._rc_startup({"spans": []}, stage, eng)
        self.assertEqual(rc["driver"], "waiting on remote call 'get_instances_data'")
        self.assertIn("loads your integration", rc["mechanism"])
        self.assertEqual(rc["confidence"], "measured")
        self.assertTrue(any(o.startswith("integration:") for o in rc["optimize"]))

    def test_post_processing_is_not_analyzed(self):
        view = {"timeline": [
            {"stage": "evaluation loop", "start": 0.0, "end": 20.0, "seconds": 20.0},
            {"stage": "post-processing: insights (first analysis)", "start": 20.0, "end": 80.0, "seconds": 60.0,
             "step": "after_first_analysis"},
            {"stage": "visualization", "start": 80.0, "end": 100.0, "seconds": 20.0, "step": "after_visualizations"}]}
        roots = perf_online.build_root_causes({"spans": []}, view, {}, 100.0, None, 0.0, 20.0, 1, 0)
        self.assertFalse(any(r.get("phase", "").startswith("post-processing") for r in roots))
        self.assertFalse(any(m.get("phase", "").startswith("post-processing")
                             for r in roots for m in r.get("mechanisms") or []))
        self.assertEqual(perf_online._post_processing_spans(view), [(20.0, 80.0)])

    def test_post_processing_intervals_from_spans_without_step_markers(self):
        tables = {"spans": [_span(100.0, self.EV, "trainer.post_processing", 1, 60.0, aggregate=False),
                            _span(90.0, self.EV, "visualization.calculate_and_upload_visualizers", 1, 20.0,
                                  aggregate=False)]}
        self.assertEqual(perf_online._post_processing_spans({"timeline": []}, tables), [(40.0, 70.0), (90.0, 100.0)])
        # step markers missing from the start of a log: the span still gives the start
        view = {"timeline": [{"stage": "post-processing: insights (first analysis)", "start": 60.0, "end": 70.0,
                              "seconds": 10.0, "step": "after_first_analysis"},
                             {"stage": "visualization", "start": 70.0, "end": 90.0, "seconds": 20.0,
                              "step": "after_visualizations"}]}
        self.assertEqual(perf_online._post_processing_spans(view, tables), [(40.0, 70.0), (90.0, 100.0)])
        # a call that ends on the boundary started before it: it belongs to post-processing
        calls = {"spans": [_span(70.0, self.EV, "insights.precompute_instance_populations", 1, 3.0, aggregate=False)]}
        self.assertEqual(perf_online.window_stats(calls, 0.0, 100.0, skip=[(40.0, 70.0)]), {})

    def test_statistics_and_engine_lines_leave_post_processing_out(self):
        tables = {"spans": [_span(10.0, self.EV, "trainer.infer", 1, 1.0, aggregate=False),
                            _span(50.0, self.EV, "trainer.infer", 1, 1.0, aggregate=False),
                            _span(65.0, self.EV, "auto_pca.fit", 10, 30.0, window=30.0)]}
        st = perf_online.window_stats(tables, 0.0, 100.0, skip=[(30.0, 70.0)])
        self.assertEqual(st["evaluate:trainer.infer"]["calls"], 1)
        self.assertNotIn("evaluate:auto_pca.fit", st)                  # its window lies inside post-processing
        eng = {"warnings": [{"message": "late", "count": 2, "first": 40.0, "last": 50.0, "times": [[40.0, 1], [50.0, 1]]},
                            {"message": "both", "count": 3, "first": 10.0, "last": 60.0,
                             "times": [[10.0, 1], [60.0, 2]]}],
               "events": {"ls_reload": {"count": 2, "pods": ["p"], "first": 40.0, "last": 45.0,
                                        "records": [{"t": 40.0, "pod": "p"}, {"t": 45.0, "pod": "p"}]},
                          "heap_trim": {"count": 2, "pods": ["p"], "first": 5.0, "last": 50.0,
                                        "records": [{"t": 5.0, "pod": "p"}, {"t": 50.0, "pod": "p"}]}},
               "uploads": [{"t": 40.0, "kind": "image", "seconds": 1.0}, {"t": 90.0, "kind": "image", "seconds": 1.0}]}
        sc = perf_online._scoped_engine(eng, [(30.0, 70.0)])
        self.assertEqual([(w["message"], w["count"]) for w in sc["warnings"]], [("both", 1)])
        self.assertNotIn("ls_reload", sc["events"])
        self.assertEqual(sc["events"]["heap_trim"]["count"], 1)
        self.assertEqual([u["t"] for u in sc["uploads"]], [90.0])

    def test_parser_reads_new_lines_and_recovers_throttled_counts(self):
        d = tempfile.mkdtemp()
        lines = [_log(_ts(10), "process resources", label="generic_processor", cpu_limit_cores=4.0,
                      container_cores_used=3.9, cpu_throttled_share=0.4),
                 _log(_ts(11), "sparse custom-LS stash over capacity; evicting", throttle_interval_sec=10.0,
                      throttle_suppressed=41, throttle_count=150),
                 _log(_ts(12), "[multi-LS-insights-timing] Phase 4: fetch-similar resources done in 40.76s "
                               "(total so far: 45.53s)")]
        with open(os.path.join(d, "generic-process-J-1.log"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        eng = perf_online.parse_engine(d)
        rec = eng["events"]["resources"]["records"][0]
        self.assertEqual((rec["label"], rec["pod"], rec["container_cores_used"]),
                         ("generic_processor", "generic-process-J-1", 3.9))
        self.assertEqual(eng["events"]["stash_eviction"]["count"], 42)     # 1 kept + 41 dropped
        self.assertEqual(eng["insights_phases"][0]["seconds"], 40.76)


class ServerSettingsTest(unittest.TestCase):
    """K30: what each pod got against what the run used; recommends for automatic or manual settings."""

    def _eng(self):
        recs = []
        for i in range(10):
            recs.append({"t": 1000.0 + 60 * i, "pod": "generic-process-J-1", "label": "generic_processor",
                         "cpu_limit_cores": 4.0, "container_cores_used": 3.95, "cpu_throttled_share": 0.35,
                         "container_memory_gb": 1.0, "container_memory_limit_gb": 19.0})
            recs.append({"t": 1000.0 + 60 * i, "pod": "evaluate-J-x", "label": "engine",
                         "cpu_limit_cores": 16.0, "container_cores_used": 0.9, "cpu_throttled_share": 0.0,
                         "container_memory_gb": 2.0, "container_memory_limit_gb": 2.2})
        return {"events": {"resources": {"records": recs}}}

    def test_verdicts_and_effects(self):
        roots = [{"pacing": "the generic workers"}]
        st = perf_online.server_settings(self._eng(), {}, {}, {"pods": {}}, {"batch_size": 1}, 0, None, roots,
                                         "manual")
        rows = {r["setting"]: r for r in st["rows"]}
        self.assertEqual(rows["worker pods CPU"]["verdict"], "limited the run")
        self.assertEqual(rows["worker pods CPU"]["effect"], "runtime")
        self.assertIn("raise the worker pods CPU limit above 4 cores", rows["worker pods CPU"]["recommendation"])
        self.assertEqual(rows["worker pods memory"]["verdict"], "mostly unused")
        self.assertEqual(rows["worker pods memory"]["effect"], "capacity for other jobs, not runtime")
        self.assertEqual(rows["evaluation pod memory"]["verdict"], "near its limit")
        self.assertEqual(rows["evaluation pod CPU"]["verdict"], "mostly unused")
        self.assertEqual(rows["worker pods × processes"]["verdict"], "limited the run")
        self.assertTrue(rows["worker pods CPU"]["recommendation"].startswith("Manual settings:"))

    def test_mode_wording(self):
        roots = [{"pacing": "the generic workers"}]
        auto = perf_online.server_settings(self._eng(), {}, {}, {"pods": {}}, {}, 0, None, roots, "automatic")
        unknown = perf_online.server_settings(self._eng(), {}, {}, {"pods": {}}, {}, 0, None, roots, None)
        r = {x["setting"]: x for x in auto["rows"]}["worker pods CPU"]["recommendation"]
        self.assertTrue(r.startswith("Automatic settings:") and "Manual" not in r)
        u = {x["setting"]: x for x in unknown["rows"]}["worker pods CPU"]["recommendation"]
        self.assertIn("Automatic settings:", u)
        self.assertIn("Manual settings:", u)
        self.assertEqual(unknown["mode"], "not stated")

    def test_without_readings_memory_comes_from_the_pod_limits(self):
        memory = {"pods": {"generic-process-J-1": {"role": "generic-process", "limit_gb": 19.0, "peak_rss_gb": 2.0}}}
        st = perf_online.server_settings({"events": {}}, {}, {}, memory, {}, 1, None, [], None)
        rows = {r["setting"]: r for r in st["rows"]}
        self.assertEqual(rows["worker pods memory"]["verdict"], "mostly unused")
        self.assertNotIn("worker pods CPU", rows)
        self.assertTrue(st["note"])

    def test_sections_render(self):
        roots = [perf_online._rc_chain("post-processing: PCA: default", 30.0, "the evaluation pod",
                                       [perf_online._rc_part("waiting for the results stream to drain", 21.0)],
                                       "waiting for the results stream to drain", "The stage mostly waited.",
                                       ["the end-of-evaluation drain"], [], [], [])]
        roots[0]["share_of_run"] = 0.1
        md = "\n".join(perf_online.render_root_causes(roots))
        self.assertIn("### Root causes", md)
        self.assertIn("**Pace set by:** the evaluation pod", md)
        self.assertIn("70% of the phase (9.0 s unexplained); confidence: measured", md)
        empty = "\n".join(perf_online.render_root_causes([]))
        self.assertIn("no step-level engine timing", empty)
        st = perf_online.server_settings(self._eng(), {}, {}, {"pods": {}}, {}, 0, None,
                                         [{"pacing": "the generic workers"}], "automatic")
        md = "\n".join(perf_online.render_server_settings(st))
        self.assertIn("### Server settings", md)
        self.assertIn("never changes server settings", md)
        self.assertIn("| worker pods CPU | 4.0 cores |", md)


class CountersAreNotTimeTest(unittest.TestCase):

    def test_counters_leave_the_time_tables(self):
        spans = [_span(1060.0, "evaluate-J-x", "redis.vis_queue.payload_mib_NOT_SECONDS", 10, 47.0),
                 _span(1060.0, "evaluate-J-x", "redis.vis_queue.serialize_and_push", 10, 0.5)]
        view = perf_online.engine_view({"spans": spans}, {}, 1000.0, 1100.0, None, None)
        self.assertEqual([s["span"] for s in view["stages"]], ["redis.vis_queue.serialize_and_push"])
        c = view["counters"][0]
        self.assertEqual((c["label"], c["mean"]), ("visualization element size (MiB)", 4.7))


class VisualizationSteadyTest(unittest.TestCase):
    """6B: the collector asks for the run to be stopped only when the visualization's pace is steady
    over enough samples and stopping still saves a real part of it."""

    def _polls(self, minutes, rate=2.0, total=10000):
        polls = []
        for i in range(int(minutes * 2) + 1):
            polls.append({"t": 30.0 * i, "steps": [{"id": "visualize_samples", "status": "RUNNING",
                                                    "current": min(total, int(rate * 30 * i)), "total": total}],
                          "pods": [{"pod": "generic-process-J-1", "role": "generic-process", "ready": True,
                                    "status": "Running", "state": "Running"}]})
        return polls

    def test_needed_samples_scale_with_the_visualization(self):
        self.assertEqual(perf_online.steady_samples_needed(10000), 1000)
        self.assertEqual(perf_online.steady_samples_needed(2000), 400)
        self.assertEqual(perf_online.steady_samples_needed(300), 200)

    def test_steady_over_enough_samples(self):
        st = perf_online.visualization_steady(self._polls(12))
        self.assertIsNotNone(st)
        self.assertAlmostEqual(st["rate"], 2.0, places=1)
        self.assertGreaterEqual(st["steady_samples"], st["samples_needed"])
        self.assertGreater(st["saving_seconds"], perf_online.STOP_MIN_SAVING_SECONDS)

    def test_steady_but_not_over_enough_samples_yet(self):
        self.assertIsNone(perf_online.visualization_steady(self._polls(5)))         # ~500 of 1000 samples

    def test_a_slow_visualizer_keeps_going_until_it_has_the_samples(self):
        self.assertIsNone(perf_online.visualization_steady(self._polls(15, rate=0.2)))  # ~150 steady samples

    def test_a_short_visualization_runs_to_the_end(self):
        self.assertIsNone(perf_online.visualization_steady(self._polls(3, rate=2.0, total=300)))

    def test_a_nearly_finished_visualization_runs_to_the_end(self):
        self.assertIsNone(perf_online.visualization_steady(self._polls(12, rate=2.0, total=1600)))


class MemoryPriorityOnlineTest(unittest.TestCase):
    """--priority memory: the analysis leads with where the memory went."""

    def test_memory_root_cause_from_resource_readings(self):
        eng = {"events": {"resources": {"records": [
            {"t": 100.0, "pod": "generic-process-J-1", "container_memory_gb": 9.0, "container_memory_limit_gb": 19.0},
            {"t": 160.0, "pod": "generic-process-J-1", "container_memory_gb": 9.3, "container_memory_limit_gb": 19.0},
            {"t": 160.0, "pod": "evaluate-J-x", "container_memory_gb": 2.0, "container_memory_limit_gb": 10.0}]}}}
        view = {"timeline": [{"stage": "post-processing: visualizations", "start": 150.0, "end": 200.0}]}
        memory = {"pods": {"generic-process-J-1": {"role": "generic-process", "limit_gb": 19.0, "peak_rss_gb": 9.3},
                           "evaluate-J-x": {"role": "evaluate", "limit_gb": 10.0, "peak_rss_gb": 2.0}}}
        rc = perf_online.memory_root_cause(eng, view, memory, {"generation_peak_rss_gb": 8.0},
                                           {"children_per_pod": {"generic-process-J-1": 1}})
        self.assertEqual((rc["subject"], rc["peak_gb"], rc["limit_gb"]), ("worker pod", 9.3, 19.0))
        self.assertEqual(rc["peak_phase"], "post-processing: visualizations")
        self.assertEqual(rc["confidence"], "measured")
        self.assertEqual(rc["parts"][0]["gb"], 8.0)                              # the integration's share
        self.assertTrue(any(o.startswith("integration:") for o in rc["optimize"]))
        self.assertTrue(any("reservation" in o for o in rc["optimize"]))       # 19 GB for a 9.3 GB peak

    def test_analysis_leads_with_memory_under_pressure(self):
        a = build_run(tempfile.mkdtemp(), loop=0.10, pull=0.003, infer=0.085, eval_seconds=300.0,
                      vis_seconds=900.0, engine=True, priority="memory", eval_rss=9.5, eval_limit="10Gi")
        self.assertTrue(a["memory_pressure"]["pressure"])
        self.assertIn("the evaluation pod peaked at 9.5 of 10.0 GB (95%)", a["memory_pressure"]["reasons"])
        self.assertEqual((a["priority"], a["primary"], a["memory_leads"]), ("memory", "memory-priority", True))
        self.assertEqual(a["root_causes"][0]["kind"], "memory")
        self.assertIsNotNone(a["runtime_primary"])
        md = "\n".join(perf_online.render_root_causes(a["root_causes"]))
        self.assertIn("Memory (the priority)", md)

    def test_without_pressure_the_runtime_leads_even_with_memory_first(self):
        a = build_run(tempfile.mkdtemp(), loop=0.10, pull=0.003, infer=0.085, eval_seconds=300.0,
                      vis_seconds=900.0, engine=True, priority="memory")
        self.assertEqual(a["memory_pressure"], {"pressure": False, "reasons": []})
        self.assertFalse(a["memory_leads"])
        self.assertEqual(a["primary"], a["runtime_primary"])
        kinds = [r.get("kind") for r in a["root_causes"]]
        self.assertNotEqual(kinds[0], "memory")
        self.assertIn("memory", kinds)                                  # still there, after the runtime chains

    def test_memory_pressure_reasons(self):
        mem = {"pods": {"generic-process-J-abcde": {"limit_gb": 19.0, "peak_rss_gb": 9.0},
                        "evaluate-J-x": {"oom": True}}}
        eng = {"warnings": [{"message": "Potential memory leak detected in worker", "count": 3}]}
        p = perf_online.memory_pressure(mem, eng, ["This job needs more memory than the installation can guarantee"],
                                        {"memory_status": "AMBER", "memory_reasons": ["one worker holds 6 GB"]})
        self.assertTrue(p["pressure"])
        self.assertEqual(len(p["reasons"]), 4)
        self.assertFalse(perf_online.memory_pressure({"pods": {"generic-process-J-abcde": {
            "limit_gb": 19.0, "peak_rss_gb": 9.0}}}, {}, [], {"memory_status": "GREEN"})["pressure"])


class MechanismRulesTest(unittest.TestCase):
    """K29 generic rules: each reads a shape in the run's own measurements and states a mechanism
    with its cost — never the name of a platform feature or setting."""

    EV, W1, W2, SH = "evaluate-J-x", "generic-process-J-1", "generic-process-J-2", "streaming-handler-J-1"

    def _ctx(self, kind, spans=(), events=None, start=0.0, end=600.0, config=None, view=None, pacing="evaluate",
             e0=None, e1=None, children=1):
        tables = {"spans": list(spans), "config": config or {}}
        eng = {"events": {k: {"records": v, "count": len(v)} for k, v in (events or {}).items()}}
        stage = {"stage": kind, "start": start, "end": end, "seconds": end - start}
        ctx = perf_online._phase_ctx(tables, eng, view or {}, stage, kind, pacing, children)
        ctx["e0"], ctx["e1"] = e0, e1
        return ctx

    def test_per_step_work_around_a_small_model(self):
        t = 600.0
        spans = [_span(t, self.EV, "trainer.validation_step", 10000, 560.0, window=600),
                 _span(t, self.EV, "trainer.validation_step.forward.originals", 10000, 10.0, window=600),
                 _span(t, self.EV, "trainer.validation_step.marshal_numpy", 10000, 300.0, window=600),
                 _span(t, self.EV, "trainer.validation_step.extract_ls", 10000, 50.0, window=600),
                 _span(t, self.EV, "trainer.validation_step.pull_batch", 10000, 20.0, window=600)]
        [s] = perf_online.g3_step_overhead(self._ctx("evaluation", spans, config={"batch_size": 1}))
        self.assertEqual(s["rule"], "G3")
        self.assertIn("10000 steps", s["statement"])
        self.assertIn("'output conversion'", s["statement"])          # the engine's step, labelled
        self.assertAlmostEqual(s["cost_seconds"], 350.0)
        self.assertTrue(s["on_pacing_side"])

    def test_a_data_movement_step_once_per_row(self):
        t = 600.0
        spans = [_span(t, self.EV, "trainer.validation_step", 100, 300.0, window=600),
                 _span(t, self.EV, "redis.dataset_queue.pop_deser", 800, 120.0, window=600)]
        view = {"measurements": {"rows_per_batch": {"median": 8.0}}}
        [s] = perf_online.g3_per_row(self._ctx("evaluation", spans, view=view))
        self.assertIn("once per row, not once per batch", s["statement"])
        self.assertIn("800 rows in 100 batches", s["statement"])

    def test_the_same_store_loaded_again_and_again(self):
        recs = [{"t": 10.0 + i, "duration_seconds": 0.5, "ls_type": "a" if i % 2 else "b",
                 "caller_span": "insights.single_calculate_insights"} for i in range(10)]
        [s] = perf_online.g4_store_loads(self._ctx("post-processing", events={"ls_reload": recs}))
        self.assertEqual(s["rule"], "G4")
        self.assertIn("loaded 10 times for 2 distinct", s["statement"])
        self.assertAlmostEqual(s["cost_seconds"], 4.0)                 # 8 repeated loads × 0.5 s

    def test_every_worker_repeats_the_integration_load(self):
        ready = [{"t": 50.0 + i, "pod": "generic-process-J-%d" % i, "seconds": 40.0, "raw_lines": 12} for i in range(3)]
        loads = [{"t": 50.0 + i, "pod": "generic-process-J-%d" % i, "peak_rss_gb": 6.0} for i in range(3)]
        [s] = perf_online.g4_startup(self._ctx("start-up", events={"worker_ready": ready, "worker_code_load": loads},
                                               end=100.0))
        self.assertIn("3 worker processes each loaded your integration on their own", s["statement"])
        self.assertIn("6.0 GB each", s["statement"])
        cheap = [dict(r, seconds=1.0) for r in ready]
        self.assertEqual(perf_online.g4_startup(self._ctx("start-up", events={
            "worker_ready": cheap, "worker_code_load": [dict(r, peak_rss_gb=0.5) for r in loads]})), [])

    def test_a_worker_replaced_mid_evaluation(self):
        spans = [_span(100.0, self.W1, "generic_processor.iteration.generate", 10, 5.0),
                 _span(200.0, self.W1, "generic_processor.iteration.generate", 10, 5.0),
                 _span(320.0, self.W2, "generic_processor.iteration.generate", 10, 5.0),
                 _span(590.0, self.W2, "generic_processor.iteration.generate", 10, 5.0),
                 _span(300.0, self.EV, "trainer.validation_step.pull_batch", 1, 90.0, aggregate=False)]
        events = {"worker_ready": [{"t": 300.0, "pod": self.W2, "seconds": 38.0}],
                  "worker_memory_resize": [{"t": 190.0, "current_memory_gb": 16.0, "calculated_memory_limit_gb": 19,
                                            "current_stored_memory_limit_gb": 18}]}
        [s] = perf_online.g4_replaced_workers(self._ctx("evaluation", spans, events, start=50.0))
        self.assertIn("its replacement loaded your integration again (38.0 s", s["statement"])
        self.assertIn("memory setting (18 → 19 GB", s["statement"])
        self.assertAlmostEqual(s["cost_seconds"], 90.0)

    def test_a_loss_that_costs_a_forward_pass(self):
        t = 600.0
        spans = [_span(t, self.EV, "trainer.validation_step.forward.originals", 1000, 100.0, window=600),
                 _span(t, self.W1, "metrics.loss", 1000, 90.0, window=600)]
        [s] = perf_online.g4_metric_like_forward(self._ctx("evaluation", spans, pacing="generic-process"))
        self.assertEqual((s["rule"], s["how"]), ("G4", "inferred"))
        self.assertIn("likely runs the model again", s["statement"])
        self.assertTrue(s["change"].startswith("integration:"))

    def test_inputs_sent_again_with_the_metrics(self):
        rec = {"t": 10.0, "total_bytes": 5e6, "field.batch": 4.2e6}
        spans = [_span(600.0, self.EV, "trainer.validation_step.push_metrics", 100, 60.0, window=600)]
        [s] = perf_online.g4_payload_twice(self._ctx("evaluation", spans, {"metrics_payload": [rec]}))
        self.assertIn("4.2 of 5.0 MB", s["statement"])

    def test_a_forward_pass_per_derived_row(self):
        events = {"instance_metrics": [{"t": 10.0 + i, "batch_size": 1} for i in range(100)]}
        legacy = [_span(600.0, self.EV, "trainer.validation_step.forward.originals", 360, 360.0, window=600)]
        [s] = perf_online.g4_rows_per_source(self._ctx("evaluation", legacy, events, config={"total_samples": 360}))
        self.assertIn("ran 360 times for 100 source samples (3.6 per source sample)", s["statement"])
        self.assertIn("the 260 rows derived from those samples", s["statement"])
        self.assertAlmostEqual(s["cost_seconds"], 260.0)
        shared = [_span(600.0, self.EV, "trainer.validation_step.forward.originals", 100, 100.0, window=600)]
        self.assertEqual(perf_online.g4_rows_per_source(self._ctx("evaluation", shared, events,
                                                                   config={"total_samples": 360})), [])
        self.assertEqual(perf_online.g4_rows_per_source(self._ctx("evaluation", legacy, None,
                                                                   config={"total_samples": 360})), [])

    def test_sources_generated_more_than_once(self):
        spans = [_span(600.0, self.W1, "samples_generator.generate_sample", 3000, 300.0, window=600)]
        [s] = perf_online.g4_regeneration(self._ctx("evaluation", spans, config={"total_samples": 1000},
                                                     pacing="generic-process"))
        self.assertIn("generated 3.0 times each", s["statement"])

    def test_memory_that_keeps_rising(self):
        recs = [{"t": 60.0 * i, "pod": self.EV, "container_memory_gb": 2.0 + 0.5 * i, "container_memory_limit_gb": 10.0}
                for i in range(8)]
        [s] = perf_online.g5_memory(self._ctx("evaluation", events={"resources": recs}))
        self.assertEqual(s["rule"], "G5")
        self.assertIn("0.50 GB/min", s["statement"])
        self.assertIn("reaches its 10 GB limit", s["statement"])
        flat = [dict(r, container_memory_gb=2.0 + 0.01 * (i % 2)) for i, r in enumerate(recs)]
        self.assertEqual(perf_online.g5_memory(self._ctx("evaluation", events={"resources": flat})), [])

    def test_a_wait_that_grows_through_the_phase(self):
        spans = [_span(60.0 * (i + 1), self.W1, "storage.async_upload.queue_wait", 10, 1.0 + 3.0 * i) for i in range(9)]
        [s] = perf_online.g5_wait_growth(self._ctx("evaluation", spans, pacing="generic-process"))
        self.assertEqual(s["rule"], "G5")
        self.assertIn("per call through the phase", s["statement"])

    def test_one_core_of_four_while_setting_the_pace(self):
        recs = [{"t": 60.0 * i, "pod": self.W1, "cores_used": 0.98, "cpu_limit_cores": 4.0} for i in range(5)]
        [s] = perf_online.g6_cores(self._ctx("visualization", events={"resources": recs}, pacing="generic-process"))
        self.assertEqual(s["rule"], "G6")
        self.assertIn("1.0 of its 4 cores", s["statement"])
        busy = [dict(r, cores_used=3.5) for r in recs]
        self.assertEqual(perf_online.g6_cores(self._ctx("visualization", events={"resources": busy},
                                                        pacing="generic-process")), [])

    def test_one_saturated_pod_beside_idle_pods(self):
        spans = [_span(60.0 * i, self.W1, "generic_processor.iteration.vis", 10, 58.0) for i in range(1, 11)] + \
                [_span(60.0 * i, self.W2, "generic_processor.iteration.vis", 1, 1.0) for i in range(1, 11)]
        [s] = perf_online.g6_imbalance(self._ctx("visualization", spans, pacing="generic-process"))
        self.assertIn("One worker pod was busy", s["statement"])

    def test_the_metrics_hand_off_tells_its_two_causes_apart(self):
        base = [_span(600.0, self.EV, "trainer.validation_step", 1000, 500.0, window=600),
                _span(600.0, self.EV, "trainer.validation_step.push_metrics", 1000, 150.0, window=600),
                _span(600.0, self.W1, "generic_processor.iteration.metrics", 1000, 560.0, window=600)]
        full = base + [_span(600.0, self.EV, "redis.metrics_queue.queue_full_wait", 1000, 400.0, window=600),
                       _span(600.0, self.EV, "redis.metrics_queue.push", 1000, 50.0, window=600)]
        [s] = perf_online.g1_metrics_handoff(self._ctx("evaluation", full))
        self.assertEqual(s["rule"], "G1")
        self.assertIn("because the metrics queue was full", s["statement"])
        busy = base + [_span(600.0, self.EV, "redis.metrics_queue.push", 1000, 400.0, window=600)]
        [s] = perf_online.g1_metrics_handoff(self._ctx("evaluation", busy))
        self.assertEqual(s["rule"], "G6")
        self.assertIn("busy serializing and storing", s["statement"])

    def test_the_parser_times_each_workers_own_load(self):
        d = tempfile.mkdtemp()
        lines = [_log(_ts(10), "connected to rabbitmq server"), "integration print", "another print",
                 _log(_ts(52), "checking state: 0 for max sample in queue"),
                 _log(_ts(60), "Updating existing generic pods settings based on current memory",
                      current_memory_gb=16.0, calculated_memory_limit_gb=19)]
        with open(os.path.join(d, "generic-process-J-1.log"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        eng = perf_online.parse_engine(d)
        [r] = eng["events"]["worker_ready"]["records"]
        self.assertEqual((r["seconds"], r["raw_lines"]), (42.0, 2))
        self.assertEqual(eng["events"]["worker_memory_resize"]["records"][0]["calculated_memory_limit_gb"], 19)

    def test_rules_never_key_on_feature_or_setting_names(self):
        import inspect
        src = "".join(inspect.getsource(f) for fs in perf_online.DETECTORS.values() for f in fs)
        for word in ("FEATURE_FLAG", "EVAL_", "VIS_", "HEATMAP_", "STORAGE_", "LEAP_FORK", "PRIORITIZE",
                     "STREAMING_", "fast path", "batch_reuse", "heap trim", "autosizer", "AutoSizer"):
            self.assertNotIn(word, src)

    def test_no_finding_or_mechanism_reads_like_a_checklist(self):
        a = build_run(tempfile.mkdtemp(), loop=0.10, pull=0.003, infer=0.085, eval_seconds=300.0,
                      vis_seconds=900.0, engine=True)
        text = json.dumps([[f["title"], f["explanation"]] for f in a["findings"]] +
                          [m.get("statement") for r in a["root_causes"] for m in r.get("mechanisms") or []])
        for word in ("Degraded", "degraded", "not seen", "Active:", "is active", "was active"):
            self.assertNotIn(word, text)
