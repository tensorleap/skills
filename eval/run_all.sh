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
#   --check               run the read-only preflight (check.sh) for the selection
#                         and exit — verifies tools/server/creds BEFORE anything runs
# Behaviour:
#   --force               re-run fixtures that already have a report (default: skip = resume)
#   --no-bootstrap        skip `prepare --bootstrap-poetry` (assume envs already built)
#   --no-check            skip the automatic preflight (escape hatch if check.sh is wrong)
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
"${EVAL_ROOT}/fetch_manifest.sh" || exit 1
REPORTS="${EVAL_ROOT}/reports"

SELECT="default"; FIXTURES_CSV=""; LIST_ONLY=0; FORCE=0; BOOTSTRAP=1
CHECK_ONLY=0; NO_CHECK=0
PASS_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)          SELECT="all"; shift ;;
    --fixtures)     SELECT="csv"; FIXTURES_CSV="$2"; shift 2 ;;
    --list)         LIST_ONLY=1; shift ;;
    --check)        CHECK_ONLY=1; shift ;;
    --no-check)     NO_CHECK=1; shift ;;
    --force)        FORCE=1; shift ;;
    --no-bootstrap) BOOTSTRAP=0; shift ;;
    --plugin-dir)   PASS_ARGS+=(--plugin-dir "$2"); shift 2 ;;
    --leap-cmd)     PASS_ARGS+=(--leap-cmd "$2"); shift 2 ;;
    # The header comment IS the help text — print it up to the first code line
    # rather than a hardcoded range that drifts every time the header changes.
    -h|--help)      awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"; exit 0 ;;
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
  log "(default = fixtures with no required DATA prerequisites. Use --all or"
  log " --fixtures to override; the preflight below drops anything unreachable.)"
fi

CSV_SEL="$(IFS=,; echo "${SELECTED[*]}")"
if [[ "${CHECK_ONLY}" -eq 1 ]]; then
  exec "${BASH_BIN}" "${EVAL_ROOT}/check.sh" --fixtures "${CSV_SEL}" ${PASS_ARGS[@]+"${PASS_ARGS[@]}"}
fi

mkdir -p "${REPORTS}"
declare -A RESULT   # id -> PASS/FAIL/STUCK/SKILL-UNUSED/PREREQ-FAIL/RUN-ERROR/VERIFY-FAIL/PREP-FAIL/SKIPPED

# --- Preflight: fail fast, and degrade to the runnable subset ---------------- #
# Every prerequisite gap used to be discovered at the most expensive moment:
# expired SSO after full clones (once per private fixture), a bad data root
# inside the evaluate pod, a concurrent evaluate by corrupting both runs.
# check.sh verifies everything up front; fixtures that only lack credentials
# are dropped (PREREQ-FAIL) instead of failing the whole run.
if [[ "${NO_CHECK}" -eq 0 ]]; then
  runnable="$("${BASH_BIN}" "${EVAL_ROOT}/check.sh" --fixtures "${CSV_SEL}" \
                --emit-runnable ${PASS_ARGS[@]+"${PASS_ARGS[@]}"})"
  case $? in
    0) log "preflight: all selected fixtures runnable" ;;
    3) readarray -t RUNNABLE <<<"${runnable}"
       for id in "${SELECTED[@]}"; do
         printf '%s\n' ${RUNNABLE[@]+"${RUNNABLE[@]}"} | grep -qxF "${id}" \
           || { RESULT[$id]="PREREQ-FAIL"; log "preflight: dropping ${id} (see check output above)"; }
       done ;;
    *) log "preflight BLOCKED — fix the [FAIL] items above (bash run_all.sh --check), or --no-check to override"
       exit 2 ;;
  esac
fi

# Armed only here, past --list/--help: those are read-only queries and must not
# reap a session someone is attached to.
trap kill_agent_sessions EXIT
trap 'echo; log "interrupted — releasing agent sessions"; exit 130' INT TERM HUP

for id in "${SELECTED[@]}"; do
  echo; log "======== ${id} ========"
  report="${REPORTS}/${id}.md"

  if [[ -n "${RESULT[$id]:-}" ]]; then
    log "dropped by preflight (${RESULT[$id]}) — not running"; continue
  fi
  if [[ -f "${report}" && "${FORCE}" -ne 1 ]]; then
    log "report exists — skipping (use --force to re-run)"
    RESULT[$id]="SKIPPED"; continue
  fi

  # --reset-only: restore an already-prepared fixture in seconds (env, LFS
  # blobs and staged data kept). prepare.sh itself falls back to a full
  # prepare when the fixture was never built here or its definition changed.
  prep_args=(--fixture "${id}" --reset-only)
  [[ "${BOOTSTRAP}" -eq 1 ]] && prep_args+=(--bootstrap-poetry)
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
    # The platform reached a verdict but zero assistant turns ran under the
    # skill (report.py checks the transcript's attribution stamps): the run
    # graded raw Claude, not the skill. Void — also out of the ratio.
    13) RESULT[$id]="SKILL-UNUSED" ;;
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
  # A skip, an unmet prerequisite, or a run the skill never took part in says
  # nothing about the skill — none belong in the ratio; counting them as
  # failures reads as a regression.
  if [[ "${r}" == "SKIPPED" || "${r}" == "PREREQ-FAIL" || "${r}" == "SKILL-UNUSED" ]]; then
    excluded=$((excluded+1)); continue
  fi
  total=$((total+1)); [[ "${r}" == "PASS" ]] && pass=$((pass+1))
done
echo "-------------------------------------------------"
echo "${pass}/${total} PASS   (reports in ${REPORTS}/)"
((excluded > 0)) && echo "${excluded} not evaluated (skipped or missing prerequisites)"

# The run report is a FILE, not this stdout summary: stdout dies with the terminal
# and cannot be diffed against the previous run. report.py --aggregate rolls the
# per-fixture json sidecars into reports/REPORT_V<n>.md (CREATE_REPORT structure:
# per-repo push/eval table, aggregates, shared problems, deltas vs V<n-1>).
csv=""
for id in "${SELECTED[@]}"; do csv+="${id}:${RESULT[$id]:-?},"; done
python3 "${EVAL_ROOT}/report.py" --aggregate --reports-dir "${REPORTS}" \
  --fixtures "${csv%,}" || log "WARNING: roll-up report failed (per-fixture reports are intact)"

[[ "${pass}" -eq "${total}" && "${total}" -gt 0 ]]
