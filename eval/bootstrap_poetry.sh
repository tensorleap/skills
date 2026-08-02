#!/usr/bin/env bash
set -euo pipefail

# macOS ships bash 3.2; these scripts use bash-4 features. Re-exec under bash 4+.
if [ "${BASH_VERSINFO:-0}" -lt 4 ]; then
  for _b in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    [ -x "$_b" ] && exec "$_b" "$0" "$@"
  done
  echo "This script needs bash 4+ (macOS ships 3.2). Install: brew install bash" >&2; exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"
FIXTURES_ROOT="${REPO_ROOT}/.fixtures"
RESET_LIB_PATH="${REPO_ROOT}/lib/reset_lib.sh"

DEFAULT_PYTHON_VERSION="${FIXTURE_BOOTSTRAP_PYTHON:-3.10.14}"

# shellcheck source=./lib/reset_lib.sh
source "${RESET_LIB_PATH}"

usage() {
  cat <<'EOF'
Usage: bash bootstrap_poetry.sh [options]

Bootstrap Poetry environments for prepared fixture repositories.
Normally invoked by `prepare.sh --bootstrap-poetry`, not directly.

Options:
  --fixture ID         Limit bootstrap to one fixture ID from manifest.json.
  --variant KIND       One of pre, post, or all (default: pre — the only variant
                       anything executes; post is a static answer key).
  --python VERSION     Python version to install/use via pyenv (default: 3.10.14).
  --help               Show this help text.
EOF
}

fail() {
  echo "error: $*" >&2
  exit 1
}

log() {
  echo "[bootstrap] $*"
}

require_cmd() {
  local cmd="$1"
  command -v "${cmd}" >/dev/null 2>&1 || fail "required command '${cmd}' not found"
}

fixture_id=""
variant="pre"
python_version="${DEFAULT_PYTHON_VERSION}"

while (($# > 0)); do
  case "$1" in
    --fixture)
      shift
      [[ $# -gt 0 ]] || fail "--fixture requires a value"
      fixture_id="$1"
      ;;
    --variant)
      shift
      [[ $# -gt 0 ]] || fail "--variant requires a value"
      variant="$1"
      ;;
    --python)
      shift
      [[ $# -gt 0 ]] || fail "--python requires a value"
      python_version="$1"
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
  shift
done

case "${variant}" in
  pre|post|all)
    ;;
  *)
    fail "unsupported --variant value '${variant}'"
    ;;
esac

require_cmd git
require_cmd jq
require_cmd poetry
require_cmd pyenv
require_cmd python3
[[ -f "${RESET_LIB_PATH}" ]] || fail "fixture reset library not found: ${RESET_LIB_PATH}"
[[ -d "${FIXTURES_ROOT}" ]] || fail "fixture output root not found: ${FIXTURES_ROOT} (run prepare.sh first)"

log "Ensuring Python ${python_version} is available via pyenv"
pyenv install -s "${python_version}" >/dev/null
python_executable="$(pyenv prefix "${python_version}")/bin/python"
[[ -x "${python_executable}" ]] || fail "resolved python executable not found: ${python_executable}"

bootstrap_repo() {
  local repo_dir="$1"

  [[ -f "${repo_dir}/pyproject.toml" ]] || {
    log "Skipping ${repo_dir}: no pyproject.toml"
    return 0
  }

  log "Bootstrapping ${repo_dir}"
  (
    cd "${repo_dir}"
    export POETRY_VIRTUALENVS_IN_PROJECT=true
    # Prefer the pinned interpreter so envs are reproducible across machines, but
    # it is only a default: a fixture's own requires-python wins. asensus is
    # ~3.12 and poetry rightly refuses 3.10.14. `poetry env use` validates the
    # constraint for us, so use poetry as the authority rather than parsing
    # version specifiers here. Falling back to poetry's own interpreter search is
    # what prepare.sh's relock step already does, so this also stops the two
    # steps disagreeing about which python a fixture gets.
    if poetry env use "${python_executable}" >/dev/null 2>&1; then
      log "  python ${python_version} (pinned)"
    else
      # ponytail: poetry picks from PATH, so the exact patch level can differ per
      # machine. Pin per-fixture via FIXTURE_BOOTSTRAP_PYTHON if that ever matters.
      POETRY_VIRTUALENVS_USE_POETRY_PYTHON=true poetry install --no-root >/dev/null \
        || return 1
      log "  python $(poetry run python -V 2>&1 | awk '{print $2}') (poetry chose; pinned ${python_version} is incompatible with this fixture's requires-python)"
      return 0
    fi
    poetry install --no-root >/dev/null
  )
}

collect_fixture_dirs() {
  local kind="$1"
  local id

  if [[ -n "${fixture_id}" ]]; then
    printf '%s\n' "${FIXTURES_ROOT}/${fixture_id}/${kind}"
    return 0
  fi

  jq -r '.fixtures[].id' "${REPO_ROOT}/manifest.json" \
    | while IFS= read -r id; do
        [[ -n "${id}" ]] || continue
        printf '%s\n' "${FIXTURES_ROOT}/${id}/${kind}"
      done
}

declare -A seen_dirs=()
bootstrap_targets=()

add_target() {
  local dir="$1"
  [[ -d "${dir}/.git" ]] || return 0
  if [[ -z "${seen_dirs["${dir}"]+x}" ]]; then
    seen_dirs["${dir}"]=1
    bootstrap_targets+=("${dir}")
  fi
}

if [[ "${variant}" == "pre" || "${variant}" == "all" ]]; then
  while IFS= read -r dir; do
    [[ -n "${dir}" ]] || continue
    add_target "${dir}"
  done < <(collect_fixture_dirs "pre")
fi

if [[ "${variant}" == "post" || "${variant}" == "all" ]]; then
  while IFS= read -r dir; do
    [[ -n "${dir}" ]] || continue
    add_target "${dir}"
  done < <(collect_fixture_dirs "post")
fi

((${#bootstrap_targets[@]} > 0)) || fail "no matching fixture repositories found for bootstrap"

for dir in "${bootstrap_targets[@]}"; do
  bootstrap_repo "${dir}"
done

log "Fixture Poetry bootstrap complete"
