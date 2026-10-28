#!/usr/bin/env bash
#
# Interim installer for Tensorleap AI-assistant skills.
#
# Installs the generated per-tool wrappers into a target repo (or your home dir)
# from the committed dist/. Standalone tools (Claude, Cursor, Copilot, Devin) are
# written as their own files; AGENTS.md gets an idempotent marked-section upsert
# that never clobbers content you already own. Copilot gets a native Agent Skill
# folder — .github/skills/<name>/ in a repo, or ~/.copilot/skills/<name>/ with
# --global (read by both VS Code and Copilot CLI). Cursor gets the same skill
# folder at .cursor/skills/<name>/ in a repo, or ~/.cursor/skills/<name>/ with
# --global. Codex gets it at .agents/skills/<name>/ in a repo, or
# ~/.agents/skills/<name>/ with --global. Windsurf gets it at
# .windsurf/skills/<name>/ in a repo, or ~/.codeium/windsurf/skills/<name>/ with
# --global. Devin gets the same skill folder at .devin/skills/<name>/ in a repo
# — no --global: Devin scans the repo it clones for a session on an ephemeral
# cloud machine, not the user's home dir. Installs every skill in the repo (the
# Claude "plugin" grouping only matters for the native marketplace path, not
# for this file-copy install).
#
# Users normally never run this directly: `leap mcp config` runs it for the
# assistant they pick, and the skills re-run it to update themselves.
#
# Usage:
#   ./install.sh [--tool <claude|cursor|copilot|codex|windsurf|agents|devin|all>] [--repo|--global]
#                [--ref <branch>] [TARGET_DIR]
#   curl -fsSL https://raw.githubusercontent.com/tensorleap/skills/main/install.sh \
#     | sh -s -- --tool copilot [--global] [TARGET_DIR]
#
#   --tool     which wrapper(s) to install (default: all)
#   --repo     install into TARGET_DIR (default: current directory)  [default mode]
#   --global   install into your home dir (personal skills, shared scripts)
#   --ref      git branch/tag to fetch when the installer clones itself
#              (default: the repo's default branch; only affects curl|sh runs)
#   TARGET_DIR the repo to install into (only in --repo mode; default: ".")
#
set -eu   # not pipefail: dash (Linux `curl|sh`) rejects `set -o pipefail`

REPO_URL="https://github.com/tensorleap/skills"

TOOL="all"
SCOPE="repo"
TARGET=""
REF=""

# --- args ------------------------------------------------------------------- #
while [ $# -gt 0 ]; do
  case "$1" in
    --tool)   TOOL="${2:?--tool needs a value}"; shift 2 ;;
    --tool=*) TOOL="${1#*=}"; shift ;;
    --repo)   SCOPE="repo"; shift ;;
    --global) SCOPE="global"; shift ;;
    --ref)    REF="${2:?--ref needs a value}"; shift 2 ;;
    --ref=*)  REF="${1#*=}"; shift ;;
    -h|--help)
      sed -n '3,28p' "$0" 2>/dev/null || echo "see header of install.sh"; exit 0 ;;
    -*) echo "install: unknown flag '$1'" >&2; exit 2 ;;
    *)  TARGET="$1"; shift ;;
  esac
done

case "$TOOL" in
  claude|cursor|copilot|codex|windsurf|agents|devin|all) ;;
  *) echo "install: --tool must be one of claude|cursor|copilot|codex|windsurf|agents|devin|all" >&2; exit 2 ;;
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
  if [ -d "$cand/dist" ]; then SRC="$cand"; fi
fi
CLONED=""
if [ -z "$SRC" ]; then
  command -v git >/dev/null 2>&1 || { echo "install: git required to fetch skills" >&2; exit 2; }
  CLONED="$(mktemp -d)"
  echo "Fetching $REPO_URL${REF:+ (ref $REF)} ..."
  if [ -n "$REF" ]; then
    git clone --depth 1 --branch "$REF" "$REPO_URL" "$CLONED" >/dev/null 2>&1 \
      || { echo "install: git clone of $REPO_URL (ref '$REF') failed" >&2; exit 2; }
  else
    git clone --depth 1 "$REPO_URL" "$CLONED" >/dev/null 2>&1 \
      || { echo "install: git clone of $REPO_URL failed" >&2; exit 2; }
  fi
  SRC="$CLONED"
fi
cleanup() { [ -n "$CLONED" ] && rm -rf "$CLONED"; return 0; }
trap cleanup EXIT

DIST="$SRC/dist"
SKILLS_SRC="$SRC/skills"
[ -d "$DIST" ] || { echo "install: $DIST not found (run build/generate.py?)" >&2; exit 2; }

# --- helpers ---------------------------------------------------------------- #
say() { printf '  %s\n' "$1"; }

list_skills() { for d in "$SKILLS_SRC"/*/; do if [ -f "$d/skill.md" ]; then basename "$d"; fi; done; }

want() { [ "$TOOL" = "all" ] || [ "$TOOL" = "$1" ]; }

# Replace the marked block for a skill in $1, or append it if absent.
upsert_section() {
  target="$1"; section="$2"; name="$3"
  begin="<!-- BEGIN TENSORLEAP SKILL: ${name} -->"
  end="<!-- END TENSORLEAP SKILL: ${name} -->"
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
    say "updated $name in $target"
  else
    if [ -s "$target" ]; then printf '\n' >> "$target"; fi
    cat "$section" >> "$target"
    say "added $name to $target"
  fi
}

install_shared_scripts() {
  mkdir -p "$TARGET/.tensorleap/scripts" "$TARGET/.tensorleap/reference"
  # Files only (a stray __pycache__/ etc. must not break the install).
  for skill in $(list_skills); do
    find "$SKILLS_SRC/$skill/scripts" -maxdepth 1 -type f -exec cp {} "$TARGET/.tensorleap/scripts/" \; 2>/dev/null || true
    find "$SKILLS_SRC/$skill/reference" -maxdepth 1 -type f -exec cp {} "$TARGET/.tensorleap/reference/" \; 2>/dev/null || true
  done
  chmod +x "$TARGET"/.tensorleap/scripts/*.sh 2>/dev/null || true
  say "shared scripts -> $TARGET/.tensorleap/scripts"
}

# One Agent Skill folder per skill from dist/$1 into $2.
install_skill_folders() {
  for skill in $(list_skills); do
    src="$DIST/$1/$skill"
    [ -d "$src" ] || continue
    dest="$2/$skill"
    rm -rf "$dest"   # fresh copy: files removed upstream must not linger after updates
    mkdir -p "$dest"
    cp -R "$src/." "$dest/"
    chmod +x "$dest"/scripts/*.sh 2>/dev/null || true
    say "$1 skill -> $dest"
  done
}

# --- installs --------------------------------------------------------------- #
echo "Installing Tensorleap skills (tool=$TOOL, scope=$SCOPE) into $TARGET"

# AGENTS references {{scripts_dir}} = .tensorleap/scripts, so place them.
# (The skill-folder tools and Claude are self-contained and don't need these.)
if want agents; then
  install_shared_scripts
fi

if want claude; then
  for skill in $(list_skills); do
    src="$(find "$DIST/claude" -type d -path "*/skills/$skill" 2>/dev/null | head -1)"
    [ -n "$src" ] || continue
    dest="$TARGET/.claude/skills/$skill"
    mkdir -p "$dest"
    cp -R "$src/." "$dest/"
    chmod +x "$dest"/scripts/*.sh 2>/dev/null || true
    say "claude skill -> $dest"
  done
fi

if want cursor; then
  if [ "$SCOPE" = "global" ]; then
    install_skill_folders cursor "$HOME/.cursor/skills"   # personal skills — read in every project
  else
    install_skill_folders cursor "$TARGET/.cursor/skills"
  fi
fi

if want codex; then
  if [ "$SCOPE" = "global" ]; then
    install_skill_folders codex "$HOME/.agents/skills"    # Codex's user skills — read in every repo
  else
    install_skill_folders codex "$TARGET/.agents/skills"
  fi
fi

if want windsurf; then
  if [ "$SCOPE" = "global" ]; then
    install_skill_folders windsurf "$HOME/.codeium/windsurf/skills"
  else
    install_skill_folders windsurf "$TARGET/.windsurf/skills"
  fi
fi

if want devin; then
  if [ "$SCOPE" = "global" ]; then
    echo "install: --tool devin has no --global install — Devin scans the repo" >&2
    echo "it clones for a session, not the user's home dir; skipping." >&2
  else
    install_skill_folders devin "$TARGET/.devin/skills"
  fi
fi

if want agents; then
  for f in "$DIST"/agents/*.section.md; do
    [ -e "$f" ] || continue
    upsert_section "$TARGET/AGENTS.md" "$f" "$(basename "$f" .section.md)"
  done
fi

if want copilot; then
  if [ "$SCOPE" = "global" ]; then
    install_skill_folders copilot "$HOME/.copilot/skills"   # read by BOTH VS Code and Copilot CLI
  else
    install_skill_folders copilot "$TARGET/.github/skills"
  fi
fi

echo "Done."
