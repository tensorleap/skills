#!/usr/bin/env python3
"""Fail when a skill's shipped files change without a version bump.

self_update.sh delivers a change only when the version goes up: skill folders
compare the skill.md `version:`, Claude Code plugins the plugin's version in
plugins.json. A change that bumps neither (or only one) never reaches those users.

Shipped files are skill.md, scripts/ and reference/ of a skill, plus
skills/_shared/scripts/ (copied into every skill).

  python build/check_versions.py <base ref>    # e.g. origin/main
"""
import json
import os
import re
import subprocess
import sys

SHIPPED = re.compile(r"^skills/([^/]+)/(skill\.md$|scripts/|reference/)")


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def at_base(base, path):
    try:
        return git("show", "%s:%s" % (base, path))
    except subprocess.CalledProcessError:
        return None


def skill_version(text):
    m = re.search(r"^version:\s*(\S+)", text or "", re.M)
    return m.group(1) if m else None


def problems(base):
    changed = git("diff", "--name-only", "%s...HEAD" % base, "--", "skills/").split()
    with open("plugins.json", encoding="utf-8") as f:
        plugins = json.load(f)["plugins"]
    base_plugins = {p["name"]: p["version"]
                    for p in json.loads(at_base(base, "plugins.json") or '{"plugins": []}')["plugins"]}

    names = {m.group(1) for m in map(SHIPPED.match, changed) if m} - {"_shared"}
    if any(p.startswith("skills/_shared/scripts/") for p in changed):
        names |= {s for p in plugins for s in p["skills"]}

    out = []
    for name in sorted(names):
        path = "skills/%s/skill.md" % name
        old = skill_version(at_base(base, path))
        if old is None or not os.path.isfile(path):
            continue  # added or removed skill
        with open(path, encoding="utf-8") as f:
            if skill_version(f.read()) == old:
                out.append("%s changed but its skill.md version is still %s" % (name, old))
        for p in plugins:
            if name in p["skills"] and base_plugins.get(p["name"]) == p["version"]:
                out.append("%s changed but plugin %r in plugins.json is still %s"
                           % (name, p["name"], p["version"]))
    return out


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    found = problems(argv[1])
    for line in found:
        print("ERROR: " + line, file=sys.stderr)
    if found:
        print("Bump both versions so self_update.sh delivers the change.", file=sys.stderr)
        return 1
    print("OK: every changed skill bumps its skill.md and plugin versions.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
