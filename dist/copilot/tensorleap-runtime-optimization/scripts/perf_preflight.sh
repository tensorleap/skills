#!/usr/bin/env bash
#
# Server-validation gate for the runtime-optimization skill.
#
# CHECK-ONLY. Answers one question: can this session push the optimized integration
# and run a validation Evaluate? It never installs, authenticates, or starts anything.
# The skill's local phases (floor, profiling, lossless fixes) do NOT need a server, so
# a non-zero exit here only disables the validation push; it does not stop the skill.
# One exception: on Copilot/Cursor installs (skill folders that ship a VERSION file)
# step 0 may refresh the skill's OWN files — see self_update.sh.
#
# A remote server is fine: the validation push goes to whichever server the CLI is
# logged into (the user's own). A localhost URL is probed with `server info`.
#
# Usage:   scripts/perf_preflight.sh [integration-root]     (default: .)
# CLI:     default `leap`; override with TL_CLI (e.g. TL_CLI=leapdev)
#
# Exit codes:
#   0  ready — CLI present, authenticated, server reachable, leap.yaml found
#   2  validation push unavailable (no CLI, or no leap.yaml in the integration root):
#      continue with the local phases, and say in the report that the result was
#      not validated on a server
#   3  not authenticated — guide `leap auth login`, then re-run
#   6  the CLI points at localhost but no server answers — ask whether the server is
#      remote (reachable through a port-forward) or needs to be started
#
set -uo pipefail

TL_CLI="${TL_CLI:-leap}"
ROOT="${1:-.}"

pass(){ printf '  [PASS] %-22s %s\n' "$1" "$2"; }
fail(){ printf '  [FAIL] %-22s %s\n' "$1" "$2"; }
note(){ printf '         -> %s\n' "$1"; }

# 0. Skill self-update (Copilot/Cursor installs only, see self_update.sh).
if [[ -z "${TL_SKILL_NO_UPDATE:-}" && -f "$(dirname "$0")/self_update.sh" ]]; then
  bash "$(dirname "$0")/self_update.sh"; rc=$?
  [[ $rc -eq 10 ]] && TL_SKILL_NO_UPDATE=1 exec "$0" "$@"
fi

echo "Tensorleap server-validation preflight"

# 1. leap.yaml — the push target ------------------------------------------------
if [[ ! -f "$ROOT/leap.yaml" ]]; then
  fail "leap.yaml" "not found in $ROOT"
  note "The validation push needs the integration's leap.yaml."
  echo; echo "VALIDATION PUSH UNAVAILABLE — continue locally; report the result as not server-validated."
  exit 2
fi
pass "leap.yaml" "$ROOT/leap.yaml"

# 2. CLI on PATH ---------------------------------------------------------------
if ! command -v "$TL_CLI" >/dev/null 2>&1; then
  fail "CLI present" "'$TL_CLI' not found on PATH"
  note "Install the Tensorleap CLI to enable the validation push."
  echo; echo "VALIDATION PUSH UNAVAILABLE — continue locally; report the result as not server-validated."
  exit 2
fi
pass "CLI present" "$(command -v "$TL_CLI")"

# 3. Auth + server endpoint ----------------------------------------------------
WHO="$("$TL_CLI" auth whoami 2>&1)"
URL="$(printf '%s\n' "$WHO" | sed -n 's/^API Url:[[:space:]]*//p' | head -1)"
EMAIL="$(printf '%s\n' "$WHO" | sed -n 's/^User email:[[:space:]]*//p' | head -1)"
if [[ -z "$URL" || -z "$EMAIL" ]]; then
  fail "Authenticated" "not logged in${URL:+ to $URL}"
  note "Log in: $TL_CLI auth login   (then re-run this gate)"
  exit 3
fi
pass "Authenticated" "$EMAIL"

HOST="$(printf '%s' "$URL" | sed -e 's#^[a-zA-Z][a-zA-Z0-9+.-]*://##' -e 's#/.*$##' -e 's#:.*$##' | tr 'A-Z' 'a-z')"
case "$HOST" in
  localhost|127.0.0.1|::1|0.0.0.0)
    INFO="$("$TL_CLI" server info 2>&1)"
    if printf '%s\n' "$INFO" | grep -qiE "no installation information|not running|cluster not found"; then
      fail "Server online" "no local server answered at $URL"
      echo
      echo "NO LOCAL SERVER — ask whether the server is remote (a port-forward can use any"
      echo "local port) or must be started ($TL_CLI server run). Re-run this gate after."
      exit 6
    fi
    pass "Server" "local ($URL)"
    ;;
  *)
    pass "Server" "remote ($URL)"
    ;;
esac

echo
echo "READY — the optimized integration can be pushed and validated on $URL."
exit 0
