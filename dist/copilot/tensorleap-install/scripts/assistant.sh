#!/usr/bin/env bash
# Which AI assistant is running this skill. Shared by every skill; build/generate.py
# copies it into each skill's scripts/ folder.
#
#   bash assistant.sh            prints one word: codex, claude-code, cursor, copilot,
#                                windsurf, or unknown
#   source assistant.sh          defines tl_assistant (same answer), prints nothing
#
# The assistant's own environment variable decides first (each is set in the shell the
# assistant runs commands in): CODEX_THREAD_ID -> codex, CLAUDECODE -> claude-code,
# CURSOR_TRACE_ID -> cursor. Without one, the folder this skill is installed in decides,
# using the same paths as self_update.sh.

tl_assistant() {
  if [ -n "${CODEX_THREAD_ID:-}" ]; then echo codex; return; fi
  if [ -n "${CLAUDECODE:-}" ]; then echo claude-code; return; fi
  if [ -n "${CURSOR_TRACE_ID:-}" ]; then echo cursor; return; fi
  local dir
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)"
  case "$dir" in
    */plugins/cache/tensorleap/*/*/skills/*)                     echo claude-code ;;
    "${HOME:-}/.copilot/skills/"*|*/.github/skills/*)             echo copilot ;;
    "${HOME:-}/.cursor/skills/"*|*/.cursor/skills/*)              echo cursor ;;
    "${HOME:-}/.agents/skills/"*|*/.agents/skills/*)              echo codex ;;
    "${HOME:-}/.codeium/windsurf/skills/"*|*/.windsurf/skills/*)  echo windsurf ;;
    *)                                                            echo unknown ;;
  esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  tl_assistant
fi
