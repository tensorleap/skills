#!/usr/bin/env bash
# run.sh — drive an interactive Claude session (via tmux) to run the
# integration skill against ONE prepared blind fixture, then track the Evaluate
# to a terminal state.
#
# This enforces in code the setup that a blind eval needs (see README.md):
#   - CLAUDE_CONFIG_DIR is UNSET for the agent (an empty string hides the plugin)
#   - `leap` is shimmed to a dev CLI, first on PATH (so a bare `leap` never
#     reaches a production server)
#   - exactly one source of the integration skill is present (no local copy
#     shadowing the plugin, which would silently test the wrong skill)
#   - interactive session (no -p) so the push isn't reaped before its evaluate
#
# Prereqs: tmux, a reachable Tensorleap dev server + non-interactive auth, and a
# prepared+verified fixture (run prepare.sh + verify.sh first).
set -euo pipefail

# macOS ships bash 3.2; these scripts use bash-4 features. Re-exec under bash 4+.
if [ "${BASH_VERSINFO:-0}" -lt 4 ]; then
  for _b in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    [ -x "$_b" ] && exec "$_b" "$0" "$@"
  done
  echo "This script needs bash 4+ (macOS ships 3.2). Install: brew install bash" >&2; exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EVAL_ROOT="${SCRIPT_DIR}"
"${EVAL_ROOT}/fetch_manifest.sh"
FIXTURES_ROOT="${EVAL_ROOT}/.fixtures"

FIXTURE=""
PLUGIN_DIR=""
LEAP_CMD=""            # default resolved below: leapdev if installed, else leap
IDLE_STUCK_TICKS="${IDLE_STUCK_TICKS:-40}"   # ~40 * 30s = 20 min of no pane change & no eval
POLL_SECS="${POLL_SECS:-30}"
# Two independent clocks, because the two things they bound cost different money.
# The agent session is unbounded by construction (nothing ever types into the pane
# again, so an agent that yields with a question waits forever) and burns tokens
# while alive, so it gets a hard lifetime. The Evaluate is a server-side job that
# costs nothing to wait on, so it is timed from its own start and outlives the
# agent — a slow evaluate is never charged for the time the agent spent authoring.
AGENT_MAX_SECS="${AGENT_MAX_SECS:-3600}"     # max agent session lifetime, from prompt submit (1h)
EVAL_MAX_SECS="${EVAL_MAX_SECS:-7200}"       # max wait for a detected Evaluate to go terminal (2h)

usage() {
  cat <<'EOF'
Usage: run.sh --fixture <id> [--plugin-dir <dir>] [--leap-cmd <cmd>]

  --fixture ID       Fixture id from manifest.json (must be prepared + verified).
  --plugin-dir DIR   Run the skill from a built dist dir (e.g. dist/claude/integration)
                     instead of the installed marketplace plugin. Optional.
  --leap-cmd CMD     The Tensorleap CLI the `leap` shim forwards to. Default:
                     `leapdev` if installed, otherwise `leap`.

Whichever CLI is used, its configured api_url must be local — a blind eval must
never touch a shared or production server. Set EVAL_ALLOW_REMOTE_LEAP=1 to run
against a remote deliberately.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fixture)    FIXTURE="$2"; shift 2 ;;
    --plugin-dir) PLUGIN_DIR="$2"; shift 2 ;;
    --leap-cmd)   LEAP_CMD="$2"; shift 2 ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done
[[ -n "${FIXTURE}" ]] || { echo "error: --fixture is required" >&2; usage; exit 2; }

fail() { echo "error: $*" >&2; exit 1; }
log()  { echo "[run] $*"; }

# --- Every attempt leaves a log; only THIS attempt's report survives --------- #
# The log: run_all's stdout dies with the terminal, which left V5's asensus
# RUN-ERROR (2h of agent time) with zero retained diagnostics. Tee everything.
# The invalidation: reports/<id>.{md,json} describe the LAST attempt only.
# Deleted up front — not in an exit trap, which a SIGKILL never runs — so an
# attempt that dies leaves a log and NO report, instead of a stale PASS from an
# earlier run (which run_all's skip-if-report-exists resume would then trust).
REPORTS_DIR="${EVAL_ROOT}/reports"
RUN_LOG="${REPORTS_DIR}/${FIXTURE}.run.log"
mkdir -p "${REPORTS_DIR}"
rm -f "${REPORTS_DIR}/${FIXTURE}.md" "${REPORTS_DIR}/${FIXTURE}.json"
: >"${RUN_LOG}"
exec > >(tee -a "${RUN_LOG}") 2>&1
log "run.sh --fixture ${FIXTURE} — $(date '+%Y-%m-%dT%H:%M:%S') (log: ${RUN_LOG})"

require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "required command '$1' not found"; }
require_cmd tmux
command -v claude >/dev/null 2>&1 || fail "claude CLI not found on PATH"

# `leapdev` is the convention on a Tensorleap dev box, but plenty of machines only
# have `leap` pointed at a local install — resolve rather than hard-fail on a name.
if [[ -z "${LEAP_CMD}" ]]; then
  for _c in leapdev leap; do
    command -v "${_c}" >/dev/null 2>&1 && { LEAP_CMD="${_c}"; break; }
  done
  [[ -n "${LEAP_CMD}" ]] || fail "no Tensorleap CLI found (looked for 'leapdev' then 'leap'); pass --leap-cmd"
fi
require_cmd "${LEAP_CMD}"
# Absolute path, always: the shim below is named `leap` and sits FIRST on RUN_PATH,
# so a shim body of `exec leap` (the case when no `leapdev` exists) re-resolves to
# the shim itself and exec-loops forever at 100% CPU with no output.
LEAP_CMD="$(command -v "${LEAP_CMD}")"

# What matters is not the command's NAME but the server it talks to: `leap auth
# select` can repoint a CLI at a shared/prod environment at any time, and a blind
# eval pushing there would be both wrong and visible to others. Assert the target.
LEAP_API_URL="$(python3 "${EVAL_ROOT}/lib/leap_api_url.py")" \
  || fail "could not determine which server '${LEAP_CMD}' targets (see above). Set EVAL_ALLOW_REMOTE_LEAP=1 to skip this check."
if [[ "${EVAL_ALLOW_REMOTE_LEAP:-0}" != "1" ]]; then
  case "${LEAP_API_URL}" in
    *//localhost[:/]*|*//127.0.0.1[:/]*|*//localhost|*//127.0.0.1)
      log "Preflight: ${LEAP_CMD} -> ${LEAP_API_URL} (local)" ;;
    *) fail "'${LEAP_CMD}' targets ${LEAP_API_URL}, which is not local. A blind eval must not push to a shared server. Run 'leap auth select' to pick the local env, or set EVAL_ALLOW_REMOTE_LEAP=1 to override." ;;
  esac
else
  log "Preflight: ${LEAP_CMD} -> ${LEAP_API_URL} (remote allowed by EVAL_ALLOW_REMOTE_LEAP=1)"
fi

PRE_DIR="${FIXTURES_ROOT}/${FIXTURE}/pre"
[[ -d "${PRE_DIR}/.git" ]] || fail "fixture '${FIXTURE}' not prepared: ${PRE_DIR} missing (run prepare.sh + verify.sh first)"

# --- The fixture must be the PREPARED, still-blind tree ---------------------- #
# A used fixture contains the previous agent's finished integration, so a
# second run on it starts from the answer and "passes" in minutes — a verdict
# about nothing. Legit machinery (.venv from bootstrap, .claude from gen_deny)
# is hidden from git first so a clean tree is a true invariant.
for _pat in '/.venv/' '/.claude/'; do
  grep -qxF "${_pat}" "${PRE_DIR}/.git/info/exclude" 2>/dev/null \
    || echo "${_pat}" >>"${PRE_DIR}/.git/info/exclude"
done
git -C "${PRE_DIR}" rev-parse --verify --quiet HEAD^ >/dev/null 2>&1 \
  && fail "fixture '${FIXTURE}' has git history — not a scrubbed blind tree. Re-run prepare.sh + verify.sh."
[[ -z "$(git -C "${PRE_DIR}" status --porcelain)" ]] \
  || fail "fixture '${FIXTURE}' is dirty — a previous run's leftovers (git -C ${PRE_DIR} status). Re-run prepare.sh + verify.sh to rebuild it blind."
leftovers="$(find "${PRE_DIR}" -maxdepth 1 \( -name 'leap*' -o -name 'NOTES.md' \) 2>/dev/null | head -5)"
[[ -z "${leftovers}" ]] \
  || fail "fixture '${FIXTURE}' already contains integration artifacts: ${leftovers} — re-run prepare.sh + verify.sh."
log "Preflight: fixture tree is clean and blind"

# --- leap -> dev-CLI shim, first on PATH (so a bare `leap` never hits prod) -- #
SHIM_DIR="${EVAL_ROOT}/.shim"
mkdir -p "${SHIM_DIR}"
printf '#!/usr/bin/env bash\nexec %q "$@"\n' "${LEAP_CMD}" > "${SHIM_DIR}/leap"
chmod +x "${SHIM_DIR}/leap"
RUN_PATH="${SHIM_DIR}:${HOME}/.local/bin:${PATH}"

# Job tracking is the verdict's backbone: prove `run list` works BEFORE spending
# an agent session that could otherwise only ever end in a silent STUCK.
env PATH="${RUN_PATH}" "${LEAP_CMD}" run list -t Evaluate >/dev/null 2>&1 \
  || fail "'${LEAP_CMD} run list' failed — server unreachable or CLI not authenticated (try: ${LEAP_CMD} auth login). Not starting an agent whose run could not be graded."

# --- Exactly one source of the integration skill ---------------------------- #
# Detection lives in lib/skill_sources.py (shared with check.sh): filesystem
# facts, not a parsed skill listing. The concern is a local COPY shadowing the
# plugin's copy of the SAME skill, which would silently test the wrong one.
# Other unrelated Tensorleap skills (e.g. tensorleap-migration) are fine.
CLAUDE_LAUNCH="claude --dangerously-skip-permissions"
SKILL_NAME="tensorleap-integration-creation"
PLUGIN_PKG="integration@tensorleap"
LOCAL_COPY="${HOME}/.claude/skills/${SKILL_NAME}"
if [[ -n "${PLUGIN_DIR}" ]]; then
  [[ -d "${PLUGIN_DIR}" ]] || fail "--plugin-dir not found: ${PLUGIN_DIR}"
  CLAUDE_LAUNCH+=" --plugin-dir $(printf '%q' "${PLUGIN_DIR}")"
fi
readarray -t sources < <(python3 "${EVAL_ROOT}/lib/skill_sources.py" \
  ${PLUGIN_DIR:+--plugin-dir "${PLUGIN_DIR}"} "${PRE_DIR}/.claude/settings.json")
log "Preflight: integration-skill source(s) = ${sources[*]:-NONE}"
[[ ${#sources[@]} -ge 1 ]] || fail "the ${SKILL_NAME} skill is not available — install the plugin or pass --plugin-dir"
if [[ ${#sources[@]} -ne 1 ]]; then
  cat >&2 <<EOF
error: ${#sources[@]} sources of ${SKILL_NAME} would shadow each other, so the run
       could silently test the wrong copy:
$(printf '         - %s\n' "${sources[@]}")

       Keep exactly one. To test a LOCAL BUILD of this repo's skill:
         python build/generate.py                       # refresh dist/claude/integration
         run.sh --fixture ${FIXTURE} --plugin-dir ../dist/claude/integration
       and switch the other two off:
         "enabledPlugins": { "${PLUGIN_PKG}": false }   in ~/.claude/settings.json
         mv ${LOCAL_COPY}{,.off}
       To test the PUBLISHED plugin instead: drop --plugin-dir and remove the
       local copy above.
EOF
  exit 1
fi
log "  ok: single source"

# --- Provenance: pin down WHAT was tested ------------------------------------ #
# "Skill source: installed plugin" can point at different bytes in different
# runs (plugin updates, rebuilt dist), and the fixture definition can change
# under the same id (post_ref bump, edited guidance). Without recording these,
# a delta between two run reports is uninterpretable — the roll-up's REVIEW
# comment asks a human to reconstruct exactly this, after the fact, from
# nothing. Fingerprints are content hashes, so "same/different" is decidable
# without trusting names or versions.
skill_dir="${PLUGIN_DIR:+${PLUGIN_DIR}/skills/${SKILL_NAME}}"
if [[ -z "${skill_dir}" ]]; then
  # Installed plugin: newest cached version of the marketplace package.
  skill_dir="$(ls -d "${HOME}/.claude/plugins/cache/tensorleap/integration/"*"/skills/${SKILL_NAME}" 2>/dev/null | sort -V | tail -1 || true)"
fi
SKILL_FINGERPRINT=""
if [[ -n "${skill_dir}" && -d "${skill_dir}" ]]; then
  SKILL_FINGERPRINT="$(cd "${skill_dir}" && find . -type f -print0 \
    | LC_ALL=C sort -z | xargs -0 shasum -a 256 | shasum -a 256 | cut -c1-12)"
fi
# When testing a local build, the repo state names the exact code under test.
SKILL_GIT=""
[[ -n "${PLUGIN_DIR}" ]] && SKILL_GIT="$(git -C "${EVAL_ROOT}/.." describe --always --dirty 2>/dev/null || true)"
FIXTURE_SHA="$(python3 - "${EVAL_ROOT}/manifest.json" "${FIXTURE}" <<'PY'
import hashlib, json, sys
try:
    f = next((x for x in json.load(open(sys.argv[1]))["fixtures"] if x["id"] == sys.argv[2]), None)
    print("" if f is None else hashlib.sha256(
        json.dumps(f, sort_keys=True).encode()).hexdigest()[:12])
except Exception:
    print("")
PY
)"
log "Provenance: skill=${SKILL_FINGERPRINT:-?}${SKILL_GIT:+ (git ${SKILL_GIT})} fixture=${FIXTURE_SHA:-?}"

# --- Optional: apply the leakage deny-list if the generator is present ------ #
if [[ -f "${EVAL_ROOT}/gen_deny.py" ]]; then
  log "Applying leakage deny-list"
  python3 "${EVAL_ROOT}/gen_deny.py" "${PRE_DIR}" || fail "gen_deny.py failed"
else
  log "WARNING: gen_deny.py absent — agent is NOT fenced off from sibling repos/memory"
fi

# --- The operator prompt (minimal; the skill drives its own deploy steps) --- #
# prepare.sh staged this fixture's data somewhere machine-specific and recorded the
# path; the manifest's guidance refers to it as ${<VAR>}. Resolve those here so the
# agent is handed a path that exists on THIS machine.
STAGED_DATA=""
[[ -f "${FIXTURES_ROOT}/${FIXTURE}/staged_data_path" ]] \
  && STAGED_DATA="$(<"${FIXTURES_ROOT}/${FIXTURE}/staged_data_path")"
GUIDANCE="$(python3 - "${EVAL_ROOT}/manifest.json" "${FIXTURE}" "${STAGED_DATA}" <<'PY'
import json, os, re, sys
manifest, fid, staged = sys.argv[1], sys.argv[2], sys.argv[3].strip()
try:
    fixtures = json.load(open(manifest))["fixtures"]
except Exception as exc:
    sys.exit(f"cannot read {manifest}: {exc}")
f = next((x for x in fixtures if x["id"] == fid), None)
if f is None:
    # Hand-made fixture dir, not manifest-driven: no guidance to inject. Only an
    # unresolvable data placeholder (below) is fatal.
    print(f"warning: fixture '{fid}' is not in {manifest} — no operator guidance", file=sys.stderr)
    print("(none)")
    raise SystemExit(0)

parts = [g["text"] for g in (f.get("operator_guidance") or []) if g.get("text")]
# Prerequisite guidance is where the data paths live. It is useless to the agent
# unless it reaches the prompt, so it is included rather than left as operator docs.
prereqs = f.get("runtime_prerequisites") or []
parts += [p["operator_guidance"] for p in prereqs if p.get("operator_guidance")]
text = "\n".join(parts) or "(none)"

# --- Preflight: every REQUIRED prerequisite must resolve to real data NOW ------
# Without this a missing prerequisite is only discovered by the agent, mid-session,
# and surfaces as STUCK — indistinguishable from the agent going in circles. The
# webinar run that cost $13.64 over 61 turns died exactly this way. Exit 12 gives
# run_all a distinct PREREQ-FAIL verdict that never blames the skill.
PREREQ_FAIL = 12
def resolve(var):
    """An explicitly exported env var wins over prepare.sh's staged path, so an
    operator can supply data the harness cannot stage."""
    val = os.environ.get(var, "").strip()
    return val or staged

problems = []
for p in prereqs:
    if not p.get("required"):
        continue
    variables = (p.get("local_resolution") or {}).get("env_vars") or []
    if not variables:
        continue
    for var in variables:
        path = resolve(var)
        if not path:
            problems.append(f"  - {p.get('id', '?')}: ${{{var}}} is unset and no staged data "
                            f"path was recorded by prepare.sh")
        elif not os.path.isdir(path):
            problems.append(f"  - {p.get('id', '?')}: ${{{var}}} -> {path} does not exist")
        elif not any(os.scandir(path)):
            problems.append(f"  - {p.get('id', '?')}: ${{{var}}} -> {path} is empty")
        else:
            text = text.replace("${%s}" % var, path)

if problems:
    print(f"fixture '{fid}': required data prerequisite(s) not satisfied:", file=sys.stderr)
    print("\n".join(problems), file=sys.stderr)
    for p in prereqs:
        if p.get("required") and p.get("description"):
            print(f"\n{p.get('id','?')}: {p['description']}", file=sys.stderr)
    print("\nStage the data (prepare.sh without --skip-staging) or export the variable(s) "
          "above at a materialized copy. Not running the agent.", file=sys.stderr)
    raise SystemExit(PREREQ_FAIL)

unresolved = sorted(set(re.findall(r"\$\{([A-Z0-9_]+)\}", text)))
if unresolved:
    sys.exit(f"unresolved data placeholders {unresolved} for fixture '{fid}': no staged "
             "data path recorded. Re-run prepare.sh for this fixture (without --skip-staging).")
print(text)
PY
)" || { rc=$?; [[ "${rc}" -eq 12 ]] && exit 12; fail "could not build the operator prompt (see above)"; }

STAGED_NOTE=""
if [[ -n "${STAGED_DATA}" ]]; then
  STAGED_NOTE="
This fixture's data is already staged at ${STAGED_DATA} — read it from there. Do
not download or fetch datasets or model weights yourself."
fi
read -r -d '' MSG <<EOF || true
Use the tensorleap-integration-creation skill to create a complete Tensorleap
integration for THIS repository and get a CONFIRMED evaluate.

Operator guidance for this fixture:
${GUIDANCE}
${STAGED_NOTE}

Rules:
- Work only from THIS repository, its dependencies, the code-loader you install,
  and the skill. Do NOT read any other repo/project on this machine, and do NOT
  rely on your memory of other Tensorleap integrations.
- Use \`leap\` for every Tensorleap command (it is shimmed to the dev server).
- Follow the skill's deploy steps to push and get the evaluation running, then
  track the Evaluate job to a terminal state as the skill describes.
- Keep a NOTES.md logging what you did and the push/eval job ids. Don't commit.
EOF
# The prompt is part of what was tested: guidance edits change fixture
# difficulty, so the hash lets a delta reader see "same prompt or not".
PROMPT_SHA="$(printf '%s' "${MSG}" | shasum -a 256 | cut -c1-12)"

# --- This run's jobs, identified by CREATION TIME --------------------------- #
# A job id is a Mongo ObjectId; its first 8 hex chars are the creation epoch, so
# "created at/after this run's prompt submission" is a stateless test of
# ownership. The previous mechanism — diff against an id snapshot taken before
# the run — turned one failed/empty `run list` at snapshot time into "every
# existing job is new", and graded V5 ner_roberta PASS on the PREVIOUS fixture's
# two-hour-old Evaluate. With creation-time gating a failed `run list` can only
# delay detection, never claim an old job. (Local server = same clock as this
# script; under EVAL_ALLOW_REMOTE_LEAP=1 a skewed remote clock can at worst miss
# a job and report STUCK — the safe direction.)
# Only real 24-hex ids are considered — never help/usage text (which the CLI
# dumps, FINISHED/FAILED words included, on a 503/504); stderr dropped likewise.
newest_job_since() {   # $1 = Push|Evaluate, $2 = min creation epoch -> id or empty
  local id
  while IFS= read -r id; do
    if (( 16#${id:0:8} >= $2 )); then
      printf '%s\n' "${id}"
      return 0
    fi
  done < <(env PATH="${RUN_PATH}" "${LEAP_CMD}" run list -t "$1" 2>/dev/null \
             | awk '{print $NF}' | grep -E '^[0-9a-f]{24}$' || true)
  return 0
}

# --- Interactive session over tmux (no -p, so the push is not reaped) ------- #
SESS="op-${FIXTURE}"

AGENT_ALIVE=0
release_agent() {   # stop paying for the agent; the Evaluate keeps running server-side
  [[ "${AGENT_ALIVE}" -eq 1 ]] || return 0
  tmux kill-session -t "${SESS}" 2>/dev/null || true
  AGENT_ALIVE=0
}

# Defined BEFORE the session exists and trapped immediately after: an interrupt
# during the ~5min readiness wait would otherwise leave the session — and a live
# agent spending tokens — running forever with nothing tracking it.
trap release_agent EXIT
trap 'log "interrupted — releasing the agent session"; exit 130' INT TERM HUP

tmux kill-session -t "${SESS}" 2>/dev/null || true
START_EPOCH="$(date +%s)"   # transcripts modified at/after this belong to THIS run
tmux new-session -d -s "${SESS}" -x 220 -y 50 -c "${PRE_DIR}"
AGENT_ALIVE=1
# CLAUDE_CONFIG_DIR unset inside the pane (empty string hides the plugin); shim + local bin on PATH.
tmux send-keys -t "${SESS}" 'unset CLAUDE_CONFIG_DIR; export PATH='"$(printf '%q' "${RUN_PATH}")" Enter
tmux send-keys -t "${SESS}" "${CLAUDE_LAUNCH}" Enter

log "Waiting for the REPL to be ready…"
ready=0
for _ in $(seq 1 150); do          # up to ~5 min
  pane="$(tmux capture-pane -t "${SESS}" -p 2>/dev/null || true)"
  # '❯' is the current composer prompt; '│ >' the older boxed one. Matching only
  # the transient Welcome banner would race against it scrolling away.
  if grep -qE 'Welcome|│ >|❯|> $' <<<"${pane}"; then ready=1; break; fi
  # First-run bypass-permissions acceptance: select "Yes, I accept" and confirm.
  if grep -qiE 'Bypass Permissions mode|Yes, I accept|accept all responsibility' <<<"${pane}"; then
    log "  dismissing bypass-permissions acceptance prompt"
    tmux send-keys -t "${SESS}" Down; sleep 0.5
    tmux send-keys -t "${SESS}" Enter; sleep 2
  fi
  sleep 2
done
[[ "${ready}" -eq 1 ]] || fail "REPL never became ready (inspect: tmux attach -t ${SESS})"

# Paste the prompt as one message (send-keys would submit at the first newline).
PROMPT_FILE="$(mktemp)"; printf '%s' "${MSG}" > "${PROMPT_FILE}"
tmux load-buffer -t "${SESS}" "${PROMPT_FILE}"
tmux paste-buffer -t "${SESS}"
rm -f "${PROMPT_FILE}"

# The REPL ingests a bracketed paste asynchronously. An Enter sent immediately
# lands mid-paste, is swallowed, and the prompt sits in the composer forever while
# this script happily polls for an Evaluate that will never be created. So: let the
# paste settle, submit, then CONFIRM the agent actually started before moving on.
log "Submitting the prompt…"
SUBMIT_EPOCH="$(date +%s)"   # platform jobs created at/after this belong to THIS run
submitted=0
for _ in $(seq 1 10); do
  sleep 2
  tmux send-keys -t "${SESS}" Enter
  sleep 3
  pane="$(tmux capture-pane -t "${SESS}" -p 2>/dev/null || true)"
  # "esc to interrupt" only renders while the agent is working on a submitted turn.
  if grep -qiE 'esc to interrupt' <<<"${pane}"; then submitted=1; break; fi
done
[[ "${submitted}" -eq 1 ]] \
  || fail "prompt never submitted — it is probably still sitting in the composer (inspect: tmux attach -t ${SESS})"
log "Prompt submitted. Tracking Evaluate to a terminal state…"

cancel_eval() {
  # The CLI has no `run stop`, but Evaluate pods are owned by a batch/v1 Job named
  # exactly `evaluate-<run id>` — the id `run list` prints — so the job we polled is
  # the job we can delete (cascades to its pod). Best-effort: `server tools` only
  # exists for a LOCAL install, so a remote run just warns and moves on.
  local id="$1"
  if env PATH="${RUN_PATH}" "${LEAP_CMD}" server tools kubectl \
       delete job "evaluate-${id}" -n tensorleap >/dev/null 2>&1; then
    log "  cancelled: deleted job evaluate-${id}"
  else
    log "  WARNING: could not delete job evaluate-${id} — it may still be running."
    log "           A concurrent job corrupts the next fixture; check before continuing."
  fi
}

# --- Poll: find this run's eval id, then wait for it to reach terminal ------ #
# PHASE A (no Evaluate yet): the agent is authoring. Guarded by its lifetime clock
#   plus a pane-idle check — a pane whose rendered text stops changing means the
#   agent stopped producing output (typically it yielded at the prompt), and
#   nothing will ever answer it. No Evaluate by the end of either => STUCK.
# PHASE B (Evaluate exists): the agent's contribution is done — the verdict is the
#   job's terminal state, which arrives whether or not the session is still alive.
#   So no pane guard, and the agent is released at its lifetime cap while polling
#   continues: a legitimately slow evaluate still gets graded on its real outcome.
EVAL_ID=""; idle=0; last=""; RESULT="STUCK"; NOTE=""
agent_start=${SECONDS}; eval_start=${SECONDS}
while :; do
  if [[ -z "${EVAL_ID}" ]]; then
    EVAL_ID="$(newest_job_since Evaluate "${SUBMIT_EPOCH}")"
    if [[ -n "${EVAL_ID}" ]]; then
      log "  detected Evaluate ${EVAL_ID} — verdict is now server-side; starting eval budget"
      eval_start=${SECONDS}
      continue
    fi
    # Both Phase-A guards fire only on an iteration that just looked for an
    # Evaluate and found none, so neither can abandon one created moments ago.
    if (( SECONDS - agent_start >= AGENT_MAX_SECS )); then
      log "  no Evaluate created within ${AGENT_MAX_SECS}s — flagging STUCK"; break
    fi
    now="$(tmux capture-pane -t "${SESS}" -p | cksum)"
    [[ "${now}" == "${last}" ]] && idle=$((idle+1)) || idle=0; last="${now}"
    if (( idle >= IDLE_STUCK_TICKS )); then
      log "  no Evaluate created and pane idle for ${idle} polls — flagging STUCK"; break
    fi
  else
    line="$(env PATH="${RUN_PATH}" "${LEAP_CMD}" run list -t Evaluate 2>/dev/null | grep -F "${EVAL_ID}" || true)"
    case "${line}" in
      *FINISHED*)  RESULT="PASS"; break ;;
      *FAILED*|*STOPPED*|*TERMINATED*) RESULT="FAIL"; break ;;
    esac
    if (( AGENT_ALIVE == 1 && SECONDS - agent_start >= AGENT_MAX_SECS )); then
      log "  agent hit its ${AGENT_MAX_SECS}s lifetime — releasing the session, still tracking ${EVAL_ID}"
      release_agent
      NOTE="Agent session was killed at its ${AGENT_MAX_SECS}s lifetime cap while Evaluate ${EVAL_ID} was still running; the verdict below is the job's own outcome. No NOTES.md wrap-up from the agent."
    fi
    if (( SECONDS - eval_start >= EVAL_MAX_SECS )); then
      log "  Evaluate ${EVAL_ID} still non-terminal after ${EVAL_MAX_SECS}s — flagging STUCK"
      cancel_eval "${EVAL_ID}"
      NOTE="Evaluate ${EVAL_ID} never reached a terminal state within ${EVAL_MAX_SECS}s and was cancelled."
      break
    fi
  fi
  sleep "${POLL_SECS}"
done

# --- The Push this run created (read once, at teardown) --------------------- #
# No polling: `run list` keeps history, so whatever state the push is in when the
# run ends is the state to report. Same creation-time gate as the Evaluate —
# push and evaluate are graded as SEPARATE columns because a broken integration
# can reach a FINISHED push and still fail its eval.
PUSH_ID="$(newest_job_since Push "${SUBMIT_EPOCH}")"
PUSH_STATUS=""
if [[ -n "${PUSH_ID}" ]]; then
  PUSH_STATUS="$(env PATH="${RUN_PATH}" "${LEAP_CMD}" run list -t Push 2>/dev/null \
    | awk -v id="${PUSH_ID}" '$NF == id {print $(NF-1); exit}' || true)"
fi

# --- Capture the transcript path for the report, then tear down ------------- #
# Claude encodes the project dir by replacing /, ., and _ with '-'.
CWD_KEY="$(echo "${PRE_DIR}" | sed 's#[/._]#-#g')"
TRANSCRIPT_DIR="${HOME}/.claude/projects/${CWD_KEY}"
release_agent

log "Fixture '${FIXTURE}': ${RESULT} (push ${PUSH_STATUS:-none}, eval ${EVAL_ID:-none})"
if [[ -f "${EVAL_ROOT}/report.py" ]]; then
  python3 "${EVAL_ROOT}/report.py" \
    --fixture "${FIXTURE}" --result "${RESULT}" --eval-id "${EVAL_ID:-}" \
    --push-id "${PUSH_ID}" --push-status "${PUSH_STATUS}" \
    --transcript-dir "${TRANSCRIPT_DIR}" --started-at "${START_EPOCH}" \
    --duration "${SECONDS}" \
    --skill-fingerprint "${SKILL_FINGERPRINT}" --skill-git "${SKILL_GIT}" \
    --fixture-sha "${FIXTURE_SHA}" --prompt-sha "${PROMPT_SHA}" \
    --notes "${PRE_DIR}/NOTES.md" \
    --note "${NOTE}" --skill-source "${sources[0]}" \
    --out "${REPORTS_DIR}/${FIXTURE}.md" || true
else
  log "report.py absent — skipping report (result above is authoritative)"
fi

# A graded verdict is only ABOUT THE SKILL if the skill actually ran: report.py
# voids the sidecar when zero assistant turns in this run's transcript carry the
# skill attribution (then the run graded raw Claude, not the skill — the same
# "eval is meaningless" class as CLAUDE_CONFIG_DIR=""). Surfaced as exit 13 so
# run_all reports SKILL-UNUSED and keeps it out of the pass ratio.
if [[ ("${RESULT}" == "PASS" || "${RESULT}" == "FAIL") && -f "${REPORTS_DIR}/${FIXTURE}.json" ]]; then
  void_reason="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("void_reason", ""))' \
                   "${REPORTS_DIR}/${FIXTURE}.json" 2>/dev/null || true)"
  if [[ -n "${void_reason}" ]]; then
    log "${FIXTURE}: verdict VOID — ${void_reason}"
    exit 13
  fi
fi

# The exit code IS the verdict, so callers never have to parse the report (which
# inlines the agent's own NOTES.md, where a stray "FAILED to load model" reads as
# a verdict). Distinct codes also keep a preflight failure (fail() → exit 1) from
# being laundered into a fixture FAIL. 13 (above) = platform verdict reached but
# the skill under test never ran a turn — void.
case "${RESULT}" in
  PASS) exit 0 ;;
  FAIL) exit 10 ;;
  *)    exit 11 ;;   # STUCK
esac
