#!/usr/bin/env bash
# Tensorleap skill self-update. Shared by every skill; build/generate.py copies
# it into each skill's scripts/ folder.
#
# The skills follow the installed leap CLI: each CLI release names the skills
# git tag it ships with (`skillsRef` in `leap version -o json`), and this script
# moves the installed skills to exactly that tag, so a skill never runs ahead of
# the CLI and server it was released with. Upgrading the CLI is what brings new
# skills.
#
# The install path names the tool and scope. Skill folders (Copilot, Cursor,
# Codex, Windsurf) record their ref in a REF file and reinstall with install.sh:
# global installs automatically, a project-local install never without approval.
# A Claude Code plugin's marketplace is pinned to the ref; moving the pin means
# removing the marketplace, adding it back at the new ref and reinstalling the
# plugins (the old plugin files stay, so this session keeps working); the new
# version loads after /reload-plugins or in the next session. Other copies (AGENTS, stand-alone Claude
# skills) exit 0. An older leap without skillsRef leaves the skills as they are.
# Set TL_SKILL_NO_UPDATE=1 to opt out.
#
# Exit codes:
#   0   nothing changed here (up to date, not applicable, offline, declined,
#       an older leap, or a Claude plugin update that applies after /reload-plugins)
#   10  the skill's files were replaced; the caller must re-read them
#       (a shell caller re-execs itself, an agent re-reads SKILL.md)
set -u -o pipefail
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TL_TOOL=""; TL_SCOPE=""
case "$SKILL_DIR" in
  */plugins/cache/tensorleap/*/*/skills/*)  TL_TOOL=claude;   TL_SCOPE=plugin ;;
  "${HOME:-}/.copilot/skills/"*)            TL_TOOL=copilot;  TL_SCOPE=global ;;
  "${HOME:-}/.cursor/skills/"*)             TL_TOOL=cursor;   TL_SCOPE=global ;;
  "${HOME:-}/.agents/skills/"*)             TL_TOOL=codex;    TL_SCOPE=global ;;
  "${HOME:-}/.codeium/windsurf/skills/"*)   TL_TOOL=windsurf; TL_SCOPE=global ;;
  */.github/skills/*)                       TL_TOOL=copilot;  TL_SCOPE=project ;;
  */.cursor/skills/*)                       TL_TOOL=cursor;   TL_SCOPE=project ;;
  */.agents/skills/*)                       TL_TOOL=codex;    TL_SCOPE=project ;;
  */.windsurf/skills/*)                     TL_TOOL=windsurf; TL_SCOPE=project ;;
esac
[[ -n "$TL_TOOL" && -z "${TL_SKILL_NO_UPDATE:-}" ]] || exit 0
CLI="${TL_CLI:-leap}"
command -v "$CLI" >/dev/null 2>&1 || exit 0   # the preflight reports a missing CLI itself

PIN="$("$CLI" version -o json 2>/dev/null | grep -o '"skillsRef":"[^"]*"' | head -1 | cut -d'"' -f4)"
if [[ -z "$PIN" ]]; then
  echo "NOTE: your leap CLI predates skill updates, so these skills stay as installed."
  echo "      To get newer skills, tell the user to upgrade it: leap cli upgrade -s | bash"
  echo "      Continue with the installed skills meanwhile."
  exit 0
fi
REPO="tensorleap/skills"
RAW_BASE="${TL_SKILLS_RAW_BASE:-https://raw.githubusercontent.com/$REPO}"

if [[ "$TL_TOOL" == claude ]]; then
  command -v claude >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1 || exit 0
  CUR="$(claude plugin marketplace list --json 2>/dev/null | python3 -c '
import json, sys
print(next((m.get("ref") or "" for m in json.load(sys.stdin) if m.get("name") == "tensorleap"), ""))' 2>/dev/null)"
  [[ "$CUR" == "$PIN" ]] && exit 0
  # offline, or the CLI names a ref that doesn't exist yet: keep what's installed
  command -v curl >/dev/null 2>&1 && curl -fsSL --max-time 5 -o /dev/null "$RAW_BASE/$PIN/.claude-plugin/marketplace.json" 2>/dev/null || exit 0
  PLUGINS="$(claude plugin list --json 2>/dev/null | python3 -c '
import json, sys
print(" ".join(p["id"] for p in json.load(sys.stdin) if p["id"].endswith("@tensorleap")))' 2>/dev/null)"
  pin_to() {   # $1: ref, or empty for the default branch
    claude plugin marketplace add "$REPO${1:+#$1}" >/dev/null 2>&1 || return 1
    for p in $PLUGINS; do claude plugin install "$p" >/dev/null 2>&1 || return 1; done
  }
  if claude plugin marketplace remove tensorleap >/dev/null 2>&1 && pin_to "$PIN"; then
    echo "NOTE: the Tensorleap plugins moved from ${CUR:-the default branch} to $PIN, the skills the user's leap CLI ships with."
    echo "      This session still runs the previous skills, which may not match the upgraded CLI. Tell the user to run"
    echo "      /reload-plugins now and then ask again (or start a new session). Continue with the installed version meanwhile."
  else
    claude plugin marketplace remove tensorleap >/dev/null 2>&1; pin_to "$CUR"
    echo "NOTE: the Tensorleap plugins could not move to $PIN. To update, run in a terminal:"
    echo "      leap mcp config claude-code"
    echo "      Continue with the installed version meanwhile."
  fi
  exit 0
fi

command -v curl >/dev/null 2>&1 || exit 0
CUR="$(cat "$SKILL_DIR/REF" 2>/dev/null || true)"
[[ "$CUR" == "$PIN" ]] && exit 0
# A captive portal answering 200/HTML must never be piped into sh: check the pinned ref really has this skill.
VER_RE='^[0-9]+\.[0-9]+\.[0-9]+$'
REMOTE_V="$(curl -fsSL --max-time 5 "$RAW_BASE/$PIN/dist/$TL_TOOL/$(basename "$SKILL_DIR")/VERSION" 2>/dev/null || true)"
[[ "$REMOTE_V" =~ $VER_RE ]] || exit 0   # offline, or the CLI names a ref that doesn't exist yet

if [[ "$TL_SCOPE" == global ]]; then
  echo "Tensorleap skill: updating to $PIN (v$REMOTE_V), the skills your leap CLI ships with ..."
  curl -fsSL --max-time 120 "$RAW_BASE/$PIN/install.sh" | sh -s -- --tool "$TL_TOOL" --global --ref "$PIN" >/dev/null \
    || { echo "Tensorleap skill: update failed (a sandbox may block writes to \$HOME); continuing with the installed version."
         echo "  To update manually: curl -fsSL $RAW_BASE/$PIN/install.sh | sh -s -- --tool $TL_TOOL --global --ref $PIN"; }
  exit 10   # an attempt may have replaced files, caller must re-read either way
fi

# project-local install
PROJ_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"
CMD="curl -fsSL $RAW_BASE/$PIN/install.sh | sh -s -- --tool $TL_TOOL --ref $PIN \"$PROJ_ROOT\""
if [ -t 0 ]; then
  printf 'Tensorleap skills %s (v%s) match your leap CLI (installed: %s). Update the project-local copy? [y/N] ' "$PIN" "$REMOTE_V" "${CUR:-unknown}"
  ans=""; read -r -t 60 ans || ans=""
  if [[ "$ans" == [yY] ]]; then
    sh -c "$CMD" >/dev/null || echo "Tensorleap skill: update failed; continuing with the installed version"
    exit 10
  fi
  exit 0
fi
echo "NOTE: Tensorleap skills $PIN (v$REMOTE_V) match your leap CLI (installed: ${CUR:-unknown})."
echo "      Project-local install: ASK THE USER for approval before updating. If approved, run:"
echo "      $CMD"
echo "      then start over from Step 1. If declined, continue with the installed version."
exit 0
