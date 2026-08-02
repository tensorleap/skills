#!/usr/bin/env bash
set -euo pipefail

# Heading of the README that replaces a stripped one. Written by
# fixture_restore_declared_readmes, asserted by verify.sh — shared so the writer
# and the checker cannot drift apart.
FIXTURE_PLACEHOLDER_README_HEADING="# Placeholder"

fixture_fail() {
  echo "error: $*" >&2
  exit 1
}

fixture_add_local_exclude() {
  local repo_dir="$1"
  local pattern="$2"
  local exclude_file="${repo_dir}/.git/info/exclude"

  [[ -d "${repo_dir}/.git" ]] || fixture_fail "fixture repo is missing .git: ${repo_dir}"

  mkdir -p "$(dirname "${exclude_file}")"
  touch "${exclude_file}"
  grep -qxF "${pattern}" "${exclude_file}" || printf '%s\n' "${pattern}" >>"${exclude_file}"
}

fixture_list_stripped_python_basenames() {
  local rel_path
  for rel_path in "$@"; do
    if [[ "${rel_path}" == *.py ]]; then
      basename "${rel_path}" .py
    fi
  done
}

fixture_collect_declared_readme_files() {
  local repo_dir="$1"
  local pyproject_path="${repo_dir}/pyproject.toml"

  [[ -f "${pyproject_path}" ]] || return 0

  sed -nE 's/^[[:space:]]*readme[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "${pyproject_path}"
}

fixture_restore_declared_readmes() {
  local repo_dir="$1"
  local rel_path

  while IFS= read -r rel_path; do
    [[ -n "${rel_path}" ]] || continue
    [[ -e "${repo_dir}/${rel_path}" ]] && continue

    mkdir -p "$(dirname "${repo_dir}/${rel_path}")"
    # Agent-visible: the blind repo's README is often the first file read, so it
    # says only what it has to (Poetry resolves `readme = ...` at install time).
    cat >"${repo_dir}/${rel_path}" <<EOF
${FIXTURE_PLACEHOLDER_README_HEADING}

This placeholder keeps the Poetry \`readme\` metadata resolvable.
EOF
  done < <(fixture_collect_declared_readme_files "${repo_dir}")
}

fixture_commit_with_fixed_metadata() {
  local repo_dir="$1"
  local message="$2"

  # Fixed identity + date so the blind commit is reproducible. Agent-visible via
  # `git log`, so it names nothing the agent can go looking for.
  GIT_AUTHOR_NAME="Fixture Bot" \
  GIT_AUTHOR_EMAIL="fixtures@local" \
  GIT_AUTHOR_DATE="2000-01-01T00:00:00Z" \
  GIT_COMMITTER_NAME="Fixture Bot" \
  GIT_COMMITTER_EMAIL="fixtures@local" \
  GIT_COMMITTER_DATE="2000-01-01T00:00:00Z" \
    git -C "${repo_dir}" -c commit.gpgsign=false commit --quiet -m "${message}"
}

fixture_strip_pre_variant_files() {
  local repo_dir="$1"
  shift

  local strip_files=("$@")
  local rel_path
  for rel_path in "${strip_files[@]}"; do
    rm -rf -- "${repo_dir:?}/${rel_path}"
  done

  local stripped_py_basenames=()
  while IFS= read -r base_name; do
    [[ -n "${base_name}" ]] || continue
    stripped_py_basenames+=("${base_name}")
  done < <(fixture_list_stripped_python_basenames "${strip_files[@]}")

  while IFS= read -r abs_path; do
    [[ -n "${abs_path}" ]] || continue
    rm -f -- "${abs_path}"
  done < <(find "${repo_dir}" -type f -path '*/__pycache__/leap*.pyc' | sort)

  local base_name
  for base_name in "${stripped_py_basenames[@]}"; do
    while IFS= read -r abs_path; do
      [[ -n "${abs_path}" ]] || continue
      rm -f -- "${abs_path}"
    done < <(find "${repo_dir}" -type f -path "*/__pycache__/${base_name}*.pyc" | sort)
  done

  find "${repo_dir}" -type d -name '__pycache__' -empty -delete
  fixture_restore_declared_readmes "${repo_dir}"
}

fixture_commit_pre_variant() {
  local repo_dir="$1"

  git -C "${repo_dir}" add -A
  git -C "${repo_dir}" diff --cached --quiet \
    && fixture_fail "fixture pre variant had no changes after stripping files: ${repo_dir}"

  fixture_commit_with_fixed_metadata "${repo_dir}" "Create pre-integration fixture variant"
}

fixture_reset_post_variant() {
  local repo_dir="$1"
  local post_ref="$2"

  git -C "${repo_dir}" reset --hard "${post_ref}" >/dev/null
  git -C "${repo_dir}" clean -fdx -e .fixture_reset.sh >/dev/null
}

# Flatten a fixture variant into a single rootless commit with no remote and no
# reset script. Used for blind eval fixtures so an agent working in the variant
# cannot recover the integrated "after" state from git history, the reflog, or
# the origin remote.
fixture_make_blind_variant() {
  local repo_dir="$1"
  local message="$2"

  rm -rf "${repo_dir:?}/.git"
  rm -f "${repo_dir}/.fixture_reset.sh"
  git -C "${repo_dir}" init --quiet
  # Neutralize LFS BEFORE the snapshot commit (info/attributes outranks any
  # committed .gitattributes), so hydrated model files are stored as real
  # content instead of being re-cleaned into LFS pointers. This repo has no
  # remote: a later checkout/reset that invoked the LFS smudge filter would
  # have nowhere to fetch from and would corrupt models into 134-byte pointers
  # — prepare.sh's --reset-only fast path does exactly such a reset.
  mkdir -p "${repo_dir}/.git/info"
  echo '* -filter' >"${repo_dir}/.git/info/attributes"
  git -C "${repo_dir}" checkout --quiet -b main 2>/dev/null || true
  git -C "${repo_dir}" add -A
  fixture_commit_with_fixed_metadata "${repo_dir}" "${message}"
}

# --- Shared manifest-entry accessors (used by prepare + verify) -------------
# Centralize the blind / pre_ref / allowlist parse so the contract lives in one
# place instead of being re-implemented in each script.

fixture_entry_pre_ref() {
  jq -r '.pre_ref // empty' <<<"$1"
}

fixture_entry_text_allowlist() {
  jq -r '.blind_text_allowlist[]?' <<<"$1"
}

fixture_list_contains() {
  local needle="$1"
  shift
  local item
  for item in "$@"; do
    [[ "${item}" == "${needle}" ]] && return 0
  done
  return 1
}

# Whether an incidental 'tensorleap' text match in a blind pre variant is
# allowed. Only blind fixtures qualify, and then only when the pre tree is
# independently sourced (pre_ref) or the file is explicitly allowlisted.
# Strip-derived blind fixtures otherwise keep the hard check, so incomplete
# stripping still fails like any derived fixture.
fixture_blind_text_exempt() {
  local pre_ref="$1"
  local rel="$2"
  shift 2
  # An explicit pre_ref supplies a real pre-integration tree, so whatever it
  # says is by definition not a leak; a strip-derived tree needs an allowlist.
  [[ -n "${pre_ref}" ]] && return 0
  fixture_list_contains "${rel}" "$@"
}

fixture_extract_leap_yaml_entry_file() {
  local leap_yaml_path="$1"

  [[ -f "${leap_yaml_path}" ]] || return 0

  sed -nE 's/^[[:space:]]*entryFile[[:space:]]*:[[:space:]]*"?([^"]+)"?.*/\1/p' "${leap_yaml_path}" | head -n 1
}

fixture_list_requirements_files() {
  local repo_dir="$1"
  find "${repo_dir}" -maxdepth 2 -type f \
    \( -name 'requirements.txt' -o -name 'requirements-*.txt' -o -name 'requirements_*.txt' \) \
    | sort
}

# Drops every code-loader (and code-loader-helpers) dependency line from a
# pyproject.toml or requirements*.txt. Exits 0 when the file changed, 1 when
# there was nothing to drop.
fixture_remove_code_loader_lines() {
  python3 - "$1" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
# Matches every form a declaration takes: `code-loader = "^1.0.152"` and
# `code_loader = { version = ... }` (poetry), `"code-loader>=1.0,<2.0",` (PEP 621
# `dependencies` array), `code-loader[all]==1.0.152` and a bare `code-loader` line
# (requirements). Anchored so `my-code-loader-shim` is left alone.
# ponytail: line-based, so a single-line array (`deps = ["a", "code-loader"]`) is
# missed. No fixture uses that form, and verify.sh greps these files and fails the
# fixture if any reference survives — upgrade to a real TOML rewrite only if that
# gate actually trips.
pattern = re.compile(r"^\s*\"?code[-_]loader(?:[-_]helpers)?\s*(?:[=<>!~\"\[]|$)")
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
kept = [line for line in lines if not pattern.match(line)]
if len(kept) == len(lines):
    raise SystemExit(1)
path.write_text("".join(kept), encoding="utf-8")
PY
}

# A blind pre variant must not reference code-loader anywhere: a code-loader pin
# in pyproject.toml or requirements*.txt announces the repo as a Tensorleap
# integration before the agent has read a line of code. The agent installs
# code-loader itself when it wants to validate locally, which is the only place
# it is needed at all — nothing in the harness executes the post variant.
fixture_strip_code_loader_dependency() {
  local repo_dir="$1"
  local label="$2"
  local requirements_path
  local pyproject_changed=0
  local changed=0

  if [[ -f "${repo_dir}/pyproject.toml" ]] && fixture_remove_code_loader_lines "${repo_dir}/pyproject.toml"; then
    pyproject_changed=1
    changed=1
  fi

  while IFS= read -r requirements_path; do
    [[ -n "${requirements_path}" ]] || continue
    if fixture_remove_code_loader_lines "${requirements_path}"; then
      changed=1
    fi
  done < <(fixture_list_requirements_files "${repo_dir}")

  ((changed)) || return 0

  # Editing pyproject.toml stales poetry.lock's content-hash, so `poetry install`
  # refuses to run — for the agent as much as for bootstrap. Relocking also drops
  # the code-loader package entry the lock would otherwise still name.
  if ((pyproject_changed)) && [[ -f "${repo_dir}/poetry.lock" ]]; then
    (
      cd "${repo_dir}"
      POETRY_VIRTUALENVS_USE_POETRY_PYTHON=true poetry lock >/dev/null
    ) || fixture_fail "${label}: failed to refresh poetry.lock after removing code-loader. Relocking re-resolves the whole graph, so every package source in pyproject.toml must be reachable — see poetry's output above (a 401 from a private index means credentials are missing: poetry config http-basic.<source> <user> <token>)."
  fi
}
