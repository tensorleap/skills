#!/usr/bin/env bash
# Read-only preflight for `leap server install|upgrade|reinstall`.
# Prints a report and changes nothing. Exit 1 only on blocking issues.
# Catalog numbers refer to reference/install-troubleshooting.md.
set -u

FAILS=0
ok()   { printf '  [ok]   %s\n' "$*"; }
warn() { printf '  [warn] %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; FAILS=$((FAILS + 1)); }

echo "== Prior state =="
CFG="$HOME/.config/tensorleap/config.yaml"
DATA_DIR="$(awk '/^data_dir:/{print $2}' "$CFG" 2>/dev/null || true)"
DATA_DIR="${DATA_DIR:-/var/lib/tensorleap/standalone}"
echo "  data dir: $DATA_DIR"
if [ -f "$DATA_DIR/install-notes.md" ]; then
  ok "install-notes.md present - read it before acting (Step 0)"
else
  warn "no install-notes.md - fresh machine, or installed before this skill existed"
fi
if [ -f "$DATA_DIR/manifests/params.yaml" ]; then
  ok "installer params.yaml present"
else
  warn "no manifests/params.yaml - fresh machine, or --clear-data ran (an upgrade would reset settings, catalog #45b)"
fi
if command -v leap >/dev/null 2>&1; then
  ok "leap CLI $(leap --version 2>/dev/null || echo '?')"
else
  warn "leap CLI not on PATH (Step 3)"
fi

echo "== Docker =="
if ! command -v docker >/dev/null 2>&1; then
  fail "docker not installed (catalog #6)"
elif ! docker ps >/dev/null 2>&1; then
  fail "docker ps failed: daemon down, or (Linux) user not in the docker group (catalog #6)"
else
  ok "docker reachable"
  MEM="$(docker info -f '{{.MemTotal}}' 2>/dev/null || echo 0)"
  ROOT="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo '?')"
  MEM_GB=$((MEM / 1024 / 1024 / 1024))
  if [ "$MEM" -lt 6227000000 ]; then
    fail "docker memory ${MEM_GB}GB is below the 6GB installer gate (catalog #1)"
  elif [ "$MEM" -lt 12884901888 ]; then
    warn "docker memory ${MEM_GB}GB passes the gate but Elasticsearch (6Gi) will not schedule under ~12GB (catalog #1b)"
  else
    ok "docker memory ${MEM_GB}GB"
  fi
  echo "  docker data-root: $ROOT (move it if this sits on a small OS disk - catalog #2)"
  FREE_KB="$(docker run --rm alpine:3.18.3 df -P / 2>/dev/null | awk 'NR==2{print $4}')"
  if [ -n "$FREE_KB" ]; then
    FREE_GB=$((FREE_KB / 1024 / 1024))
    if [ "$FREE_KB" -lt 68157440 ]; then
      fail "docker storage ${FREE_GB}GB free is below the 65GB gate (catalog #1/#2)"
    else
      ok "docker storage ${FREE_GB}GB free"
    fi
  else
    warn "could not probe docker storage (alpine:3.18.3 pull blocked? catalog #1c)"
  fi
fi

echo "== Data-dir disk =="
PROBE="$DATA_DIR"
while [ ! -d "$PROBE" ] && [ "$PROBE" != "/" ]; do PROBE="$(dirname "$PROBE")"; done
df -h "$PROBE" | awk 'NR==2{printf "  %s free of %s on %s (%s used); keep max(30-50GB, 15-20%%) free\n",$4,$2,$NF,$5}'

echo "== sudo (probed once) =="
SUDO_OUT="$(sudo -n true 2>&1 || true)"
case "$SUDO_OUT" in
  "") ok "passwordless sudo" ;;
  *password*) ok "sudo available (will prompt for a password during install)" ;;
  *) warn "sudo unavailable: the installer needs it for data-dir/dataset-volume paths (catalog #7c)" ;;
esac

echo "== Ports =="
for p in 4589 5699; do
  L=""
  if command -v lsof >/dev/null 2>&1; then
    L="$(lsof -nP -iTCP:"$p" -sTCP:LISTEN 2>/dev/null | awk 'NR==2{print $1}')"
  elif command -v ss >/dev/null 2>&1; then
    L="$(ss -ltnp 2>/dev/null | awk -v p=":$p" '$4 ~ p"$"{print $6}' | head -1)"
  fi
  if [ -z "$L" ]; then
    ok "port $p free"
  else
    case "$L" in
      *docke*|*k3d*) warn "port $p held by $L - an existing Tensorleap install (go to Step 0), not a conflict" ;;
      *) fail "port $p in use by $L - use --port/--registry-port or free it (catalog #13c)" ;;
    esac
  fi
done

echo "== GPU =="
if [ "$(uname -s)" = "Darwin" ]; then
  ok "macOS: no GPU support - never pass GPU flags"
elif command -v nvidia-smi >/dev/null 2>&1; then
  if nvidia-smi >/dev/null 2>&1; then
    ok "nvidia driver OK: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | paste -sd, -)"
    if docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi >/dev/null 2>&1; then
      ok "docker can use the GPU (container toolkit OK)"
    else
      fail "docker cannot use the GPU: install nvidia-container-toolkit, run nvidia-ctk runtime configure --runtime=docker, restart docker (catalog #20)"
    fi
  else
    fail "nvidia-smi present but failing - driver not loaded (catalog #21)"
  fi
else
  ok "no nvidia-smi: CPU-only machine"
fi

if grep -qi microsoft /proc/version 2>/dev/null; then
  echo "== WSL2 =="
  warn "WSL2: pass --data-dir inside the WSL filesystem (not /var/lib, not /mnt/*); the vdisk lives on C: (catalog #4/#5)"
fi

echo "== Shell =="
if [ -n "${CONDA_DEFAULT_ENV:-}${VIRTUAL_ENV:-}" ]; then
  warn "conda/virtualenv active - deactivate before running the installer (catalog #9)"
else
  ok "no python environment active"
fi
if env | grep -qiE '^(http|https)_proxy='; then
  ok "proxy env vars set: $(env | grep -iE '^(http|https|no)_proxy=' | cut -d= -f1 | paste -sd, -)"
else
  echo "  (no proxy env vars - fine on open networks; required behind a corporate proxy, catalog #35)"
fi

echo
if [ "$FAILS" -gt 0 ]; then
  echo "$FAILS blocking issue(s). Fix them before running leap server install."
  exit 1
fi
echo "No blocking issues found."
