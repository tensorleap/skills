#!/usr/bin/env python3
"""Fence the agent into a single blind fixture.

Writes a `.claude/settings.json` deny-list into the fixture's `pre` dir so the
agent cannot read the solution or peek at other fixtures/sessions — enforced
even under --dangerously-skip-permissions. This is the *filesystem* half of "no
peeking"; prepare.sh/verify.sh are the *git* half. It does NOT block the web.

Blocks Read/Grep/Glob/Edit on:
  - this fixture's sibling `post/` (the reference solution)
  - every OTHER fixture dir under .fixtures/
  - all Claude project memory/transcripts (~/.claude/projects)
  - any extra repo roots named in EVAL_BLOCK_ROOTS (colon-separated: block their children)

Leaves reachable: this fixture's own `pre/`, the data volume, the poetry .venv,
the leap binary, and the web.

Usage:  python3 gen_deny.py <path-to-fixture>/pre
        EVAL_BLOCK_ROOTS=~/repos:~/work python3 gen_deny.py .fixtures/<id>/pre
"""

import json
import os
import sys

TOOLS = ("Read", "Grep", "Glob", "Edit")


def rules(path):
    p = os.path.abspath(path).lstrip("/")
    return [f"{t}(//{p}/**)" for t in TOOLS]


def build_deny(pre_dir):
    pre_dir = os.path.abspath(pre_dir)
    fixture_dir = os.path.dirname(pre_dir)          # .fixtures/<id>
    fixtures_root = os.path.dirname(fixture_dir)    # .fixtures
    deny = []

    # This fixture's own solution + any non-`pre` sibling (post/, ...).
    for name in sorted(os.listdir(fixture_dir)):
        child = os.path.join(fixture_dir, name)
        if os.path.isdir(child) and os.path.abspath(child) != pre_dir:
            deny += rules(child)

    # Every other fixture dir.
    if os.path.isdir(fixtures_root):
        for name in sorted(os.listdir(fixtures_root)):
            child = os.path.join(fixtures_root, name)
            if os.path.isdir(child) and os.path.abspath(child) != fixture_dir:
                deny += rules(child)

    # Extra repo roots: block their children (siblings of this checkout elsewhere).
    for root in filter(None, os.environ.get("EVAL_BLOCK_ROOTS", "").split(":")):
        root = os.path.expanduser(root)
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            child = os.path.join(root, name)
            if os.path.isdir(child) and os.path.abspath(child) != pre_dir:
                deny += rules(child)

    # All sessions' memory/transcripts.
    deny += rules(os.path.expanduser("~/.claude/projects"))
    return deny


def write_settings(pre_dir, deny):
    cfg_dir = os.path.join(pre_dir, ".claude")
    os.makedirs(cfg_dir, exist_ok=True)
    path = os.path.join(cfg_dir, "settings.json")
    cfg = {}
    if os.path.exists(path):
        try:
            cfg = json.load(open(path))
        except Exception:
            cfg = {}
    cfg.setdefault("permissions", {})["deny"] = deny
    json.dump(cfg, open(path, "w"), indent=2)
    return path


def _selfcheck():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        # .fixtures/{a,b}/{pre,post}
        for fx in ("a", "b"):
            for v in ("pre", "post"):
                os.makedirs(os.path.join(tmp, fx, v))
        pre = os.path.join(tmp, "a", "pre")
        deny = build_deny(pre)
        joined = "\n".join(deny)
        assert "/a/post/**" in joined, "must block own solution (post)"
        assert "/b/**" in joined, "must block other fixtures"
        assert "/a/pre/**" not in joined, "must NOT block own working dir (pre)"
        assert ".claude/projects/**" in joined, "must block session memory"
        print("selfcheck ok")


def main(argv):
    if len(argv) == 2 and argv[1] == "--selfcheck":
        _selfcheck()
        return 0
    if len(argv) != 2:
        print(__doc__)
        return 2
    pre_dir = argv[1]
    if not os.path.isdir(pre_dir):
        print(f"error: not a directory: {pre_dir}", file=sys.stderr)
        return 1
    deny = build_deny(pre_dir)
    path = write_settings(pre_dir, deny)
    print(f"wrote {path} ({len(deny)} deny rules)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
