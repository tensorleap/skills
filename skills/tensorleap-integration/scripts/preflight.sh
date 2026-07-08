#!/usr/bin/env bash
#
# Preflight gate for the Tensorleap integration loop.
#
# CHECK-ONLY. Detects the platform prerequisites, prints a PASS/FAIL table, and
# exits non-zero when something is wrong. It NEVER installs, authenticates,
# starts a server, or configures anything — it reports and stops. Run it FIRST,
# before authoring (see SKILL.md "Preflight gate").
#
# It intentionally checks only prerequisites that do NOT need a Python
# environment, so it can run before the project env is provisioned. The Python
# environment and `code_loader` (install + version floor) are handled separately
# by the skill as a setup step — they require the env to exist, so they are not
# part of this gate.
#
# v1 supports only the case where the CLI and the Tensorleap server run on the
# SAME machine (a local server). A remote server is detected and reported as
# unsupported.
#
# Usage:   scripts/preflight.sh
# CLI:     default `leap`; override with TL_CLI (e.g. TL_CLI=leapdev)
#
# Exit codes:
#   0  all clear — platform prerequisites met
#   2  BLOCKER (CLI missing / remote server / server down / no data volume) —
#      relay the printed guidance to the user and STOP; do not fix it here
#   3  SETUP pending (not authenticated) — the skill guides login, then re-run
#
set -uo pipefail

TL_CLI="${TL_CLI:-leap}"

pass(){ printf '  [PASS] %-24s %s\n' "$1" "$2"; }
fail(){ printf '  [FAIL] %-24s %s\n' "$1" "$2"; }
note(){ printf '         -> %s\n' "$1"; }
blocked(){ echo; echo "BLOCKED — do not begin authoring. Resolve the item above and re-run preflight."; exit 2; }

echo "Tensorleap preflight (v1 — local server only)"

# 1. CLI on PATH -------------------------------------------------------------
if ! command -v "$TL_CLI" >/dev/null 2>&1; then
  fail "CLI present" "'$TL_CLI' not found on PATH"
  note "This skill runs on the Tensorleap server host, where the CLI ships with the install."
  note "If you are on that host and the CLI is missing, contact Tensorleap."
  blocked
fi
pass "CLI present" "$(command -v "$TL_CLI")"

# 2. Topology (read the configured API URL from whoami — no file parsing) -----
WHO="$("$TL_CLI" auth whoami 2>&1)"
URL="$(printf '%s\n' "$WHO" | sed -n 's/^API Url:[[:space:]]*//p' | head -1)"
if [[ -z "$URL" ]]; then
  fail "Server endpoint" "could not read the API URL from '$TL_CLI auth whoami'"
  note "Authenticate first: $TL_CLI auth login"
  blocked
fi
HOST="$(printf '%s' "$URL" | sed -e 's#^[a-zA-Z][a-zA-Z0-9+.-]*://##' -e 's#[:/].*$##' | tr 'A-Z' 'a-z')"
case "$HOST" in
  localhost|127.0.0.1|::1|0.0.0.0)
    pass "Local server" "$URL" ;;
  *)
    fail "Local server" "detected a remote server ($URL)"
    note "This skill currently only runs on the same machine as the Tensorleap server."
    note "Please run it again from the server host."
    blocked ;;
esac

# 3 + 4. Server running + data volume (server info reports the LOCAL install) --
INFO="$("$TL_CLI" server info 2>&1)"
if printf '%s\n' "$INFO" | grep -qiE "no installation information|not running|cluster not found"; then
  fail "Server online" "the local server is not running (or not installed)"
  note "If Tensorleap is installed here, start it:  $TL_CLI server run"
  blocked
fi
pass "Server online" "reachable at $URL"

VOL="$(printf '%s\n' "$INFO" | awk '
  /datasetvolumes:/ {f=1; next}
  f && /^[[:space:]]*-[[:space:]]*/ { sub(/^[[:space:]]*-[[:space:]]*/,""); print; exit }
  f && /^[^[:space:]]/ { exit }
')"
if [[ -z "$VOL" ]]; then
  fail "Data volume" "no dataset volume is configured on this server"
  note "Contact Tensorleap to configure a dataset volume."
  blocked
fi
pass "Data volume" "$(printf '%s' "$VOL" | cut -d: -f1)"

# 5. Auth (setup — the skill guides login; not a hard blocker) ---------------
if printf '%s\n' "$WHO" | grep -q "^User email:"; then
  pass "Authenticated" "$(printf '%s\n' "$WHO" | sed -n 's/^User email:[[:space:]]*//p' | head -1)"
  echo
  echo "PREFLIGHT OK — platform prerequisites met. (Env + code_loader are set up next by the skill.)"
  exit 0
fi

fail "Authenticated" "not logged in to $URL"
note "The skill will guide login:  $TL_CLI auth login"
echo
echo "SETUP PENDING — authenticate, then re-run preflight."
exit 3
