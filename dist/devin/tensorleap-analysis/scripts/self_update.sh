#!/usr/bin/env bash
# Tensorleap skill self-update. Shared by every skill; build/generate.py copies
# it into each skill's scripts/ folder. The install path names the tool and
# scope. Skill folders (Copilot, Cursor, Codex, Windsurf) carry a VERSION file
# and update by re-running install.sh: global installs automatically, a
# project-local install never without approval. A Claude Code plugin updates
# through its marketplace (`claude plugin update`), keyed off the plugin
# version; the new version loads in the next session. Other copies (AGENTS,
# stand-alone Claude skills) exit 0. Skips silently when offline. Set
# TL_SKILL_NO_UPDATE=1 to opt out.
#
# Exit codes:
#   0   nothing changed here (up to date, not applicable, offline, declined,
#       or a Claude plugin update that applies from the next session)
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
command -v curl >/dev/null 2>&1 || exit 0

RAW_BASE="${TL_SKILLS_RAW_BASE:-https://raw.githubusercontent.com/tensorleap/skills/main}"
json_field() { sed -n "s/.*\"$1\": *\"\([^\"]*\)\".*/\1/p" | head -1; }
if [[ "$TL_TOOL" == claude ]]; then
  MANIFEST="$SKILL_DIR/../../.claude-plugin/plugin.json"
  PLUGIN="$(json_field name < "$MANIFEST" 2>/dev/null)"
  LOCAL_V="$(json_field version < "$MANIFEST" 2>/dev/null)"
  REMOTE_V="$(curl -fsSL --max-time 5 "$RAW_BASE/dist/claude/$PLUGIN/.claude-plugin/plugin.json" 2>/dev/null | json_field version || true)"
else
  [[ -f "$SKILL_DIR/VERSION" ]] || exit 0
  LOCAL_V="$(cat "$SKILL_DIR/VERSION" 2>/dev/null)"
  REMOTE_V="$(curl -fsSL --max-time 5 "$RAW_BASE/dist/$TL_TOOL/$(basename "$SKILL_DIR")/VERSION" 2>/dev/null || true)"
fi
# Update only when both values look like real versions (a captive portal
# answering 200/HTML must never trigger an install) AND the remote is
# strictly newer (a branch/dev install ahead of main must not be downgraded).
VER_RE='^[0-9]+\.[0-9]+\.[0-9]+$'
[[ "$LOCAL_V" =~ $VER_RE && "$REMOTE_V" =~ $VER_RE && "$REMOTE_V" != "$LOCAL_V" ]] || exit 0
NEWEST="$(printf '%s\n%s\n' "$LOCAL_V" "$REMOTE_V" | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)"
[[ "$NEWEST" == "$REMOTE_V" ]] || exit 0

if [[ "$TL_TOOL" == claude ]]; then
  # the plugin system installs the new version beside this one, so this session keeps its files
  if command -v claude >/dev/null 2>&1 \
     && claude plugin marketplace update tensorleap >/dev/null 2>&1 \
     && claude plugin update "$PLUGIN@tensorleap" >/dev/null 2>&1; then
    echo "Tensorleap plugin: updated $PLUGIN v$LOCAL_V -> v$REMOTE_V; it loads in the next session (or run /reload-plugins). Continuing with the installed version."
  else
    echo "NOTE: Tensorleap plugin $PLUGIN v$REMOTE_V is available (installed: v$LOCAL_V). To update, run in a terminal:"
    echo "      claude plugin marketplace update tensorleap && claude plugin update $PLUGIN@tensorleap"
    echo "      Continue with the installed version meanwhile."
  fi
  exit 0
fi

if [[ "$TL_SCOPE" == global ]]; then
  echo "Tensorleap skill: updating v$LOCAL_V -> v$REMOTE_V (global install auto-updates) ..."
  curl -fsSL --max-time 120 "$RAW_BASE/install.sh" | sh -s -- --tool "$TL_TOOL" --global >/dev/null \
    || { echo "Tensorleap skill: update failed (a sandbox may block writes to \$HOME); continuing with the installed version."
         echo "  To update manually: curl -fsSL $RAW_BASE/install.sh | sh -s -- --tool $TL_TOOL --global"; }
  exit 10   # an attempt may have replaced files, caller must re-read either way
fi

# project-local install
PROJ_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"
if [ -t 0 ]; then
  printf 'Tensorleap skill v%s is available (installed: v%s). Update the project-local copy? [y/N] ' "$REMOTE_V" "$LOCAL_V"
  ans=""; read -r -t 60 ans || ans=""
  if [[ "$ans" == [yY] ]]; then
    curl -fsSL --max-time 120 "$RAW_BASE/install.sh" | sh -s -- --tool "$TL_TOOL" "$PROJ_ROOT" >/dev/null \
      || echo "Tensorleap skill: update failed; continuing with the installed version"
    exit 10
  fi
  exit 0
fi
echo "NOTE: Tensorleap skill v$REMOTE_V is available (installed: v$LOCAL_V)."
echo "      Project-local install: ASK THE USER for approval before updating. If approved, run:"
echo "      curl -fsSL $RAW_BASE/install.sh | sh -s -- --tool $TL_TOOL \"$PROJ_ROOT\""
echo "      then start over from Step 1. If declined, continue with the installed version."
exit 0
