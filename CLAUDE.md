# tensorleap/skills

## Each skill states the server level it needs

Every skill runs `compat_check.sh <server level>` once, right after its self-update: in its preflight script, or in Step 1 of the analysis `skill.md`. A user whose Tensorleap server is below that level gets a warning with the upgrade command. There is no CLI level: the skills always match the leap CLI they ship with (see below).

When you change a skill:

1. If it starts using something from the server that it didn't use before (an MCP tool or field, an export field), find the level where that arrived with `git log -S 'SKILLS_LEVEL =' -p -- src/metadata/logic.ts` in tensorleap/node-server, and make sure the skill requires at least that level. If it arrived after the newest level, a node-server PR has to bump the level first. Something new from the leap CLI needs no level: it ships with the CLI.
2. When this PR goes with a node-server PR that raises the level, node-server's Skills check requires this PR to pass the new level to `compat_check.sh` for at least one skill.
3. Bump the skill's `version:` in `skill.md` and its plugin's version in `plugins.json`. The self-update only delivers a change when the version goes up, and CI checks both.

## Shipping skills: they follow the leap CLI

Users get the skills a leap CLI release names in `pkg/version/skills-ref` (tensorleap/leap-cli), never `main`: `leap mcp config` installs that tag and each skill's `self_update.sh` moves to it when the user upgrades the CLI. So merging here is always safe; nothing reaches users until a CLI release points at it.

To ship: tag `main` here with the next `skills-vN` (`git tag skills-vN && git push origin skills-vN`), then set `pkg/version/skills-ref` to that tag in a leap-cli PR, usually the one this change goes with. The CLI and server are released together, so the skills, CLI and server a user gets were released together.
