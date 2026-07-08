#!/usr/bin/env python3
"""Validate the Claude marketplace + plugin manifests.

Checks that `.claude-plugin/marketplace.json` is well-formed and that every
local-path plugin it lists actually resolves to a plugin with a readable
`.claude-plugin/plugin.json` and a `skills/<name>/SKILL.md`.

Usage:  python build/check_manifests.py
Exit:   0 valid, 1 otherwise.
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKETPLACE = os.path.join(REPO_ROOT, ".claude-plugin", "marketplace.json")


def _load(path, errors):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        errors.append("missing: %s" % os.path.relpath(path, REPO_ROOT))
    except json.JSONDecodeError as exc:
        errors.append("invalid JSON in %s: %s" % (os.path.relpath(path, REPO_ROOT), exc))
    return None


def main():
    errors = []
    mkt = _load(MARKETPLACE, errors)
    if mkt is None:
        return _report(errors)

    if not mkt.get("name"):
        errors.append("marketplace.json: missing required 'name'")
    if not isinstance(mkt.get("owner"), dict) or not mkt["owner"].get("name"):
        errors.append("marketplace.json: missing required 'owner.name'")
    plugins = mkt.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        errors.append("marketplace.json: 'plugins' must be a non-empty array")
        return _report(errors)

    for i, entry in enumerate(plugins):
        where = "marketplace.json plugins[%d]" % i
        name = entry.get("name")
        source = entry.get("source")
        if not name:
            errors.append("%s: missing 'name'" % where)
        if not source:
            errors.append("%s: missing 'source'" % where)
            continue
        # Only local-path sources are resolvable here.
        if not isinstance(source, str) or not source.startswith("."):
            continue
        plugin_root = os.path.normpath(os.path.join(REPO_ROOT, source))
        pj_path = os.path.join(plugin_root, ".claude-plugin", "plugin.json")
        pj = _load(pj_path, errors)
        if pj is None:
            continue
        if not pj.get("name"):
            errors.append("%s -> plugin.json: missing required 'name'" % where)
        if name and pj.get("name") and name != pj["name"]:
            errors.append("%s: name '%s' != plugin.json name '%s'"
                          % (where, name, pj["name"]))
        skills_dir = os.path.join(plugin_root, "skills")
        if not os.path.isdir(skills_dir):
            errors.append("%s -> no skills/ dir at %s"
                          % (where, os.path.relpath(skills_dir, REPO_ROOT)))
            continue
        found = [
            d for d in os.listdir(skills_dir)
            if os.path.isfile(os.path.join(skills_dir, d, "SKILL.md"))
        ]
        if not found:
            errors.append("%s -> no skills/<name>/SKILL.md under %s"
                          % (where, os.path.relpath(skills_dir, REPO_ROOT)))

    return _report(errors)


def _report(errors):
    if errors:
        print("MANIFEST CHECK FAILED:", file=sys.stderr)
        for e in errors:
            print("  " + e, file=sys.stderr)
        return 1
    print("Manifests valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
