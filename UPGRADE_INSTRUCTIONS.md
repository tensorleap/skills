# Upgrade instructions — releasing a new skill version

Maintainer guide: what to do when you change a skill so the new version
actually reaches customers. Customers never do any of this — their installs
update through the channels in the table at the bottom.

## Release checklist

1. **Edit the skill** — `skills/<name>/skill.md` (body/frontmatter), its
   `scripts/`, or `reference/`. Remember:
   - The canonical body ships to **every** tool, byte-identical. A script
     change lands in the Claude copy too, so anything tool-specific in a
     shared script must stay behaviorally inert for the other tools (see
     `scripts/preflight.sh` step 0: it arms only when a `VERSION` file is
     present **and** the install path matches a Copilot/Cursor skills root).
   - If your change alters the generated **Claude** `SKILL.md`, the golden
     check will fail. That is intentional: when the Claude change is
     deliberate, update `build/golden/<name>/SKILL.md` in the same commit so
     the diff is reviewed.

2. **Bump the skill version** — `version:` in `skills/<name>/skill.md`
   frontmatter.
   - **Strict `x.y.z` only** (must match `^[0-9]+\.[0-9]+\.[0-9]+$`). A
     suffix like `0.1.3-rc1` is silently ignored by the self-update gate —
     fielded installs would never upgrade to it.
   - Must be **strictly greater** (numeric per-field compare) than what
     customers have — the updater never downgrades, and equal versions
     never fire.

3. **Bump the plugin version** — `version` in `plugins.json` — whenever
   anything shipped to Claude changed (SKILL.md, scripts, reference).
   The Claude marketplace keys updates off this number; changing Claude
   bytes without bumping it means marketplace users never receive them.

4. **Regenerate** — from the repo root:

   ```bash
   python build/generate.py
   ```

   This rewrites `dist/` and `.claude-plugin/marketplace.json`, including
   the `VERSION` files inside `dist/copilot/<name>/` and `dist/cursor/<name>/`
   (they are stamped from the skill's `version:` — never edit them by hand).

5. **Verify** — all of:

   ```bash
   python build/generate.py --check   # golden + dist/ + marketplace freshness
   python build/leak_scan.py
   python build/check_manifests.py
   ```

   If you touched a shared script, also diff its behavior for tools it must
   not affect (e.g. run old vs new `preflight.sh` from a `.claude/skills/`
   install and compare output + exit code).

6. **Commit `dist/` together with the source change** and open a PR. CI runs
   the same `--check`; a stale `dist/` fails it.

7. **Merge to `main` — this is the release moment.** Nothing is versioned or
   tagged separately; `main` is what every update channel reads:
   - `raw.githubusercontent.com/tensorleap/skills/main/dist/<tool>/<name>/VERSION`
     starts serving the new version (allow ~5 minutes of CDN cache after the
     merge).
   - The Claude marketplace serves the new plugin version.

8. **Post-merge sanity** (optional but cheap):

   ```bash
   curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/dist/copilot/<name>/VERSION
   curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/dist/cursor/<name>/VERSION
   ```

   Both must print the new `x.y.z`. To watch an upgrade happen, run
   `scripts/preflight.sh` from an older global install — it should announce
   `updating vOLD -> vNEW`, reinstall, and re-exec.

## How the new version reaches customers

| Channel | Trigger | Behavior |
|---|---|---|
| **Copilot** (`.github/skills/` / `~/.copilot/skills/`) | next `preflight.sh` run (the skill runs it first, every session) | Global installs auto-update and re-exec; project-local installs ask for approval (TTY `y/N`, or an ask-the-user note under an agent). Offline runs skip silently. `TL_SKILL_NO_UPDATE=1` opts out. |
| **Cursor** (`.cursor/skills/` / `~/.cursor/skills/`) | same as Copilot | Same behavior. If a sandbox blocks the global write, preflight prints the manual update command instead. |
| **Claude Code** (plugin marketplace) | plugin version in the marketplace | Updates flow through the plugin system; Claude copies ship **no `VERSION` file** and never self-update. This is why step 3 matters. |
| **Claude / any tool via `install.sh`** | customer re-runs the README install command | Fresh copy of the current `main` (the installer `rm -rf`s the dest skill folder first, so removed files don't linger). |
| **AGENTS.md** | customer re-runs the installer | The marked section is upserted in place; the skill version is stamped in its header comment. No self-update. |

## Rules of thumb

- One source of truth: the skill's `version:` frontmatter drives the
  `VERSION` files and the AGENTS stamp; `plugins.json` drives the Claude
  plugin/marketplace version. Bump the first for every release; bump the
  second whenever Claude-shipped bytes changed.
- Never ship a `VERSION` file in the Claude output — it is the self-update
  trigger, and Claude must stay marketplace-updated (`build/generate.py`
  already enforces this shape; don't work around it).
- Feature-branch installs (`--ref <branch>`) never self-update: the checker
  reads `main`, gets a 404 for versions that don't exist there yet, and
  skips silently. Updates go live only at merge.
