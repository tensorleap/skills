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
BOOTSTRAP_SCRIPT_PATH="${REPO_ROOT}/bootstrap_poetry.sh"

# shellcheck source=./fixtures_reset_lib.sh
source "${RESET_LIB_PATH}"

usage() {
  cat <<'EOF'
Usage: bash scripts/fixtures_prepare.sh [--fixture <id>] [--bootstrap-poetry]

Prepare the pinned pre/post fixture repositories in .fixtures/.

Options:
  --fixture ID         Limit preparation to one fixture ID from fixtures/manifest.json.
  --bootstrap-poetry   After preparing fixtures, bootstrap Poetry environments explicitly.
  --help               Show this help text.
EOF
}

fail() {
  echo "error: $*" >&2
  exit 1
}

log() {
  echo "[fixtures_prepare] $*"
}

warn() {
  echo "[fixtures_prepare] warning: $*" >&2
}

require_cmd() {
  local cmd="$1"
  command -v "${cmd}" >/dev/null 2>&1 || fail "required command '${cmd}' not found"
}

bootstrap_poetry=0
fixture_id=""
fixture_auth_tmpdir=""
checkout_github_extraheaders_file=""
fixture_git_auth_mode=""
while (($# > 0)); do
  case "$1" in
    --fixture)
      shift
      [[ $# -gt 0 ]] || fail "--fixture requires a value"
      fixture_id="$1"
      ;;
    --bootstrap-poetry)
      bootstrap_poetry=1
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

ensure_relevant_model_lfs_hydrated() {
  local repo_dir="$1"
  local label="$2"
  local strict_lfs="${STRICT_FIXTURE_LFS:-0}"

  local relevant_model_files=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] || continue
    relevant_model_files+=("${rel_path}")
  done < <(collect_relevant_model_files "${repo_dir}")

  ((${#relevant_model_files[@]} > 0)) || return 0

  local lfs_pointer_files=()
  local rel_path
  for rel_path in "${relevant_model_files[@]}"; do
    if is_lfs_pointer_file "${repo_dir}/${rel_path}"; then
      lfs_pointer_files+=("${rel_path}")
    fi
  done

  ((${#lfs_pointer_files[@]} > 0)) || return 0

  git lfs version >/dev/null 2>&1 \
    || fail "${label}: found LFS pointer model files but git-lfs is unavailable: ${lfs_pointer_files[*]}"

  local include_csv
  include_csv="$(printf '%s,' "${lfs_pointer_files[@]}")"
  include_csv="${include_csv%,}"

  log "  Hydrating ${#lfs_pointer_files[@]} LFS model file(s) in ${label}"
  if ! git_fixture -C "${repo_dir}" lfs pull --include "${include_csv}" --exclude ""; then
    if [[ "${strict_lfs}" == "1" ]]; then
      fail "${label}: git lfs pull failed for model file(s): ${lfs_pointer_files[*]}"
    fi
    warn "${label}: git lfs pull failed in best-effort mode for model file(s): ${lfs_pointer_files[*]}"
  fi

  local unresolved_lfs=()
  for rel_path in "${lfs_pointer_files[@]}"; do
    if is_lfs_pointer_file "${repo_dir}/${rel_path}"; then
      unresolved_lfs+=("${rel_path}")
    fi
  done

  if ((${#unresolved_lfs[@]} > 0)); then
    if [[ "${strict_lfs}" == "1" ]]; then
      fail "${label}: model file(s) still unresolved after git lfs pull: ${unresolved_lfs[*]}"
    fi
    warn "${label}: model file(s) remained as LFS pointers after pull (best-effort mode): ${unresolved_lfs[*]}"
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

assert_guide_native_post_variant() {
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

write_fixture_reset_script() {
  local repo_dir="$1"
  local variant_kind="$2"
  local post_ref="$3"
  shift 3

  local strip_files=("$@")
  local script_path="${repo_dir}/.fixture_reset.sh"

  {
    echo '#!/usr/bin/env bash'
    echo 'set -euo pipefail'
    echo
    echo 'SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"'
    echo 'EVAL_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"'
    echo
    echo '# shellcheck source=/dev/null'
    echo 'source "${EVAL_ROOT}/lib/reset_lib.sh"'
    printf 'POST_REF=%q\n' "${post_ref}"
    echo

    if [[ "${variant_kind}" == "pre" ]]; then
      echo 'STRIP_FILES=('
      local rel_path
      for rel_path in "${strip_files[@]}"; do
        printf '  %q\n' "${rel_path}"
      done
      echo ')'
      echo
      echo 'fixture_reset_pre_variant "${SCRIPT_DIR}" "${POST_REF}" "${STRIP_FILES[@]}"'
    else
      echo 'fixture_reset_post_variant "${SCRIPT_DIR}" "${POST_REF}"'
    fi
  } >"${script_path}"

  chmod +x "${script_path}"
  fixture_add_local_exclude "${repo_dir}" "/.fixture_reset.sh"
}

reset_fixture_dir() {
  local dir="$1"
  [[ -e "${dir}" ]] || return 0
  chmod -R u+w "${dir}" 2>/dev/null || true
  find "${dir}" -depth -delete
  [[ ! -e "${dir}" ]] || fail "failed to reset fixture directory: ${dir}"
}

resolve_tensorleap_hub_git_credential_helper() {
  if [[ "${TENSORLEAP_HUB_DISABLE_GIT_CREDENTIAL_HELPER:-0}" == "1" ]]; then
    return 1
  fi

  local configured_helper="${TENSORLEAP_HUB_GIT_CREDENTIAL_HELPER:-}"
  if [[ -n "${configured_helper}" ]]; then
    if [[ ! -x "${configured_helper}" ]]; then
      echo "error: TENSORLEAP_HUB_GIT_CREDENTIAL_HELPER is set but is not executable: ${configured_helper}" >&2
      return 2
    fi
    printf '%s\n' "${configured_helper}"
    return 0
  fi

  if command -v github-app-git-credential >/dev/null 2>&1; then
    command -v github-app-git-credential
    return 0
  fi

  return 1
}

write_fixture_auth_gitconfig() {
  local github_helper="$1"

  {
    printf '[credential]\n'
    printf '\thelper =\n'
    printf '[credential "https://github.com"]\n'
    printf '\thelper ='
    if [[ -n "${github_helper}" ]]; then
      printf ' %s' "${github_helper}"
    fi
    printf '\n'
  } >"${tensorleap_hub_gitconfig}"
}

require_cmd git
require_cmd jq
require_cmd poetry
require_cmd rg
require_cmd python3
[[ -f "${MANIFEST_PATH}" ]] || fail "manifest not found: ${MANIFEST_PATH}"
[[ -f "${RESET_LIB_PATH}" ]] || fail "fixture reset library not found: ${RESET_LIB_PATH}"
[[ -f "${BOOTSTRAP_SCRIPT_PATH}" ]] || fail "fixture bootstrap script not found: ${BOOTSTRAP_SCRIPT_PATH}"

cleanup_fixture_auth() {
  if [[ -n "${checkout_github_extraheaders_file}" ]]; then
    git config --local --unset-all http.https://github.com/.extraheader 2>/dev/null || true
    while IFS= read -r extraheader; do
      [[ -n "${extraheader}" ]] || continue
      git config --local --add http.https://github.com/.extraheader "${extraheader}" || true
    done <"${checkout_github_extraheaders_file}"
  fi
  if [[ -n "${fixture_auth_tmpdir}" ]]; then
    rm -rf "${fixture_auth_tmpdir}"
  fi
}
trap cleanup_fixture_auth EXIT

prepare_fixture_auth_workspace() {
  fixture_auth_tmpdir="$(mktemp -d)"
  tensorleap_hub_gitconfig="${fixture_auth_tmpdir}/gitconfig"
  checkout_github_extraheaders_file="${fixture_auth_tmpdir}/checkout-extraheaders"
  git config --local --get-all http.https://github.com/.extraheader >"${checkout_github_extraheaders_file}" 2>/dev/null || true
  git config --local --unset-all http.https://github.com/.extraheader 2>/dev/null || true
}

tensorleap_hub_fixture_token="${TENSORLEAP_HUB_FIXTURE_TOKEN:-}"
tensorleap_hub_read_token="${TENSORLEAP_HUB_READ_TOKEN:-}"
tensorleap_hub_askpass=""
tensorleap_hub_gitconfig=""
tensorleap_hub_git_credential_helper=""

if [[ -n "${tensorleap_hub_fixture_token}" ]]; then
  fixture_git_auth_mode="token"
  tensorleap_hub_read_token="${tensorleap_hub_fixture_token}"
  log "Using TENSORLEAP_HUB_FIXTURE_TOKEN for private Tensorleap Hub fixture clones"
else
  if tensorleap_hub_git_credential_helper="$(resolve_tensorleap_hub_git_credential_helper)"; then
    :
  else
    helper_status=$?
    [[ "${helper_status}" == "1" ]] \
      || fail "failed to resolve Tensorleap Hub git credential helper"
    tensorleap_hub_git_credential_helper=""
  fi

  if [[ -n "${tensorleap_hub_git_credential_helper}" ]]; then
    fixture_git_auth_mode="helper"
    log "Using GitHub App git credential helper for private Tensorleap Hub fixture clones"
  elif [[ -n "${tensorleap_hub_read_token}" ]]; then
    fixture_git_auth_mode="token"
    log "Using TENSORLEAP_HUB_READ_TOKEN for private Tensorleap Hub fixture clones"
  fi
fi

if [[ "${fixture_git_auth_mode}" == "token" ]]; then
  prepare_fixture_auth_workspace
  tensorleap_hub_askpass="${fixture_auth_tmpdir}/askpass"
  write_fixture_auth_gitconfig ""
  cat >"${tensorleap_hub_askpass}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  *Username*) printf '%s\n' "x-access-token" ;;
  *Password*) printf '%s\n' "${TENSORLEAP_HUB_GIT_TOKEN:?}" ;;
  *) printf '\n' ;;
esac
EOF
  chmod 700 "${tensorleap_hub_askpass}"
elif [[ "${fixture_git_auth_mode}" == "helper" ]]; then
  prepare_fixture_auth_workspace
  write_fixture_auth_gitconfig "${tensorleap_hub_git_credential_helper}"
fi

jq -e '.fixtures and (.fixtures | type == "array")' "${MANIFEST_PATH}" >/dev/null \
  || fail "invalid manifest schema in ${MANIFEST_PATH}"

if [[ -n "${fixture_id}" ]]; then
  jq -e --arg id "${fixture_id}" '.fixtures[] | select(.id == $id)' "${MANIFEST_PATH}" >/dev/null \
    || fail "unknown fixture id '${fixture_id}' (see ${MANIFEST_PATH})"
fi

git_fixture() {
  local -a git_args=()

  (
    export GIT_LFS_SKIP_SMUDGE=1

    if [[ "${fixture_git_auth_mode}" == "token" || "${fixture_git_auth_mode}" == "helper" ]]; then
      export GIT_TERMINAL_PROMPT=0
      export GIT_CONFIG_GLOBAL="${tensorleap_hub_gitconfig}"
      git_args=(
        -c credential.useHttpPath=true
        -c http.https://github.com/.extraheader=
      )

      if [[ "${fixture_git_auth_mode}" == "token" ]]; then
        export TENSORLEAP_HUB_GIT_TOKEN="${tensorleap_hub_read_token}"
        export GIT_ASKPASS="${tensorleap_hub_askpass}"
        git_args=(
          -c credential.helper=
          -c credential.https://github.com.helper=
          "${git_args[@]}"
        )
      fi
    fi

    git ${git_args[@]+"${git_args[@]}"} "$@"
  )
}

mkdir -p "${FIXTURES_ROOT}"
log "Manifest: ${MANIFEST_PATH}"
log "Fixture output root: ${FIXTURES_ROOT}"
if [[ -n "${fixture_id}" ]]; then
  log "Target fixture: ${fixture_id}"
fi

manifest_filter='.fixtures[]'
if [[ -n "${fixture_id}" ]]; then
  manifest_filter='.fixtures[] | select(.id == $id)'
fi

while IFS= read -r fixture_json; do
  id="$(jq -r '.id // empty' <<<"${fixture_json}")"
  repo="$(jq -r '.repo // empty' <<<"${fixture_json}")"
  post_ref="$(jq -r '.post_ref // empty' <<<"${fixture_json}")"
  pre_ref="$(fixture_entry_pre_ref "${fixture_json}")"
  blind="$(fixture_entry_blind "${fixture_json}")"
  blind_text_allowlist=()
  while IFS= read -r allow_path; do
    [[ -n "${allow_path}" ]] && blind_text_allowlist+=("${allow_path}")
  done < <(fixture_entry_text_allowlist "${fixture_json}")

  [[ -n "${id}" ]] || fail "fixture id is missing in manifest entry: ${fixture_json}"
  [[ -n "${repo}" ]] || fail "fixture repo is missing for fixture '${id}'"
  [[ -n "${post_ref}" ]] || fail "fixture post_ref is missing for fixture '${id}'"

  strip_files=()
  while IFS= read -r rel_path; do
    strip_files+=("${rel_path}")
  done < <(jq -r '.strip_for_pre[]?' <<<"${fixture_json}")
  # strip_for_pre is required when the pre variant is derived from post_ref; an
  # explicit pre_ref supplies the pre tree directly, so strip may be empty.
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

  log "Preparing fixture '${id}'"
  log "  Source repository: ${repo}"
  log "  Pinned post_ref: ${post_ref}"
  log "  Resetting existing fixture directories"
  reset_fixture_dir "${post_dir}"
  reset_fixture_dir "${pre_dir}"
  mkdir -p "${fixture_root}"

  log "  Cloning post variant and checking out pinned commit"
  git_fixture clone --quiet --no-checkout --filter=blob:none "${repo}" "${post_dir}"
  git_fixture -C "${post_dir}" checkout --quiet "${post_ref}"
  ensure_relevant_model_lfs_hydrated "${post_dir}" "post variant for fixture '${id}'"
  assert_guide_native_post_variant "${post_dir}" "post variant for fixture '${id}'"
  post_pin_info="$(fixture_detect_code_loader_pin "${post_dir}")"
  post_pin_version="${post_pin_info#*|}"
  if [[ -z "${post_pin_info}" ]] || ! fixture_version_at_least "${post_pin_version}" "$(fixture_min_code_loader_version)"; then
    log "  Refreshing local code-loader pin for post variant"
    fixture_prepare_local_code_loader_pin "${post_dir}" "post variant for fixture '${id}'"
  fi
  fixture_assert_min_code_loader_pin "${post_dir}" "post variant for fixture '${id}'"
  prepared_post_ref="$(git -C "${post_dir}" rev-parse HEAD)"
  write_fixture_reset_script "${post_dir}" post "${prepared_post_ref}"

  # strip_for_pre is removed from post to derive pre, so it must exist in post.
  # With an explicit pre_ref, strip targets the pre tree (which may contain files
  # absent from post), so this post-existence check does not apply.
  if [[ -z "${pre_ref}" ]]; then
    log "  Verifying required integration files exist in post variant"
    for rel_path in ${strip_files[@]+"${strip_files[@]}"}; do
      [[ -e "${post_dir}/${rel_path}" ]] || fail "fixture '${id}' missing '${rel_path}' in post variant"
    done
  fi

  # Coverage checks below verify that strip_for_pre removes every integration
  # artifact when the pre variant is DERIVED from post. They are skipped when an
  # explicit pre_ref supplies the pre tree (its cleanliness is checked directly
  # on the pre variant further down).
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
        # A strip-derived blind fixture may keep a file only when it is explicitly
        # allowlisted as incidental (e.g. a config bucket name); otherwise an
        # uncovered 'tensorleap' file is treated as incomplete stripping and fails.
        if fixture_blind_text_exempt "${blind}" "${pre_ref}" "${rel_path}" ${blind_text_allowlist[@]+"${blind_text_allowlist[@]}"}; then
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

  assert_clean_git_tree "${post_dir}" "post variant for fixture '${id}'"

  log "  Creating pre variant from source repository"
  pre_source_checkout="${pre_ref:-${post_ref}}"
  git_fixture clone --quiet --no-checkout --filter=blob:none "${repo}" "${pre_dir}"
  git_fixture -C "${pre_dir}" checkout --quiet "${pre_source_checkout}"
  ensure_relevant_model_lfs_hydrated "${pre_dir}" "pre variant source for fixture '${id}'"

  if [[ "${blind}" == "1" ]]; then
    # Blind eval fixture: build the pre tree (optionally stripping integration
    # files), then scrub history so the "after" cannot be recovered via git.
    log "  Building blind pre variant (history scrubbed)"
    if ((${#strip_files[@]} > 0)); then
      fixture_strip_pre_variant_files "${pre_dir}" "${strip_files[@]}"
    fi
    fixture_make_blind_variant "${pre_dir}" "Create pre-integration fixture variant (blind)"
  else
    fixture_prepare_local_code_loader_pin "${pre_dir}" "pre variant source for fixture '${id}'"
    pre_source_ref="$(git -C "${pre_dir}" rev-parse HEAD)"
    [[ "${pre_source_ref}" == "${prepared_post_ref}" ]] \
      || fail "fixture '${id}': prepared pre source ref '${pre_source_ref}' does not match prepared post ref '${prepared_post_ref}'"
    write_fixture_reset_script "${pre_dir}" pre "${prepared_post_ref}" "${strip_files[@]}"

    log "  Stripping pre-integration files from pre variant"
    "${pre_dir}/.fixture_reset.sh"
  fi

  remaining_pre_root_leap_files=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] && remaining_pre_root_leap_files+=("${rel_path}")
  done < <(find "${pre_dir}" -maxdepth 1 -type f -name 'leap*' -exec basename {} \; | sort)
  ((${#remaining_pre_root_leap_files[@]} == 0)) \
    || fail "fixture '${id}': pre variant still has root leap files after stripping: ${remaining_pre_root_leap_files[*]}"

  pre_leap_pyc_files=()
  while IFS= read -r abs_path; do
    [[ -n "${abs_path}" ]] || continue
    pre_leap_pyc_files+=("${abs_path#"${pre_dir}/"}")
  done < <(find "${pre_dir}" -type f -path '*/__pycache__/leap*.pyc' | sort)
  ((${#pre_leap_pyc_files[@]} == 0)) \
    || fail "fixture '${id}': pre variant still has compiled leap artifacts after stripping: ${pre_leap_pyc_files[*]}"

  local_base_name=""
  for local_base_name in ${stripped_py_basenames[@]+"${stripped_py_basenames[@]}"}; do
    pre_compiled_matches=()
    while IFS= read -r abs_path; do
      [[ -n "${abs_path}" ]] || continue
      pre_compiled_matches+=("${abs_path#"${pre_dir}/"}")
    done < <(find "${pre_dir}" -type f -path "*/__pycache__/${local_base_name}*.pyc" | sort)
    ((${#pre_compiled_matches[@]} == 0)) \
      || fail "fixture '${id}': pre variant still has compiled artifacts for stripped '${local_base_name}.py': ${pre_compiled_matches[*]}"
  done

  pre_code_loader_python_files=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] || continue
    pre_code_loader_python_files+=("${rel_path}")
  done < <(collect_python_code_loader_files "${pre_dir}")
  ((${#pre_code_loader_python_files[@]} == 0)) \
    || fail "fixture '${id}': pre variant still contains Python files importing code_loader: ${pre_code_loader_python_files[*]}"

  pre_tensorleap_dirs=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] || continue
    pre_tensorleap_dirs+=("${rel_path}")
  done < <(collect_tensorleap_folder_dirs "${pre_dir}")
  ((${#pre_tensorleap_dirs[@]} == 0)) \
    || fail "fixture '${id}': pre variant still contains Tensorleap directories: ${pre_tensorleap_dirs[*]}"

  pre_tensorleap_mapping_files=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] || continue
    pre_tensorleap_mapping_files+=("${rel_path}")
  done < <(collect_tensorleap_mapping_files "${pre_dir}")
  ((${#pre_tensorleap_mapping_files[@]} == 0)) \
    || fail "fixture '${id}': pre variant still contains Tensorleap mapping files: ${pre_tensorleap_mapping_files[*]}"

  pre_tensorleap_files=()
  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] || continue
    pre_tensorleap_files+=("${rel_path}")
  done < <(collect_tensorleap_text_files "${pre_dir}")
  for rel_path in ${pre_tensorleap_files[@]+"${pre_tensorleap_files[@]}"}; do
    if fixture_blind_text_exempt "${blind}" "${pre_ref}" "${rel_path}" ${blind_text_allowlist[@]+"${blind_text_allowlist[@]}"}; then
      warn "fixture '${id}': blind pre variant keeps '${rel_path}' containing 'tensorleap' (exempt: pre_ref/allowlist)"
    else
      fail "fixture '${id}': pre variant still contains files with 'tensorleap': ${rel_path}"
    fi
  done

  assert_clean_git_tree "${pre_dir}" "pre variant for fixture '${id}'"
  log "Prepared fixture '${id}' at ${fixture_root}"
done < <(jq -c --arg id "${fixture_id}" "${manifest_filter}" "${MANIFEST_PATH}")

if [[ "${bootstrap_poetry}" == "1" ]]; then
  log "Bootstrapping Poetry environments for prepared fixtures"
  bootstrap_args=(--variant all)
  if [[ -n "${fixture_id}" ]]; then
    bootstrap_args+=(--fixture "${fixture_id}")
  fi
  bash "${BOOTSTRAP_SCRIPT_PATH}" "${bootstrap_args[@]}"
fi

log "Fixture preparation complete"
