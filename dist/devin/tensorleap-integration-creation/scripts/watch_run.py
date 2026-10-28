#!/usr/bin/env python3
"""Follow a Tensorleap push to its chained Evaluate and exit when there is something to act on.

Reads only `leap run info <id>` JSON (the CLI's script-facing output), never console text,
and only a few fields: status, updatedAt, steps[].status/total, chainedEvaluate.jobId,
errorReport/hints. Stdlib only; runs under any Python 3.8+.

Usage:
  watch_run.py --push-log push.log     # id from the output of `leap push -e --no-wait ...`
  watch_run.py <run-id>                # a Push id (follows its evaluate) or an Evaluate id
  watch_run.py --resume                # ids saved in .tensorleap/runs.json by an earlier call
  watch_run.py --resume --keep-waiting # after a STALLED report the user chose to wait out:
                                       # the next stall is measured from now, not from the old progress
  add --once to check a single time and exit (status RUNNING while still in flight)

Ends with one line: RESULT status=<STATUS> push=<id> eval=<id> step=<id> progress=<c/t> ...
  0 FINISHED              the evaluate finished
  1 FAILED                a run failed / was stopped / terminated (errorReport and log tail printed above)
  2 STALLED               no progress for the step's window (counter steps 30m, others 2h)
  3 NO_EVAL               the push finished but no evaluate was chained to it
  4 CLI_CONTRACT_CHANGED  `leap run info` output is no longer understood (raw JSON printed)
  5 RUNNING               --once only: still in flight
  6 CLI_ERROR             the CLI kept failing (auth, network, missing run); with --once, on the first failure
Set TL_CLI to use another CLI binary (default `leap`).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

KNOWN_SCHEMA = 1  # `schemaVersion` in run info, once the CLI emits it; newer => contract changed
ACTIVE = {"UNSTARTED", "QUEUED", "PENDING", "INITIALIZING", "STARTED"}
FAILED = {"FAILED", "STOPPED", "TERMINATED"}
CODES = {"FINISHED": 0, "FAILED": 1, "STALLED": 2, "NO_EVAL": 3,
         "CLI_CONTRACT_CHANGED": 4, "RUNNING": 5, "CLI_ERROR": 6}
COUNTER_WINDOW = 30 * 60   # a step reporting current/total that has not moved
QUIET_WINDOW = 2 * 60 * 60  # a step without a counter, or a run waiting to start
MAX_CLI_FAILURES = 15
STATE_FILE = os.path.join(".tensorleap", "runs.json")
CLI = os.environ.get("TL_CLI", "leap")


class ContractChanged(Exception):
    pass


def run_info(run_id):
    """Parsed `run info` JSON, None on a CLI failure (retryable), ContractChanged on unreadable output."""
    try:
        proc = subprocess.run([CLI, "run", "info", run_id], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"watch_run: {CLI} run info failed: {e}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(f"watch_run: {CLI} run info {run_id} exited {proc.returncode}: {proc.stderr.strip()[-300:]}",
              file=sys.stderr)
        return None
    try:
        info = json.loads(proc.stdout)
    except ValueError:
        raise ContractChanged(proc.stdout[-2000:])
    if not isinstance(info, dict) or "status" not in info:
        raise ContractChanged(proc.stdout[-2000:])
    if isinstance(info.get("schemaVersion"), int) and info["schemaVersion"] > KNOWN_SCHEMA:
        raise ContractChanged(proc.stdout[-2000:])
    return info


def is_push(info):
    return "push" in f"{info.get('type', '')} {info.get('subType', '')}".lower()


def running_step(info):
    for step in info.get("steps") or []:
        if isinstance(step, dict) and step.get("status") == "RUNNING":
            return step
    return None


def progress_fields(info):
    step = running_step(info)
    if step is None:
        return {}
    fields = {"step": step.get("id")}
    if step.get("total") is not None:
        fields["progress"] = f"{step.get('current')}/{step.get('total')}"
    return fields


def stall_seconds(info, waiting_since=None):
    """Seconds since the server last saw progress; updatedAt moves whenever a counter moves.

    waiting_since (epoch seconds) is when the user chose to wait out a reported stall."""
    try:
        updated = datetime.strptime(info["updatedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        raise ContractChanged(json.dumps(info)[-2000:])
    since = max(updated.timestamp(), waiting_since or 0)
    return datetime.now(timezone.utc).timestamp() - since


def stall_window(info):
    step = running_step(info)
    return COUNTER_WINDOW if step is not None and step.get("total") is not None else QUIET_WINDOW


def save_state(push_id, eval_id, waiting_since=None):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"push": push_id, "eval": eval_id, "waiting_since": waiting_since}, f)


def load_state():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        return state.get("push"), state.get("eval"), state.get("waiting_since")
    except (OSError, ValueError):
        return None, None, None


def id_from_push_log(path):
    with open(path, errors="replace") as f:
        text = f.read()
    for line in text.splitlines():  # a future `leap push --output json` line
        line = line.strip()
        if line.startswith("{"):
            try:
                job = json.loads(line).get("jobId")
            except ValueError:
                continue
            if job:
                return job
    match = re.search(r"run info\s+([0-9a-f]{24})", text)
    return match.group(1) if match else None


def failure_details(info, run_id):
    for key in ("errorReport", "hints"):
        if info.get(key):
            print(f"{key}: {json.dumps(info[key])}")
    try:
        logs = subprocess.run([CLI, "run", "logs", run_id], capture_output=True, text=True, timeout=180)
        tail = logs.stdout.strip().splitlines()[-40:]
        if tail:
            print(f"last {len(tail)} log lines of {run_id}:")
            print("\n".join(tail))
    except (OSError, subprocess.TimeoutExpired):
        pass


def finish(status, push_id, eval_id, **fields):
    parts = [f"status={status}", f"push={push_id or '-'}", f"eval={eval_id or '-'}"]
    parts += [f"{k}={v}" for k, v in fields.items() if v is not None]
    print("RESULT " + " ".join(parts), flush=True)
    sys.exit(CODES[status])


def watch(push_id, eval_id, once, interval, waiting_since=None):
    failures = 0
    while True:
        current = eval_id or push_id
        try:
            info = run_info(current)
        except ContractChanged as raw:
            print(f"watch_run: cannot read `{CLI} run info {current}` output:\n{raw}")
            finish("CLI_CONTRACT_CHANGED", push_id, eval_id)
        if info is None:
            failures += 1
            if failures >= MAX_CLI_FAILURES:
                finish("CLI_ERROR", push_id, eval_id, failures=failures)
            if once:
                finish("CLI_ERROR", push_id, eval_id, reason="run_info_failed")
            time.sleep(interval)
            continue
        failures = 0
        status = info["status"]
        try:
            if eval_id is None and is_push(info):
                if status in FAILED:
                    failure_details(info, current)
                    finish("FAILED", push_id, None, stage="push", run_status=status)
                if status == "FINISHED":
                    chained = info.get("chainedEvaluate") or {}
                    eval_id = chained.get("jobId")
                    if not eval_id:
                        failure_details(info, current)
                        finish("NO_EVAL", push_id, None, chained=chained.get("status"))
                    save_state(push_id, eval_id, waiting_since)
                    continue
            else:
                if eval_id is None:  # started from an Evaluate id directly
                    eval_id, push_id = current, None
                    save_state(push_id, eval_id, waiting_since)
                if status == "FINISHED":
                    finish("FINISHED", push_id, eval_id)
                if status in FAILED:
                    failure_details(info, current)
                    finish("FAILED", push_id, eval_id, stage="evaluate", run_status=status,
                           **progress_fields(info))
            if status not in ACTIVE:
                print(f"watch_run: unknown status {status!r} on {current}; treating it as in flight",
                      file=sys.stderr)
            stalled = stall_seconds(info, waiting_since)
            if stalled > stall_window(info):
                finish("STALLED", push_id, eval_id, run_status=status,
                       stalled_min=int(stalled // 60), **progress_fields(info))
        except ContractChanged as raw:
            print(f"watch_run: cannot read `{CLI} run info {current}` output:\n{raw}")
            finish("CLI_CONTRACT_CHANGED", push_id, eval_id)
        if once:
            finish("RUNNING", push_id, eval_id, run_status=status, **progress_fields(info))
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id", nargs="?", help="a Push or Evaluate run id")
    parser.add_argument("--push-log", help="file holding the output of `leap push -e --no-wait ...`")
    parser.add_argument("--resume", action="store_true", help=f"continue from {STATE_FILE}")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--keep-waiting", action="store_true",
                        help="with --resume: the user chose to wait out a reported stall; measure from now")
    parser.add_argument("--interval", type=int, default=120, help="seconds between checks (default 120)")
    args = parser.parse_args()

    push_id = eval_id = waiting_since = None
    if args.keep_waiting and not args.resume:
        parser.error("--keep-waiting needs --resume")
    if args.resume:
        push_id, eval_id, waiting_since = load_state()
        if args.keep_waiting:
            waiting_since = time.time()
            save_state(push_id, eval_id, waiting_since)
    elif args.push_log:
        push_id = id_from_push_log(args.push_log)
        if not push_id:
            print(f"watch_run: no run id in {args.push_log}; find the push with `{CLI} run list -t Push`")
            finish("CLI_ERROR", None, None, reason="no_id_in_push_log")
    elif args.run_id:
        push_id = args.run_id
    if not (push_id or eval_id):
        parser.error("give a run id, --push-log, or --resume (with saved ids)")
    if push_id and not eval_id:
        save_state(push_id, None)
    watch(push_id, eval_id, args.once, args.interval, waiting_since)


if __name__ == "__main__":
    main()
