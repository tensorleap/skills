#!/usr/bin/env python3
"""Print each visible source of the integration skill, one per line.

A run must see exactly ONE source — a local ~/.claude/skills copy shadowing the
installed plugin (or a --plugin-dir on top of either) silently tests the wrong
skill. Sources checked on the filesystem (deterministic), not by parsing a
skill listing. The installed plugin counts only when it is installed AND
enabled: `enabledPlugins` in settings can switch it off, which is the
non-destructive way to hand a run over to --plugin-dir.

usage: skill_sources.py [--plugin-dir DIR] [extra settings.json paths ...]

Extra settings paths are MORE specific than the user settings (project wins).
Shared by run.sh (hard gate per fixture) and check.sh (preflight table).
"""
import json
import os
import sys

SKILL_NAME = "tensorleap-integration-creation"
PLUGIN_PKG = "integration@tensorleap"


def _load(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}


def sources(plugin_dir="", extra_settings=()):
    home = os.path.expanduser("~")
    out = []
    if plugin_dir:
        out.append(f"--plugin-dir {plugin_dir}")
    local = os.path.join(home, ".claude", "skills", SKILL_NAME)
    if os.path.lexists(local):
        target = os.path.realpath(local) if os.path.islink(local) else ""
        out.append(f"local copy {local}" + (f" -> {target}" if target else ""))
    installed = _load(os.path.join(home, ".claude", "plugins", "installed_plugins.json"))
    if PLUGIN_PKG in (installed.get("plugins") or {}):
        settings = [os.path.join(home, ".claude", "settings.json")] + list(extra_settings)
        enabled = True
        for path in reversed(settings):        # most specific first
            val = (_load(path).get("enabledPlugins") or {}).get(PLUGIN_PKG)
            if val is False:
                enabled = False
                break
            if val is True:
                break
        if enabled:
            out.append(f"installed plugin {PLUGIN_PKG}")
    return out


def main(argv):
    plugin_dir = ""
    args = argv[1:]
    if args and args[0] == "--plugin-dir":
        if len(args) < 2:
            print("--plugin-dir requires a value", file=sys.stderr)
            return 2
        plugin_dir, args = args[1], args[2:]
    sys.stdout.write("".join(s + "\n" for s in sources(plugin_dir, args)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
