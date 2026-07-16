#!/usr/bin/env python3
"""Generate per-tool wrappers from canonical skills + the plugin catalog.

MODEL
  skill   — the atom. One `skills/<name>/skill.md` (a *superset* of the proven
            Claude SKILL.md: same body, extra frontmatter Claude ignores) plus
            shared `scripts/` and `reference/`. The dir name IS the skill name.
  plugin  — a Claude-Code-only grouping of skills, declared in `plugins.json`.
            It carries the plugin-level metadata (name/description/version) and
            the list of skills it ships. Other tools have no such concept.

OUTPUTS (into dist/, plus a generated .claude-plugin/marketplace.json)
  claude   -> dist/claude/<plugin>/            a self-contained Claude plugin:
              .claude-plugin/plugin.json + skills/<skill>/SKILL.md + scripts + reference.
              Skills are GROUPED under their plugin.
  cursor   -> dist/cursor/<skill>/             self-contained Cursor Agent Skill:
              SKILL.md (minimal frontmatter) + scripts + reference. Discovered
              natively by Cursor from .cursor/skills/ (project) or
              ~/.cursor/skills/ (global).
  agents   -> dist/agents/<skill>.section.md   flat marked-section fragment per skill.
  copilot  -> dist/copilot/<skill>/            self-contained Copilot Agent Skill:
              SKILL.md (minimal frontmatter) + scripts + reference. Discovered
              natively by Copilot from .github/skills/ (project) or
              ~/.copilot/skills/ (global).

  Non-Claude outputs FLATTEN: the plugin grouping is invisible to them.

INVARIANT
  Each generated Claude SKILL.md must be byte-for-byte identical to the pinned
  golden baseline at build/golden/<skill>/SKILL.md (the proven Claude skill).
  That is what lets every other tool be generated *from* Claude without ever
  degrading it. `--check` enforces it, plus dist/ + marketplace.json freshness.

The two per-tool-variable paths in the body are the shared scripts/ and
reference/ dirs, written canonically as {{scripts_dir}} / {{reference_dir}}.
Claude, Copilot and Cursor resolve them skill-relative (`scripts` /
`reference`, self-contained); only the AGENTS output resolves them to the
installed shared paths from frontmatter.

Usage:
  python build/generate.py            # (re)write dist/ + .claude-plugin/marketplace.json
  python build/generate.py --check    # generate to a temp dir; fail on golden drift or staleness

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
SKILLS_DIR = os.path.join(REPO_ROOT, "skills")
GOLDEN_DIR = os.path.join(REPO_ROOT, "build", "golden")
PLUGINS_JSON = os.path.join(REPO_ROOT, "plugins.json")

# Paths (relative to a build root) that this script owns and regenerates.
DIST_REL = "dist"
MARKETPLACE_REL = os.path.join(".claude-plugin", "marketplace.json")

# Frontmatter keys the superset ADDS on top of the proven Claude SKILL.md.
# The Claude adapter drops exactly these, reproducing the baseline frontmatter.
SUPERSET_ONLY = ("version", "globs", "alwaysApply", "tools", "scripts_dir", "reference_dir")

MARKER_BEGIN = "<!-- BEGIN TENSORLEAP SKILL: %s -->"
MARKER_END = "<!-- END TENSORLEAP SKILL: %s -->"


def stamp(name, version):
    return ("<!-- Tensorleap skill '%s' v%s — generated from skills/%s/skill.md; "
            "do not edit here. -->" % (name, version, name))


# --------------------------------------------------------------------------- #
# Canonical skill.md parsing
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
            while i < len(lines) and not key_re.match(lines[i]):
                block.append(lines[i])
                i += 1
            self.order.append(key)
            self.raw[key] = "".join(block)

    def inline(self, key):
        return self.raw[key].split(":", 1)[1].strip()

    def description_flat(self):
        cont = self.raw["description"].splitlines()[1:]  # drop the `description: >` line
        return " ".join(s.strip() for s in cont if s.strip())

    def globs(self):
        return re.findall(r'"([^"]+)"', self.raw["globs"])

    def scripts_dir(self):
        return self.inline("scripts_dir")

    def reference_dir(self):
        return self.inline("reference_dir")

    def version(self):
        return self.inline("version")

    def tools(self):
        inner = self.inline("tools").strip("[]")
        return [t.strip() for t in inner.split(",") if t.strip()]


def resolve_body(canon, scripts_dir, reference_dir):
    return (canon.body
            .replace("{{scripts_dir}}", scripts_dir)
            .replace("{{reference_dir}}", reference_dir))


def render_claude_skill_md(canon):
    """The proven Claude SKILL.md: baseline frontmatter + skill-relative paths."""
    kept = [k for k in canon.order if k not in SUPERSET_ONLY]
    frontmatter = "---\n" + "".join(canon.raw[k] for k in kept) + "---\n"
    return frontmatter + resolve_body(canon, "scripts", "reference")


def render_native_skill_md(canon):
    """Copilot / Cursor Agent Skill: only the documented keys (name,
    description) — extra-key tolerance is undocumented — plus the same
    self-contained body as Claude (skill-relative scripts/ and reference/)."""
    frontmatter = ("---\n"
                   "name: %s\n" % canon.inline("name")
                   + "description: %s\n" % canon.description_flat()
                   + "---\n")
    return frontmatter + resolve_body(canon, "scripts", "reference")


# --------------------------------------------------------------------------- #
# Loading skills + the plugin catalog
# --------------------------------------------------------------------------- #

def load_skills():
    skills = {}
    for name in sorted(os.listdir(SKILLS_DIR)):
        skill_md = os.path.join(SKILLS_DIR, name, "skill.md")
        if not os.path.isfile(skill_md):
            continue
        with open(skill_md, encoding="utf-8") as f:
            canon = Canonical(f.read())
        declared = canon.inline("name")
        if declared != name:
            raise SystemExit(
                "skill dir %r does not match name %r in its skill.md "
                "(the dir name is the skill's identity)" % (name, declared))
        skills[name] = canon
    if not skills:
        raise SystemExit("no skills found under skills/")
    return skills


def load_catalog():
    with open(PLUGINS_JSON, encoding="utf-8") as f:
        cat = json.load(f)
    for key in ("name", "owner", "homepage", "license", "plugins"):
        if key not in cat:
            raise SystemExit("plugins.json: missing required top-level '%s'" % key)
    return cat


def validate(catalog, skills):
    """Check catalog<->skills consistency; return warnings (non-fatal)."""
    warnings = []
    referenced = set()
    for p in catalog["plugins"]:
        for key in ("name", "description", "version", "skills"):
            if key not in p:
                raise SystemExit("plugins.json plugin %r: missing '%s'"
                                 % (p.get("name", "?"), key))
        if not p["skills"]:
            raise SystemExit("plugins.json plugin %r: empty 'skills'" % p["name"])
        for s in p["skills"]:
            if s not in skills:
                raise SystemExit("plugins.json plugin %r references unknown skill %r"
                                 % (p["name"], s))
            referenced.add(s)
            if "claude" not in skills[s].tools():
                warnings.append("skill %r is in plugin %r but omits 'claude' from "
                                "tools; it will NOT ship to Claude" % (s, p["name"]))
    for s in skills:
        if s not in referenced:
            warnings.append("skill %r is in no plugin; it won't reach the Claude "
                            "marketplace (still generated for other tools)" % s)
    return warnings


# --------------------------------------------------------------------------- #
# Emit
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


def emit_claude(catalog, skills, root):
    owner = catalog["owner"]
    for p in catalog["plugins"]:
        plugin_dir = os.path.join(root, DIST_REL, "claude", p["name"])
        plugin_json = {
            "name": p["name"],
            "description": p["description"],
            "version": p["version"],
            "author": {"name": owner["name"], "email": owner.get("email", "")},
            "homepage": catalog["homepage"],
            "license": catalog["license"],
        }
        _write(os.path.join(plugin_dir, ".claude-plugin", "plugin.json"),
               json.dumps(plugin_json, indent=2, ensure_ascii=False) + "\n")
        for skill_name in p["skills"]:
            canon = skills[skill_name]
            if "claude" not in canon.tools():
                continue
            skill_out = os.path.join(plugin_dir, "skills", skill_name)
            _write(os.path.join(skill_out, "SKILL.md"), render_claude_skill_md(canon))
            src = os.path.join(SKILLS_DIR, skill_name)
            _copytree(os.path.join(src, "scripts"), os.path.join(skill_out, "scripts"))
            _copytree(os.path.join(src, "reference"), os.path.join(skill_out, "reference"))
            for name in os.listdir(os.path.join(skill_out, "scripts")):
                if name.endswith(".sh"):
                    os.chmod(os.path.join(skill_out, "scripts", name), 0o755)


def emit_skill_folders(skills, root, tool):
    """Copilot / Cursor: one self-contained Agent Skill folder per skill
    (native discovery). The VERSION file (enables the self-update check in
    preflight.sh) ships with both folder tools but NEVER with Claude — Claude
    updates come through the plugin marketplace and its copy stays inert."""
    for name, canon in skills.items():
        if tool not in canon.tools():
            continue
        skill_out = os.path.join(root, DIST_REL, tool, name)
        _write(os.path.join(skill_out, "SKILL.md"), render_native_skill_md(canon))
        _write(os.path.join(skill_out, "VERSION"), canon.version() + "\n")
        src = os.path.join(SKILLS_DIR, name)
        _copytree(os.path.join(src, "scripts"), os.path.join(skill_out, "scripts"))
        _copytree(os.path.join(src, "reference"), os.path.join(skill_out, "reference"))
        for fname in os.listdir(os.path.join(skill_out, "scripts")):
            if fname.endswith(".sh"):
                os.chmod(os.path.join(skill_out, "scripts", fname), 0o755)


def emit_flat(skills, root):
    """AGENTS: one flat marked-section fragment per skill (no plugin grouping)."""
    for name, canon in skills.items():
        tools = canon.tools()
        sd, rd = canon.scripts_dir(), canon.reference_dir()
        head = stamp(name, canon.version())
        if "agents" in tools:
            section = "%s\n%s\n%s\n%s\n" % (
                MARKER_BEGIN % name, head,
                resolve_body(canon, sd, rd).strip("\n"),
                MARKER_END % name,
            )
            _write(os.path.join(root, DIST_REL, "agents", "%s.section.md" % name), section)


def render_marketplace(catalog):
    obj = {
        "name": catalog["name"],
        "owner": catalog["owner"],
        "plugins": [
            {
                "name": p["name"],
                "source": "./%s/claude/%s" % (DIST_REL, p["name"]),
                "description": p["description"],
                "version": p["version"],
                "author": {"name": catalog["owner"]["name"]},
                "homepage": catalog["homepage"],
                "license": catalog["license"],
            }
            for p in catalog["plugins"]
        ],
    }
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def build_all(root):
    catalog = load_catalog()
    skills = load_skills()
    warnings = validate(catalog, skills)
    emit_claude(catalog, skills, root)
    emit_flat(skills, root)
    emit_skill_folders(skills, root, "copilot")
    emit_skill_folders(skills, root, "cursor")
    _write(os.path.join(root, MARKETPLACE_REL), render_marketplace(catalog))
    return catalog, skills, warnings


# --------------------------------------------------------------------------- #
# Golden + freshness checks
# --------------------------------------------------------------------------- #

def assert_golden(root, catalog):
    """Every generated Claude SKILL.md must equal its pinned golden baseline."""
    # Map skill -> the plugin dir it was emitted into (first match is enough).
    for p in catalog["plugins"]:
        for skill_name in p["skills"]:
            golden = os.path.join(GOLDEN_DIR, skill_name, "SKILL.md")
            if not os.path.isfile(golden):
                raise SystemExit("no golden baseline for skill %r at %s"
                                 % (skill_name, os.path.relpath(golden, REPO_ROOT)))
            generated = os.path.join(root, DIST_REL, "claude", p["name"],
                                     "skills", skill_name, "SKILL.md")
            with open(generated, encoding="utf-8") as f:
                got = f.read()
            with open(golden, encoding="utf-8") as f:
                want = f.read()
            if got != want:
                raise SystemExit(
                    "GOLDEN MISMATCH for skill %r: generated Claude SKILL.md differs "
                    "from build/golden/%s/SKILL.md.\nThe Claude skill is the reference "
                    "and must not be degraded. If this change is intentional, update the "
                    "golden baseline in the same commit." % (skill_name, skill_name))


def _diff_trees(a, b):
    diffs = []

    def walk(cmp, rel):
        for name in cmp.left_only:
            diffs.append("only in generated: %s" % os.path.join(rel, name))
        for name in cmp.right_only:
            diffs.append("only in committed: %s" % os.path.join(rel, name))
        for name in cmp.diff_files:
            diffs.append("content differs: %s" % os.path.join(rel, name))
        for name, sub in cmp.subdirs.items():
            walk(sub, os.path.join(rel, name))

    walk(filecmp.dircmp(a, b), "")
    return diffs


def check():
    tmp = tempfile.mkdtemp(prefix="tl-skills-gen-")
    try:
        catalog, _, warnings = build_all(tmp)
        for w in warnings:
            print("warning: " + w, file=sys.stderr)
        assert_golden(tmp, catalog)

        committed_dist = os.path.join(REPO_ROOT, DIST_REL)
        if not os.path.isdir(committed_dist):
            raise SystemExit("dist/ is missing; run: python build/generate.py")
        diffs = _diff_trees(os.path.join(tmp, DIST_REL), committed_dist)

        gen_mkt = os.path.join(tmp, MARKETPLACE_REL)
        com_mkt = os.path.join(REPO_ROOT, MARKETPLACE_REL)
        if not os.path.isfile(com_mkt) or not filecmp.cmp(gen_mkt, com_mkt, shallow=False):
            diffs.append(MARKETPLACE_REL)

        if diffs:
            raise SystemExit(
                "STALE — regenerate with `python build/generate.py`:\n  "
                + "\n  ".join(diffs))
        print("OK: dist/ + marketplace.json fresh; every Claude wrapper matches golden.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    if "--check" in argv[1:]:
        check()
        return 0
    dist = os.path.join(REPO_ROOT, DIST_REL)
    if os.path.isdir(dist):
        shutil.rmtree(dist)
    catalog, _, warnings = build_all(REPO_ROOT)
    for w in warnings:
        print("warning: " + w, file=sys.stderr)
    assert_golden(REPO_ROOT, catalog)
    print("Wrote dist/ and %s" % MARKETPLACE_REL)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
