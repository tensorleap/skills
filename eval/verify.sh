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
MANIFEST_PATH="${REPO_ROOT}/manifest.json"
FIXTURES_ROOT="${REPO_ROOT}/.fixtures"
RESET_LIB_PATH="${REPO_ROOT}/lib/reset_lib.sh"

# shellcheck source=./lib/reset_lib.sh
source "${RESET_LIB_PATH}"

usage() {
  cat <<'EOF'
Usage: bash verify.sh [--fixture <id>]

Verify prepared pre/post fixtures.
Run prepare.sh first.

Options:
  --fixture ID         Limit verification to one fixture ID from fixtures/manifest.json.
  --help               Show this help text.
EOF
}

fail() {
  echo "error: $*" >&2
  exit 1
}

log() {
  echo "[verify] $*"
}

warn() {
  echo "[verify] warning: $*" >&2
}

require_cmd() {
  local cmd="$1"
  command -v "${cmd}" >/dev/null 2>&1 || fail "required command '${cmd}' not found"
}

fixture_id=""
while (($# > 0)); do
  case "$1" in
    --fixture)
      shift
      [[ $# -gt 0 ]] || fail "--fixture requires a value"
      fixture_id="$1"
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

is_lfs_pointer_file() {
  local path="$1"
  [[ -f "${path}" ]] || return 1
  local first_line
  IFS= read -r first_line <"${path}" || return 1
  [[ "${first_line}" == "version https://git-lfs.github.com/spec/v1" ]]
}

collect_relevant_model_files() {
  local repo_dir="$1"
  while IFS= read -r abs_path; do
    [[ -n "${abs_path}" ]] || continue
    echo "${abs_path#"${repo_dir}/"}"
  done < <(
    find "${repo_dir}" -type f \
      \( -name '*.onnx' \
      -o -name '*.h5' \
      -o -name '*.hdf5' \
      -o -name '*.keras' \
      -o -name '*.pt' \
      -o -name '*.pth' \
      -o -name '*.ckpt' \
      -o -name '*.pb' \
      -o -name '*.tflite' \
      -o -name '*.engine' \) \
      | sort
  )
}

assert_relevant_model_files_hydrated() {
  local repo_dir="$1"
  local label="$2"
  local strict_lfs="${STRICT_FIXTURE_LFS:-0}"

  local relevant_model_files=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] || continue
    relevant_model_files+=("${rel_path}")
  done < <(collect_relevant_model_files "${repo_dir}")

  ((${#relevant_model_files[@]} > 0)) || return 0

  local unresolved_lfs=()
  local rel_path
  for rel_path in "${relevant_model_files[@]}"; do
    if is_lfs_pointer_file "${repo_dir}/${rel_path}"; then
      unresolved_lfs+=("${rel_path}")
    fi
  done

  if ((${#unresolved_lfs[@]} > 0)); then
    if [[ "${strict_lfs}" == "1" ]]; then
      fail "${label}: relevant model files are still LFS pointers: ${unresolved_lfs[*]}"
    fi
    warn "${label}: relevant model files are still LFS pointers (best-effort mode): ${unresolved_lfs[*]}"
  fi
}

strip_entry_covers_path() {
  local needle="$1"
  shift
  local item
  for item in "$@"; do
    [[ "${item}" == "${needle}" ]] && return 0
    [[ "${needle}" == "${item}/"* ]] && return 0
  done
  return 1
}

collect_python_code_loader_files() {
  local repo_dir="$1"
  while IFS= read -r abs_path; do
    [[ -n "${abs_path}" ]] || continue
    echo "${abs_path#"${repo_dir}/"}"
  done < <(rg -l --glob '*.py' 'code_loader|inner_leap_binder|leapbinder_decorators' "${repo_dir}" | sort || true)
}

# A code-loader pin in a dependency file is as much of a giveaway as an import:
# it tells the agent this is a Tensorleap project before it reads any code. The
# `tensorleap`-substring scan below does not catch the string `code-loader`.
collect_code_loader_dependency_files() {
  local repo_dir="$1"
  while IFS= read -r abs_path; do
    [[ -n "${abs_path}" ]] || continue
    echo "${abs_path#"${repo_dir}/"}"
  done < <(
    rg -l --glob 'pyproject.toml' --glob 'poetry.lock' --glob 'requirements*.txt' \
      'code[-_]loader' "${repo_dir}" | sort || true
  )
}

collect_tensorleap_folder_dirs() {
  local repo_dir="$1"
  while IFS= read -r abs_path; do
    [[ -n "${abs_path}" ]] || continue
    echo "${abs_path#"${repo_dir}/"}"
  done < <(find "${repo_dir}" -type d \( -name 'tensorleap_folder' -o -name '.tensorleap' \) | sort)
}

collect_tensorleap_mapping_files() {
  local repo_dir="$1"
  while IFS= read -r abs_path; do
    [[ -n "${abs_path}" ]] || continue
    echo "${abs_path#"${repo_dir}/"}"
  done < <(find "${repo_dir}" -type f \( -name 'leap_mapping*.yaml' -o -name 'leap_mapping*.yml' \) | sort)
}

should_ignore_tensorleap_text_match() {
  local rel_path="$1"
  [[ "$(basename "${rel_path}")" == "pyproject.toml" ]]
}

collect_tensorleap_text_files() {
  local repo_dir="$1"
  while IFS= read -r abs_path; do
    [[ -n "${abs_path}" ]] || continue
    local rel_path="${abs_path#"${repo_dir}/"}"
    should_ignore_tensorleap_text_match "${rel_path}" && continue
    echo "${rel_path}"
  done < <(rg -n --ignore-case --files-with-matches "tensorleap" "${repo_dir}" || true)
}

assert_clean_git_tree() {
  local dir="$1"
  local label="$2"
  if [[ -n "$(git -C "${dir}" status --porcelain)" ]]; then
    fail "${label} is not a clean git tree: ${dir}"
  fi
}

path_is_listed() {
  local needle="$1"
  shift
  local item
  for item in "$@"; do
    [[ "${item}" == "${needle}" ]] && return 0
  done
  return 1
}

assert_placeholder_readme() {
  local path="$1"
  local label="$2"
  [[ -f "${path}" ]] || fail "${label} expected placeholder README at ${path}"
  grep -qxF "${FIXTURE_PLACEHOLDER_README_HEADING}" "${path}" \
    || fail "${label} placeholder README missing heading '${FIXTURE_PLACEHOLDER_README_HEADING}': ${path}"
}

assert_fixture_reset_script() {
  local repo_dir="$1"
  local label="$2"
  local script_path="${repo_dir}/.fixture_reset.sh"

  [[ -f "${script_path}" ]] || fail "${label} is missing .fixture_reset.sh"
  [[ -x "${script_path}" ]] || fail "${label} has a non-executable .fixture_reset.sh"
}

assert_canonical_entrypoint() {
  local repo_dir="$1"
  local label="$2"
  local leap_yaml_path="${repo_dir}/leap.yaml"
  local entry_file

  [[ -f "${leap_yaml_path}" ]] || fail "${label} is missing leap.yaml"
  entry_file="$(fixture_extract_leap_yaml_entry_file "${leap_yaml_path}")"
  [[ "${entry_file}" == "leap_integration.py" ]] \
    || fail "${label} must use leap_integration.py as leap.yaml entryFile, found '${entry_file:-<empty>}'"
  [[ -f "${repo_dir}/leap_integration.py" ]] || fail "${label} is missing leap_integration.py"
}

choose_reset_probe_file() {
  local repo_dir="$1"
  local rel_path

  rel_path="$(git -C "${repo_dir}" ls-files -- '*.md' '*.py' '*.yaml' '*.yml' '*.txt' | head -n 1)"
  if [[ -z "${rel_path}" ]]; then
    rel_path="$(git -C "${repo_dir}" ls-files | head -n 1)"
  fi

  [[ -n "${rel_path}" ]] || fail "could not find a tracked file to dirty in ${repo_dir}"
  printf '%s\n' "${rel_path}"
}

dirty_repo_for_reset_check() {
  local repo_dir="$1"
  local label="$2"
  local probe_file
  probe_file="$(choose_reset_probe_file "${repo_dir}")"

  printf '\n# fixture reset probe\n' >>"${repo_dir}/${probe_file}"
  touch "${repo_dir}/.fixture_reset_probe.tmp"

  if [[ -z "$(git -C "${repo_dir}" status --porcelain)" ]]; then
    fail "${label} did not become dirty during reset-script verification"
  fi
}

exercise_fixture_reset_script() {
  local repo_dir="$1"
  local label="$2"
  local expected_head="$3"
  local script_path="${repo_dir}/.fixture_reset.sh"

  assert_fixture_reset_script "${repo_dir}" "${label}"
  dirty_repo_for_reset_check "${repo_dir}" "${label}"
  "${script_path}"

  local actual_head
  actual_head="$(git -C "${repo_dir}" rev-parse HEAD)"
  [[ "${actual_head}" == "${expected_head}" ]] \
    || fail "${label} reset script restored unexpected HEAD: expected ${expected_head}, got ${actual_head}"

  assert_clean_git_tree "${repo_dir}" "${label}"
}

require_cmd git
require_cmd jq
require_cmd rg
[[ -f "${MANIFEST_PATH}" ]] || fail "manifest not found: ${MANIFEST_PATH}"
[[ -f "${RESET_LIB_PATH}" ]] || fail "fixture reset library not found: ${RESET_LIB_PATH}"

jq -e '.fixtures and (.fixtures | type == "array")' "${MANIFEST_PATH}" >/dev/null \
  || fail "invalid manifest schema in ${MANIFEST_PATH}"
if [[ -n "${fixture_id}" ]]; then
  jq -e --arg id "${fixture_id}" '.fixtures[] | select(.id == $id)' "${MANIFEST_PATH}" >/dev/null \
    || fail "unknown fixture id '${fixture_id}' (see ${MANIFEST_PATH})"
fi

log "Manifest: ${MANIFEST_PATH}"
log "Fixture output root: ${FIXTURES_ROOT}"
if [[ -n "${fixture_id}" ]]; then
  log "Target fixture: ${fixture_id}"
fi

fixture_filter='.fixtures[]'
if [[ -n "${fixture_id}" ]]; then
  fixture_filter='.fixtures[] | select(.id == $id)'
fi

while IFS= read -r fixture_json; do
  id="$(jq -r '.id // empty' <<<"${fixture_json}")"
  post_ref="$(jq -r '.post_ref // empty' <<<"${fixture_json}")"
  pre_ref="$(fixture_entry_pre_ref "${fixture_json}")"
  blind_text_allowlist=()
  while IFS= read -r allow_path; do
    [[ -n "${allow_path}" ]] && blind_text_allowlist+=("${allow_path}")
  done < <(fixture_entry_text_allowlist "${fixture_json}")
  [[ -n "${id}" ]] || fail "fixture id is missing in manifest entry: ${fixture_json}"
  [[ -n "${post_ref}" ]] || fail "fixture post_ref is missing for fixture '${id}'"

  strip_files=()
  while IFS= read -r rel_path; do
    strip_files+=("${rel_path}")
  done < <(jq -r '.strip_for_pre[]?' <<<"${fixture_json}")
  if [[ -z "${pre_ref}" ]]; then
    ((${#strip_files[@]} > 0)) || fail "fixture '${id}' has empty strip_for_pre list (required unless pre_ref is set)"
  fi

  stripped_py_basenames=()
  while IFS= read -r base_name; do
    [[ -n "${base_name}" ]] || continue
    stripped_py_basenames+=("${base_name}")
  done < <(fixture_list_stripped_python_basenames ${strip_files[@]+"${strip_files[@]}"})

  fixture_root="${FIXTURES_ROOT}/${id}"
  post_dir="${fixture_root}/post"
  pre_dir="${fixture_root}/pre"

  log "Verifying fixture '${id}'"
  [[ -d "${post_dir}/.git" ]] || fail "post variant missing git repo for fixture '${id}'"
  [[ -d "${pre_dir}/.git" ]] || fail "pre variant missing git repo for fixture '${id}'"
  post_head="$(git -C "${post_dir}" rev-parse HEAD)"
  assert_fixture_reset_script "${post_dir}" "post variant for fixture '${id}'"
  # Blind pre variants have no reset script (history is scrubbed by design).
  assert_canonical_entrypoint "${post_dir}" "post variant for fixture '${id}'"

  declared_pre_readmes=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] || continue
    declared_pre_readmes+=("${rel_path}")
  done < <(fixture_collect_declared_readme_files "${pre_dir}")

  # strip_for_pre expectations describe the derive-from-post model (file present
  # in post, absent from pre). With an explicit pre_ref the pre tree is sourced
  # directly, so pre cleanliness is checked by the residual scans below instead.
  if [[ -z "${pre_ref}" ]]; then
    log "  Checking strip_for_pre expectations across post/pre variants"
    for rel_path in ${strip_files[@]+"${strip_files[@]}"}; do
      [[ -e "${post_dir}/${rel_path}" ]] || fail "fixture '${id}': '${rel_path}' missing in post variant"
      if path_is_listed "${rel_path}" ${declared_pre_readmes[@]+"${declared_pre_readmes[@]}"}; then
        assert_placeholder_readme "${pre_dir}/${rel_path}" "fixture '${id}'"
        continue
      fi
      [[ ! -e "${pre_dir}/${rel_path}" ]] || fail "fixture '${id}': '${rel_path}' still exists in pre variant"
    done
  fi

  # Coverage checks apply only to the derive-from-post model (skipped for pre_ref).
  if [[ -z "${pre_ref}" ]]; then
    post_root_leap_files=()
    while IFS= read -r rel_path; do
      [[ -n "${rel_path}" ]] && post_root_leap_files+=("${rel_path}")
    done < <(find "${post_dir}" -maxdepth 1 -type f -name 'leap*' -exec basename {} \; | sort)
    for rel_path in "${post_root_leap_files[@]}"; do
      strip_entry_covers_path "${rel_path}" "${strip_files[@]}" \
        || fail "fixture '${id}': root leap file '${rel_path}' is present in post but missing from strip_for_pre"
    done

    post_tensorleap_files=()
    while IFS= read -r rel_path; do
      [[ -n "${rel_path}" ]] || continue
      post_tensorleap_files+=("${rel_path}")
    done < <(collect_tensorleap_text_files "${post_dir}")
    for rel_path in ${post_tensorleap_files[@]+"${post_tensorleap_files[@]}"}; do
      if ! strip_entry_covers_path "${rel_path}" ${strip_files[@]+"${strip_files[@]}"}; then
        if fixture_blind_text_exempt "${pre_ref}" "${rel_path}" ${blind_text_allowlist[@]+"${blind_text_allowlist[@]}"}; then
          warn "fixture '${id}': blind pre keeps post file '${rel_path}' containing 'tensorleap' (allowlisted)"
        else
          fail "fixture '${id}': post file '${rel_path}' contains 'tensorleap' but is missing from strip_for_pre"
        fi
      fi
    done

    post_code_loader_python_files=()
    while IFS= read -r rel_path; do
      [[ -n "${rel_path}" ]] || continue
      post_code_loader_python_files+=("${rel_path}")
    done < <(collect_python_code_loader_files "${post_dir}")
    for rel_path in "${post_code_loader_python_files[@]}"; do
      strip_entry_covers_path "${rel_path}" "${strip_files[@]}" \
        || fail "fixture '${id}': post Python file '${rel_path}' imports code_loader but is missing from strip_for_pre"
    done

    post_tensorleap_dirs=()
    while IFS= read -r rel_path; do
      [[ -n "${rel_path}" ]] || continue
      post_tensorleap_dirs+=("${rel_path}")
    done < <(collect_tensorleap_folder_dirs "${post_dir}")
    for rel_path in "${post_tensorleap_dirs[@]}"; do
      strip_entry_covers_path "${rel_path}" "${strip_files[@]}" \
        || fail "fixture '${id}': post directory '${rel_path}' is Tensorleap-only content but is missing from strip_for_pre"
    done

    post_tensorleap_mapping_files=()
    while IFS= read -r rel_path; do
      [[ -n "${rel_path}" ]] || continue
      post_tensorleap_mapping_files+=("${rel_path}")
    done < <(collect_tensorleap_mapping_files "${post_dir}")
    for rel_path in "${post_tensorleap_mapping_files[@]}"; do
      strip_entry_covers_path "${rel_path}" "${strip_files[@]}" \
        || fail "fixture '${id}': post mapping file '${rel_path}' is Tensorleap-only content but is missing from strip_for_pre"
    done
  fi

  pre_root_leap_files=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] && pre_root_leap_files+=("${rel_path}")
  done < <(find "${pre_dir}" -maxdepth 1 -type f -name 'leap*' -exec basename {} \; | sort)
  ((${#pre_root_leap_files[@]} == 0)) \
    || fail "fixture '${id}': pre variant still has root leap files: ${pre_root_leap_files[*]}"

  pre_leap_pyc_files=()
  while IFS= read -r abs_path; do
    [[ -n "${abs_path}" ]] || continue
    pre_leap_pyc_files+=("${abs_path#"${pre_dir}/"}")
  done < <(find "${pre_dir}" -type f -path '*/__pycache__/leap*.pyc' | sort)
  ((${#pre_leap_pyc_files[@]} == 0)) \
    || fail "fixture '${id}': pre variant has compiled leap artifacts: ${pre_leap_pyc_files[*]}"

  base_name=""
  for base_name in ${stripped_py_basenames[@]+"${stripped_py_basenames[@]}"}; do
    pre_compiled_matches=()
    while IFS= read -r abs_path; do
      [[ -n "${abs_path}" ]] || continue
      pre_compiled_matches+=("${abs_path#"${pre_dir}/"}")
    done < <(find "${pre_dir}" -type f -path "*/__pycache__/${base_name}*.pyc" | sort)
    ((${#pre_compiled_matches[@]} == 0)) \
      || fail "fixture '${id}': pre variant has compiled artifacts for stripped '${base_name}.py': ${pre_compiled_matches[*]}"
  done

  pre_code_loader_python_files=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] || continue
    pre_code_loader_python_files+=("${rel_path}")
  done < <(collect_python_code_loader_files "${pre_dir}")
  ((${#pre_code_loader_python_files[@]} == 0)) \
    || fail "fixture '${id}': pre variant contains Python files importing code_loader: ${pre_code_loader_python_files[*]}"

  pre_code_loader_dependency_files=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] || continue
    pre_code_loader_dependency_files+=("${rel_path}")
  done < <(collect_code_loader_dependency_files "${pre_dir}")
  ((${#pre_code_loader_dependency_files[@]} == 0)) \
    || fail "fixture '${id}': pre variant declares code-loader as a dependency in: ${pre_code_loader_dependency_files[*]} (the agent installs code-loader itself; a pin here leaks that this is a Tensorleap project)"

  pre_tensorleap_dirs=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] || continue
    pre_tensorleap_dirs+=("${rel_path}")
  done < <(collect_tensorleap_folder_dirs "${pre_dir}")
  ((${#pre_tensorleap_dirs[@]} == 0)) \
    || fail "fixture '${id}': pre variant contains Tensorleap directories: ${pre_tensorleap_dirs[*]}"

  pre_tensorleap_mapping_files=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] || continue
    pre_tensorleap_mapping_files+=("${rel_path}")
  done < <(collect_tensorleap_mapping_files "${pre_dir}")
  ((${#pre_tensorleap_mapping_files[@]} == 0)) \
    || fail "fixture '${id}': pre variant contains Tensorleap mapping files: ${pre_tensorleap_mapping_files[*]}"

  pre_tensorleap_files=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] || continue
    pre_tensorleap_files+=("${rel_path}")
  done < <(collect_tensorleap_text_files "${pre_dir}")
  for rel_path in ${pre_tensorleap_files[@]+"${pre_tensorleap_files[@]}"}; do
    if fixture_blind_text_exempt "${pre_ref}" "${rel_path}" ${blind_text_allowlist[@]+"${blind_text_allowlist[@]}"}; then
      warn "fixture '${id}': blind pre variant keeps '${rel_path}' containing 'tensorleap' (exempt: pre_ref/allowlist)"
    else
      fail "fixture '${id}': pre variant contains files with 'tensorleap': ${rel_path}"
    fi
  done

  git -C "${post_dir}" merge-base --is-ancestor "${post_ref}" HEAD >/dev/null 2>&1 \
    || fail "fixture '${id}': post variant does not derive from post_ref '${post_ref}'"
  # No equivalent check on pre: it deliberately has no shared history with
  # post_ref — that is the point of scrubbing, asserted just below.

  assert_relevant_model_files_hydrated "${post_dir}" "post variant for fixture '${id}'"
  assert_clean_git_tree "${post_dir}" "post variant for fixture '${id}'"
  assert_clean_git_tree "${pre_dir}" "pre variant for fixture '${id}'"

  # The pre variant must be uncheatable: no remote and a single rootless commit.
  [[ -z "$(git -C "${pre_dir}" remote)" ]] \
    || fail "fixture '${id}': pre variant must have no git remote"
  git -C "${pre_dir}" rev-parse --verify --quiet HEAD^ >/dev/null 2>&1 \
    && fail "fixture '${id}': pre variant must be a single rootless commit (history not scrubbed)"

  exercise_fixture_reset_script "${post_dir}" "post variant for fixture '${id}'" "${post_head}"

  log "Verified fixture '${id}'"
done < <(jq -c --arg id "${fixture_id}" "${fixture_filter}" "${MANIFEST_PATH}")

log "Fixture verification complete"
