#!/usr/bin/env bash
# run_all.sh — the regression run: prepare -> verify -> run every selected
# fixture, STRICTLY sequentially (the server handles only one push/evaluate at a
# time), then print an aggregate summary. One failure never aborts the rest.
#
# Selection (default = the "no extra creds" set: fixtures with no REQUIRED
# runtime_prerequisites, e.g. cifar10 — the ones any dev can run):
#   --all                 every fixture in the manifest (private ones need creds/data)
#   --fixtures a,b,c      explicit subset
#   --list                print the selection and exit (dry run)
# Behaviour:
#   --force               re-run fixtures that already have a report (default: skip = resume)
#   --no-bootstrap        skip `prepare --bootstrap-poetry` (assume envs already built)
#   --plugin-dir DIR      forwarded to run.sh (test a local skill build)
#   --leap-cmd CMD        forwarded to run.sh (default leapdev)
set -uo pipefail   # deliberately NOT -e: keep going past a failed fixture

# macOS ships bash 3.2, but these scripts use bash-4 features (readarray, etc.).
# Re-exec under a newer bash if we were launched with an old one.
if [ "${BASH_VERSINFO:-0}" -lt 4 ]; then
  for _b in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    [ -x "$_b" ] && exec "$_b" "$0" "$@"
  done
  echo "This script needs bash 4+ (macOS ships 3.2). Install: brew install bash" >&2; exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EVAL_ROOT="${SCRIPT_DIR}"
BASH_BIN="${BASH:-/opt/homebrew/bin/bash}"; [[ -x "${BASH_BIN}" ]] || BASH_BIN="$(command -v bash)"
MANIFEST="${EVAL_ROOT}/manifest.json"
REPORTS="${EVAL_ROOT}/reports"

SELECT="default"; FIXTURES_CSV=""; LIST_ONLY=0; FORCE=0; BOOTSTRAP=1
PASS_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)          SELECT="all"; shift ;;
    --fixtures)     SELECT="csv"; FIXTURES_CSV="$2"; shift 2 ;;
    --list)         LIST_ONLY=1; shift ;;
    --force)        FORCE=1; shift ;;
    --no-bootstrap) BOOTSTRAP=0; shift ;;
    --plugin-dir)   PASS_ARGS+=(--plugin-dir "$2"); shift 2 ;;
    --leap-cmd)     PASS_ARGS+=(--leap-cmd "$2"); shift 2 ;;
    -h|--help)      sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

log() { echo "[run_all] $*"; }

# run.sh releases its own session, but only if it is still alive to do so: a
# SIGKILL, a crash, or a terminal closing leaves `op-<id>` running with a live
# agent spending tokens and nothing tracking it. Sweep on every exit path.
# Scoped to `op-<manifest id>` names so this can never touch an unrelated session
# of the user's, and covering ALL manifest ids (not just the selection) so strays
# from an earlier aborted run get collected too.
kill_agent_sessions() {
  local id killed=0
  for id in $(python3 -c 'import json,sys; print("\n".join(f["id"] for f in json.load(open(sys.argv[1]))["fixtures"]))' "${MANIFEST}"); do
    tmux has-session -t "op-${id}" 2>/dev/null || continue
    tmux kill-session -t "op-${id}" 2>/dev/null && killed=$((killed+1))
  done
  ((killed > 0)) && log "released ${killed} agent tmux session(s)"
  return 0
}

# --- Which fixtures? -------------------------------------------------------- #
readarray -t SELECTED < <(
  python3 - "${MANIFEST}" "${SELECT}" "${FIXTURES_CSV}" <<'PY'
import json, sys
manifest, select, csv = sys.argv[1], sys.argv[2], sys.argv[3]
fx = json.load(open(manifest))["fixtures"]
def needs_creds(f):
    return any(p.get("required") for p in (f.get("runtime_prerequisites") or []))
if select == "csv":
    want = [x.strip() for x in csv.split(",") if x.strip()]
    ids = [f["id"] for f in fx if f["id"] in want]
elif select == "all":
    ids = [f["id"] for f in fx]
else:  # default: no required runtime prerequisites
    ids = [f["id"] for f in fx if not needs_creds(f)]
print("\n".join(ids))
PY
)

if [[ "${LIST_ONLY}" -eq 1 ]]; then
  echo "Available fixtures (from manifest.json):"
  python3 - "${MANIFEST}" <<'PY'
import json, sys
for f in json.load(open(sys.argv[1]))["fixtures"]:
    needs = any(p.get("required") for p in (f.get("runtime_prerequisites") or []))
    tag = "needs staged data (creds required)" if needs else "no data prereqs — in default set"
    print(f"  {f['id']:<24} {tag}")
PY
  echo
  echo "Run:  bash run_all.sh                    # the default set (above, 'in default set')"
  echo "      bash run_all.sh --all              # every fixture"
  echo "      bash run_all.sh --fixtures a,b,c   # a specific subset by id"
  echo "      bash run.sh --fixture <id>         # a single fixture"
  exit 0
fi

[[ ${#SELECTED[@]} -gt 0 ]] || { echo "no fixtures selected (see: run_all.sh --list)" >&2; exit 1; }
log "Selected ${#SELECTED[@]} fixture(s): ${SELECTED[*]}"
if [[ "${SELECT}" == "default" ]]; then
  log "(default = fixtures with no required DATA prerequisites; note some are still"
  log " private repos that need Tensorleap-hub access to clone — they PREP-FAIL"
  log " without it and the run continues. Use --all or --fixtures to override.)"
fi

mkdir -p "${REPORTS}"
declare -A RESULT   # id -> PASS/FAIL/STUCK/PREREQ-FAIL/RUN-ERROR/VERIFY-FAIL/PREP-FAIL/SKIPPED

# Armed only here, past --list/--help: those are read-only queries and must not
# reap a session someone is attached to.
trap kill_agent_sessions EXIT
trap 'echo; log "interrupted — releasing agent sessions"; exit 130' INT TERM HUP

for id in "${SELECTED[@]}"; do
  echo; log "======== ${id} ========"
  report="${REPORTS}/${id}.md"

  if [[ -f "${report}" && "${FORCE}" -ne 1 ]]; then
    log "report exists — skipping (use --force to re-run)"
    RESULT[$id]="SKIPPED"; continue
  fi

  prep_args=(--fixture "${id}"); [[ "${BOOTSTRAP}" -eq 1 ]] && prep_args+=(--bootstrap-poetry)
  if ! "${BASH_BIN}" "${EVAL_ROOT}/prepare.sh" "${prep_args[@]}"; then
    log "prepare FAILED"; RESULT[$id]="PREP-FAIL"; continue
  fi
  if ! "${BASH_BIN}" "${EVAL_ROOT}/verify.sh" --fixture "${id}"; then
    log "verify FAILED (fixture not blind/clean) — not running the agent"; RESULT[$id]="VERIFY-FAIL"; continue
  fi

  # run.sh's exit code is the verdict; reports/<id>.md is for humans. Anything
  # outside the three verdict codes is the harness failing (no tmux, no skill
  # source, …) — surfaced as RUN-ERROR, never as a fixture verdict.
  "${BASH_BIN}" "${EVAL_ROOT}/run.sh" --fixture "${id}" "${PASS_ARGS[@]}"
  case $? in
    0)  RESULT[$id]="PASS" ;;
    10) RESULT[$id]="FAIL" ;;
    11) RESULT[$id]="STUCK" ;;
    # A required data prerequisite was missing, so the agent never ran. An
    # environment gap, never a skill verdict — kept out of the PASS/total ratio.
    12) RESULT[$id]="PREREQ-FAIL" ;;
    *)  RESULT[$id]="RUN-ERROR" ;;
  esac
  log "${id}: ${RESULT[$id]}"
done

# --- Aggregate summary ------------------------------------------------------ #
echo; echo "==================== SUMMARY ===================="
pass=0; total=0; excluded=0
printf '%-24s %s\n' "fixture" "result"
printf '%-24s %s\n' "-------" "------"
for id in "${SELECTED[@]}"; do
  r="${RESULT[$id]:-?}"
  printf '%-24s %s\n' "${id}" "${r}"
  # Neither a skip nor an unmet prerequisite says anything about the skill, so
  # neither belongs in the ratio — counting them as failures reads as a regression.
  if [[ "${r}" == "SKIPPED" || "${r}" == "PREREQ-FAIL" ]]; then
    excluded=$((excluded+1)); continue
  fi
  total=$((total+1)); [[ "${r}" == "PASS" ]] && pass=$((pass+1))
done
echo "-------------------------------------------------"
echo "${pass}/${total} PASS   (reports in ${REPORTS}/)"
((excluded > 0)) && echo "${excluded} not evaluated (skipped or missing prerequisites)"
[[ "${pass}" -eq "${total}" && "${total}" -gt 0 ]]
