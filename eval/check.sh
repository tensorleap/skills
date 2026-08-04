#!/usr/bin/env bash
# check.sh — read-only preflight: verify every prerequisite BEFORE anything
# clones, stages, or spends an agent session. Late discovery is the expensive
# failure mode this replaces: expired AWS SSO used to surface only after full
# clones (once per private fixture), a missing data volume only inside the
# evaluate pod (a full agent session), a concurrent evaluate only by
# corrupting both runs.
#
# Global blockers (broken tools/server/skill setup) fail the whole run.
# Missing credentials only DROP the fixtures that need them — the rest stay
# runnable, so a dev without private-repo access can still run the public set.
#
# Usage: check.sh --fixtures a,b,c [--plugin-dir DIR] [--leap-cmd CMD] [--emit-runnable]
#   --fixtures a,b,c   the selection to vet (run_all.sh passes its SELECTED list)
#   --emit-runnable    machine mode for run_all.sh: table -> stderr, runnable
#                      fixture ids -> stdout (one per line)
# Exit codes: 0 all runnable | 3 some fixtures dropped | 2 global blocker
set -uo pipefail

# macOS ships bash 3.2; these scripts use bash-4 features. Re-exec under bash 4+.
if [ "${BASH_VERSINFO:-0}" -lt 4 ]; then
  for _b in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    [ -x "$_b" ] && exec "$_b" "$0" "$@"
  done
  echo "This script needs bash 4+ (macOS ships 3.2). Install: brew install bash" >&2; exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EVAL_ROOT="${SCRIPT_DIR}"
MANIFEST="${EVAL_ROOT}/manifest.json"
"${EVAL_ROOT}/fetch_manifest.sh" || exit 1
EVAL_DATA_ROOT="${EVAL_DATA_ROOT:-${HOME}/tensorleap/data/eval}"
AWS_PROFILE="${AWS_PROFILE:-dev}"

FIXTURES_CSV=""; PLUGIN_DIR=""; LEAP_CMD=""; EMIT=0; OUT=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fixtures)      FIXTURES_CSV="$2"; shift 2 ;;
    --plugin-dir)    PLUGIN_DIR="$2"; shift 2 ;;
    --leap-cmd)      LEAP_CMD="$2"; shift 2 ;;
    --emit-runnable) EMIT=1; OUT=2; shift ;;
    -h|--help)       awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[[ -n "${FIXTURES_CSV}" ]] || { echo "error: --fixtures is required (see --help)" >&2; exit 2; }
IFS=',' read -r -a SELECTED <<<"${FIXTURES_CSV}"

ok()   { printf '  [PASS] %-16s %s\n' "$1" "$2" >&"${OUT}"; }
bad()  { printf '  [FAIL] %-16s %s\n' "$1" "$2" >&"${OUT}"; GLOBAL_FAIL=1; }
wrn()  { printf '  [WARN] %-16s %s\n' "$1" "$2" >&"${OUT}"; }
hint() { printf '         -> %s\n' "$1" >&"${OUT}"; }

GLOBAL_FAIL=0
# `=()` matters: bash 5.3 + set -u treats a declared-but-uninitialized assoc
# array as unbound on ${#DROP[@]}.
declare -A DROP=()   # fixture id -> reason it cannot run with what we have now

echo "Skill-eval preflight — nothing is cloned, staged, or run by this check" >&"${OUT}"

# --- 1. Tools (global) ------------------------------------------------------ #
missing=()
for c in tmux jq rg git git-lfs poetry pyenv python3 claude; do
  command -v "$c" >/dev/null 2>&1 || missing+=("$c")
done
if ((${#missing[@]})); then
  bad "tools" "missing: ${missing[*]}"
  hint "brew install ${missing[*]} (claude: https://claude.com/claude-code)"
else
  ok "tools" "tmux jq rg git git-lfs poetry pyenv python3 claude"
fi

# --- 2. Tensorleap CLI: present, local target, authenticated (global) -------- #
if [[ -z "${LEAP_CMD}" ]]; then
  for _c in leapdev leap; do
    command -v "${_c}" >/dev/null 2>&1 && { LEAP_CMD="${_c}"; break; }
  done
fi
LEAP_BIN="$(command -v "${LEAP_CMD:-leap}" 2>/dev/null || true)"
if [[ -z "${LEAP_BIN}" ]]; then
  bad "leap CLI" "no Tensorleap CLI found (looked for leapdev, leap)"
  hint "install the CLI or pass --leap-cmd"
else
  api_url="$(python3 "${EVAL_ROOT}/lib/leap_api_url.py" 2>&1)"
  if [[ $? -ne 0 ]]; then
    bad "leap target" "${api_url}"
    hint "leap auth login"
  elif [[ "${EVAL_ALLOW_REMOTE_LEAP:-0}" != "1" ]]; then
    case "${api_url}" in
      *//localhost[:/]*|*//127.0.0.1[:/]*|*//localhost|*//127.0.0.1)
        ok "leap target" "${LEAP_BIN} -> ${api_url} (local)" ;;
      *)
        bad "leap target" "${api_url} is not local — a blind eval must not push to a shared server"
        hint "leap auth select   (or EVAL_ALLOW_REMOTE_LEAP=1 to run remote deliberately)" ;;
    esac
  else
    ok "leap target" "${api_url} (remote allowed by EVAL_ALLOW_REMOTE_LEAP=1)"
  fi

  if "${LEAP_BIN}" run list -t Evaluate </dev/null >/dev/null 2>&1; then
    ok "leap auth" "run list works — server reachable, CLI authenticated"
    # --- 3. No job already in flight (global) -------------------------------- #
    # One push/evaluate at a time is rule 4: a concurrent job corrupts both runs.
    inflight="$({ "${LEAP_BIN}" run list -t Evaluate 2>/dev/null; "${LEAP_BIN}" run list -t Push 2>/dev/null; } \
      | awk '$NF ~ /^[0-9a-f]{24}$/ && $(NF-1) !~ /^(FINISHED|FAILED|STOPPED|TERMINATED)$/ {print $(NF-1), $NF}' | head -3)"
    if [[ -n "${inflight}" ]]; then
      bad "no active job" "a Push/Evaluate is already running: ${inflight}"
      hint "wait for it to reach a terminal state (leap run list) — or kill it — before starting a run"
    else
      ok "no active job" "no non-terminal Push/Evaluate on the server"
    fi
    # --- 4. EVAL_DATA_ROOT inside a Tensorleap dataset volume (global) ------- #
    # Encoders read staged data from inside the evaluate pod; data outside a
    # mounted volume fails there, mid-session, where the agent can't diagnose it.
    if [[ "${EVAL_ALLOW_REMOTE_LEAP:-0}" == "1" ]]; then
      wrn "data volume" "remote server — cannot verify EVAL_DATA_ROOT containment from here"
    else
      # `server info` logs to stderr, host paths are the left side of `- host:pod`.
      readarray -t VOLS < <("${LEAP_BIN}" server info 2>&1 \
        | sed $'s/\x1b\\[[0-9;]*m//g' \
        | awk '/datasetvolumes:/{f=1} f && match($0, /- *\/[^:]+/) {s=substr($0, RSTART, RLENGTH); sub(/^- */,"",s); print s} f && /^[a-z]/ && !/datasetvolumes:/{f=0}')
      contained=0
      for v in ${VOLS[@]+"${VOLS[@]}"}; do
        [[ "${EVAL_DATA_ROOT}/" == "${v}/"* ]] && contained=1
      done
      if ((${#VOLS[@]} == 0)); then
        wrn "data volume" "could not parse datasetvolumes from 'server info' — containment unverified"
      elif ((contained)); then
        ok "data volume" "EVAL_DATA_ROOT=${EVAL_DATA_ROOT} is inside ${VOLS[0]}"
      else
        bad "data volume" "EVAL_DATA_ROOT=${EVAL_DATA_ROOT} is OUTSIDE the dataset volume(s): ${VOLS[*]}"
        hint "export EVAL_DATA_ROOT=<inside a volume> — staged data outside it fails inside the evaluate pod"
      fi
    fi
  else
    bad "leap auth" "'${LEAP_BIN} run list' failed — server unreachable or not authenticated"
    hint "${LEAP_CMD:-leap} auth login   (and check the server is up: ${LEAP_CMD:-leap} server info)"
  fi
fi

# --- 5. Exactly one source of the skill under test (global) ------------------ #
readarray -t SOURCES < <(python3 "${EVAL_ROOT}/lib/skill_sources.py" \
  ${PLUGIN_DIR:+--plugin-dir "${PLUGIN_DIR}"})
case ${#SOURCES[@]} in
  1) ok "skill source" "${SOURCES[0]}" ;;
  0) bad "skill source" "the integration skill is not available"
     hint "claude plugin install integration@tensorleap — or pass --plugin-dir dist/claude/integration" ;;
  *) bad "skill source" "${#SOURCES[@]} sources would shadow each other: ${SOURCES[*]}"
     hint "keep exactly one (disable the plugin or remove the local copy — see run.sh's message)" ;;
esac

# --- 6. Disk (warn only) ------------------------------------------------------ #
df_dir="${EVAL_DATA_ROOT}"
while [[ ! -d "${df_dir}" && "${df_dir}" != "/" ]]; do df_dir="$(dirname "${df_dir}")"; done
free_gb="$(df -Pk "${df_dir}" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}')"
if [[ -n "${free_gb}" && "${free_gb}" -lt 20 ]]; then
  wrn "disk" "${free_gb} GB free at ${df_dir} — fixtures stage models (1 GB+) and build poetry envs"
else
  ok "disk" "${free_gb:-?} GB free at ${df_dir}"
fi

# --- 7. Per-fixture: credentials the selection actually needs ---------------- #
# manifest facts: id, needs-aws-binary, needs-aws-credentials, repo url
readarray -t NEEDS < <(python3 - "${MANIFEST}" "${FIXTURES_CSV}" <<'PY'
import json, sys
want = [x for x in sys.argv[2].split(",") if x]
fx = {f["id"]: f for f in json.load(open(sys.argv[1]))["fixtures"]}
for fid in want:
    f = fx.get(fid)
    if f is None:
        print(f"{fid}\tUNKNOWN\t0\t")
        continue
    entries = [e for p in (f.get("runtime_prerequisites") or [])
               for e in (p.get("s3_files") or []) + (p.get("s3_prefixes") or [])]
    needs_aws = int(bool(entries))
    needs_creds = int(any(not e.get("no_sign_request") for e in entries))
    print(f"{fid}\t{needs_aws}\t{needs_creds}\t{f.get('repo', '')}")
PY
)

aws_ok=""; aws_creds_ok=""
git_auth=""   # empty = probe each repo with ambient git (mirrors prepare.sh's fallback)
if [[ -n "${TENSORLEAP_HUB_FIXTURE_TOKEN:-}${TENSORLEAP_HUB_READ_TOKEN:-}" ]]; then
  git_auth="token env var"
elif [[ -n "${TENSORLEAP_HUB_GIT_CREDENTIAL_HELPER:-}" || -n "$(command -v github-app-git-credential 2>/dev/null)" ]]; then
  git_auth="credential helper"
elif command -v gh >/dev/null 2>&1 && gh auth token >/dev/null 2>&1; then
  git_auth="gh CLI"
fi
[[ -n "${git_auth}" ]] && ok "github auth" "${git_auth} (private Tensorleap-hub clones will authenticate)"

for line in ${NEEDS[@]+"${NEEDS[@]}"}; do
  IFS=$'\t' read -r fid needs_aws needs_creds repo <<<"${line}"
  if [[ "${needs_aws}" == "UNKNOWN" ]]; then
    DROP["${fid}"]="not in manifest.json"
    continue
  fi
  # AWS binary / credentials, resolved lazily and cached across fixtures.
  if [[ "${needs_aws}" == "1" ]]; then
    if [[ -z "${aws_ok}" ]]; then
      command -v aws >/dev/null 2>&1 && aws_ok=yes || aws_ok=no
      [[ "${aws_ok}" == "no" ]] && { wrn "aws" "aws CLI not installed"; hint "brew install awscli"; }
    fi
    [[ "${aws_ok}" == "no" ]] && { DROP["${fid}"]="needs the aws CLI to stage data"; continue; }
  fi
  if [[ "${needs_creds}" == "1" ]]; then
    if [[ -z "${aws_creds_ok}" ]]; then
      if aws sts get-caller-identity --profile "${AWS_PROFILE}" >/dev/null 2>&1; then
        aws_creds_ok=yes; ok "aws creds" "profile '${AWS_PROFILE}' is valid"
      else
        aws_creds_ok=no
        wrn "aws creds" "profile '${AWS_PROFILE}' has no valid credentials"
        hint "aws sso login --profile ${AWS_PROFILE}"
      fi
    fi
    [[ "${aws_creds_ok}" == "no" ]] && { DROP["${fid}"]="needs AWS creds (profile ${AWS_PROFILE}) for private S3 data"; continue; }
  fi
  # Repo reachability: with resolvable auth assume clonable; otherwise probe the
  # way prepare.sh would clone — ambient git config, no interactive prompt.
  if [[ -z "${git_auth}" && -n "${repo}" ]]; then
    if ! GIT_TERMINAL_PROMPT=0 git ls-remote --quiet "${repo}" HEAD >/dev/null 2>&1; then
      DROP["${fid}"]="repo not readable without auth: ${repo}"
      repo_probe_failed=1
    fi
  fi
done
if [[ -z "${git_auth}" ]]; then
  if [[ -n "${repo_probe_failed:-}" ]]; then
    wrn "github auth" "no token/helper/gh auth — private Tensorleap-hub repos are unreachable"
    hint "gh auth login"
  else
    ok "github auth" "no explicit auth, but every selected repo is readable as-is"
  fi
fi

# --- Verdict ------------------------------------------------------------------ #
echo >&"${OUT}"
if ((GLOBAL_FAIL)); then
  echo "BLOCKED — fix the [FAIL] items above; nothing was cloned or staged." >&"${OUT}"
  exit 2
fi
RUNNABLE=()
for fid in ${SELECTED[@]+"${SELECTED[@]}"}; do
  if [[ -n "${DROP[${fid}]+x}" ]]; then
    echo "DROPPED  ${fid} — ${DROP[${fid}]}" >&"${OUT}"
  else
    RUNNABLE+=("${fid}")
  fi
done
if ((${#DROP[@]} > 0)); then
  echo "Runnable with what you have now: ${RUNNABLE[*]:-none}" >&"${OUT}"
  ((EMIT)) && printf '%s\n' ${RUNNABLE[@]+"${RUNNABLE[@]}"}
  exit 3
fi
echo "All ${#RUNNABLE[@]} selected fixture(s) runnable." >&"${OUT}"
((EMIT)) && printf '%s\n' ${RUNNABLE[@]+"${RUNNABLE[@]}"}
exit 0
