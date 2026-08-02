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

# shellcheck source=./lib/reset_lib.sh
source "${RESET_LIB_PATH}"

usage() {
  cat <<'EOF'
Usage: bash prepare.sh [--fixture <id>] [--bootstrap-poetry]

Prepare the pinned pre/post fixture repositories in .fixtures/.

Options:
  --fixture ID         Limit preparation to one fixture ID from fixtures/manifest.json.
  --bootstrap-poetry   After preparing fixtures, bootstrap Poetry environments explicitly.
  --skip-staging       Don't fetch S3 data prerequisites (assume already staged).
  --reset-only         Fast path: restore an already-prepared fixture to its blind
                       state in seconds (git reset+clean; poetry env, LFS blobs and
                       staged data are kept). Falls back to a full prepare when the
                       fixture was never fully prepared here or its manifest entry
                       changed since (post_ref bump, new strip rule, edited guidance).
  --help               Show this help text.

Environment:
  EVAL_DATA_ROOT   Where S3 data prerequisites are staged. MUST live inside a
                   mounted Tensorleap dataset volume (see `leap server info`),
                   because encoders read it from inside the evaluate pod.
                   Default: ${HOME}/tensorleap/data/eval
  AWS_PROFILE      Profile used for private buckets (default: dev). Refresh with
                   `aws sso login --profile <profile>`.

Private Tensorleap Hub fixtures need github.com read access over https. Inherited
credential helpers are deliberately cleared, so the harness must be told how to
authenticate. Resolved in this order:
  TENSORLEAP_HUB_FIXTURE_TOKEN / TENSORLEAP_HUB_READ_TOKEN (a PAT)
  TENSORLEAP_HUB_GIT_CREDENTIAL_HELPER (path to an executable helper)
  github-app-git-credential on PATH
  an authenticated `gh` CLI  <- usually already true; just run `gh auth login`
Set TENSORLEAP_HUB_DISABLE_GIT_CREDENTIAL_HELPER=1 to skip helper resolution.
EOF
}

fail() {
  echo "error: $*" >&2
  exit 1
}

log() {
  echo "[prepare] $*"
}

warn() {
  echo "[prepare] warning: $*" >&2
}

require_cmd() {
  local cmd="$1"
  command -v "${cmd}" >/dev/null 2>&1 || fail "required command '${cmd}' not found"
}

bootstrap_poetry=0
skip_staging=0
reset_only=0
eval_data_root="${EVAL_DATA_ROOT:-${HOME}/tensorleap/data/eval}"
aws_profile="${AWS_PROFILE:-dev}"
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
    --skip-staging)
      skip_staging=1
      ;;
    --reset-only)
      reset_only=1
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
  # Fatal by default: a 134-byte pointer file where a model should be does not
  # fail prep on its own, it fails later inside the agent's run as a baffling
  # model-parse error. Set STRICT_FIXTURE_LFS=0 to downgrade to a warning.
  local strict_lfs="${STRICT_FIXTURE_LFS:-1}"

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
  # filter.lfs.* normally lives in the user's GLOBAL gitconfig, which git_fixture
  # replaces with its isolated credential-only file whenever auth is active. Without
  # this, `lfs pull` reports "Git LFS is not installed for this repository", no-ops,
  # and leaves pointer files behind. --local writes the filters into this repo's own
  # .git/config, so hydration works regardless of ambient config.
  git_fixture -C "${repo_dir}" lfs install --local >/dev/null \
    || fail "${label}: git lfs install --local failed"
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

aws_s3() {
  # aws_s3 <no_sign_request:true|false> <region> <aws args...>
  local unsigned="$1" region="$2"
  shift 2
  local -a auth=(--profile "${aws_profile}")
  [[ "${unsigned}" == "true" ]] && auth=(--no-sign-request)
  aws "${auth[@]}" --region "${region}" "$@"
}

assert_staged_data_blind() {
  # Staged data is as much a leak channel as the repo: these buckets keep the
  # reference integration next to the data (renault has tl_integration_v2.zip at
  # its root). Without this check, blindness would rest on whoever wrote the
  # manifest prefix being careful. Same detectors verify.sh runs on the repo.
  local dir="$1" label="$2"
  local hits=() rel_path

  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] && hits+=("opaque archive (nothing below can scan it): ${rel_path#"${dir}/"}")
  done < <(find "${dir}" -type f \( -name '*.zip' -o -name '*.tar' -o -name '*.tar.gz' \
    -o -name '*.tgz' -o -name '*.7z' -o -name '*.rar' \) | sort)

  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] && hits+=("leap-named file: ${rel_path#"${dir}/"}")
  done < <(find "${dir}" -type f -name 'leap*' | sort)

  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] && hits+=("imports code_loader: ${rel_path}")
  done < <(collect_python_code_loader_files "${dir}")

  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] && hits+=("mentions tensorleap: ${rel_path}")
  done < <(collect_tensorleap_text_files "${dir}")

  ((${#hits[@]} == 0)) && return 0
  printf '      %s\n' "${hits[@]}" >&2
  fail "${label}: staged data could leak the reference integration (listed above). Narrow this fixture's s3_prefixes/s3_files allowlist in the manifest."
}

stage_fixture_data() {
  local id="$1" fixture_json="$2"
  local entry_count dest_root path_file
  entry_count="$(jq '[(.runtime_prerequisites // [])[] | ((.s3_files // []) + (.s3_prefixes // []))[]] | length' <<<"${fixture_json}")"
  path_file="${FIXTURES_ROOT}/${id}/staged_data_path"
  if ((entry_count == 0)); then
    rm -f "${path_file}"
    return 0
  fi

  dest_root="${eval_data_root}/${id}"
  # run.sh reads this to resolve the manifest's ${...} data placeholders — record
  # where the data actually landed rather than making run.sh re-derive it.
  printf '%s\n' "${dest_root}" >"${path_file}"
  if [[ "${skip_staging}" == "1" ]]; then
    warn "fixture '${id}': --skip-staging, assuming ${entry_count} data prerequisite(s) already at ${dest_root}"
    return 0
  fi

  require_cmd aws
  require_cmd shasum
  log "  Staging ${entry_count} data prerequisite(s) into ${dest_root}"
  mkdir -p "${dest_root}"

  local entry bucket prefix key dest region unsigned want_sum got_sum extract fmt xdest target remote_len local_len
  while IFS= read -r entry; do
    bucket="$(jq -r '.bucket' <<<"${entry}")"
    prefix="$(jq -r '.prefix' <<<"${entry}")"
    dest="$(jq -r '.dest // "."' <<<"${entry}")"
    region="$(jq -r '.region // "us-east-1"' <<<"${entry}")"
    log "    sync  s3://${bucket}/${prefix} -> ${dest}/"
    aws_s3 false "${region}" s3 sync "s3://${bucket}/${prefix}" "${dest_root}/${dest}" --only-show-errors \
      || fail "fixture '${id}': failed to sync s3://${bucket}/${prefix} (expired SSO? run: aws sso login --profile ${aws_profile})"
  done < <(jq -c '(.runtime_prerequisites // [])[] | (.s3_prefixes // [])[]' <<<"${fixture_json}")

  while IFS= read -r entry; do
    bucket="$(jq -r '.bucket' <<<"${entry}")"
    key="$(jq -r '.key' <<<"${entry}")"
    dest="$(jq -r '.dest // (.key | split("/") | last)' <<<"${entry}")"
    region="$(jq -r '.region // "us-east-1"' <<<"${entry}")"
    unsigned="$(jq -r '.no_sign_request // false' <<<"${entry}")"
    want_sum="$(jq -r '.checksum_sha256 // ""' <<<"${entry}")"
    extract="$(jq -r '.extract // false' <<<"${entry}")"
    fmt="$(jq -r '.extract_format // ""' <<<"${entry}")"
    xdest="$(jq -r '.extract_dest // "."' <<<"${entry}")"
    target="${dest_root}/${dest}"

    # Cached-object fast path: `s3 cp` is unconditional, which re-downloaded a
    # 1 GB model on every prepare. Skip when the local copy is provably the
    # remote object — checksum when the manifest pins one, remote size match
    # otherwise (guards against a partial file from a killed earlier prepare).
    # Extracted archives never qualify: extraction deletes its archive, so the
    # archive's absence says nothing about the extracted tree.
    if [[ "${extract}" != "true" && -f "${target}" ]]; then
      if [[ -n "${want_sum}" ]]; then
        if [[ "$(shasum -a 256 "${target}" | awk '{print $1}')" == "${want_sum}" ]]; then
          log "    cached ${dest} (checksum ok — skipping download)"
          continue
        fi
      else
        remote_len="$(aws_s3 "${unsigned}" "${region}" s3api head-object \
          --bucket "${bucket}" --key "${key}" --query ContentLength --output text 2>/dev/null || true)"
        local_len="$(stat -f%z "${target}" 2>/dev/null || stat -c%s "${target}" 2>/dev/null)"
        if [[ -n "${remote_len}" && "${remote_len}" == "${local_len}" ]]; then
          log "    cached ${dest} (size matches the s3 object — skipping download)"
          continue
        fi
      fi
    fi

    log "    fetch s3://${bucket}/${key} -> ${dest}"
    mkdir -p "$(dirname "${target}")"
    aws_s3 "${unsigned}" "${region}" s3 cp "s3://${bucket}/${key}" "${target}" --only-show-errors \
      || fail "fixture '${id}': failed to fetch s3://${bucket}/${key} (expired SSO? run: aws sso login --profile ${aws_profile})"

    if [[ -n "${want_sum}" ]]; then
      got_sum="$(shasum -a 256 "${target}" | awk '{print $1}')"
      [[ "${got_sum}" == "${want_sum}" ]] \
        || fail "fixture '${id}': checksum mismatch for '${dest}' (want ${want_sum}, got ${got_sum})"
      log "      checksum ok"
    fi

    if [[ "${extract}" == "true" ]]; then
      case "${fmt}" in
        tar_gz)
          mkdir -p "${dest_root}/${xdest}"
          tar -xzf "${target}" -C "${dest_root}/${xdest}" \
            || fail "fixture '${id}': failed to extract '${dest}'"
          # Must not survive: assert_staged_data_blind cannot see inside archives.
          rm -f "${target}"
          log "      extracted -> ${xdest}/"
          ;;
        *) fail "fixture '${id}': unsupported extract_format '${fmt}' for '${dest}'" ;;
      esac
    fi
  done < <(jq -c '(.runtime_prerequisites // [])[] | (.s3_files // [])[]' <<<"${fixture_json}")

  assert_staged_data_blind "${dest_root}" "fixture '${id}'"
  log "  Staged data ready: ${dest_root}"
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
  local post_ref="$2"
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

    echo 'fixture_reset_post_variant "${SCRIPT_DIR}" "${POST_REF}"'
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

  # Fall back to an authenticated gh CLI. `gh auth login` does NOT teach git how
  # to authenticate over https (with protocol=ssh it never touches git's config
  # at all), and this harness deliberately clears inherited credential helpers —
  # so being logged into gh is not enough on its own unless we ask gh directly.
  # The leading '!' registers the value as a shell command, not a binary path.
  if command -v gh >/dev/null 2>&1 && gh auth token >/dev/null 2>&1; then
    printf '%s\n' '!gh auth git-credential'
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
    log "Using git credential helper '${tensorleap_hub_git_credential_helper}' for private Tensorleap Hub fixture clones"
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

  # Fingerprint of this fixture's manifest entry (canonical JSON). Recorded at
  # full-prepare time, checked by --reset-only: a fast reset may only stand in
  # for a rebuild while the fixture's DEFINITION is unchanged.
  entry_sha="$(python3 -c 'import hashlib,json,sys; print(hashlib.sha256(json.dumps(json.loads(sys.argv[1]), sort_keys=True).encode()).hexdigest()[:12])' "${fixture_json}")"
  entry_sha_file="${fixture_root}/manifest_entry_sha"

  # --- Fast path: reset a previously prepared fixture in place --------------- #
  # A used pre tree differs from a fresh one only by the agent's uncommitted
  # edits (the blind snapshot commit is deterministic), so reset+clean restores
  # the byte-identical blind tree in seconds and keeps the expensive parts:
  # the poetry env, hydrated LFS blobs, and staged data. Safe against LFS
  # pointer corruption because fixture_make_blind_variant disables the filter
  # repo-locally before the snapshot commit — which also means fixtures
  # prepared BEFORE that fix have no sha file and take the full path here.
  if ((reset_only)); then
    if [[ -d "${pre_dir}/.git" && -f "${entry_sha_file}" \
          && "$(<"${entry_sha_file}")" == "${entry_sha}" ]]; then
      log "Fast-resetting fixture '${id}' (definition unchanged since full prepare)"
      git -C "${pre_dir}" reset --hard --quiet
      # .venv (poetry env) and .claude (deny-list) survive; everything else the
      # agent left — NOTES.md, push.log, __pycache__, scratch files — goes.
      git -C "${pre_dir}" clean -fdxq -e .venv -e .claude
      assert_clean_git_tree "${pre_dir}" "pre variant for fixture '${id}' (after fast reset)"
      log "Reset fixture '${id}' — poetry env and staged data kept (verify.sh still gates the run)"
      continue
    fi
    log "fixture '${id}': cannot fast-reset (never fully prepared here, or its manifest entry changed) — doing a full prepare"
  fi

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
  # The post variant is a static answer key — verify.sh only inspects it and
  # nothing ever executes it — so its code-loader pin is left exactly as upstream
  # committed it.
  prepared_post_ref="$(git -C "${post_dir}" rev-parse HEAD)"
  write_fixture_reset_script "${post_dir}" "${prepared_post_ref}"

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

  assert_clean_git_tree "${post_dir}" "post variant for fixture '${id}'"

  log "  Creating pre variant from source repository"
  pre_source_checkout="${pre_ref:-${post_ref}}"
  git_fixture clone --quiet --no-checkout --filter=blob:none "${repo}" "${pre_dir}"
  git_fixture -C "${pre_dir}" checkout --quiet "${pre_source_checkout}"
  ensure_relevant_model_lfs_hydrated "${pre_dir}" "pre variant source for fixture '${id}'"

  # Build the pre tree (optionally stripping integration files) and the
  # code-loader pin, then scrub history so the "after" cannot be recovered.
  log "  Building blind pre variant (history scrubbed)"
  if ((${#strip_files[@]} > 0)); then
    fixture_strip_pre_variant_files "${pre_dir}" "${strip_files[@]}"
  fi
  fixture_strip_code_loader_dependency "${pre_dir}" "pre variant for fixture '${id}'"
  fixture_make_blind_variant "${pre_dir}" "Create pre-integration fixture variant (blind)"

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
    if fixture_blind_text_exempt "${pre_ref}" "${rel_path}" ${blind_text_allowlist[@]+"${blind_text_allowlist[@]}"}; then
      warn "fixture '${id}': blind pre variant keeps '${rel_path}' containing 'tensorleap' (exempt: pre_ref/allowlist)"
    else
      fail "fixture '${id}': pre variant still contains files with 'tensorleap': ${rel_path}"
    fi
  done

  assert_clean_git_tree "${pre_dir}" "pre variant for fixture '${id}'"
  stage_fixture_data "${id}" "${fixture_json}"
  # Recorded last, so a prepare that died mid-way never qualifies for the
  # --reset-only fast path.
  printf '%s\n' "${entry_sha}" >"${entry_sha_file}"
  log "Prepared fixture '${id}' at ${fixture_root}"
done < <(jq -c --arg id "${fixture_id}" "${manifest_filter}" "${MANIFEST_PATH}")

if [[ "${bootstrap_poetry}" == "1" ]]; then
  log "Bootstrapping Poetry environments for prepared fixtures"
  # pre only: the agent works in pre, and nothing ever executes post.
  bootstrap_args=(--variant pre)
  if [[ -n "${fixture_id}" ]]; then
    bootstrap_args+=(--fixture "${fixture_id}")
  fi
  bash "${BOOTSTRAP_SCRIPT_PATH}" "${bootstrap_args[@]}"
fi

# --- Prerequisite data a fixture can build itself --------------------------- #
# Some data does not belong in S3 because the repo can regenerate it from a public
# source (ner_roberta builds the MEDDOCAN splits with scripts/prepare_data.py).
# Run those builders only when their outputs are absent, so the download is once
# per machine and not once per run. Deliberately AFTER bootstrap: the builders run
# inside the fixture's poetry env. Outputs go to the staged data dir, never into
# the repo, so the blind pre tree stays clean.
while IFS= read -r fixture_json; do
  id="$(jq -r '.id' <<<"${fixture_json}")"
  dest_root="${eval_data_root}/${id}"
  pre_dir="${FIXTURES_ROOT}/${id}/pre"

  while IFS= read -r build_json; do
    [[ -n "${build_json}" ]] || continue
    build_cmd="$(jq -r '.command // empty' <<<"${build_json}")"
    [[ -n "${build_cmd}" ]] || continue

    build_missing=0
    while IFS= read -r rel_path; do
      [[ -n "${rel_path}" ]] || continue
      [[ -e "${dest_root}/${rel_path}" ]] || build_missing=1
    done < <(jq -r '.when_missing[]?' <<<"${build_json}")
    ((build_missing)) || continue

    if [[ ! -d "${pre_dir}/.venv" ]]; then
      # Not fatal: run.sh's preflight refuses the fixture with PREREQ-FAIL, which
      # says the same thing at the point where it actually matters.
      warn "fixture '${id}': prerequisite data is missing and must be built, but ${pre_dir}/.venv does not exist — re-run with --bootstrap-poetry"
      continue
    fi

    build_env=()
    while IFS= read -r kv; do
      [[ -n "${kv}" ]] || continue
      build_env+=("${kv//\$\{DEST\}/${dest_root}}")
    done < <(jq -r '(.env // {}) | to_entries[] | "\(.key)=\(.value)"' <<<"${build_json}")

    mkdir -p "${dest_root}"
    log "  Building missing prerequisite data for '${id}': ${build_cmd}"
    (
      cd "${pre_dir}"
      env POETRY_VIRTUALENVS_IN_PROJECT=true ${build_env[@]+"${build_env[@]}"} \
        bash -c "${build_cmd}"
    ) || fail "fixture '${id}': prerequisite build failed: ${build_cmd}"
  done < <(jq -c '(.runtime_prerequisites // [])[] | select(.build) | .build' <<<"${fixture_json}")
done < <(jq -c --arg id "${fixture_id}" "${manifest_filter}" "${MANIFEST_PATH}")

log "Fixture preparation complete"
