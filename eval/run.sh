#!/usr/bin/env bash
# run.sh — drive an interactive Claude session (via tmux) to run the
# integration skill against ONE prepared blind fixture, then track the Evaluate
# to a terminal state.
#
# The operational rules from README.md are enforced here, not just documented:
#   1. CLAUDE_CONFIG_DIR is UNSET for the agent      (env -u below)
#   2. `leap` -> `leapdev` shim first on PATH         (never hit prod)
#   3. exactly one Tensorleap skill visible           (--plugin-dir + preflight)
#   4. one fixture at a time                          (this script does one; gate the next on it)
#   5. interactive (no -p) so the push is not reaped  (tmux REPL)
#
# Prereqs: tmux, a reachable Tensorleap dev server + non-interactive auth, and a
# prepared+verified fixture (run prepare.sh + verify.sh first).
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EVAL_ROOT="${SCRIPT_DIR}"
FIXTURES_ROOT="${EVAL_ROOT}/.fixtures"
REPO_ROOT="$(cd -- "${EVAL_ROOT}/.." && pwd)"

FIXTURE=""
PLUGIN_DIR=""
LEAP_CMD="leapdev"
IDLE_STUCK_TICKS="${IDLE_STUCK_TICKS:-40}"   # ~40 * 30s = 20 min of no pane change & no eval
POLL_SECS="${POLL_SECS:-30}"
MAX_TRACK_SECS="${MAX_TRACK_SECS:-3600}"     # hard wall-clock cap on the tracking loop (1h)

usage() {
  cat <<'EOF'
Usage: run.sh --fixture <id> [--plugin-dir <dir>] [--leap-cmd leapdev]

  --fixture ID       Fixture id from manifest.json (must be prepared + verified).
  --plugin-dir DIR   Run the skill from a built dist dir (e.g. dist/claude/integration)
                     instead of the installed marketplace plugin. Optional.
  --leap-cmd CMD     The real Tensorleap dev CLI the `leap` shim forwards to
                     (default: leapdev).
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

require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "required command '$1' not found"; }
require_cmd tmux
require_cmd "${LEAP_CMD}"
command -v claude >/dev/null 2>&1 || fail "claude CLI not found on PATH"

PRE_DIR="${FIXTURES_ROOT}/${FIXTURE}/pre"
[[ -d "${PRE_DIR}/.git" ]] || fail "fixture '${FIXTURE}' not prepared: ${PRE_DIR} missing (run prepare.sh + verify.sh first)"

# --- Rule 2: leap -> leapdev shim, first on PATH ---------------------------- #
SHIM_DIR="${EVAL_ROOT}/.shim"
mkdir -p "${SHIM_DIR}"
printf '#!/usr/bin/env bash\nexec %q "$@"\n' "${LEAP_CMD}" > "${SHIM_DIR}/leap"
chmod +x "${SHIM_DIR}/leap"
RUN_PATH="${SHIM_DIR}:${HOME}/.local/bin:${PATH}"

# --- Rule 3: exactly one SOURCE of the integration skill -------------------- #
# Checked on the filesystem (deterministic), not by parsing a skill listing:
# rule 3 is about not having a local COPY shadow the plugin's copy of the SAME
# skill. Other unrelated Tensorleap skills (e.g. tensorleap-migration) are fine.
CLAUDE_LAUNCH="claude --dangerously-skip-permissions"
SKILL_NAME="tensorleap-integration-creation"
LOCAL_COPY="${HOME}/.claude/skills/${SKILL_NAME}"
INSTALLED_PLUGINS="${HOME}/.claude/plugins/installed_plugins.json"
sources=()
if [[ -n "${PLUGIN_DIR}" ]]; then
  [[ -d "${PLUGIN_DIR}" ]] || fail "--plugin-dir not found: ${PLUGIN_DIR}"
  CLAUDE_LAUNCH+=" --plugin-dir $(printf '%q' "${PLUGIN_DIR}")"
  sources+=("--plugin-dir ${PLUGIN_DIR}")
fi
[[ -e "${LOCAL_COPY}" ]] && sources+=("local copy ${LOCAL_COPY}")
grep -q '"integration@tensorleap"' "${INSTALLED_PLUGINS}" 2>/dev/null \
  && sources+=("installed plugin integration@tensorleap")
log "Preflight: integration-skill source(s) = ${sources[*]:-NONE}"
[[ ${#sources[@]} -ge 1 ]] || fail "the ${SKILL_NAME} skill is not available — install the plugin or pass --plugin-dir"
[[ ${#sources[@]} -eq 1 ]] || fail "rule 3: ${#sources[@]} sources would shadow each other (${sources[*]}). Keep exactly one — remove ${LOCAL_COPY}, or drop --plugin-dir."
log "  ok: single source"

# --- Optional: apply the leakage deny-list if the generator is present ------ #
if [[ -f "${EVAL_ROOT}/gen_deny.py" ]]; then
  log "Applying leakage deny-list"
  python3 "${EVAL_ROOT}/gen_deny.py" "${PRE_DIR}" || fail "gen_deny.py failed"
else
  log "WARNING: gen_deny.py absent — agent is NOT fenced off from sibling repos/memory"
fi

# --- The operator prompt (minimal; the skill drives its own deploy steps) --- #
GUIDANCE="$(python3 - "${FIXTURE}" <<'PY'
import json, sys
fid = sys.argv[1]
try:
    f = next(x for x in json.load(open("manifest.json"))["fixtures"] if x["id"] == fid)
    print("\n".join(g["text"] for g in (f.get("operator_guidance") or [])) or "(none)")
except Exception:
    print("(none)")
PY
)"
read -r -d '' MSG <<EOF || true
Use the tensorleap-integration-creation skill to create a complete Tensorleap
integration for THIS repository and get a CONFIRMED evaluate.

Operator guidance for this fixture:
${GUIDANCE}

Rules:
- Work only from THIS repository, its dependencies, the code-loader you install,
  and the skill. Do NOT read any other repo/project on this machine, and do NOT
  rely on your memory of other Tensorleap integrations.
- Use \`leap\` for every Tensorleap command (it is shimmed to the dev server).
- Follow the skill's deploy steps to push and get the evaluation running, then
  track the Evaluate job to a terminal state as the skill describes.
- Keep a NOTES.md logging what you did and the push/eval job ids. Don't commit.
EOF

# --- Rule 4 baseline: record existing Evaluate ids so we can spot THIS run's - #
# Only real 24-hex job ids — never help/usage text (which the CLI dumps, with the
# words FINISHED/FAILED in it, on a 503/504). stderr is dropped for the same reason.
eval_ids() { env PATH="${RUN_PATH}" "${LEAP_CMD}" run list -t Evaluate 2>/dev/null \
  | awk '{print $NF}' | grep -E '^[0-9a-f]{24}$' || true; }
BASE_EVALS="$(eval_ids | sort -u || true)"

# --- Rule 5: interactive session over tmux (no -p, so the push is not reaped) #
SESS="op-${FIXTURE}"
tmux kill-session -t "${SESS}" 2>/dev/null || true
tmux new-session -d -s "${SESS}" -x 220 -y 50 -c "${PRE_DIR}"
# Rule 1: CLAUDE_CONFIG_DIR unset inside the pane; shim + local bin on PATH.
tmux send-keys -t "${SESS}" 'unset CLAUDE_CONFIG_DIR; export PATH='"$(printf '%q' "${RUN_PATH}")" Enter
tmux send-keys -t "${SESS}" "${CLAUDE_LAUNCH}" Enter

log "Waiting for the REPL to be ready…"
ready=0
for _ in $(seq 1 150); do          # up to ~5 min
  pane="$(tmux capture-pane -t "${SESS}" -p 2>/dev/null || true)"
  if grep -qE 'Welcome|│ >|> $' <<<"${pane}"; then ready=1; break; fi
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
tmux send-keys -t "${SESS}" Enter
rm -f "${PROMPT_FILE}"
log "Prompt submitted. Tracking Evaluate to a terminal state…"

# --- Poll: find this run's eval id, then wait for it to reach terminal ------ #
EVAL_ID=""; idle=0; last=""; RESULT="STUCK"; track_start=${SECONDS}
while :; do
  # Hard wall-clock cap: a chatty-but-stuck agent keeps the pane changing, which
  # would reset the idle guard forever. This backstops that.
  if (( SECONDS - track_start >= MAX_TRACK_SECS )); then
    log "  tracking exceeded ${MAX_TRACK_SECS}s — flagging STUCK"; RESULT="STUCK"; break
  fi
  if [[ -z "${EVAL_ID}" ]]; then
    EVAL_ID="$(eval_ids | grep -vxF "${BASE_EVALS}" | head -1 || true)"
    [[ -n "${EVAL_ID}" ]] && log "  detected Evaluate ${EVAL_ID}"
  else
    line="$(env PATH="${RUN_PATH}" "${LEAP_CMD}" run list -t Evaluate 2>/dev/null | grep -F "${EVAL_ID}" || true)"
    case "${line}" in
      *FINISHED*)  RESULT="PASS"; break ;;
      *FAILED*|*STOPPED*|*TERMINATED*) RESULT="FAIL"; break ;;
    esac
  fi
  # idle-stuck guard: no new eval AND the pane hasn't changed for a while
  now="$(tmux capture-pane -t "${SESS}" -p | cksum)"
  [[ "${now}" == "${last}" ]] && idle=$((idle+1)) || idle=0; last="${now}"
  if [[ -z "${EVAL_ID}" && "${idle}" -ge "${IDLE_STUCK_TICKS}" ]]; then
    RESULT="STUCK"; log "  no Evaluate created and pane idle — flagging STUCK"; break
  fi
  sleep "${POLL_SECS}"
done

# --- Capture the transcript path for the report, then tear down ------------- #
# Claude encodes the project dir by replacing /, ., and _ with '-'.
CWD_KEY="$(echo "${PRE_DIR}" | sed 's#[/._]#-#g')"
TRANSCRIPT_DIR="${HOME}/.claude/projects/${CWD_KEY}"
tmux kill-session -t "${SESS}" 2>/dev/null || true

log "Fixture '${FIXTURE}': ${RESULT} (eval ${EVAL_ID:-none})"
if [[ -f "${EVAL_ROOT}/report.py" ]]; then
  mkdir -p "${EVAL_ROOT}/reports"
  python3 "${EVAL_ROOT}/report.py" \
    --fixture "${FIXTURE}" --result "${RESULT}" --eval-id "${EVAL_ID:-}" \
    --transcript-dir "${TRANSCRIPT_DIR}" --notes "${PRE_DIR}/NOTES.md" \
    --out "${EVAL_ROOT}/reports/${FIXTURE}.md" || true
else
  log "report.py absent — skipping report (result above is authoritative)"
fi

[[ "${RESULT}" == "PASS" ]] && exit 0 || exit 1
