#!/usr/bin/env bash
# Tensorleap skill self-update. Shared by every skill; build/generate.py copies
# it into each skill's scripts/ folder. Copilot/Cursor installs only: the
# VERSION file ships only in those skill folders, so Claude/AGENTS copies (no
# VERSION file) exit 0 immediately; as a second gate, the install path must
# match a known Copilot/Cursor skills root (tool and scope derive from it).
# Global installs update automatically; a project-local install is never
# changed without approval. Skips silently when offline. Set
# TL_SKILL_NO_UPDATE=1 to opt out.
#
# Exit codes:
#   0   nothing changed (up to date, not applicable, offline, declined)
#   10  the skill's files were replaced; the caller must re-read them
#       (a shell caller re-execs itself, an agent re-reads SKILL.md)
set -u -o pipefail
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TL_TOOL=""; TL_SCOPE=""
case "$SKILL_DIR" in
  "${HOME:-}/.copilot/skills/"*) TL_TOOL=copilot; TL_SCOPE=global ;;
  "${HOME:-}/.cursor/skills/"*)  TL_TOOL=cursor;  TL_SCOPE=global ;;
  */.github/skills/*)        TL_TOOL=copilot; TL_SCOPE=project ;;
  */.cursor/skills/*)        TL_TOOL=cursor;  TL_SCOPE=project ;;
esac
[[ -n "$TL_TOOL" && -f "$SKILL_DIR/VERSION" && -z "${TL_SKILL_NO_UPDATE:-}" ]] || exit 0
command -v curl >/dev/null 2>&1 || exit 0

RAW_BASE="${TL_SKILLS_RAW_BASE:-https://raw.githubusercontent.com/tensorleap/skills/main}"
LOCAL_V="$(cat "$SKILL_DIR/VERSION" 2>/dev/null)"
REMOTE_V="$(curl -fsSL --max-time 5 "$RAW_BASE/dist/$TL_TOOL/$(basename "$SKILL_DIR")/VERSION" 2>/dev/null || true)"
# Update only when both values look like real versions (a captive portal
# answering 200/HTML must never trigger an install) AND the remote is
# strictly newer (a branch/dev install ahead of main must not be downgraded).
VER_RE='^[0-9]+\.[0-9]+\.[0-9]+$'
[[ "$LOCAL_V" =~ $VER_RE && "$REMOTE_V" =~ $VER_RE && "$REMOTE_V" != "$LOCAL_V" ]] || exit 0
NEWEST="$(printf '%s\n%s\n' "$LOCAL_V" "$REMOTE_V" | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)"
[[ "$NEWEST" == "$REMOTE_V" ]] || exit 0

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
