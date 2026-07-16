#!/usr/bin/env bash
#
# Preflight gate for the Tensorleap integration loop.
#
# CHECK-ONLY. Detects the platform prerequisites, prints a PASS/FAIL table, and
# exits non-zero when something is wrong. It NEVER installs, authenticates,
# starts a server, or configures anything on the platform — it reports and
# stops. One exception: on Copilot/Cursor installs (skill folders that ship a
# VERSION file) step 0 may refresh the skill's OWN files — automatically for a
# global install, only with approval for a project-local one. Run it FIRST,
# before authoring (see SKILL.md "Preflight gate").
#
# It intentionally checks only prerequisites that do NOT need a Python
# environment, so it can run before the project env is provisioned. The Python
# environment and `code_loader` (install + version floor) are handled separately
# by the skill as a setup step — they require the env to exist, so they are not
# part of this gate.
#
# Server topology is decided from the API URL in `leap auth whoami` (the server
# the CLI actually talks to — a local install may exist yet the CLI point at a
# remote server; whoami wins), unless TL_TOPOLOGY forces it. Decision order:
#   1. TL_TOPOLOGY=local|remote set  -> use it verbatim (the skill sets this when
#      the user has already stated they work remotely, or after resolving an
#      ambiguous case below).
#   2. API URL host is NOT localhost (127.0.0.1/::1/0.0.0.0 count as local)
#      -> REMOTE.
#   3. Host IS localhost (any port) -> probe `server info` to disambiguate:
#      a. No server answers -> exit 6 AMBIGUOUS: with ANY local port this may be a
#         remote server reached via a port-forward (a forward can bind any local
#         port), or simply no server installed here. The skill asks whether it is
#         installed remotely; yes -> re-run TL_TOPOLOGY=remote, no -> install/start
#         the local server and STOP.
#      b. A server answers on a NON-4589 port -> exit 5 AMBIGUOUS: could be a local
#         server on a custom port, or a live remote port-forward. The skill asks,
#         then re-runs with TL_TOPOLOGY set.
#      c. A server answers on port 4589 (or unspecified) -> LOCAL.
# Then:
#   - LOCAL  -> full local checks (server online + data volume).
#   - REMOTE -> NOT a blocker. The local `server info` cannot describe a remote
#     host and the data volume cannot be inferred/verified from here, so the
#     script stops with exit 4 and the skill drives the remote flow (confirm
#     intent -> ask for the remote volume + remote `server info` -> creds via
#     `leap secrets`).
#
# Usage:   scripts/preflight.sh
# CLI:     default `leap`; override with TL_CLI (e.g. TL_CLI=leapdev)
# Topology override: TL_TOPOLOGY=local|remote (skips the URL-based decision)
#
# Exit codes:
#   0  all clear — LOCAL platform prerequisites met
#   2  BLOCKER (CLI missing / local server down / no data volume) —
#      relay the printed guidance to the user and STOP; do not fix it here
#   3  SETUP pending (not authenticated) — the skill guides login, then re-run
#   4  REMOTE server detected — NOT an error. The skill must confirm the user
#      wants to work remotely and then follow the remote data flow (see SKILL.md
#      "Preflight gate"). If the user does NOT want remote, they re-point the CLI
#      at the local server and re-run preflight.
#   5  AMBIGUOUS topology (localhost on a non-4589 port, and a server ANSWERS) —
#      NOT an error. Could be a local server on a custom port or a live remote
#      port-forward. The skill must ask the user which it is, then re-run with
#      TL_TOPOLOGY=local|remote.
#   6  NO local server answered at a DERIVED localhost URL (ANY port; `server info`
#      reports not running / not installed) — NOT an error. Ambiguous: this may be a
#      REMOTE server reached via a port-forward (which can bind any local port), or
#      simply no server installed here. The skill must ask whether the server is
#      installed REMOTELY. Yes -> re-run TL_TOPOLOGY=remote (remote flow). No -> the
#      local server must be installed/started; relay guidance and STOP.
#      (If TL_TOPOLOGY=local was forced, the explicit choice wins: no server is a
#      plain blocker -> exit 2, not this remote question.)
#
set -uo pipefail

TL_CLI="${TL_CLI:-leap}"

pass(){ printf '  [PASS] %-24s %s\n' "$1" "$2"; }
fail(){ printf '  [FAIL] %-24s %s\n' "$1" "$2"; }
note(){ printf '         -> %s\n' "$1"; }
blocked(){ echo; echo "BLOCKED — do not begin authoring. Resolve the item above and re-run preflight."; exit 2; }

# 0. Skill self-update — Copilot/Cursor installs only. The VERSION file ships
#    only in those skill folders, so Claude/AGENTS copies (no VERSION file)
#    skip this block entirely; as a second gate, the install path must match a
#    known Copilot/Cursor skills root (tool and scope are derived from it).
#    Global installs update automatically; a project-local install is never
#    changed without approval. Skips silently when offline. Set
#    TL_SKILL_NO_UPDATE=1 to opt out.
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TL_TOOL=""; TL_SCOPE=""
case "$SKILL_DIR" in
  "${HOME:-}/.copilot/skills/"*) TL_TOOL=copilot; TL_SCOPE=global ;;
  "${HOME:-}/.cursor/skills/"*)  TL_TOOL=cursor;  TL_SCOPE=global ;;
  */.github/skills/*)        TL_TOOL=copilot; TL_SCOPE=project ;;
  */.cursor/skills/*)        TL_TOOL=cursor;  TL_SCOPE=project ;;
esac
if [[ -n "$TL_TOOL" && -f "$SKILL_DIR/VERSION" && -z "${TL_SKILL_NO_UPDATE:-}" ]] && command -v curl >/dev/null 2>&1; then
  RAW_BASE="${TL_SKILLS_RAW_BASE:-https://raw.githubusercontent.com/tensorleap/skills/main}"
  LOCAL_V="$(cat "$SKILL_DIR/VERSION" 2>/dev/null)"
  REMOTE_V="$(curl -fsSL --max-time 5 "$RAW_BASE/dist/$TL_TOOL/$(basename "$SKILL_DIR")/VERSION" 2>/dev/null || true)"
  # Update only when both values look like real versions (a captive portal
  # answering 200/HTML must never trigger an install) AND the remote is
  # strictly newer (a branch/dev install ahead of main must not be downgraded).
  VER_RE='^[0-9]+\.[0-9]+\.[0-9]+$'
  UPDATE=""
  if [[ "$LOCAL_V" =~ $VER_RE && "$REMOTE_V" =~ $VER_RE && "$REMOTE_V" != "$LOCAL_V" ]]; then
    NEWEST="$(printf '%s\n%s\n' "$LOCAL_V" "$REMOTE_V" | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)"
    [[ "$NEWEST" == "$REMOTE_V" ]] && UPDATE=1
  fi
  # An install attempt may replace THIS running file, so after any attempt we
  # always exec a fresh copy — never read further from a possibly-stale offset.
  if [[ -n "$UPDATE" && "$TL_SCOPE" == global ]]; then
    echo "Tensorleap skill: updating v$LOCAL_V -> v$REMOTE_V (global install auto-updates) ..."
    curl -fsSL --max-time 120 "$RAW_BASE/install.sh" | sh -s -- --tool "$TL_TOOL" --global >/dev/null \
      || { echo "Tensorleap skill: update failed (a sandbox may block writes to \$HOME); continuing with the installed version."
           echo "  To update manually: curl -fsSL $RAW_BASE/install.sh | sh -s -- --tool $TL_TOOL --global"; }
    TL_SKILL_NO_UPDATE=1 exec "$0" "$@"
  elif [[ -n "$UPDATE" ]]; then   # project-local install
    PROJ_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"
    if [ -t 0 ]; then
      printf 'Tensorleap skill v%s is available (installed: v%s). Update the project-local copy? [y/N] ' "$REMOTE_V" "$LOCAL_V"
      ans=""; read -r -t 60 ans || ans=""
      if [[ "$ans" == [yY] ]]; then
        curl -fsSL --max-time 120 "$RAW_BASE/install.sh" | sh -s -- --tool "$TL_TOOL" "$PROJ_ROOT" >/dev/null \
          || echo "Tensorleap skill: update failed; continuing with the installed version"
        TL_SKILL_NO_UPDATE=1 exec "$0" "$@"
      fi
    else
      echo "NOTE: Tensorleap skill v$REMOTE_V is available (installed: v$LOCAL_V)."
      echo "      Project-local install: ASK THE USER for approval before updating. If approved, run:"
      echo "      curl -fsSL $RAW_BASE/install.sh | sh -s -- --tool $TL_TOOL \"$PROJ_ROOT\""
      echo "      then re-run preflight. If declined, continue with the installed version."
    fi
  fi
fi

echo "Tensorleap preflight (v1 — local server only)"

# 1. CLI on PATH -------------------------------------------------------------
if ! command -v "$TL_CLI" >/dev/null 2>&1; then
  fail "CLI present" "'$TL_CLI' not found on PATH"
  note "This skill runs on the Tensorleap server host, where the CLI ships with the install."
  note "If you are on that host and the CLI is missing, contact Tensorleap."
  blocked
fi
pass "CLI present" "$(command -v "$TL_CLI")"

# 2. Topology (the API URL from whoami decides which server the CLI talks to) --
#    A local install may exist yet the CLI point at a remote server — whoami wins.
WHO="$("$TL_CLI" auth whoami 2>&1)"
URL="$(printf '%s\n' "$WHO" | sed -n 's/^API Url:[[:space:]]*//p' | head -1)"
if [[ -z "$URL" ]]; then
  fail "Server endpoint" "could not read the API URL from '$TL_CLI auth whoami'"
  note "Authenticate first: $TL_CLI auth login"
  echo; echo "SETUP PENDING — authenticate, then re-run preflight."; exit 3
fi
AUTHED=0
printf '%s\n' "$WHO" | grep -q "^User email:" && AUTHED=1
NOSCHEME="$(printf '%s' "$URL" | sed -e 's#^[a-zA-Z][a-zA-Z0-9+.-]*://##' -e 's#/.*$##')"  # host[:port]
HOST="$(printf '%s' "$NOSCHEME" | sed 's#:.*$##' | tr 'A-Z' 'a-z')"
PORT="$(printf '%s' "$NOSCHEME" | sed -n 's#^[^:]*:##p')"                                    # empty if no port

# Decide topology (see header). TL_TOPOLOGY forces it; otherwise derive from URL.
FORCED="${TL_TOPOLOGY:-}"
if [[ -n "$FORCED" && "$FORCED" != "local" && "$FORCED" != "remote" ]]; then
  fail "Server topology" "TL_TOPOLOGY='$FORCED' is invalid (use local|remote)"
  blocked
fi
case "$HOST" in
  localhost|127.0.0.1|::1|0.0.0.0) IS_LOCALHOST=1 ;;
  *) IS_LOCALHOST=0 ;;
esac

# REMOTE — forced remote, or (no override) a non-localhost host. Not a blocker;
# local `server info` cannot describe a remote server, so the skill drives the flow.
if [[ "$FORCED" == "remote" || ( -z "$FORCED" && "$IS_LOCALHOST" -eq 0 ) ]]; then
  pass "Server topology" "remote server ($URL)"
  if [[ "$AUTHED" -ne 1 ]]; then
    fail "Authenticated" "not logged in to $URL"
    note "Authenticate first: $TL_CLI auth login, then re-run preflight."
    echo; echo "SETUP PENDING — authenticate, then re-run preflight."; exit 3
  fi
  pass "Authenticated" "$(printf '%s\n' "$WHO" | sed -n 's/^User email:[[:space:]]*//p' | head -1)"
  note "Local 'server info' describes only a LOCAL install, so it cannot see this"
  note "remote server's data volume — data presence cannot be verified from here."
  echo
  echo "REMOTE SERVER DETECTED — confirm intent before authoring. The skill must:"
  echo "  1. Ask whether you want to work remotely (you may have BOTH a local and a"
  echo "     remote server; the CLI is currently pointed at the remote one)."
  echo "     - If NOT remote: re-point the CLI at the local server (reconfigure"
  echo "       apiUrl / re-auth), then re-run preflight."
  echo "  2. If remote: ask for the remote data volume path, and ask you to run"
  echo "     '$TL_CLI server info' ON THE REMOTE HOST and paste its datasetvolumes."
  echo "  3. For a remote data store, register credentials via '$TL_CLI secrets'"
  echo "     (they become env vars on the platform); the local test uses the same"
  echo "     env vars set in your local shell."
  exit 4
fi

# LOCAL-ish (forced local, or a localhost URL). The real test is whether a local
# server actually answers — probe it before committing to the local flow.
# --- 3 + 4. Server running + data volume (server info reports the LOCAL install) --
INFO="$("$TL_CLI" server info 2>&1)"
if printf '%s\n' "$INFO" | grep -qiE "no installation information|not running|cluster not found"; then
  if [[ "$FORCED" == "local" ]]; then
    # The user explicitly said LOCAL — honor it. No server responding is then a
    # plain blocker (install/start the local server), NOT a remote question.
    fail "Server online" "topology forced local, but no local server responded at $URL"
    note "Install/start the local server:  $TL_CLI server run"
    blocked
  fi
  # DERIVED localhost + no server answering — AMBIGUOUS regardless of port. It may
  # be a REMOTE server reached via a port-forward (a forward can bind ANY local
  # port), or simply no server installed here. Do NOT block outright; the skill asks.
  fail "Server online" "no local server responded at $URL (not running or not installed)"
  echo
  echo "NO LOCAL SERVER — the URL is local but nothing answered. This may be a REMOTE"
  echo "server reached via a port-forward (which can be on ANY local port, not just 4589),"
  echo "or no server installed here. The skill must:"
  echo "  1. Ask whether the Tensorleap server is installed REMOTELY."
  echo "     - Yes: ensure the CLI points at a REACHABLE remote endpoint (the remote URL,"
  echo "            or a live port-forward on whatever port), then re-run as remote:"
  echo "            TL_TOPOLOGY=remote $0   (then follow the remote flow)."
  echo "     - No:  the local server must be installed/started -> $TL_CLI server run; then STOP."
  exit 6
fi

# A server answered. If topology was DERIVED (no override) from a localhost URL on
# a NON-4589 port, it is still ambiguous whether this is a local server on a custom
# port or a live remote port-forward — the skill must ask.
if [[ -z "$FORCED" && "$IS_LOCALHOST" -eq 1 && -n "$PORT" && "$PORT" != "4589" ]]; then
  pass "Server topology" "localhost:$PORT — AMBIGUOUS (local custom port or live remote port-forward?)"
  echo
  echo "AMBIGUOUS TOPOLOGY — a server answers on localhost:$PORT, not the default"
  echo "local-server port 4589. This may be a local server on a custom port, or a"
  echo "REMOTE server reached via a live port-forward. The skill must:"
  echo "  1. Ask the user which it is."
  echo "  2. Re-run this gate with the answer:"
  echo "     - local:  TL_TOPOLOGY=local  $0"
  echo "     - remote: TL_TOPOLOGY=remote $0"
  exit 5
fi

# LOCAL server confirmed (localhost:4589 / unspecified, or forced local).
pass "Server topology" "local server ($URL)"
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
