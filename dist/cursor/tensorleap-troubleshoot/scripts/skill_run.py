#!/usr/bin/env python3
"""skill_run: one Mixpanel event per stage of a Tensorleap skill run.

Shared by every skill (build/generate.py copies it into each skill's scripts/
folder). Stdlib only, Python 3.8+. Never raises and never prints: an
unreachable Mixpanel must not fail the skill. TL_DISABLE_ANALYTICS=1 sends
nothing (code-loader honors the same variable).

CLI, for shell scripts and SKILL.md steps:
    skill_run.py SKILL started   [--exit-code N]
    skill_run.py SKILL iteration             # counts one fix-loop pass, sends nothing
    skill_run.py SKILL finished  [--outcome WORD] [--exit-code N]
    skill_run.py SKILL failed    [--outcome WORD] [--exit-code N]

Module:
    from skill_run import skill_run
    skill_run("tensorleap-analysis", "finished", exit_code=0)

Event `skill_run`: service=skills, tracking_version, skill_name, skill_version
(Copilot/Cursor/Devin installs ship a VERSION file), stage, outcome, exit_code,
duration_s (since `started`), fix_loop_iterations (passes counted since
`started`), mcp_assistant (when the environment says), $device_id and user_id
from ~/.tensorleap (the leap CLI's identity). A non-zero exit_code turns
`finished` into `failed`; `started` keeps its stage and carries the code.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid

TOKEN = "0c1710c9656bbfb1056bb46093e23ca1"
ENDPOINT = "https://api.mixpanel.com/track"
TRACKING_VERSION = 1
TL_DIR = os.path.expanduser("~/.tensorleap")


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _write(path, text):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
    except OSError:
        pass


def _device_id():
    path = os.path.join(TL_DIR, "device_id")
    did = _read(path)
    if not did:
        did = uuid.uuid4().hex
        _write(path, did)
    return did


def _assistant():
    if os.environ.get("CLAUDECODE"):
        return "claude-code"
    if os.environ.get("CURSOR_TRACE_ID"):
        return "cursor"
    return None


def skill_run(skill, stage, outcome=None, exit_code=None, **props):
    """Record one stage of a skill run. Never raises."""
    try:
        _skill_run(skill, stage, outcome, exit_code, props)
    except Exception:
        pass


def _skill_run(skill, stage, outcome, exit_code, props):
    if os.environ.get("TL_DISABLE_ANALYTICS"):
        return
    # ponytail: one state file per skill, not per project; two concurrent runs
    # of the same skill on one machine share a start time and a counter.
    state_path = os.path.join(TL_DIR, "skill_run", skill + ".json")
    try:
        state = json.loads(_read(state_path) or "{}")
    except ValueError:
        state = {}
    if stage == "started":
        state = {"started": time.time(), "iterations": 0}
        _write(state_path, json.dumps(state))
    elif stage == "iteration":
        state["iterations"] = state.get("iterations", 0) + 1
        _write(state_path, json.dumps(state))
        return
    else:
        if exit_code and stage == "finished":
            stage = "failed"
        if state.get("started"):
            props["duration_s"] = round(time.time() - state["started"], 1)
        if state.get("iterations"):
            props["fix_loop_iterations"] = state["iterations"]
        props["outcome"] = outcome or ("ok" if stage == "finished" else "error")

    user_id = _read(os.path.join(TL_DIR, "user_id"))
    device_id = _device_id()
    p = {
        "token": TOKEN,
        "time": int(time.time()),
        "distinct_id": user_id or device_id,
        "$device_id": device_id,
        "$user_id": user_id,
        "user_id": user_id,
        "service": "skills",
        "tracking_version": TRACKING_VERSION,
        "skill_name": skill,
        "stage": stage,
        "skill_version": _read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "VERSION")),
        "mcp_assistant": _assistant(),
        "exit_code": exit_code,
    }
    p.update(props)
    p = {k: v for k, v in p.items() if v is not None}
    body = urllib.parse.urlencode({"data": json.dumps({"event": "skill_run", "properties": p})}).encode()
    urllib.request.urlopen(urllib.request.Request(ENDPOINT, data=body), timeout=3).read()


def main(argv):
    ap = argparse.ArgumentParser(prog="skill_run.py", description=__doc__.splitlines()[0])
    ap.add_argument("skill")
    ap.add_argument("stage", choices=["started", "iteration", "finished", "failed"])
    ap.add_argument("--outcome", help="short snake_case word, e.g. eval_finished")
    ap.add_argument("--exit-code", type=int)
    a = ap.parse_args(argv)
    skill_run(a.skill, a.stage, outcome=a.outcome, exit_code=a.exit_code)


if __name__ == "__main__":
    main(sys.argv[1:])
