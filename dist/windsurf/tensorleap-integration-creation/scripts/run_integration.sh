#!/usr/bin/env bash
#
# Monitor-mode run for the Tensorleap integration loop.
#
# Runs leap_integration.py through the project's environment (poetry by default;
# set TL_PY to override) and surfaces
# the signals you care about: exit code, the readiness verdict, crash lines, and
# default-use warnings. Call this after EVERY meaningful edit (see SKILL.md run loop).
#
# Exit status (passed through from leap_integration.py):
#   0  ready to push: every mandatory part ran in the integration test
#   3  clean run, but the integration is NOT ready to push: a mandatory part
#      (e.g. the loss) is not in the integration test yet. Add the named one next.
#   anything else: the run failed; fix the earliest failure first.
# Older code-loader versions exit 0 in the "not ready" case and print
# "Some mandatory components have not yet been added ..."; this script reports
# that as not ready (exit 3) too.
#
# Usage:
#   scripts/run_integration.sh [repo_root] [entry_file]
#
#   repo_root   defaults to the current directory
#   entry_file  defaults to leap_integration.py
#
# The exit-status table only prints when the entry file is named exactly
# leap_integration.py, so keep that name.
#
# Interpreter selection:
#   - default: `poetry run python <entry_file>`
#   - override: set TL_PY to a resolved interpreter path to bypass Poetry shims,
#     e.g. TL_PY=/path/to/.venv/bin/python scripts/run_integration.sh
#
set -uo pipefail

REPO_ROOT="${1:-.}"
ENTRY_FILE="${2:-leap_integration.py}"
HERE="$(cd "$(dirname "$0")" && pwd)"

cd "$REPO_ROOT" || { echo "run_integration: cannot cd to $REPO_ROOT" >&2; exit 2; }

if [[ ! -f "$ENTRY_FILE" ]]; then
  echo "run_integration: $ENTRY_FILE not found in $(pwd)" >&2
  exit 2
fi

if [[ "$(basename "$ENTRY_FILE")" != "leap_integration.py" ]]; then
  echo "run_integration: WARNING entry file is '$ENTRY_FILE', not 'leap_integration.py'." >&2
  echo "                 The exit-status table will NOT print for other names." >&2
fi

OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT

echo "=== running $ENTRY_FILE ==="
if [[ -n "${TL_PY:-}" ]]; then
  "$TL_PY" "$ENTRY_FILE" 2>&1 | tee "$OUT"
  CODE=${PIPESTATUS[0]}
else
  poetry run python "$ENTRY_FILE" 2>&1 | tee "$OUT"
  CODE=${PIPESTATUS[0]}
fi

echo
echo "=== signals ==="
echo "exit code: $CODE"

grep -nE "crashed at function|Script crashed|Traceback \(most recent call last\)" "$OUT" \
  && echo "-> fix the crashing function FIRST"
grep -nE "Warnings \(Default use|defaults to" "$OUT" \
  && echo "-> make the warned values explicit"
grep -nE "validation failed|code flow failed|not ready to push|Ready to push|Optional next|Recommended next interface to add|All mandatory parts have been|All parts have been" "$OUT"

# The next mandatory part: from code-loader's verdict line, or from the older
# "Recommended next interface to add is: X" line.
NEXT="$(sed -nE 's/.*not ready to push: `([^`]+)`.*/\1/p' "$OUT" | tail -n 1)"
if [[ -z "$NEXT" ]]; then
  NEXT="$(sed -nE 's/.*Some mandatory components have not yet been added.*Recommended next interface to add is: *([^ ]+).*/\1/p' "$OUT" | tail -n 1)"
fi
if [[ "$CODE" -eq 0 && -n "$NEXT" ]]; then
  CODE=3  # older code-loader: same verdict, but it still exited 0
fi

echo
if [[ "$CODE" -eq 3 ]]; then
  echo "RESULT: integration incomplete, NOT ready to push. Next: ${NEXT:-see the status table above}."
  echo "        Add it to the integration test, then re-run. Do not push yet."
elif [[ "$CODE" -ne 0 ]]; then
  echo "RESULT: run failed (exit $CODE). Address the earliest failure above, then re-run."
elif grep -qE "Ready to push|All mandatory parts have been|All parts have been" "$OUT"; then
  echo "RESULT: ready to push. Every mandatory part ran in the integration test."
else
  echo "RESULT: clean exit, but no readiness verdict was printed (it prints only when the"
  echo "        entry file is named leap_integration.py). Not confirmed ready to push."
fi

# Count this pass for the skill_run event the skill sends when it finishes.
python3 "$HERE/skill_run.py" tensorleap-integration-creation iteration >/dev/null 2>&1 || true

exit "$CODE"
