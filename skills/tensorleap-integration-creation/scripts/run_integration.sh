#!/usr/bin/env bash
#
# Monitor-mode run for the Tensorleap integration loop.
#
# Runs leap_integration.py through the project's environment (poetry by default;
# set TL_PY to override) and surfaces
# the signals you care about: exit code, "Successful!", crash lines, and default-
# use warnings. Call this after EVERY meaningful edit (see SKILL.md run loop).
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

grep -nE "Successful!" "$OUT" && echo "-> stage passed for this invocation"
grep -nE "crashed at function|Script crashed|Traceback \(most recent call last\)" "$OUT" \
  && echo "-> fix the crashing function FIRST"
grep -nE "Warnings \(Default use|defaults to" "$OUT" \
  && echo "-> make the warned values explicit"
grep -nE "validation failed|code flow failed|Recommended next interface to add" "$OUT"

echo
if [[ "$CODE" -ne 0 ]]; then
  echo "RESULT: run failed (exit $CODE). Address the earliest failure above, then re-run."
else
  echo "RESULT: clean exit. Confirm the current stage's row is exercised before advancing."
fi

exit "$CODE"
