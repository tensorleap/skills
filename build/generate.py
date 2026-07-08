#!/usr/bin/env python3
"""Generate per-tool skill wrappers from the canonical `skill.md`.

One skill is authored once in `skills/<skill>/skill.md` (a *superset* of the
proven Claude `SKILL.md`: same body, extra frontmatter Claude ignores). This
script emits the tool-specific wrappers into `dist/`:

  claude   -> a self-contained Claude Code plugin (plugin.json + skills/<name>/
              SKILL.md + scripts/ + reference/). This output MUST be byte-for-byte
              identical to the pinned golden baseline (see build/golden/), which is
              the proven Claude skill. That invariant is what lets every other tool
              be generated *from* Claude without ever degrading it.
  cursor   -> a standalone `.mdc` rule file.
  agents   -> a marked section fragment to upsert into a shared `AGENTS.md`.
  copilot  -> a marked section fragment to upsert into `.github/copilot-instructions.md`.

The only per-tool-variable reference in the body is the script directory, written
in canonical form as the `{{scripts_dir}}` placeholder. Each adapter resolves it:
Claude -> skill-relative `scripts`; every other tool -> the installed shared path
from the canonical `scripts_dir` frontmatter key.

Usage:
  python build/generate.py            # (re)write dist/ in place
  python build/generate.py --check    # generate to a temp dir; fail if dist/ is
                                       # stale or the Claude output drifts from golden

Stdlib only.
"""

import filecmp
import json
import os
import re
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_SRC = os.path.join(REPO_ROOT, "skills", "tensorleap-integration")
CANONICAL = os.path.join(SKILL_SRC, "skill.md")
DIST = os.path.join(REPO_ROOT, "dist")
GOLDEN_CLAUDE = os.path.join(
    REPO_ROOT, "build", "golden", "tensorleap-integration", "claude", "SKILL.md"
)

SKILL_DIR_NAME = "tensorleap-integration"          # dist/<this>/<tool>/
SKILL_NAME = "tensorleap-integration-creation"      # the skill's own name

# Frontmatter keys the superset ADDS on top of the proven Claude SKILL.md.
# The Claude adapter drops exactly these, reproducing the baseline frontmatter.
SUPERSET_ONLY = ("version", "globs", "alwaysApply", "tools", "scripts_dir", "reference_dir")

MARKER_BEGIN = "<!-- BEGIN TENSORLEAP SKILL: %s -->"
MARKER_END = "<!-- END TENSORLEAP SKILL: %s -->"
MARKER_NOTE = "<!-- Managed by tensorleap/skills. Regenerate; do not edit by hand. -->"


# --------------------------------------------------------------------------- #
# Canonical parsing
# --------------------------------------------------------------------------- #

class Canonical:
    """Parsed `skill.md`: raw per-key frontmatter blocks + the body."""

    def __init__(self, text):
        if not text.startswith("---\n"):
            raise ValueError("skill.md must open with a '---' frontmatter fence")
        _, fm, body = text.split("---\n", 2)
        self.body = body
        self.order = []            # key order as authored
        self.raw = {}              # key -> exact source lines (verbatim, with newlines)
        self._parse_frontmatter(fm)

    def _parse_frontmatter(self, fm):
        key_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):")
        lines = fm.splitlines(keepends=True)
        i = 0
        while i < len(lines):
            m = key_re.match(lines[i])
            if not m:
                raise ValueError("unexpected frontmatter line: %r" % lines[i])
            key = m.group(1)
            block = [lines[i]]
            i += 1
            # Continuation lines (indented / block-scalar content) do not start a key.
            while i < len(lines) and not key_re.match(lines[i]):
                block.append(lines[i])
                i += 1
            self.order.append(key)
            self.raw[key] = "".join(block)

    # -- targeted value extraction (stdlib only; no YAML dependency) ---------- #

    def inline(self, key):
        """Value of a single-line `key: value` entry."""
        return self.raw[key].split(":", 1)[1].strip()

    def description_flat(self):
        """The folded `description:` block collapsed to one line."""
        cont = self.raw["description"].splitlines()[1:]  # drop the `description: >` line
        return " ".join(s.strip() for s in cont if s.strip())

    def globs(self):
        return re.findall(r'"([^"]+)"', self.raw["globs"])

    def scripts_dir(self):
        return self.inline("scripts_dir")

    def reference_dir(self):
        return self.inline("reference_dir")

    def tools(self):
        inner = self.inline("tools").strip("[]")
        return [t.strip() for t in inner.split(",") if t.strip()]


def resolve_body(canon, scripts_dir, reference_dir):
    return (canon.body
            .replace("{{scripts_dir}}", scripts_dir)
            .replace("{{reference_dir}}", reference_dir))


# --------------------------------------------------------------------------- #
# Per-tool adapters — each writes its wrapper under out_dir/<SKILL_DIR_NAME>/<tool>/
# --------------------------------------------------------------------------- #

def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _copytree(src, dst):
    shutil.copytree(
        src, dst, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def emit_claude(canon, tool_root):
    """Self-contained Claude Code plugin. MUST match the golden baseline."""
    # Reproduce the baseline frontmatter: keep every key except the superset-only
    # additions, emitting each key's source block verbatim (no YAML round-trip).
    kept = [k for k in canon.order if k not in SUPERSET_ONLY]
    frontmatter = "---\n" + "".join(canon.raw[k] for k in kept) + "---\n"
    skill_md = frontmatter + resolve_body(canon, "scripts", "reference")

    skill_root = os.path.join(tool_root, "skills", SKILL_NAME)
    _write(os.path.join(skill_root, "SKILL.md"), skill_md)
    _copytree(os.path.join(SKILL_SRC, "scripts"), os.path.join(skill_root, "scripts"))
    _copytree(os.path.join(SKILL_SRC, "reference"), os.path.join(skill_root, "reference"))

    plugin_json = {
        "name": SKILL_NAME,
        "description": canon.description_flat(),
        "version": canon.inline("version"),
        "author": {"name": "Tensorleap", "email": "support@tensorleap.ai"},
        "homepage": "https://github.com/tensorleap/skills",
        "license": "Apache-2.0",
    }
    _write(
        os.path.join(tool_root, ".claude-plugin", "plugin.json"),
        json.dumps(plugin_json, indent=2, ensure_ascii=False) + "\n",
    )


def emit_cursor(canon, tool_root):
    """Standalone Cursor `.mdc` rule."""
    fm = (
        "---\n"
        "description: %s\n" % canon.description_flat()
        + "globs: %s\n" % ",".join(canon.globs())
        + "alwaysApply: %s\n" % canon.inline("alwaysApply")
        + "---\n"
    )
    body = resolve_body(canon, canon.scripts_dir(), canon.reference_dir())
    _write(os.path.join(tool_root, "%s.mdc" % SKILL_NAME), fm + body)


def _marked_section(canon):
    body = resolve_body(canon, canon.scripts_dir(), canon.reference_dir())
    return "%s\n%s\n%s\n%s\n" % (
        MARKER_BEGIN % SKILL_NAME,
        MARKER_NOTE,
        body.strip("\n"),
        MARKER_END % SKILL_NAME,
    )


def emit_agents(canon, tool_root):
    """Marked fragment for install.sh to upsert into a shared AGENTS.md."""
    _write(os.path.join(tool_root, "AGENTS.section.md"), _marked_section(canon))


def emit_copilot(canon, tool_root):
    """Marked fragment for install.sh to upsert into .github/copilot-instructions.md."""
    _write(
        os.path.join(tool_root, "copilot-instructions.section.md"),
        _marked_section(canon),
    )


ADAPTERS = {
    "claude": emit_claude,
    "cursor": emit_cursor,
    "agents": emit_agents,
    "copilot": emit_copilot,
}


# --------------------------------------------------------------------------- #
# Build + checks
# --------------------------------------------------------------------------- #

def build_all(out_dir):
    with open(CANONICAL, encoding="utf-8") as f:
        canon = Canonical(f.read())
    for tool in canon.tools():
        if tool not in ADAPTERS:
            raise ValueError("unknown tool in canonical `tools`: %r" % tool)
        tool_root = os.path.join(out_dir, SKILL_DIR_NAME, tool)
        ADAPTERS[tool](canon, tool_root)
    # Executable bit on shipped shell scripts (content is what --check compares).
    for name in os.listdir(os.path.join(SKILL_SRC, "scripts")):
        if name.endswith(".sh"):
            dst = os.path.join(
                out_dir, SKILL_DIR_NAME, "claude", "skills", SKILL_NAME, "scripts", name
            )
            if os.path.exists(dst):
                os.chmod(dst, 0o755)
    return canon


def assert_golden(out_dir):
    generated = os.path.join(
        out_dir, SKILL_DIR_NAME, "claude", "skills", SKILL_NAME, "SKILL.md"
    )
    with open(generated, encoding="utf-8") as f:
        got = f.read()
    with open(GOLDEN_CLAUDE, encoding="utf-8") as f:
        want = f.read()
    if got != want:
        raise SystemExit(
            "GOLDEN MISMATCH: generated Claude SKILL.md differs from the proven "
            "baseline (build/golden/.../claude/SKILL.md).\n"
            "The Claude skill is the reference and must not be degraded. If this "
            "change to the Claude experience is intentional, update the golden "
            "baseline in the same commit."
        )


def _diff_trees(a, b):
    """Return a list of human-readable differences between two dir trees."""
    diffs = []

    def walk(cmp, rel):
        for name in cmp.left_only:
            diffs.append("only in generated: %s" % os.path.join(rel, name))
        for name in cmp.right_only:
            diffs.append("only in committed dist/: %s" % os.path.join(rel, name))
        for name in cmp.diff_files:
            diffs.append("content differs: %s" % os.path.join(rel, name))
        for name, sub in cmp.subdirs.items():
            walk(sub, os.path.join(rel, name))

    walk(filecmp.dircmp(a, b), "")
    return diffs


def check():
    tmp = tempfile.mkdtemp(prefix="tl-skills-gen-")
    try:
        build_all(tmp)
        assert_golden(tmp)
        gen = os.path.join(tmp, SKILL_DIR_NAME)
        committed = os.path.join(DIST, SKILL_DIR_NAME)
        if not os.path.isdir(committed):
            raise SystemExit("dist/ is missing; run: python build/generate.py")
        diffs = _diff_trees(gen, committed)
        if diffs:
            raise SystemExit(
                "dist/ is STALE — regenerate with `python build/generate.py`:\n  "
                + "\n  ".join(diffs)
            )
        print("OK: dist/ is fresh and the Claude wrapper matches golden.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    if "--check" in argv[1:]:
        check()
        return 0
    # In-place build. Clear the skill's dist subtree so removals propagate.
    target = os.path.join(DIST, SKILL_DIR_NAME)
    if os.path.isdir(target):
        shutil.rmtree(target)
    build_all(DIST)
    assert_golden(DIST)
    print("Wrote wrappers to %s" % os.path.relpath(target, REPO_ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
