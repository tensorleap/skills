#!/usr/bin/env bash
# Self-check for fixture_remove_code_loader_lines — the one piece of real logic
# behind "a blind pre variant must not name code-loader anywhere".
#   bash lib/test_reset_lib.sh
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/reset_lib.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

check() {
  local name="$1" content="$2" want_changed="$3" want_remaining="$4"
  local path="${tmp}/${name}" changed=0 remaining

  printf '%s' "${content}" >"${path}"
  fixture_remove_code_loader_lines "${path}" && changed=1
  [[ "${changed}" == "${want_changed}" ]] \
    || { echo "FAIL ${name}: changed=${changed} want ${want_changed}" >&2; exit 1; }

  remaining="$(cat "${path}")"
  [[ "${remaining}" == "${want_remaining}" ]] \
    || { echo "FAIL ${name}: got '${remaining}' want '${want_remaining}'" >&2; exit 1; }

  # Stripping is idempotent: a second pass must report "nothing to drop".
  ! fixture_remove_code_loader_lines "${path}" \
    || { echo "FAIL ${name}: second pass reported a change" >&2; exit 1; }

  echo "ok ${name}"
}

check pyproject.toml \
  '[tool.poetry.dependencies]
python = ">=3.10,<3.11"
code-loader = "^1.0.152"
code-loader-helpers = "^1.0.35"
numpy = "^1.24"
' 1 '[tool.poetry.dependencies]
python = ">=3.10,<3.11"
numpy = "^1.24"'

check requirements.txt \
  'numpy==1.24.0
code-loader==1.0.184
' 1 'numpy==1.24.0'

# PEP 621 array form — asensus_segmentation ships this, and the first cut of the
# pattern missed it because of the leading quote.
check pep621.toml \
  '[project]
dependencies = [
    "onnxruntime>=1.17",
    "code-loader>=1.0.165,<2.0.0",
]
' 1 '[project]
dependencies = [
    "onnxruntime>=1.17",
]'

# Block-form entries go; the single-line array is the documented ceiling (see the
# ponytail: note in reset_lib.sh) — verify.sh is the gate that catches it.
check pep621-extras.toml \
  'dependencies = ["numpy", "code-loader[all]>=1.0", "code_loader_helpers"]
more = [
    "code-loader[all]>=1.0",
    "code_loader_helpers",
]
' 1 'dependencies = ["numpy", "code-loader[all]>=1.0", "code_loader_helpers"]
more = [
]'

check inline-table.toml \
  'code_loader = { version = "^1.0.200", source = "pypi" }
torch = "^2.2"
' 1 'torch = "^2.2"'

# No code-loader to drop -> non-zero exit, file untouched. This is what keeps
# prepare.sh from relocking a pre variant that was already clean.
check clean.toml \
  '[tool.poetry.dependencies]
numpy = "^1.24"
' 0 '[tool.poetry.dependencies]
numpy = "^1.24"'

# Nothing that merely mentions code-loader in prose or in another package name
# should be dropped.
check bystanders.txt \
  '# needs code-loader at runtime
my-code-loader-shim==2.0
' 0 '# needs code-loader at runtime
my-code-loader-shim==2.0'

echo "all reset_lib checks passed"
