#!/usr/bin/env bash
# Read-only evidence collector for a Tensorleap server that is down, stuck, or failing runs.
# Writes one folder (and a .tar.gz of it): versions, host disk and memory, the cluster's
# pods, events and node conditions, the logs of unhealthy pods and, with --job, one run's
# info and logs. Every file is redacted (private keys, passwords, tokens, signed URLs)
# before the archive is made. Changes nothing on the machine except that folder.
# One exception: the self-update step may refresh this skill's OWN files (self_update.sh).
#
#   tl_collect.sh [--job <runId>]... [--out <dir>]
#   tl_collect.sh --redact <file-or-dir>...   redact text files in place (e.g. a log bundle
#                                             downloaded from the UI) before sending them on
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ESC="$(printf '\033')"

redact_file() {
  grep -Iq . "$1" 2>/dev/null || return 0   # empty or binary: leave it
  local tmp="$1.redacting"
  awk '
    /-----BEGIN [A-Z ]*PRIVATE KEY-----/ && !/-----END [A-Z ]*PRIVATE KEY-----/ { print "[REDACTED PRIVATE KEY]"; skip = 1; next }
    skip && /-----END [A-Z ]*PRIVATE KEY-----/ { skip = 0; next }
    !skip { print }
  ' "$1" | sed -E \
    -e "s#${ESC}\[[0-9;]*[A-Za-z]##g" \
    -e 's#-----BEGIN [A-Z ]*PRIVATE KEY-----.*-----END [A-Z ]*PRIVATE KEY-----#[REDACTED PRIVATE KEY]#g' \
    -e 's#([A-Z0-9_]*(SECRET|PASSWORD|PASSWD|TOKEN|APIKEY|API_KEY|ACCESS_KEY|PRIVATE_KEY|CREDENTIALS)[A-Z0-9_]*["]?[[:space:]]*[:=][[:space:]]*).*#\1[REDACTED]#' \
    -e 's#("(password|passwd|secret|token|apiKey|api_key|apikey|access_key|accessKey|secret_key|secretKey|private_key|privateKey|client_secret|auth_secret|access_token|refresh_token|id_token)"[[:space:]]*:[[:space:]]*)"[^"]*"#\1"[REDACTED]"#g' \
    -e 's#("(phone_number|full_name|first_name|last_name)"[[:space:]]*:[[:space:]]*)"[^"]*"#\1"[REDACTED]"#g' \
    -e 's#^([[:space:]-]*(password|passwd|secret|token|apiKey|api_key|apikey|access_key|secret_key|client_secret|auth_secret)[[:space:]]*:[[:space:]]*).+$#\1[REDACTED]#' \
    -e 's#(K?Bearer)[[:space:]]+[A-Za-z0-9._~+/=-]{8,}#\1 [REDACTED]#g' \
    -e 's#(Authorization["]?[[:space:]]*[:=][[:space:]]*["]?Basic)[[:space:]]+[A-Za-z0-9+/=]{8,}#\1 [REDACTED]#g' \
    -e 's#eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}#[REDACTED JWT]#g' \
    -e 's#(AKIA|ASIA)[A-Z0-9]{16}#[REDACTED AWS KEY ID]#g' \
    -e 's#(X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token|[?&](sig|signature|token|access_token|api_key|apikey))=[^&[:space:]"]+#\1=[REDACTED]#g' \
    -e 's#://[^/[:space:]:@"]+:[^/[:space:]@"]+@#://[REDACTED]@#g' \
    >"$tmp" && mv "$tmp" "$1"
}

redact_path() {
  if [ -d "$1" ]; then
    find "$1" -type f ! -name '*.redacting' | while IFS= read -r f; do redact_file "$f"; done
  elif [ -f "$1" ]; then
    redact_file "$1"
  else
    echo "not found: $1" >&2
  fi
}

if [ "${1:-}" = "--redact" ]; then
  shift
  [ $# -gt 0 ] || { echo "usage: tl_collect.sh --redact <file-or-dir>..." >&2; exit 2; }
  for t in "$@"; do redact_path "$t"; done
  echo "Redacted in place: $*. Review the files before sending them: logs from your own code can hold secrets no pattern recognizes."
  exit 0
fi

# Self-update first: an update may replace THIS file, so exec a fresh copy and stop here.
if [[ -z "${TL_SKILL_NO_UPDATE:-}" && -f "$HERE/self_update.sh" ]]; then
  bash "$HERE/self_update.sh"; rc=$?
  [[ $rc -eq 10 ]] && TL_SKILL_NO_UPDATE=1 exec "$0" "$@"
fi
COMPAT=""
[ -f "$HERE/compat_check.sh" ] && COMPAT="$(bash "$HERE/compat_check.sh" 0)"
[ -n "$COMPAT" ] && printf '%s\n' "$COMPAT"
# One skill_run event with the leap CLI's device/user id; TL_DISABLE_ANALYTICS=1 turns it off.
record_start() { python3 "$HERE/skill_run.py" tensorleap-troubleshoot started --exit-code "$1" >/dev/null 2>&1 || true; }
trap 'record_start $?' EXIT

JOBS=()
OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --job) JOBS+=("${2:?--job needs a run id}"); shift 2 ;;
    --out) OUT="${2:?--out needs a directory}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done
OUT="${OUT:-tensorleap-support/$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$OUT/pods" "$OUT/components" || { echo "cannot create $OUT" >&2; exit 1; }

# ponytail: stock macOS has no timeout(1); there commands run unbounded, kubectl and curl keep their own limits
TO="$(command -v timeout || command -v gtimeout || true)"
cap() { # cap <file> <command...>: append the command and its output to $OUT/<file>
  local f="$OUT/$1"; shift
  { printf '$ %s\n' "$*"
    if [ -n "$TO" ]; then "$TO" 90 "$@"; else "$@"; fi
    printf '[exit %s]\n\n' "$?"
  } </dev/null >>"$f" 2>&1
}
K=(leap server tools kubectl --request-timeout=20s)

DATA_DIR="$(sed -n 's/^data_dir:[[:space:]]*//p' "$HOME/.config/tensorleap/config.yaml" 2>/dev/null | head -1)"
DD_FROM="this user's leap config"
[ -z "$DATA_DIR" ] && DD_FROM="the default: this user's leap config names none (another user may have installed Tensorleap)"
DATA_DIR="${DATA_DIR:-/var/lib/tensorleap/standalone}"
echo "Collecting Tensorleap evidence into $OUT (read-only; one to two minutes) ..."

cap versions.txt date -u
cap versions.txt date '+%Y-%m-%d %H:%M:%S %z (local time on this machine)'
cap versions.txt uname -a
[ -r /etc/os-release ] && cap versions.txt cat /etc/os-release
cap versions.txt leap --version
cap versions.txt leap server --info
cap versions.txt leap auth whoami

cap docker.txt docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.CreatedAt}}'
cap docker.txt docker info --format 'mem={{.MemTotal}} cpus={{.NCPU}} root={{.DockerRootDir}} server={{.ServerVersion}} os={{.OperatingSystem}} runtimes={{range $k, $v := .Runtimes}}{{$k}} {{end}}'
LOCAL=0
docker ps --filter name=k3d-tensorleap-server --format '{{.Names}}' 2>/dev/null | grep -q . && LOCAL=1
[ "$LOCAL" = 1 ] && cap docker.txt docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'

D="$DATA_DIR"; while [ ! -d "$D" ] && [ "$D" != / ]; do D="$(dirname "$D")"; done
cap host.txt id
cap host.txt df -h "$D" / /tmp
DROOT="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || true)"
[ -n "$DROOT" ] && [ -d "$DROOT" ] && cap host.txt df -h "$DROOT"
cap host.txt uptime
cap host.txt who -b
if [ "$(uname -s)" = Darwin ]; then
  cap host.txt sysctl -n hw.memsize
  cap host.txt docker run --rm --pull=never alpine:3.18.3 df -P /   # Docker Desktop's own disk (the installer's probe; never pulls)
  cap host.txt sh -c 'ps -Ao rss,user,comm -m | head -15'
else
  cap host.txt free -g
  cap host.txt sh -c 'ps -eo rss,user,comm --sort=-rss | head -15'
  command -v findmnt >/dev/null 2>&1 && cap host.txt findmnt -T "$D"
  command -v lsblk >/dev/null 2>&1 && cap host.txt lsblk -o NAME,SIZE,TYPE,MOUNTPOINT
  command -v systemctl >/dev/null 2>&1 && cap host.txt systemctl show docker.service -p ActiveEnterTimestamp -p RequiresMountsFor
fi
command -v nvidia-smi >/dev/null 2>&1 && cap host.txt nvidia-smi --query-gpu=index,name,driver_version,memory.used,memory.total,utilization.gpu --format=csv

[ -f "$DATA_DIR/install-notes.md" ] && cp "$DATA_DIR/install-notes.md" "$OUT/install-notes.md"
if [ -d "$DATA_DIR/logs" ]; then
  cap host.txt sh -c 'ls -lt "$1" | head -15' _ "$DATA_DIR/logs"
  LAST_LOG="$(ls -t "$DATA_DIR/logs"/*.log 2>/dev/null | head -1)"
  [ -n "$LAST_LOG" ] && cap installer-last-log.txt tail -n 400 "$LAST_LOG"
fi
cap server-info.txt leap server info
PORT="$(sed -n 's/^port:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$OUT/server-info.txt" | head -1)"
# The browser's path (through ingress-nginx), then node-server straight through the cluster's API.
cap health.txt curl -s -m 10 -w '\nHTTP %{http_code}\n' "http://localhost:${PORT:-4589}/api/v2/monitor/healthCheck"
[ "$LOCAL" = 1 ] && cap health.txt "${K[@]}" get --raw /api/v1/namespaces/tensorleap/services/tensorleap-node-server:80/proxy/api/v2/monitor/healthCheck

if [ "$LOCAL" = 1 ]; then
  cap cluster.txt "${K[@]}" get nodes -o wide
  cap cluster.txt "${K[@]}" get pods -A -o wide
  cap cluster.txt "${K[@]}" -n tensorleap get jobs,deployments,statefulsets,pvc
  cap cluster.txt "${K[@]}" -n tensorleap get deployments,statefulsets -o 'custom-columns=NAME:.metadata.name,CREATED:.metadata.creationTimestamp,IMAGE:.spec.template.spec.containers[*].image'
  cap cluster.txt "${K[@]}" -n tensorleap get elasticsearch
  cap cluster.txt "${K[@]}" -n kube-system get deployment coredns -o 'jsonpath={.spec.template.spec.containers[0].resources}'
  cap node.txt "${K[@]}" describe nodes
  cap events.txt "${K[@]}" get events -A --sort-by=.lastTimestamp
  # Three days of the lifecycle logs, without the health-probe and idle-poll lines that drown them.
  cap components/node-server.txt sh -c '"$@" 2>&1 | grep -v -E "request completed|healthCheck|kube-probe|No token provided" | tail -n 3000' _ "${K[@]}" -n tensorleap logs deploy/tensorleap-node-server --since=72h
  cap components/engine-orchestrator.txt sh -c '"$@" 2>&1 | grep -v -E "Monitoring failed jobs|Reporting active jobs" | tail -n 3000' _ "${K[@]}" -n tensorleap logs deploy/engine-orchestrator --since=72h
  # Unhealthy pods: not Running/Completed, Running but not ready, or restarted within the last day.
  "${K[@]}" get pods -A --no-headers 2>/dev/null \
    | awk '{ split($3, r, "/"); recent = ($5 + 0 > 0 && $6 !~ /^\([0-9]+d/)
             if (($4 != "Running" && $4 != "Completed") || ($4 == "Running" && r[1] != r[2]) || recent) print $1, $2, $5 + 0 }' \
    | head -25 >"$OUT/unhealthy-pods.txt"
  while read -r ns pod restarts; do
    cap "pods/$pod.txt" "${K[@]}" -n "$ns" describe pod "$pod"
    cap "pods/$pod.txt" "${K[@]}" -n "$ns" logs "$pod" --all-containers --tail=300
    [ "${restarts:-0}" -gt 0 ] && cap "pods/$pod.txt" "${K[@]}" -n "$ns" logs "$pod" --all-containers --previous --tail=300
  done <"$OUT/unhealthy-pods.txt"
fi

cap runs.txt leap run list
cap runs.txt leap run list -s FAILED
cap runs.txt leap projects list
for id in ${JOBS[@]+"${JOBS[@]}"}; do
  mkdir -p "$OUT/job-$id"
  cap "job-$id/info.txt" leap run info "$id" -o text
  cap "job-$id/logs.txt" leap run logs "$id"
  if [ "$LOCAL" = 1 ]; then
    cap "job-$id/pods.txt" "${K[@]}" -n tensorleap get pods,deployments,services -l "jobId=$id" -o wide
    cap "job-$id/pods.txt" "${K[@]}" -n tensorleap describe pods -l "jobId=$id"
  fi
done

summary() {
  [ -n "$COMPAT" ] && printf '%s\n' "$COMPAT"
  command -v leap >/dev/null 2>&1 || echo "  leap CLI not found on this machine (not on PATH)"
  grep -E '^v[0-9]|Installer Version|API Url|License' "$OUT/versions.txt" | sed 's/^/  /'
  sed -n 's/.*version:[[:space:]]*\([0-9][0-9A-Za-z.-]*\).*/  server version \1/p' "$OUT/server-info.txt" | head -1
  echo "  data dir: $DATA_DIR (from $DD_FROM); collected as $(id -un)"
  if [ "$LOCAL" = 1 ]; then
    grep -E '^k3d-tensorleap.*(Up|Exited|Created|Restarting)' "$OUT/docker.txt" | sed 's/^/  /'
    grep -E '^k3d-tensorleap-server-0.*%' "$OUT/docker.txt" | awk -F'\t' '{ printf "  node CPU %s, memory %s\n", $2, $3 }'
    echo "  health (UI port, then direct): $(grep -v '^\$' "$OUT/health.txt" | tr -s '\n' ' ')"
    if [ -s "$OUT/unhealthy-pods.txt" ]; then
      echo "  unhealthy pods (namespace pod restarts):"; sed 's/^/    /' "$OUT/unhealthy-pods.txt"
    else
      echo "  all pods Running/Completed, none restarted within a day"
    fi
    grep -E '(DiskPressure|MemoryPressure|PIDPressure)[[:space:]]+True' "$OUT/node.txt" | sed 's/^[[:space:]]*/  NODE CONDITION: /'
    awk '/^\$ / { cmd = $0 } /Warning/ && cmd ~ /get events/ { n[$4]++ } END { for (r in n) printf "  warning events: %s x%d\n", r, n[r] }' "$OUT/events.txt"
    grep -h -E 'Admission \| (queued engine job|starting oversized job|low free storage)' "$OUT/components/engine-orchestrator.txt" 2>/dev/null | tail -n 5 | cut -c1-240 | sed 's/^/  orchestrator: /'
  elif docker ps >/dev/null 2>&1; then
    echo "  no running Tensorleap cluster on this machine: see docker.txt (Exited = stopped), or the server is another machine"
  else
    echo "  docker isn't installed or readable here: not the server, or this user is not in the docker group"
  fi
  awk '$5 ~ /%$/ && $5 + 0 >= 80 { printf "  DISK %s used on %s\n", $5, $NF }' "$OUT/host.txt"
  for id in ${JOBS[@]+"${JOBS[@]}"}; do
    echo "  run $id: $(grep -m1 -E 'status:' "$OUT/job-$id/info.txt" | tr -s ' ')"
    grep -h -m4 -E '^[[:space:]]*(HTTP-Code|Body):|error:' "$OUT/job-$id/info.txt" 2>/dev/null | sed 's/[[:space:]]*$//' | cut -c1-300 | sed 's/^[[:space:]]*/    /'
    grep -h -m5 -iE 'error|exception|traceback|oomkilled|evicted|denied' "$OUT/job-$id/logs.txt" 2>/dev/null | cut -c1-300 | sed 's/^[[:space:]]*/    /'
  done
}

redact_path "$OUT"
summary >"$OUT/summary.txt"
ARCHIVE="$OUT.tar.gz"
tar -czf "$ARCHIVE" -C "$(dirname "$OUT")" "$(basename "$OUT")" 2>/dev/null || ARCHIVE=""

echo
echo "== Summary (also in summary.txt) =="
cat "$OUT/summary.txt"
echo
echo "Evidence: $OUT${ARCHIVE:+  (archive: $ARCHIVE)}"
echo "Secrets, names and phone numbers were redacted automatically; user emails and host names remain."
echo "Review the files before sending them: logs from your own code can hold secrets no pattern recognizes."
