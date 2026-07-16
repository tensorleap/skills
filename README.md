# tensorleap/skills

The single source of truth for Tensorleap's AI-assistant **skills**. Each skill is
authored **once** in a canonical format; this repo generates thin, per-tool
wrappers (GitHub Copilot, Claude Code, Cursor, `AGENTS.md`) from it and ships the
shared scripts each skill needs.

> **v1 ships one skill:** [`tensorleap-integration-creation`](skills/tensorleap-integration-creation/skill.md)
> — authoring and debugging a Tensorleap integration (`leap_integration.py` +
> `leap.yaml`, decorator style) through a progressive author → run → fix loop.

**No clone needed** — every install command below fetches what it needs.

## GitHub Copilot (VS Code and Copilot CLI)

The skill installs as a native [Copilot Agent Skill](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills):
Copilot discovers it automatically and activates it when your task matches its
description — in both Copilot Chat in VS Code and the Copilot CLI.

### Install into one project

```bash
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh -s -- --tool copilot /path/to/your-project
```

(Omit the path to install into the current directory.) This creates
`.github/skills/tensorleap-integration-creation/` in the project — a
self-contained folder (`SKILL.md` + helper scripts + reference docs) read by both
Copilot surfaces.

### Install globally (all projects)

```bash
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh -s -- --tool copilot --global
```

This installs to `~/.copilot/skills/tensorleap-integration-creation/`, which both
Copilot in VS Code and the Copilot CLI read regardless of the project you're in.

### Verify it's discovered

- **Copilot CLI** — run `copilot` in the project, then `/skills list`
  (`/skills info tensorleap-integration-creation` for details). If the session
  was already open when you installed, run `/skills reload` first.
- **VS Code** — open the project and type `/skills` in the Copilot Chat input:
  the skill appears in the list. If the window was open during the install, run
  **Developer: Reload Window** first.

### Use it

Open Copilot in the repo you want to integrate — Copilot Chat in **agent mode**
in VS Code, or `copilot` in the terminal — and describe the task. The skill
activates on Tensorleap-integration language; a good starting prompt:

> Integrate this project with Tensorleap: write leap_integration.py and leap.yaml
> in the decorator style for the model at `<path/to/model.onnx>` over the dataset
> at `<path/to/data>`. Start with the skill's preflight gate, then follow its run
> loop until check_dataset() passes and the integration-test exit table is green.

The skill first runs a preflight gate (Tensorleap CLI, server, data volume,
auth), then drives a progressive author → run → read → fix loop that keeps the
integration runnable at every step, and finishes with `leap push --eval`.

**Upgrading from an older install:** earlier installer versions pasted the skill
into `.github/copilot-instructions.md`; re-running the installer removes that
legacy section automatically (your own content in that file is preserved).

## Claude Code

Native plugin-marketplace path (recommended):

```
/plugin marketplace add tensorleap/skills
/plugin install integration@tensorleap
```

`integration` is the plugin (a domain-scoped bundle) within the `tensorleap`
marketplace; installing it gives you its skills, and Claude auto-loads the
relevant one per task. Or use the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh -s -- --tool claude /path/to/your-project
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh -s -- --tool claude --global   # personal skills (~/.claude)
```

## Cursor / AGENTS.md / everything at once

```bash
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh -s -- --tool cursor /path/to/your-project   # .cursor/rules/
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh -s -- --tool agents /path/to/your-project   # AGENTS.md section
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh                                             # all tools, current dir
```

`AGENTS.md` gets an **idempotent marked-section upsert** — existing content is
preserved and re-running only updates our section. Cursor and AGENTS installs
also place the shared helper files under `.tensorleap/`.

From a clone, `./install.sh --tool <tool> [--global] [TARGET_DIR]` does the same.
`--ref <branch>` makes the clone-free path fetch a specific branch (useful for
testing unreleased changes).

## How it works

A **skill** is the unit of authorship — `skills/<name>/skill.md` (the dir name *is*
the skill name). It's generated into per-tool wrappers. **Claude is the reference:**
each `skill.md` is a *superset* of the proven Claude `SKILL.md` (same body, plus
metadata Claude ignores), and a golden-file check asserts the generated Claude
wrapper is byte-for-byte identical to the proven baseline. No generator change can
silently degrade the Claude experience.

A **plugin** is a Claude-Code-only grouping of skills, declared in `plugins.json`
(it also carries plugin-level metadata + version). **Only Claude has plugins** — the
other tools flatten to one artifact per skill and ignore the grouping entirely. So
packaging choices only ever affect the Claude column.

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
  copilot/<skill>/                   # a Copilot Agent Skill: SKILL.md + scripts + reference (self-contained)
  cursor/<skill>.mdc                 # flat, one per skill
  agents/<skill>.section.md          # flat marked-section fragment per skill
.claude-plugin/marketplace.json      # generated; native `marketplace add` path
install.sh                           # interim installer (clone-and-run or curl|sh)
```

The two per-tool-variable paths in the body — the shared `scripts/` and `reference/`
dirs — are written canonically as `{{scripts_dir}}` / `{{reference_dir}}`. Claude and
Copilot resolve them skill-relative (self-contained folders); Cursor and AGENTS.md
resolve them to `.tensorleap/scripts` and `.tensorleap/reference`, where the
installer places them.

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
own `version`, stamped as a comment into the flat cursor/agents outputs for
traceability.

## License

[Apache-2.0](LICENSE).
