import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(HERE, "watch_run.py")

FAKE_CLI = """#!/usr/bin/env python3
import json, os, sys
runs = json.load(open(os.environ["FAKE_RUNS"]))
if sys.argv[1:3] == ["run", "logs"]:
    print("line 1\\nline 2")
    sys.exit(0)
run = runs.get(sys.argv[3])
if run is None:
    print("Error: run not found", file=sys.stderr)
    sys.exit(1)
print(run if isinstance(run, str) else json.dumps(run))
"""


def ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def push(chained=None, status="FINISHED"):
    run = {"jobId": "p", "type": "PUSH", "subType": "Push", "status": status, "updatedAt": ago(1), "steps": []}
    if chained is not None:
        run["chainedEvaluate"] = chained
    return run


def evaluate(status="STARTED", minutes=1, step=None, extra=None):
    run = {"jobId": "e", "type": "TRAINING", "subType": "Evaluate", "status": status,
           "updatedAt": ago(minutes), "steps": [step] if step else []}
    run.update(extra or {})
    return run


def watch(runs, *args, cwd=None):
    with tempfile.TemporaryDirectory() as tmp:
        cli = os.path.join(tmp, "leap")
        with open(cli, "w") as f:
            f.write(FAKE_CLI)
        os.chmod(cli, os.stat(cli).st_mode | stat.S_IEXEC)
        with open(os.path.join(tmp, "runs.json"), "w") as f:
            json.dump(runs, f)
        env = dict(os.environ, TL_CLI=cli, FAKE_RUNS=os.path.join(tmp, "runs.json"))
        proc = subprocess.run([sys.executable, WATCH, *args, "--interval", "0"], cwd=cwd or tmp, env=env,
                              capture_output=True, text=True, timeout=60)
        result = [line for line in proc.stdout.splitlines() if line.startswith("RESULT ")][-1]
        return proc.returncode, result


def test():
    counter = {"id": "train_and_evaluate", "status": "RUNNING", "current": 16, "total": 128}
    quiet = {"id": "insights_analysis", "status": "RUNNING"}
    chained = {"status": "dispatched", "jobId": "e"}

    code, line = watch({"p": push(chained), "e": evaluate("FINISHED")}, "p")
    assert code == 0 and "status=FINISHED push=p eval=e" in line, line

    code, line = watch({"p": push(chained), "e": evaluate("TERMINATED", step=counter)}, "p")
    assert code == 1 and "stage=evaluate" in line and "progress=16/128" in line, line

    code, line = watch({"p": push(status="FAILED")}, "p")
    assert code == 1 and "stage=push" in line, line

    code, line = watch({"p": push({"status": "refused"})}, "p")
    assert code == 3 and "chained=refused" in line, line

    code, line = watch({"e": evaluate(minutes=31, step=counter)}, "e")
    assert code == 2 and "step=train_and_evaluate" in line and "stalled_min=31" in line, line

    code, line = watch({"e": evaluate(minutes=31, step=quiet)}, "e", "--once")
    assert code == 5 and "step=insights_analysis" in line, line

    code, line = watch({"e": evaluate(minutes=121, step=quiet)}, "e")
    assert code == 2, line

    code, line = watch({"e": evaluate("QUEUED", minutes=60)}, "e", "--once")
    assert code == 5 and "run_status=QUEUED" in line, line

    code, line = watch({"e": evaluate("SOMETHING_NEW", minutes=1)}, "e", "--once")
    assert code == 5, line

    code, line = watch({"e": "not json"}, "e")
    assert code == 4, line

    code, line = watch({"e": evaluate(extra={"schemaVersion": 2})}, "e")
    assert code == 4, line

    code, line = watch({"e": evaluate(extra={"updatedAt": None})}, "e")
    assert code == 4, line

    with tempfile.TemporaryDirectory() as repo:
        stuck = {"e": evaluate(minutes=40, step=counter)}
        code, line = watch(stuck, "e", cwd=repo)
        assert code == 2, line
        code, line = watch(stuck, "--resume", "--once", cwd=repo)
        assert code == 2, line
        code, line = watch(stuck, "--resume", "--keep-waiting", "--once", cwd=repo)
        assert code == 5, line
        code, line = watch(stuck, "--resume", "--once", cwd=repo)
        assert code == 5, line

    code, line = watch({}, "missing", "--once")
    assert code == 6, line

    print("ok")


if __name__ == "__main__":
    test()
