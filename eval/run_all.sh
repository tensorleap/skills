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
declare -A RESULT   # id -> PASS/FAIL/STUCK/VERIFY-FAIL/PREP-FAIL/SKIPPED

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

  # run.sh writes reports/<id>.md and exits 0 only on PASS.
  "${BASH_BIN}" "${EVAL_ROOT}/run.sh" --fixture "${id}" "${PASS_ARGS[@]}"
  rc=$?
  if [[ -f "${report}" ]]; then
    RESULT[$id]="$(grep -oE 'PASS|FAIL|STUCK' "${report}" | head -1 || echo UNKNOWN)"
  else
    RESULT[$id]="$([[ ${rc} -eq 0 ]] && echo PASS || echo FAIL)"
  fi
  log "${id}: ${RESULT[$id]}"
done

# --- Aggregate summary ------------------------------------------------------ #
echo; echo "==================== SUMMARY ===================="
pass=0; total=0
printf '%-24s %s\n' "fixture" "result"
printf '%-24s %s\n' "-------" "------"
for id in "${SELECTED[@]}"; do
  r="${RESULT[$id]:-?}"
  printf '%-24s %s\n' "${id}" "${r}"
  [[ "${r}" == "SKIPPED" ]] && continue
  total=$((total+1)); [[ "${r}" == "PASS" ]] && pass=$((pass+1))
done
echo "-------------------------------------------------"
echo "${pass}/${total} PASS   (reports in ${REPORTS}/)"
[[ "${pass}" -eq "${total}" && "${total}" -gt 0 ]]
