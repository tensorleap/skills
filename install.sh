#!/usr/bin/env bash
#
# Interim installer for Tensorleap AI-assistant skills.
#
# Installs the generated per-tool wrappers into a target repo (or your home dir)
# from the committed `dist/`. Standalone tools (Claude, Cursor) are written as
# their own files; shared files (AGENTS.md, Copilot instructions) get an
# idempotent marked-section upsert that never clobbers content you already own.
#
# The future front door is `leap skills install <name>`; this script is the
# bridge until then.
#
# Usage:
#   ./install.sh [--tool <claude|cursor|copilot|agents|all>] [--repo|--global] [TARGET_DIR]
#   curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh | sh
#
#   --tool     which wrapper(s) to install (default: all)
#   --repo     install into TARGET_DIR (default: current directory)  [default mode]
#   --global   install into your home dir (Claude personal skills, shared scripts)
#   TARGET_DIR the repo to install into (only in --repo mode; default: ".")
#
set -euo pipefail

SKILL_DIR_NAME="tensorleap-integration"
SKILL_NAME="tensorleap-integration-creation"
REPO_URL="https://github.com/tensorleap/skills"

TOOL="all"
SCOPE="repo"
TARGET=""

# --- args ------------------------------------------------------------------- #
while [ $# -gt 0 ]; do
  case "$1" in
    --tool)   TOOL="${2:?--tool needs a value}"; shift 2 ;;
    --tool=*) TOOL="${1#*=}"; shift ;;
    --repo)   SCOPE="repo"; shift ;;
    --global) SCOPE="global"; shift ;;
    -h|--help)
      sed -n '3,26p' "$0" 2>/dev/null || echo "see header of install.sh"; exit 0 ;;
    -*) echo "install: unknown flag '$1'" >&2; exit 2 ;;
    *)  TARGET="$1"; shift ;;
  esac
done

case "$TOOL" in
  claude|cursor|copilot|agents|all) ;;
  *) echo "install: --tool must be one of claude|cursor|copilot|agents|all" >&2; exit 2 ;;
esac

if [ "$SCOPE" = "global" ]; then
  TARGET="$HOME"
else
  TARGET="${TARGET:-.}"
fi
[ -d "$TARGET" ] || { echo "install: target dir '$TARGET' does not exist" >&2; exit 2; }
TARGET="$(cd "$TARGET" && pwd)"

# --- locate the source (clone-and-run, or clone on the fly for curl|sh) ------ #
SCRIPT_SRC="${BASH_SOURCE:-$0}"
SRC=""
if [ -f "$SCRIPT_SRC" ]; then
  cand="$(cd "$(dirname "$SCRIPT_SRC")" && pwd)"
  [ -d "$cand/dist/$SKILL_DIR_NAME" ] && SRC="$cand"
fi
CLONED=""
if [ -z "$SRC" ]; then
  command -v git >/dev/null 2>&1 || { echo "install: git required to fetch skills" >&2; exit 2; }
  CLONED="$(mktemp -d)"
  echo "Fetching $REPO_URL ..."
  git clone --depth 1 "$REPO_URL" "$CLONED" >/dev/null 2>&1
  SRC="$CLONED"
fi
cleanup() { [ -n "$CLONED" ] && rm -rf "$CLONED"; }
trap cleanup EXIT

DIST="$SRC/dist/$SKILL_DIR_NAME"
SHARED="$SRC/skills/$SKILL_DIR_NAME"
[ -d "$DIST" ] || { echo "install: $DIST not found (run build/generate.py?)" >&2; exit 2; }

# --- helpers ---------------------------------------------------------------- #
say() { printf '  %s\n' "$1"; }

# Replace the marked block for this skill in $1, or append it if absent.
upsert_section() {
  target="$1"; section="$2"
  begin="<!-- BEGIN TENSORLEAP SKILL: ${SKILL_NAME} -->"
  end="<!-- END TENSORLEAP SKILL: ${SKILL_NAME} -->"
  mkdir -p "$(dirname "$target")"
  [ -f "$target" ] || : > "$target"
  if grep -qF "$begin" "$target"; then
    awk -v b="$begin" -v e="$end" -v f="$section" '
      function emit(){ while ((getline l < f) > 0) print l; close(f) }
      $0==b { emit(); skip=1; next }
      skip && $0==e { skip=0; next }
      !skip { print }
    ' "$target" > "$target.tmp"
    mv "$target.tmp" "$target"
    say "updated section in $target"
  else
    [ -s "$target" ] && printf '\n' >> "$target"
    cat "$section" >> "$target"
    say "added section to $target"
  fi
}

install_shared_scripts() {
  mkdir -p "$TARGET/.tensorleap/scripts" "$TARGET/.tensorleap/reference"
  # Copy files only (a stray __pycache__/ etc. must not break the install).
  find "$SHARED/scripts" -maxdepth 1 -type f -exec cp {} "$TARGET/.tensorleap/scripts/" \;
  find "$SHARED/reference" -maxdepth 1 -type f -exec cp {} "$TARGET/.tensorleap/reference/" \;
  chmod +x "$TARGET"/.tensorleap/scripts/*.sh 2>/dev/null || true
  say "shared scripts -> $TARGET/.tensorleap/scripts"
}

want() { [ "$TOOL" = "all" ] || [ "$TOOL" = "$1" ]; }

# --- installs --------------------------------------------------------------- #
echo "Installing Tensorleap skills (tool=$TOOL, scope=$SCOPE) into $TARGET"

# Non-Claude tools reference {{scripts_dir}} = .tensorleap/scripts, so place them.
if want cursor || want copilot || want agents; then
  install_shared_scripts
fi

if want claude; then
  dest="$TARGET/.claude/skills/$SKILL_NAME"
  mkdir -p "$dest"
  cp -R "$DIST/claude/skills/$SKILL_NAME/." "$dest/"
  chmod +x "$dest"/scripts/*.sh 2>/dev/null || true
  say "claude skill -> $dest"
fi

if want cursor; then
  dest="$TARGET/.cursor/rules"
  mkdir -p "$dest"
  cp "$DIST/cursor/$SKILL_NAME.mdc" "$dest/"
  say "cursor rule -> $dest/$SKILL_NAME.mdc"
fi

if want agents; then
  upsert_section "$TARGET/AGENTS.md" "$DIST/agents/AGENTS.section.md"
fi

if want copilot; then
  upsert_section "$TARGET/.github/copilot-instructions.md" "$DIST/copilot/copilot-instructions.section.md"
fi

echo "Done."
