#!/usr/bin/env python3
"""Fail if internal-only references leak into this public repo.

Scans tracked text files for tokens that should never ship publicly: the
internal source repo/paths, review notes, and absolute home paths. Runs in CI.

It scans the *published* file set — the git-tracked files — so anything in
`.gitignore` (e.g. the internal PLAN.md) is correctly out of scope. Without a git
checkout it falls back to a working-tree walk with the same exclusions.

This file excludes itself from the scan so its own pattern list can't trip it.

Usage:  python build/leak_scan.py
Exit:   0 clean, 1 if any pattern is found.
"""

import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF = os.path.abspath(__file__)

# Case-insensitive substrings / regexes that must not appear in published files.
PATTERNS = [
    r"leaphub",
    r"tensorleap-claude",
    r"skill-tests",
    r"RUN_REVIEW",
    r"/Users/",              # absolute macOS home paths
    r"/home/[a-z]",          # absolute Linux home paths
]

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}
# Not published even when present in the working tree (mirrors .gitignore).
SKIP_FILES = {"PLAN.md"}
# Only scan text; skip anything that isn't obviously source/doc/config.
TEXT_EXT = {".md", ".py", ".sh", ".json", ".yml", ".yaml", ".txt", ".mdc", ".cfg", ".toml", ""}


def _git_tracked():
    """The published set: git-tracked files (respects .gitignore). None if no repo."""
    try:
        out = subprocess.run(
            ["git", "-C", REPO_ROOT, "ls-files", "-z"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    files = [f for f in out.stdout.split("\0") if f]
    return [os.path.join(REPO_ROOT, f) for f in files] or None


def _walk_tree():
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name not in SKIP_FILES:
                yield os.path.join(root, name)


def iter_files():
    for path in (_git_tracked() or _walk_tree()):
        if os.path.abspath(path) == SELF:
            continue
        if os.path.splitext(path)[1].lower() not in TEXT_EXT:
            continue
        yield path


def main():
    regexes = [re.compile(p, re.IGNORECASE) for p in PATTERNS]
    hits = []
    for path in iter_files():
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(lines, 1):
            for rx in regexes:
                if rx.search(line):
                    rel = os.path.relpath(path, REPO_ROOT)
                    hits.append("%s:%d: matches /%s/  ->  %s"
                                % (rel, lineno, rx.pattern, line.strip()))
    if hits:
        print("LEAK SCAN FAILED — internal references found:", file=sys.stderr)
        for h in hits:
            print("  " + h, file=sys.stderr)
        return 1
    print("Leak scan clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
