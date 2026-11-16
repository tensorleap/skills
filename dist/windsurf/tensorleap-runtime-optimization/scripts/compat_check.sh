#!/usr/bin/env bash
# Warn when this skill needs a newer Tensorleap server than the user has. Never
# blocks: always exits 0. Shared by every skill; build/generate.py copies it into
# each skill's scripts/ folder. (The skills always match the installed leap CLI,
# see self_update.sh, so only the server can be behind.)
#
# Usage: compat_check.sh <server level>
# The level comes from `leap version -o json` (serverLevel). A null serverLevel
# (not signed in, unreachable) or a leap without that command skips the check.
set -u -o pipefail
NEED_SERVER="${1:-0}"
CLI="${TL_CLI:-leap}"
command -v "$CLI" >/dev/null 2>&1 || exit 0   # the preflight reports a missing CLI itself

out="$("$CLI" version -o json 2>/dev/null)" || out=""
server_level="$(printf '%s' "$out" | grep -o '"serverLevel":[^,}]*' | head -1 | cut -d: -f2 | tr -d ' ')"
if [[ "$server_level" =~ ^[0-9]+$ ]] && (( server_level < NEED_SERVER )); then
  echo "WARNING: this skill needs a newer Tensorleap server (skills level $NEED_SERVER; this server: $server_level)."
  echo "  Tell the user: on a server they run, run 'leap server upgrade'; otherwise ask their Tensorleap admin to upgrade."
  echo "  Continue meanwhile; steps that rely on the newer server may fail until it is upgraded."
fi
exit 0
