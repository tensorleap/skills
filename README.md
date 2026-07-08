# tensorleap/skills

The single source of truth for Tensorleap's AI-assistant **skills**. Each skill is
authored **once** in a canonical format; this repo generates thin, per-tool
wrappers (Claude Code, Cursor, GitHub Copilot, `AGENTS.md`) from it and ships the
shared scripts each skill needs.

Internal and external users consume the same published artifact. Delivery is via
the installer below today, and eventually via `leap skills install <name>`.

> **v1 ships one skill:** [`tensorleap-integration-creation`](skills/tensorleap-integration-creation/skill.md)
> — authoring and debugging a Tensorleap integration (`leap_integration.py` +
> `leap.yaml`, decorator style) through a progressive author → run → fix loop.

## Install

### Any tool (Claude, Cursor, Copilot, AGENTS.md)

From a clone, into a target repo:

```bash
./install.sh --tool all /path/to/your-repo      # or --tool claude|cursor|copilot|agents
./install.sh --tool claude --global             # Claude personal skills (~/.claude)
```

Or over the network (once published):

```bash
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh
```

Standalone tools (Claude, Cursor) are written as their own files. Shared files you
may already own (`AGENTS.md`, `.github/copilot-instructions.md`) get an **idempotent
marked-section upsert** — existing content is preserved and re-running only updates
our section.

### Claude Code plugin marketplace (native path)

```
/plugin marketplace add tensorleap/skills
/plugin install integration@tensorleap
```

`integration` is the plugin (a domain-scoped bundle) within the `tensorleap`
marketplace; installing it gives you its skills, and Claude auto-loads the
relevant one per task.

## How it works

A **skill** is the unit of authorship — `skills/<name>/skill.md` (the dir name *is*
the skill name). It's generated into per-tool wrappers. **Claude is the reference:**
each `skill.md` is a *superset* of the proven Claude `SKILL.md` (same body, plus
metadata Claude ignores), and a golden-file check asserts the generated Claude
wrapper is byte-for-byte identical to the proven baseline. No generator change can
silently degrade the Claude experience.

A **plugin** is a Claude-Code-only grouping of skills, declared in `plugins.json`
(it also carries plugin-level metadata + version). **Only Claude has plugins** — the
other tools (Cursor, Copilot, AGENTS.md) **flatten** to one file/section per skill
and ignore the grouping entirely. So packaging choices only ever affect the Claude
column.

```
skills/                             # the atoms — author here
  tensorleap-integration-creation/
    skill.md                        # canonical: superset frontmatter + tool-neutral body
    reference/*.md                   # shared reference docs
    scripts/                         # shared, repo-agnostic (preflight / run / check)
plugins.json                         # which skills compose which Claude plugins (+ metadata/version)
build/
  generate.py                        # skills + plugins.json -> wrappers   (--check for CI)
  check_manifests.py                 # marketplace.json + plugin.json validity
  leak_scan.py                       # no internal references in published files
  golden/<skill>/SKILL.md            # pinned proven Claude SKILL.md (the don't-degrade tripwire)
dist/                                # generated wrappers (committed; CI asserts fresh)
  claude/<plugin>/                   # a Claude plugin: plugin.json + skills/<skill>/{SKILL.md,scripts,reference}
  cursor/<skill>.mdc                 # flat, one per skill
  agents/<skill>.section.md          # flat marked-section fragment per skill
  copilot/<skill>.section.md         # flat marked-section fragment per skill
.claude-plugin/marketplace.json      # generated; native `marketplace add` path
install.sh                           # interim installer (clone-and-run or curl|sh)
```

The two per-tool-variable paths in the body — the shared `scripts/` and `reference/`
dirs — are written canonically as `{{scripts_dir}}` / `{{reference_dir}}`. Claude
resolves them skill-relative (self-contained); every other tool resolves them to
`.tensorleap/scripts` and `.tensorleap/reference`, where the installer places them.

## Developing

```bash
python build/generate.py           # regenerate dist/ + marketplace.json after an edit
python build/generate.py --check   # what CI runs: golden + dist/ + marketplace freshness
python build/leak_scan.py
python build/check_manifests.py
```

**Editing a skill.** Change `skills/<name>/skill.md` (or its `scripts/` /
`reference/`), then regenerate. If your change alters the Claude output, the golden
check will fail — that is intentional. If the change to the Claude experience is
deliberate, update the golden baseline under `build/golden/<name>/` in the same
commit so the diff is reviewed.

**Adding a skill.** Create `skills/<new-name>/skill.md` (+ `scripts/` / `reference/`),
pin its proven Claude output at `build/golden/<new-name>/SKILL.md`, add the skill to
a plugin's `skills` list in `plugins.json` (or a new plugin entry), then regenerate.

**Versioning.** Plugin version lives in `plugins.json` and is stamped into
`plugin.json` + `marketplace.json` by the generator. Each `skill.md` also carries its
own `version`, stamped as a comment into the flat (non-Claude) outputs for traceability.

## License

[Apache-2.0](LICENSE).
