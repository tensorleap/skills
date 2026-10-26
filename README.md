# tensorleap/skills

The single source of truth for Tensorleap's AI-assistant **skills**. Each skill is
authored **once** in a canonical format; this repo generates thin, per-tool
wrappers (GitHub Copilot, Claude Code, Cursor, Devin, `AGENTS.md`) from it and
ships the shared scripts each skill needs.

> **Skills in this repo:**
> - [`tensorleap-integration-creation`](skills/tensorleap-integration-creation/skill.md)
>   — authoring and debugging a Tensorleap integration (`leap_integration.py` +
>   `leap.yaml`, decorator style) through a progressive author → run → fix loop.
> - [`tensorleap-install`](skills/tensorleap-install/skill.md) — installing,
>   upgrading, or reinstalling a Tensorleap server on your own machine (Linux, macOS,
>   WSL2, cloud VMs; online, proxy, or airgap; CPU or GPU): preflight checks, the right
>   `leap server` command, first-login verification, a field-sourced failure catalog,
>   and a per-machine `install-notes.md` so later upgrades start with context.
> - [`tensorleap-analysis`](skills/tensorleap-analysis/skill.md) — reading an evaluated
>   model version's results through the Tensorleap MCP server and writing a report:
>   where the model fails and why, with evidence samples and concrete next steps.
> - [`tensorleap-aggressor-fixing`](skills/tensorleap-aggressor-fixing/skill.md) — fixing
>   the failure modes the analysis found: every aggressor inventoried and ranked with you,
>   the platform's labelling selection first, one dedicated fix per aggressor, a pre-run
>   gate (fixed member ids, an agreed failure-mode metric, noise bands), one variable per
>   run against a control, and a two-axis verdict: failure mode solved, global KPI not
>   regressed.
> - [`tensorleap-runtime-optimization`](skills/tensorleap-runtime-optimization/skill.md)
>   — measuring and optimizing a working integration's runtime and memory: the model's
>   inference floor, component-level profiling the way Tensorleap runs it, fixes kept only
>   when every output stays identical, validation on your Tensorleap server, and a report
>   naming what limits runtime and memory now
>   ([guide](skills/tensorleap-runtime-optimization/docs/README.md)).

**No clone needed** — every install command below fetches what it needs.

## Connect your AI assistant to Tensorleap (MCP server + skills)

Your assistant reads your Tensorleap analysis through the **Tensorleap MCP server**
(insights, metrics, samples, integration code, job logs) and uses the **skills** in this
repo to turn it into answers and reports. One command sets up both.

**1. Install or upgrade the leap CLI** (0.0.163 or newer has `leap mcp`):

```bash
curl -s https://raw.githubusercontent.com/tensorleap/leap-cli/master/install.sh | bash
```

Already installed? `leap cli upgrade -s | bash`

**2. Sign in to your Tensorleap server** (skip if you already push with `leap`):

```bash
leap auth login
```

**3. Connect your assistant:**

```bash
leap mcp config
```

It asks which assistant (the ones on your machine are marked) and, if you use more than
one Tensorleap server, which one. Then it checks the server answers, writes the
assistant's MCP settings (keeping everything else in the file, with a `.bak` copy), and
installs the skills where the assistant loads them. You can also name the assistant:
`leap mcp config cursor`.

**4. Restart the assistant and ask:**

> What is my model's biggest weakness in project `<name>`?
>
> Write a Tensorleap analysis report for project `<name>`

| Assistant | What `leap mcp config` sets up |
|---|---|
| Claude Code | the `analysis` plugin: MCP server + skills, updated through the plugin marketplace |
| Cursor | MCP server in `~/.cursor/mcp.json` + skills in `~/.cursor/skills` |
| VS Code (GitHub Copilot) | MCP server in your user `mcp.json` + skills in `~/.copilot/skills` |
| OpenAI Codex | MCP server in `~/.codex/config.toml` |
| Windsurf | MCP server in `~/.codeium/windsurf/mcp_config.json` |
| Claude Desktop | MCP server in `claude_desktop_config.json` |

<details>
<summary>More: manual setup, several servers, AI access, troubleshooting</summary>

- **Set it up by hand or for a whole team:** `leap mcp config <assistant> --print` shows
  the exact snippet and changes nothing. For Claude Code it also shows a `.mcp.json` you
  can commit to a repo.
- **Claude Code from the plugin marketplace instead:** inside Claude Code run
  `/plugin marketplace add tensorleap/skills` then `/plugin install analysis@tensorleap`.
  The plugin starts `leap mcp`, so the leap CLI still has to be installed and signed in.
- **Several Tensorleap servers:** the assistant follows your current `leap auth`
  environment; switch it with `leap auth select`. `leap mcp config <assistant> --env <name>`
  pins one server instead (for Claude Code that registers the MCP server without the plugin).
- **MCP only, no skills:** `leap mcp config <assistant> --no-skills`.
- **AI access:** an admin decides what assistants may read (gear icon, top right, then
  **AI ACCESS**). Everything is on by default; when something is off, the setup and the
  assistant both say so.
- **Check the connection any time:** run `leap mcp` in a terminal. It shows who you are
  signed in as, whether the MCP server answers, and what AI access withholds.
- **Troubleshooting:**
  - `unknown command "mcp"`: your leap CLI is older than 0.0.163; upgrade it (step 1).
  - "the server rejected your API key": run `leap auth login` again.
  - "has no MCP endpoint yet": the Tensorleap server needs an upgrade (ask your admin, or
    `leap server upgrade` on a server you run).
  - "Can't reach …": the server isn't reachable from this machine; check the VPN or
    tunnel you normally use to open the Tensorleap UI.
  - VS Code with Copilot Business or Enterprise: an organization admin must allow
    *MCP servers in Copilot*.

</details>

The sections below install the skills for one assistant at a time, without the MCP server.

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

**Staying up to date:** the skill checks for a newer version whenever its
preflight gate runs. A global install (`~/.copilot/skills`) updates itself
automatically; a **project-local install is never changed without your
approval** — Copilot will ask you first (or you'll get a `y/N` prompt when
running preflight yourself in a terminal). Offline runs skip the check
silently. This applies to Copilot and Cursor installs only — Claude installs
are not touched (Claude updates come through its plugin marketplace).

**Upgrading from an older install:** earlier installer versions pasted the skill
into `.github/copilot-instructions.md`. If that file contains a
`TENSORLEAP SKILL` marker section, delete the section (or the whole file, if the
section is all it contains) so the content isn't loaded twice.

## Claude Code

Native plugin-marketplace path (recommended):

```
/plugin marketplace add tensorleap/skills
/plugin install integration@tensorleap    # authoring integrations
/plugin install installer@tensorleap      # installing / upgrading a Tensorleap server
/plugin install optimization@tensorleap   # measuring and optimizing an integration's runtime and memory
```

`integration`, `installer` and `optimization` are plugins (domain-scoped bundles) within the
`tensorleap` marketplace; installing one gives you its skills, and Claude auto-loads
the relevant one per task. Or use the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh -s -- --tool claude /path/to/your-project
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh -s -- --tool claude --global   # personal skills (~/.claude)
```

## Cursor

The skill installs as a native [Cursor Agent Skill](https://cursor.com/docs/skills)
(requires Cursor 2.4+): Cursor discovers it automatically and applies it when
your task matches its description — in the IDE's Agent and the Cursor CLI.

### Install into one project

```bash
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh -s -- --tool cursor /path/to/your-project
```

(Omit the path to install into the current directory.) This creates
`.cursor/skills/tensorleap-integration-creation/` in the project — a
self-contained folder (`SKILL.md` + helper scripts + reference docs).

### Install globally (all projects)

```bash
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh -s -- --tool cursor --global
```

This installs to `~/.cursor/skills/tensorleap-integration-creation/`, which
Cursor reads regardless of the project you're in.

### Verify it's discovered

- **Cursor IDE** — open **Customize** in the sidebar and go to **Skills**: the
  skill appears in the list. You can also type `/` in the Agent chat input and
  search for it. Skills are discovered at startup — if Cursor was open during
  the install, reload the window first.
- **Cursor CLI** — run `agent` in the project and type `/` to search the skill
  name.

### Use it

Open the project in Cursor (or run `agent` in the terminal — the Cursor CLI)
and describe the task in Agent mode. The skill activates on
Tensorleap-integration language; a good starting prompt:

> Integrate this project with Tensorleap: write leap_integration.py and leap.yaml
> in the decorator style for the model at `<path/to/model.onnx>` over the dataset
> at `<path/to/data>`. Start with the skill's preflight gate, then follow its run
> loop until check_dataset() passes and the integration-test exit table is green.

**Staying up to date:** the skill checks for a newer version whenever its
preflight gate runs. A global install (`~/.cursor/skills`) updates itself
automatically; a **project-local install is never changed without your
approval** — the agent will ask you first (or you'll get a `y/N` prompt when
running preflight yourself in a terminal). Offline runs skip the check
silently. If Cursor's sandbox blocks the global auto-update (it writes to your
home directory), preflight prints the manual update command instead.

**Upgrading from an older install:** earlier installer versions wrote a Cursor
*rule* at `.cursor/rules/tensorleap-integration-creation.mdc` plus helper files
under `.tensorleap/` (with `--global`: the same paths under your home dir).
Delete that `.mdc` file so the content isn't loaded twice (and `.tensorleap/`,
unless an `AGENTS.md` install still uses it).

## Devin

[Devin](https://devin.ai) is Cognition's autonomous software engineering agent
that clones a repo onto an ephemeral cloud machine to work a session, rather
than running in your own IDE or terminal like the other tools here. The skill
installs as a native [Devin Agent Skill](https://docs.devin.ai/product-guides/skills)
(the open Agent Skills standard shared with Cursor and Copilot): Devin scans
your repo for `SKILL.md` files whenever it clones it for a session and
activates the skill when your task matches its description.

### Install into a repo

```bash
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh -s -- --tool devin /path/to/your-project
```

(Omit the path to install into the current directory.) This creates
`.devin/skills/tensorleap-integration-creation/` in the project — a
self-contained folder (`SKILL.md` + helper scripts + reference docs).

There is no `--global` install for Devin: sessions run on ephemeral cloud
machines that clone the repo fresh each time, not the user's own machine, so
there's no persistent home directory for Devin to read a personal skill from.

### Verify it's discovered

Open the skill list in a Devin session for the repo (or ask Devin to list its
skills) — `tensorleap-integration-creation` should appear, sourced from
`.devin/skills/`.

### Use it

Start a Devin session on the repo you want to integrate and describe the task.
The skill activates on Tensorleap-integration language; a good starting prompt:

> Integrate this project with Tensorleap: write leap_integration.py and leap.yaml
> in the decorator style for the model at `<path/to/model.onnx>` over the dataset
> at `<path/to/data>`. Start with the skill's preflight gate, then follow its run
> loop until check_dataset() passes and the integration-test exit table is green.

**Staying up to date:** there is no self-update check for Devin installs (no
persistent global install to check from) — rerun the install command to pull
the latest skill version into the repo.

## AGENTS.md / everything at once

```bash
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh -s -- --tool agents /path/to/your-project   # AGENTS.md section
curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh                                             # all tools, current dir
```

`AGENTS.md` gets an **idempotent marked-section upsert** — existing content is
preserved and re-running only updates our section. AGENTS installs also place
the shared helper files under `.tensorleap/`.

From a clone, `./install.sh --tool <tool> [--global] [TARGET_DIR]` does the same.

## Optimizing an integration's runtime and memory

The `tensorleap-runtime-optimization` skill installs with the same commands as above: the
file-copy installer adds every skill in this repo, and in Claude Code it is the
`optimization` plugin. Use it on an integration that already works: `leap_integration.py`
and `leap.yaml` at the repo root, `check_dataset()` passing, and a model file
(`.onnx` or `.h5`) that loads locally.

### Use it

Open your assistant in the integration repo root, ideally on a quiet machine (other heavy
jobs skew the timings), and describe the problem. The skill activates on runtime and
memory language; a good starting prompt:

> Our Tensorleap evaluation of this integration is slow. Find out why, optimize it
> without changing its outputs, and give me the report.

The skill first runs a preflight (the integration's Python environment, whether the model
actually uses the GPU, code-loader, the integration itself, the server connection), then
times the model alone to get its **floor**: the unit every component's cost is scored
against. It profiles every component the way Tensorleap runs it and drives a
one-change-at-a-time loop that keeps a fix only when every output stays identical and the
run gets faster or the per-worker memory smaller (memory first by default). It finishes
with a check on your Tensorleap server and a report naming what limits runtime and memory
now.

It runs on its own and asks only when it has to: whether to commit uncommitted changes
first (git repos), which model file to push if it can't tell, whether to apply a fix that
would change outputs, and whether to run **online diagnostics** on your server. "No" to
that last one runs a short smoke check (about 50 samples per state, minutes). Diagnostics
run the whole dataset (or a per-state cap you give), can take hours, and happen only with
your yes; when the visualization's pace is steady, the assistant asks you to stop the run
in the Tensorleap UI.

Everything it writes goes to `tensorleap/runtime-optimization/` in your repo:
**`report.html`** (the one to share), `report.md`, `optimization-log.md`, and each
accepted fix as a patch in `fixes/` (in a git repo, also one commit per fix on the
`tensorleap-runtime-optimization` branch).

**More:** [`docs/README.md`](skills/tensorleap-runtime-optimization/docs/README.md) covers
what you need, the phrases that change its behavior, and what each phase does;
[`docs/how-it-works.html`](skills/tensorleap-runtime-optimization/docs/how-it-works.html)
explains every step with flow charts and examples (GitHub shows it as source: download it
and open it in a browser).

**Staying up to date:** Copilot and Cursor installs check for a newer version whenever the
skill's preflight runs, the same way as the integration skill. Claude updates come through
the plugin marketplace (`/plugin marketplace update tensorleap`).

## How it works

A **skill** is the unit of authorship — `skills/<name>/skill.md` (the dir name *is*
the skill name). It's generated into per-tool wrappers. **Claude is the reference:**
each `skill.md` is a *superset* of the proven Claude `SKILL.md` (same body, plus
metadata Claude ignores), and a golden-file check asserts the generated Claude
wrapper is byte-for-byte identical to the proven baseline. No generator change can
silently degrade the Claude experience.

A **plugin** is a Claude-Code-only grouping of skills, declared in `plugins.json`
(it also carries plugin-level metadata + version). **Only Claude has plugins** — the
other tools (Copilot, Cursor, Devin, AGENTS.md) flatten to one artifact per skill
and ignore the grouping entirely. So packaging choices only ever affect the
Claude column.

```
skills/                             # the atoms — author here
  tensorleap-integration-creation/
    skill.md                        # canonical: superset frontmatter + tool-neutral body
    reference/*.md                   # shared reference docs
    scripts/                         # shared, repo-agnostic (preflight / run / check)
  tensorleap-install/
    skill.md                        # install / upgrade / reinstall a Tensorleap server
    reference/install-troubleshooting.md   # field-sourced failure catalog
    scripts/install_preflight.sh    # read-only machine preflight
  tensorleap-runtime-optimization/
    skill.md                        # measure and optimize an integration's runtime and memory
    reference/perf-*.md              # execution model, bottleneck catalog, levers, report template
    scripts/                         # tl_perf.py (+ perf_online, perf_code, report_html), perf_preflight.sh
    docs/                            # README.md + how-it-works.html (not installed)
    tests/                           # unit tests (not packaged)
plugins.json                         # which skills compose which Claude plugins (+ metadata/version)
build/
  generate.py                        # skills + plugins.json -> wrappers   (--check for CI)
  check_manifests.py                 # marketplace.json + plugin.json validity
  leak_scan.py                       # no internal references in published files
  golden/<skill>/SKILL.md            # pinned proven Claude SKILL.md (the don't-degrade tripwire)
dist/                                # generated wrappers (committed; CI asserts fresh)
  claude/<plugin>/                   # a Claude plugin: plugin.json + skills/<skill>/{SKILL.md,scripts,reference}
  copilot/<skill>/                   # a Copilot Agent Skill: SKILL.md + scripts + reference (self-contained)
  cursor/<skill>/                    # a Cursor Agent Skill: same shape
  devin/<skill>/                     # a Devin Agent Skill: same shape
  agents/<skill>.section.md          # flat marked-section fragment per skill
.claude-plugin/marketplace.json      # generated; native `marketplace add` path
install.sh                           # interim installer (clone-and-run or curl|sh)
```

The two per-tool-variable paths in the body — the shared `scripts/` and `reference/`
dirs — are written canonically as `{{scripts_dir}}` / `{{reference_dir}}`. Claude,
Cursor, Copilot and Devin resolve them skill-relative (self-contained folders);
AGENTS.md resolves them to `.tensorleap/scripts` and `.tensorleap/reference`,
where the installer places them.

## Analytics

Each skill records when a run starts and how it ends: one `skill_run` event per stage in
Tensorleap's product analytics, carrying the skill's name and version, the stage
(`started` / `finished` / `failed`), the outcome, the duration and, for the integration
skill, how many run-loop passes it took — with the same anonymous device id the leap CLI
uses (`~/.tensorleap/device_id`) and, once you have run `leap auth login`, its user id.
No code, paths, data or prompts are sent. Set `TL_DISABLE_ANALYTICS=1` to turn it off
(code-loader honors the same variable).

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
own `version`, stamped as a comment into the flat AGENTS output for traceability
(and shipped as the `VERSION` file in the Copilot and Cursor skills, where it
drives the self-update check).

## License

[Apache-2.0](LICENSE).
