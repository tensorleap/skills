# tensorleap/skills

The single source of truth for Tensorleap's AI-assistant **skills**. Each skill is
authored **once** in a canonical format; this repo generates thin, per-tool
wrappers (Claude Code, Cursor, GitHub Copilot, `AGENTS.md`) from it and ships the
shared scripts each skill needs.

Internal and external users consume the same published artifact. Delivery is via
the installer below today, and eventually via `leap skills install <name>`.

> **v1 ships one skill:** [`tensorleap-integration-creation`](skills/tensorleap-integration/skill.md)
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
/plugin install tensorleap-integration-creation@tensorleap
```

## How it works

One canonical skill → generated per-tool wrappers. **Claude is the reference:** the
canonical `skill.md` is a *superset* of the proven Claude `SKILL.md` (same body,
plus metadata Claude ignores), and a golden-file check asserts the generated Claude
wrapper is byte-for-byte identical to the proven baseline. No generator change can
silently degrade the Claude experience.

```
skills/
  tensorleap-integration/
    skill.md            # canonical: superset frontmatter + tool-neutral body
    reference/*.md       # shared reference docs
    scripts/             # shared, repo-agnostic (preflight / run / check)
build/
  generate.py            # canonical skill.md -> per-tool wrappers  (--check for CI)
  check_manifests.py     # marketplace.json + plugin.json validity
  leak_scan.py           # no internal references in published files
  golden/                # the pinned proven Claude SKILL.md (golden baseline)
dist/                    # generated wrappers (committed; CI asserts fresh)
  tensorleap-integration/{claude,cursor,copilot,agents}/
install.sh               # interim installer (clone-and-run or curl|sh)
.claude-plugin/marketplace.json
```

The only per-tool differences in the body are the paths to the shared `scripts/`
and `reference/` files, written canonically as the `{{scripts_dir}}` and
`{{reference_dir}}` placeholders. The Claude wrapper resolves them to the
skill-relative `scripts/` and `reference/` (it installs self-contained); every other
tool resolves them to `.tensorleap/scripts` and `.tensorleap/reference`, where the
installer places the shared files.

## Developing

```bash
python build/generate.py           # regenerate dist/ after editing a skill
python build/generate.py --check   # what CI runs: golden + dist freshness
python build/leak_scan.py
python build/check_manifests.py
```

**Editing a skill.** Change `skills/tensorleap-integration/skill.md` (or its
`scripts/` / `reference/`), then regenerate. If your change alters the Claude
output, the golden check will fail — that is intentional. If the change to the
Claude experience is deliberate, update the golden baseline under `build/golden/`
in the same commit so the diff is reviewed.

## License

[Apache-2.0](LICENSE).
