# Tensorleap install — failure-mode catalog

Compiled from installer telemetry, ~130 customer installation sessions (2023–2026), and
the installer source (`tensorleap/helm-charts`). Ordered by observed frequency within each group. Match on the
**symptom**, apply the fix, and only then retry.

**Retry traps** — these return the identical error on every retry; retrying without the fix
never works: [not enough resources](#1-docker-requirements-not-met--not-enough-resources),
[WSL2 read-only data dir](#4-wsl2-read-only-file-system),
[helm release lock](#10-another-operation-installupgraderollback-is-in-progress),
[uninstall/purge permission denied](#55-uninstall--purge-fails-with-unlinkat--permission-denied),
and [root-owned helm-cache](#7b-failed-loading-tensorleap-helm-chart--permission-denied-on-the-cached-tgz).
By contrast, **image-pull failures and `Build manifest failed` are retry-safe** — re-running
pulls only the diff, and a manifest failure happens before anything is touched.

---

## Preflight & resources

### 1. "Docker requirements not met" / "not enough resources"
Most common preflight failure. The gate: ≥6 GB Docker memory and ≥65 GB free on the Docker
filesystem, measured with `docker run --rm alpine:3.18.3 df -P /`.
**Fix:** macOS — raise Docker Desktop → Settings → Resources. Linux/VM — the host is too
small, or (very common) Docker's data-root sits on a tiny OS disk while the real storage is a
separate mount → move it (item 2). Do NOT bypass with `--yes` blindly (install then dies later
on disk pressure).

### 1b. Elasticsearch never schedules — the memory gate is below the app's floor
Preflight passes at 6GB, then the install hangs and eventually reports
`context deadline exceeded` (#11). Elasticsearch requests *and* limits **6Gi** on its own, and
minio/rabbitmq/orchestrator/node-server add ~2Gi, so **under ~12GB of Docker memory ES sits
Pending forever**. Very common on macOS (Docker Desktop defaults to 8GB).
**Fix:** confirm before blaming disk —
`leap server tools kubectl -n tensorleap describe pod tl-elasticsearch-es-master-0 | grep -A3 Events`
showing `Insufficient memory`. Raise Docker memory to ≥12GB (16GB comfortable) and re-run.

### 1c. Fatal errors *before* the storage gate
Two paths in the docker preflight abort with messages that don't read like network problems:
`Failed getting docker info, <err>` (daemon unreachable/incompatible — see #6/#13b) and
`Failed pulling alpine:3.18.3 image, <err>` (a blocked or rate-limited pull of the tiny probe
image — see #32/#34/#35). If the probe container can't run at all the installer instead asks
`Unable to check docker storage. Do you want to continue anyway?` with default **No**, and
answering the default returns `not enough resources`.

### 2. Docker data-root on the wrong (small) disk
Signature: `docker info` shows `Docker Root Dir: /var/lib/docker` on a nearly-full root
filesystem while a big NVMe/EBS/data mount sits idle. The single most common field fix across
EC2, Azure, and on-prem servers.
**Fix:** **merge** (never overwrite — the file often carries the nvidia runtime config) a
`data-root` key into `/etc/docker/daemon.json` — see the jq one-liner in the skill's Step 2 —
then `sudo systemctl restart docker` (or `stop` + `start`), verify `docker info` shows the new
root, and re-run the GPU pre-check if this is a GPU box. Keep Tensorleap's `--data-dir` a
*separate* folder on the same big disk.

### 3. "Not enough resources" right after moving the data-root
**Fixed in installer v0.9.11 (2026-04-12)** — the storage probe now reads the live Docker
daemon's actual configured root (`dockerInfo.DockerRootDir`) instead of a stale filesystem-type
match, so on a current CLI this number is real, not a cached artifact of the old path.
**Fix:** if you see it, don't reflexively bypass it — verify with `docker info` that the move
actually landed and docker actually restarted; if the daemon is confirmed pointed at the new
disk and the number is still wrong, upgrade the CLI (`leap cli upgrade -s | bash`) before
reaching for `DISABLE_DOCKER_CHECKS=true` (skips the check only, no side effects) — that flag
is a last resort, not the default response to this message.

### 4. WSL2: "read-only file system"
`mkdir /var/lib/tensorleap: read-only file system` when creating the registry or server node.
**Fix:** reinstall with `--data-dir` inside the WSL filesystem (e.g. `~/tensorleap-data`).
Never point it at a Windows drive mount (`/mnt/e/...`) — write-permission failures; if a
half-done attempt landed there, `leap server uninstall --purge` before retrying. Field data
shows users retrying this 6+ times unchanged — it never self-resolves.

### 5. WSL2: disk fills `C:` no matter what `--data-dir` says
Mid-install every command stops working, 0 bytes free: the WSL2 virtual disk physically lives
on `C:`, so installing "into WSL" consumes `C:` even when it's not the intended drive.
**Fix:** move the distro to the big drive from PowerShell:
`wsl --export <distro> E:\bk.tar` → **verify the tar exists and is plausibly sized before
continuing** (`wsl --unregister` is destructive and unrecoverable) → `wsl --unregister <distro>`
→ `wsl --import <distro> E:\wsl E:\bk.tar`. After import the default user resets to **root**:
restore it with `[user]\ndefault=<user>` in `/etc/wsl.conf` then `wsl --shutdown`, or the
install would run as root (catalog #7). Verify `df -h` inside WSL, then install.
Docker Desktop's own disk image also sits on `C:` — move it in Settings → Resources →
Advanced → Disk image location. Expect slow I/O on WSL2 (Windows→WSL→docker→k3d stacking).

### 6. "docker is not installed" / "docker is not running"
Docker missing, daemon stopped, or (Linux) the user isn't in the `docker` group — all three
produce the same message.
**Fix — Linux:** install/start Docker (get.docker.com script works; some minimal machines need
`apt install curl` first); then `sudo usermod -aG docker $USER` + `newgrp docker`. Stale
SSH/tmux sessions don't pick up the new group — open a fresh shell.
**Fix — macOS:** there is no docker group; `Cannot connect to the Docker daemon at
unix://~/.docker/run/docker.sock` means Docker Desktop isn't running (start it and
wait for the whale to settle) or the wrong context is selected — see #13b.

### 7. Root-owned folders from a sudo install → pods crash "permission denied"
Installing with sudo (or onto a root-owned mount) creates storage folders the pods (running
as non-root) can't write: Keycloak/postgres fails, Elasticsearch crashloops on its storage
folder, later `leap server upgrade` by another user dies on a root-owned helm-chart cache.
**Fix:** install as a regular docker-group user. To repair: delete the affected storage
folders and re-run `leap server install` — the installer re-validates and fixes permissions.
For the root-owned chart cache: update the CLI (newer versions fix ownership), delete the
cache folder under the data dir, re-run install. NFS-style mounts that force root ownership
(some Azure ML mounts) can't be fixed with chown — install to a different path.

### 7b. `Failed loading tensorleap helm chart` — permission denied on the cached .tgz
```
open <data-dir>/helm-cache/tensorleap/1.6.58.tgz: permission denied
```
The downloaded chart cache is owned by whoever installed first (usually via sudo); a different
user's `upgrade`/`reinstall` can't read it. Retrying is futile — 3 hosts in the field retried
immediately with the identical error.
**Fix:** `sudo rm -rf <data-dir>/helm-cache` (cache only — `storage/` and projects untouched),
update the CLI (newer versions fix the ownership going forward), re-run. Prefer running as the
original installing user.

### 7c. Installer can't stat/create the data dir or dataset volume — sudo unavailable
```
failed to create dataset volume directory: failed to stat directory with sudo: <user> is not in the sudoers file.
failed to create dataset volume directory: mkdir $HOME/tensorleap: permission denied
```
The "never install *with* sudo" rule does not mean sudo is unnecessary: the installer shells
out to sudo to create/stat the data dir and dataset-volume paths. A user in `docker` but not
in `sudoers` hard-fails here, and the prompt's suggested `$HOME/tensorleap` default is
unwritable when the login user doesn't own that home.
**Fix:** preflight with `sudo -v`. No sudo → choose a data dir and dataset volumes the user
already owns and pass them explicitly (`-d`/`-v`), or have an admin pre-create the dirs and
`chown` them to the installing user (stronger version of #43).

### 8. "no space left on device" (any phase)
Can hit **three** filesystems independently: Docker's data-root, the Tensorleap data dir, and
`/tmp`.
**Fix:** `df -h` all three; free or grow the full one. `docker image prune -a -f` reclaims a
lot (Tensorleap copies images into its own registry, so pruning Docker's copies is safe when
no install is running). The kubelet evicts pods below 30G free ("Evicted" pods / install
never finishing) even when the install itself succeeded.

### 8b. Reclaiming disk on an install that's already running
Don't reinstall from scratch to free space.
**Fix (projects and users survive):**

```bash
leap server stop
leap server uninstall            # WITHOUT --purge / --clear-data
docker image prune -a -f         # reclaimed ~30GB in the field
leap server install -d <same data dir>
```

Only a re-login is needed afterwards.

### 9. Installer breaks inside conda/virtualenv
First run of the CLI/install throws "no such file or library path"-style errors.
**Fix:** `conda deactivate` (leave any virtualenv), open a fresh terminal, re-run. Rule from
the field: don't run the installer inside Python virtual environments.

---

## Cluster & Docker runtime

### 10. "another operation (install/upgrade/rollback) is in progress"
Second most common failure overall. A previous run was interrupted mid-**helm** phase,
leaving the release `pending-install`/`pending-upgrade`.
**Fix:** run `leap server install` again *once* — the installer detects the stuck release and
offers reinstall. If it recurs, roll back/uninstall the stuck release via helm (kubeconfig at
`<data-dir>/manifests/kubeconfig.yaml`). Note: Ctrl-C during the image-pull phase is harmless
(cache kept); Ctrl-C during the helm phase is what creates this trap.

### 11. "context deadline exceeded"
Helm timed out waiting for workloads — resource starvation or very slow pulls.
**Fix:** triage disk (item 8) and memory (item 1) first; then
`leap server tools kubectl get pods -n tensorleap` and `describe` the Pending/ImagePullBackOff
pod. If the machine is simply slow, re-running continues from cache.

### 12. k8s API unreachable — "connection refused" or `: EOF` on 127.0.0.1:&lt;port&gt;
The k3d server container died mid-install or Docker restarted; the random local API port
stopped answering. Usually downstream of disk/memory pressure or a Docker Desktop update.
**Fix:** `docker ps -a | grep k3d-tensorleap`; if dead, `docker logs k3d-tensorleap-server-0`
for OOM/disk, fix the cause, then `leap server run` or re-run the install. On a shared server
also ask whether someone updated Docker that week.

### 13. macOS: docker.sock "Bad Gateway" / "error during connect"
Docker Desktop daemon hung. **Fix:** restart Docker Desktop fully, retry.

### 13b. macOS: "check if the server supports the requested API version"
```
failed to list containers: request returned Internal Server Error for API route and version
http://%2F<home>%2F.docker%2Frun%2Fdocker.sock/v1.46/containers/json?...
```
Distinct from #13 — the daemon answers, but the CLI's embedded docker client speaks an API
version the daemon doesn't serve. Usually a wrong `docker context` (Colima/Rancher/Podman
selected instead of Docker Desktop) or an old/downgraded Desktop. Seen on `uninstall` on
arm64 Macs. **Restarting Docker does not fix it.**
**Fix:** `docker context ls` and `docker version` (compare client vs server API version);
`docker context use desktop-linux`, or update Docker Desktop, then retry.

### 13c. Port already in use → late cluster-creation failure with full rollback
Something else already listens on 4589 (or 5699/443). The installer does **not** preflight
ports: it runs for minutes, then fails in cluster creation with a Docker port-bind error
(`address already allocated` / `bind: address already in use`) and logs
`Cluster creation FAILED, all changes have been rolled back!`.
**Fix:** `lsof -i :4589 -i :5699` before installing. If the listener is `com.docker`/k3d it's
an existing Tensorleap install — upgrade/reinstall instead. If it's a foreign service that
must keep running, install with `--port <free>` (and/or `--registry-port <free>`) — the port
is sticky, so changing it later forces a reinstall; use it consistently in tunnels and the
`leap auth` URL afterwards.

### 14. "network <id> not found"
`docker network prune` or a Docker reset removed the k3d network.
**Fix:** `leap server uninstall` (data survives) then reinstall.

### 15. Pods stuck ContainerCreating / cluster down after Docker died mid-session
**Fix:** free storage, wait — images and state are local, Kubernetes self-heals and containers
return on their own. A single bad pod (e.g. mongodb 0/1 after upgrade) can be deleted with
kubectl; the controller recreates it.

---

## Charts & Kubernetes ordering

### 16. `no matches for kind "Elasticsearch" ... ensure CRDs are installed first`
Most common non-preflight failure. The app chart raced the ECK operator's CRD registration.
**Fix:** re-run the same install command; the CRD exists on the second pass. Persistent →
check the eck-operator pod.

### 17. `services "redis"/"tensorleap-web-ui"/"keycloak-headless" not found`, PVC "not found"
Same ordering-race class. **Fix:** re-run once; if the same object repeats, `kubectl describe`
why it failed to create (usually disk).

### 18. `job copy-engine-generic-deps-job-N failed: BackoffLimitExceeded`
Engine dependency-copy jobs died — image pull or node disk.
**Fix:** `kubectl -n tensorleap logs job/<name>`; fix disk/registry access, re-run.

### 18b. `pre-upgrade hooks failed: timed out waiting for the condition`
Also `post-upgrade hooks failed`, `failed pre-install`, `failed post-install`. **The most
common upgrade-phase failure** — 5+ hosts across chart versions, recurring on the *same* host
across days, so it is not transient and **not** the same as `context deadline exceeded` (#11).
Root cause confirmed in cluster logs: the hook is the `node-server-db-migration` job, and it
dies when Mongo isn't reachable inside the hook window:
`MongoNetworkError: connect ECONNREFUSED …:27017` / `getaddrinfo ENOTFOUND mongodb`.
**Fix — triage Mongo first, retrying against a dead Mongo reproduces this forever:**

```bash
leap server tools kubectl -n tensorleap logs job/node-server-db-migration
leap server tools kubectl -n tensorleap get pod -l app=mongodb
```

Mongo Pending/CrashLoop → fix that first (disk #8, root-owned `storage/mongodb` #7). Mongo
Running and healthy → a plain re-run usually passes.

### 19. PVC "is invalid ... spec is immutable after creation" (upgrade)
**Fix:** never shrink; escalate before deleting a PVC — deleting `elasticsearch-data-*` loses
analysis data. Usually a coordinated `leap server reinstall` with data-loss consent.

---

## GPU (Linux)

The canonical pre-check — run BEFORE installing; nvidia-container-toolkit is the single most
commonly forgotten prerequisite:

```bash
nvidia-smi && docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi
```

### 20. `could not select device driver "" with capabilities: [[gpu]]` / installer warns "missing nvidia docker"
No NVIDIA container runtime — includes every macOS machine (never use GPU flags on Mac).
**Fix:** install nvidia-container-toolkit, `sudo nvidia-ctk runtime configure --runtime=docker`,
`sudo systemctl restart docker`, re-run the pre-check. If `apt update` fails on a stale nvidia
repo: delete the old `/etc/apt/sources.list.d/nvidia-*.list` + GPG key, re-add the official
repo, then install. If remote fixes hang, a machine reboot has been the unblock.

### 21. `failed to initialize NVML: Driver Not Loaded` / `nvidia-smi` not working
Kernel driver missing or not loaded — the installer's auto-retry cannot help.
**Fix:** install/load the NVIDIA driver; on desktops watch for the **UEFI Secure Boot (MOK)**
prompt — enroll the password and reboot or the driver stays unloaded. Then retry. Fallback
that keeps momentum: install CPU-only now, fix drivers offline, `leap server reinstall` and
select GPUs — reinstall is fast and loses no data.

### 21b. A working GPU install stops starting after a host kernel upgrade
```
Failed Cluster Start: ... failed to create the automatic CDI modifier: failed to generate CDI
spec for mode "auto": ... failed to initialize NVML: Driver Not Loaded
```
followed by `Successfully rolled back cluster changes`. Distinct from #21 (fresh install,
driver never installed): here the machine worked yesterday and the host kernel was updated,
so the NVIDIA module was never rebuilt for the running kernel. The CDI wording means the
**k3d node itself could not be created** — not that a job lacks a GPU.
**Fix:** compare `uname -r` against the kernel recorded in `install-notes.md`, check
`nvidia-smi`; rebuild the driver for the running kernel (`sudo dkms autoinstall` or re-run the
driver installer) and reboot before retrying. Record the kernel version in the notes so this
is diagnosable months later.

### 21c. `GPU setup aborted` — the install stopped and nothing happened
Not a crash: GPU detection failed, the installer asked `Do you want to continue without GPU?`
with default **No**, and the default was accepted (Enter, or `--yes`).
This prompt fires only when NVIDIA tooling is **present but broken** (driver not loaded,
toolkit missing). On a machine with no NVIDIA hardware at all there is no prompt and no
problem — do not pre-empt it with `--cpu`.
**Fix:** fix the driver (#20/#21) and re-run, or answer **yes** to install CPU-only now and
add GPUs later with `leap server reinstall`.

### 21d. GPU worked at install time, then jobs start failing with `nvml error: unknown error`
`nvidia-container-cli: detection error: nvml error: unknown error` mid-session, on a GPU that
passed the install-time pre-check and had been working. Restarting individual pods does not
clear it. **Unresolved in the field** — the one documented incident correlated with a
`leap server upgrade` having run and with a concurrent pippin dependency-build, but neither was
confirmed as the trigger, and the thread ended without a confirmed fix (reinstall was the
last resort discussed, not verified to work). If you hit this: capture `nvidia-smi` and
`leap server info` before trying anything, since the team has no repro yet. `leap server
reinstall` is the documented next step to try, in that order — not a confirmed cure.

### 22. `open /run/nvidia-persistenced/socket: no such file or directory`
**Fix:** `sudo systemctl enable --now nvidia-persistenced`, retry.

### 23. `OCI runtime create failed ... nvidia prestart hook` while host nvidia-smi works
Container runtime blocked — apparmor/security agent interference has caused this (RHEL/hardened
hosts, or IT just reinstalled drivers).
**Fix:** `nvidia-ctk runtime configure` + docker restart first; if it persists try
`--security-opt apparmor=unconfined` on the test container to confirm apparmor, fix the docker
profile, reboot. Ask IT what changed (driver/Docker updates on shared servers).

### 24. "fail to check nvidia gpu" / "fail to check GPU index"
On **WSL2**: old CLI GPU detection depended on `lspci`, which WSL Ubuntu doesn't ship.
**Fix, in order:** (1) upgrade the CLI — detection no longer needs lspci; (2) only as a
fallback `apt install pciutils` (corporate apt mirrors have not carried it, and editing
`/etc/apt` may be off-limits); then `leap server reinstall` with GPU. Verify both `nvidia-smi`
in WSL *and* inside docker before the session.
On **non-WSL hosts** (RHEL, shared servers) the same message usually means the docker↔GPU
integration broke under it — a Docker or security-agent update on a shared machine. Re-run the
docker GPU pre-check (#20) and ask IT what changed; CPU-only install unblocks meanwhile.
Related: **fractional GPU is not supported** — a `GPU per run = 0.5` setting errors the
evaluate; Kubernetes allocates whole GPUs (#25).

### 25. GPU jobs Pending: "Insufficient nvidia.com/gpu"
Kubernetes allocates whole GPUs; something else holds it — another user, or the desktop
compositor on a workstation.
**Fix:** `nvidia-smi` to see holders; free/restart to release; on shared machines select
specific GPUs at install ("select specific" at the GPU prompt — leave the display GPU out).
If docker sees the GPU but `kubectl describe node` lists no `nvidia.com/gpu`, upgrade CLI +
`leap server reinstall` (k8s GPU discovery fixes shipped for WSL2).

---

## Cloud VMs (EC2 / SageMaker / Azure)

### 26. EC2: tiny root volume
Fresh GPU EC2s ship with ~tens of GB on `/` and the real ~TB storage as separate NVMe/EBS
mounts; Docker and `/var/lib/tensorleap` land on root and choke.
**Fix:** move Docker data-root (item 2) AND install with `-d /data1/tensorleap` before
anything else. Access: see item 51 — SSM port-forward, SSH `-L`, or a security-group
allowlist that already permits `:4589`.

### 26b. EC2: after stop/start, ingress-nginx can't find files under the mounted volume
Looks like an SSM/permission problem but usually isn't — a boot-ordering race where the EBS
data volume mounts *after* Docker/k3d/containerd already started against it.
**Fix:** confirm the volume actually mounted first (`df -h`, `lsblk`) before touching
permissions; if it raced, `docker restart` (or reboot) once the volume is confirmed mounted
resolves it — don't chase a permissions theory before ruling this out.

### 27. EC2/VM: install "disappeared" after stop/start
The data dir (or docker root) was on an ephemeral disk that the stop wiped.
**Fix:** put `--data-dir` on a persistent mounted volume and reinstall pointing at it —
everything resumes from the folder. Ephemeral/short-lived VMs are otherwise fine: reinstall
against the same persistent dir is ~10 minutes.

### 28. ECS / Fargate
Impossible by design — Tensorleap is docker-in-docker (privileged k3d), which Fargate forbids.
**Fix:** plain EC2.

### 29. SageMaker notebooks
Multiple known quirks: root volume too small (grow to 200–300GB; move docker data-root to the
persistent secondary volume); every stop/start wipes tools outside the persistent volume (use
a lifecycle configuration or re-run the curl/wget bootstrap one-liner each start — locked-down
IAM may block lifecycle configs entirely, and terminals alternate sh/bash so keep both curl
and wget variants); UI reached via the Jupyter proxy — install with `--proxy-url` set to the
notebook URL and browse `<notebook-host>/proxy/4589`; `leap auth` line must be edited to
`http://localhost:4589` on-machine.

### 30. Azure ML compute
`/mnt` is wiped on every stop/start (reinstall each session), some Azure mounts force root
ownership (item 7), corporate firewalls have blocked Azure entirely (hotspot workaround), and
access goes through SSH `-L`/Bastion port-forwarding. Field verdict after ~25 cumulative hours
of fighting: **prefer a plain Azure VM** — same install "just works" there. Azure Bastion:
tunnel with its dedicated SSH port-forward command; the port serves nothing until the install
completes.

---

## Registry & network

### 31. Slow or flaky image pulls — "failed to cache public.ecr.aws/..." / stalls
Install pulls ~15GB. Transient failures and long silent stretches are normal; the CLI retries
(3 attempts per image) and re-runs pull only the diff.
**Fix:** re-run the install; verify connectivity separately (`docker pull redis`); watch
progress via the registry dir growing or `watch df -h`. Partial-layer garbage after many
failures: `docker system prune -a`, then re-run. If exactly one image stays missing,
`leap server reinstall` resets the local registry service (cached layers persist).

### 32. Pull failed: `toomanyrequests: Rate exceeded` (public.ecr.aws / docker.io)
**Fix:** wait — automatic retries with backoff usually clear it; persistent on shared NAT →
airgap pack.

### 33. `images not found: [public.ecr.aws/tensorleap/engine:<tag>]` / `manifest unknown`
Manifest references a tag never pushed or cleaned up — typical with `-t <branch-tag>`.
**Fix:** install a stable release (omit `--tag`).

### 33b. `no chart version found for tensorleap-<version>`
The GitHub manifest resolved, but `helm.tensorleap.ai` has no such chart — typically an
`-rc.N` prerelease that was never published to the chart repo. Distinct from #33 (missing
*image* tag) and #45 (`old manifest`).
**Fix:** install a stable version — omit `--tag`, or pass a published one.

### 34. Only Tensorleap's own images fail to pull (public ones fine)
Corporate network blocks ECR specifically.
**Fix:** ask IT to allowlist `public.ecr.aws`; otherwise airgap.

### 35. Proxy: cluster can't reach the internet though the host can
The k3d cluster inherits proxy config only from env vars at install time; vars set in the user
shell are lost through `sudo`, and are missing in new terminals.
**Fix:** export `HTTP_PROXY`/`HTTPS_PROXY` (both with `http://` scheme) + `NO_PROXY` in the
environment that runs the install (and the root env if sudo is involved); persist them in the
shell rc; ensure `NO_PROXY` covers localhost/cluster ranges (the installer appends these
automatically). Specific URLs blocked by the proxy (e.g. fastly-fronted registry endpoints):
capture the exact failing URL from the log and have IT allowlist it. TLS-intercepting proxy →
item 36.

### 35b. "We have internet" is not evidence — test each host
Real case: `git clone github.com` and `pip` worked while `ping google.com`, `ping github.com`
and `api.github.com` all failed — partial allowlists are the norm, so verify per host before
theorising:

```bash
for h in api.github.com github.com objects.githubusercontent.com helm.tensorleap.ai \
         public.ecr.aws registry-1.docker.io auth.docker.io registry.k8s.io quay.io \
         ghcr.io docker.elastic.co nvcr.io gcr.io pypi.org; do
  printf '%-34s %s\n' "$h" "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "https://$h" 2>&1 | tail -1)"
done
```

Related symptom: when the **CLI can't reach its own cluster** ("bad context", kubectl hangs)
behind a proxy, local/cluster traffic is being sent to the proxy — `NO_PROXY` must cover
localhost, `127.0.0.1`, `*.svc`, `*.cluster.local` and the cluster CIDRs. As a last resort
when GitHub itself stays blocked, a support engineer can install from local charts
(`--local`/`--local-dir`) with a Tensorleap-provided charts checkout, bypassing the GitHub
metadata fetch.

### 36. TLS/cert errors pulling images behind a corporate proxy
No custom-CA injection exists for containerd inside the node.
**Fix:** OS trust store for installer + Docker; in-cluster pulls still failing → airgap.

### 37. GitHub unreachable / rate-limited
`leap server install` fails resolving the latest version ("No internet connection found" /
API 403). **Fix:** `export GITHUB_TOKEN=<token>` for rate limits; behind a proxy see item 35;
fully blocked → airgap.

### 37b. `Build manifest failed` — version resolution died before anything was touched
Three sub-errors, all in the first seconds of the run, all safe to retry (nothing on the
machine has changed yet):
- `github API error: status 504` → GitHub outage/hiccup. Retry; then `export GITHUB_TOKEN`.
- `no tag found` → the requested version couldn't be resolved. Retry, or drop `--tag`.
- `failed getting file from (https://github.com/tensorleap/helm-charts/releases/download/<tag>/manifest.yaml): 404`
  → that tag has no published manifest. **Read the tag back out of the URL** — typos land in
  it verbatim (a real case shipped `matifest-1.6.68`). Drop `--tag` to take latest.

### 38. Very slow in-cluster dependency builds (pippin/torch at KB/s)
Downloads inside the build container crawl while the host is fast — proxy/QoS shaping of
in-container traffic.
**Fix:** configure the corporate PyPI mirror (`--pip-index-url` or platform Settings); compare
host vs in-container speeds to show IT; otherwise wait it out once (image is cached after).

---

## Airgap

### 39. Switching an online install to airgap keeps failing
State under the data dir remembers the online mode and corrupts the switch.
**Fix:** `leap server uninstall --purge` (or `sudo rm -rf /var/lib/tensorleap` — data loss,
confirm first), then `leap server install --airgap <tar>` with a CLI version matching the
pack. Re-register and re-apply license after.

### 39b. Airgap tar rejected: `not found images.tar` / `not found manifest.yaml`
The pack is incomplete — almost always a truncated multi-GB transfer.
**Fix:** compare the file size (and a checksum if you have one) against the source, re-transfer.
Separately, during airgap bootstrap the line `k3d image import reported an error; verifying
actual image presence in containerd before failing:` is **expected** — the installer verifies
and continues. Don't abort the session on it.

### 40. Fresh airgap install "shows the old projects — did it even work?"
Not a failure: the data dir was seeded/reused, and Tensorleap adopted it.
**Fix:** nothing — installation is fine; delete unwanted projects in the UI.

---

## Data & dataset volumes

### 41. Dataset folder empty inside the platform / "file does not exist" / "list index out of range"
Three classic causes: (a) **symlinks inside the mount don't resolve** — docker bind-mounts
don't follow links to unmounted host paths; (b) data wasn't copied under the mounted path;
(c) code resolves paths relative to cwd, which inside jobs is `/app/generic_source`.
**Fix:** put real files (not links) under the mount; reference data by absolute path under the
mount; `leap server info` shows the mapping. NAS data: mount the network path itself as the
dataset volume (via `leap server reinstall`). These usually surface during `leap push` as
`code parsing failed see errors above` — get the real error with
`leap run logs <jobId>`. Sibling push failures that are actually install-side: `python version
<x> is not available` (the list comes from the *installed server* — an old install rejects a
newer Python; upgrade), `entry file '<f>' in leap.yaml is not found`, and a code-integration
timeout while the dependency image is still building (#38 — the job continues server-side).

### 42. Need to add/change a dataset mount after install
Docker can't add mounts to running containers.
**Fix:** `leap server reinstall` — re-prompts all questions, ~5 minutes (images cached), no
data loss. Avoid recurrence: mount a stable parent folder with room for future datasets.

### 43. Root-owned data directories rejected at the dataset-volume prompt
**Fix:** `chown`/`chmod` the directory for the installing user first; then copy the dataset in
(the prompt only mounts the folder — it doesn't fetch data).

---

## Version & upgrade

### 43b. `Duplicate mount point: <path>` at cluster creation
The same path was supplied twice at the dataset-volume prompt (or collides with the default
datasets mount). Fails **late**, at cluster creation, after the whole pull phase.
**Fix:** de-duplicate the `-v` list before running; never re-add the default datasets path.

### 43c. One pod crashloops right after installing a same-day release
Suspect the **release**, not the machine — a published image has shipped ARM-only and
crashlooped web-ui on an AMD64 server.
**Fix:** `kubectl -n tensorleap describe pod <pod>` for the image ref and the real error, check
the node architecture (`uname -m`), report it, and take the corrected build with
`leap server upgrade` once CI republishes. Don't rebuild the machine for this.

### 44. `Error: CLI upgrade required` / weird CLI crash right after release
The chart needs a newer CLI — or (rarer) the same-day CLI release is itself broken.
**Fix:** re-run the CLI curl one-liner; if the *latest* CLI is the broken one, pin the
previous version with `TAG=v<x.y.z>`. `leap server upgrade` can also sidestep an
install-path-only bug.

### 44b. `unsupported manifest version. Please update your CLI.`
A third, distinct version error (alongside #44 and #45): the manifest format itself is newer
than this CLI understands. **Fix:** re-run the CLI install one-liner; in airgap, use a pack
built for a CLI version you can install.

### 45. `Error: old manifest`
CLI newer than the requested tag allows. **Fix:** newer `--tag`, or match CLI to the airgap
pack version.

### 45b. Upgrade silently reset the port / domain / TLS / proxy to defaults
`upgrade` reuses `<data-dir>/manifests/params.yaml`. If that file is gone (a prior
`uninstall --clear-data` deletes `manifests/`), the CLI cannot recover the settings, falls
back to defaults — 4589/5699, domain `localhost`, **no TLS, no proxy** — and goes straight to
a reinstall, so a TLS/domain install comes back as plain localhost.
**Fix:** check for `params.yaml` *before* upgrading. Missing → recover the values from
`install-notes.md` and run an explicit `install` with those flags instead.

### 46. Ran `upgrade` but wanted a specific version / upgrade did nothing
`upgrade` always moves to latest and reuses previous params; it can't change ports, GPU,
domain, or data-dir. **Fix:** specific version = `install --tag <v>`; changed params =
`reinstall`. Run upgrade **on the server itself**, not from a client machine. Rule of thumb
from the team: don't upgrade daily; upgrade when told a version is worth taking, and run
`leap cli upgrade` together with `leap server upgrade` (then hard-refresh the browser).

### 47. Upgrade kills running jobs
It prompts first. Long evaluates can be continued afterwards, but the team recommends
restarting them clean. Schedule upgrades between runs.

### 48. Reinstall keeps defaulting to the old (wrong) data dir
The previous dir persists in `~/.config/tensorleap` even after `--purge`.
**Fix:** pass `--data-dir` explicitly. You *can* delete that config file — but only before an
**install**, never before an `uninstall`: uninstall has no default-path fallback and with no
recorded `data_dir` it targets the **current working directory**, `sudo rm -rf`-ing
`./storage ./registry ./containerd ./manifests ./helm-cache`.

### 48b. Changing `--data-dir`: the migration prompt is destructive both ways
On `-d <new-path>` the installer asks about moving the data, and **`--yes` does not bypass it**:
- new path already exists → *"do you want to overwrite it with previous storage directory?"* —
  **yes permanently deletes whatever is already at the new path**;
- otherwise → it runs an **uninstall first**, then moves the storage.

**Fix:** read the prompt aloud to the user and decide deliberately. If the destination holds
anything at all, **ask outright whether it is backed up and get a yes before any command
touches that disk** — "we'll mount it instead of overwriting it" is not a substitute for a
backup, because the same run can still be answered the wrong way at the prompt. `TL_DATA_DIR` disagreeing with the recorded `data_dir` fires this
same prompt from `install`/`run`/`stop` — prefer `--data-dir` and leave the env var unset.

### 48c. Installing with `--local`/`--local-dir` fails on `no matches for kind "Elasticsearch"`
`--local`/`--local-dir` installs from a helm-charts checkout instead of the published chart —
a dev/support path, not the normal one. It bypasses the published chart repo, so the checkout's
subchart dependencies (including the `eck-operator` that registers the `Elasticsearch` CRD)
must already be vendored in.
**Fix:** run `make build-helm` in the checkout before `--local`/`--local-dir` — skipping it
reproduces the CRD-ordering race (catalog #16) even on a healthy machine, because the CRD
chart was never pulled in the first place.

### 49. Second user on a shared server can't use the CLI / kubectl against the install
Install lives in a non-default data dir recorded only in the installer user's config; other
users' CLIs can't find it, and kube contexts are per-user.
**Fix:** copy the `data_dir` entry into each user's `~/.config/tensorleap/config.yaml`;
`leap server upgrade` from the new user regenerates their kube context (no sudo needed).
Best practice from field IT: install under a faceless/service account, not a personal one —
a closed employee account has broken installs before.

**Security note, not just a convenience one:** current installers write this kubeconfig to
`<data-dir>/manifests/kubeconfig.yaml` **world-readable**, and on Linux also source it into
every login shell via `/etc/profile.d/tensorleap-kubeconfig.sh`. That kubeconfig carries
cluster-admin credentials — **any local user on a shared install machine gets cluster-admin
Kubernetes access automatically**, not just the ability to run `leap` commands. On a
genuinely multi-tenant machine this is a real exposure to flag to the customer, not only a
convenience feature.

---

## Access, auth & license

### 49b. Corporate SSO required instead of local users
Enterprise blocker, not a bug: the bundled Keycloak can federate to corporate SSO (Microsoft
Entra etc.), but it has needed a Tensorleap-side build installed via a **specific server tag**
plus a joint session with the customer's SSO team. Keycloak's admin console lives at
`<server>:<port>/auth/admin`.
**Fix:** don't improvise — flag it early as a scoping item, and route to the team for the tag
and the configuration session.

### 50. Browser login fails with "HTTPS required"
Real `--domain` with TLS disabled — Keycloak refuses plaintext on non-loopback hosts.
**Fix:** reinstall with `--cert`/`--key`, or serve on localhost.

### 51. Can't reach the UI on a remote/headless server
The UI binds localhost:4589 on the server (see also item 26 for EC2 specifics).
**Fix — SSH-reachable:** `ssh <server> -L 4589:localhost:4589`, browse
`http://localhost:4589` — use `localhost`, not `127.0.0.1` (tunnels have refused on the raw
IP). **Fix — SSM-only EC2** (no inbound ports, no SSH): the SSM port-forwarding session is the
first-choice tunnel, and needs the Session Manager plugin installed locally:

```bash
aws ssm start-session --target <instance-id> --region <region> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["4589"],"localPortNumber":["4589"]}'
```

Inside a VPN/LAN, `http://<server-ip>:4589` works directly. The port serves nothing until the
install completes. Opening 4589 to the internet is not the fix — tunnel instead.
**Azure VM behind Bastion:** use Bastion's own SSH port-forward (`az network bastion
tunnel`/`ssh --resource-id … -L 4589:localhost:4589`). **SSM needs IAM:** the user must hold
`ssm:StartSession` for the port-forwarding document — a common silent blocker.
**Client-side checklist when the server is confirmed healthy:** connect the VPN *first*; quote
key paths containing spaces; try CMD / WSL / the VS Code terminal (corporate laptops differ);
use `localhost`, not `127.0.0.1`.

Local install still running while tunneling to a remote one → port collision. Installing with
a non-default `--port` is fine (the CLI accepts any URL). **Remapping only the local end of a
tunnel is not** — e.g. forwarding remote 4589 to local 4590: the CLI token is bound to the URL
it was generated for, and Keycloak cookies on `localhost` aren't port-scoped, so two UIs
clobber each other's session. Stop the local server instead of remapping.

### 52. Domain access breaks after Keycloak redirect / registration token not delivered on IP access
Older builds redirected back to `127.0.0.1:4589` after login when browsing via internal DNS,
and first-login tokens have failed on raw-IP access.
**Fix:** use the SSH port-forward + localhost for registration; for stable DNS access,
reinstall with `--domain` (and TLS); upgrade — ingress fixes shipped.

### 53. First-login / license traps
First registered user = admin, approves all later signups; **no password reset exists
on-prem** — a forgotten admin password has forced a full reinstall. No spaces in usernames.
Fresh installs carry a 1-week trial: apply the license/extension token via Settings →
Manage License. After any reinstall: re-register, re-apply token, re-run `leap auth login`.

### 54. `leap auth` line from the UI fails on the machine itself
The generated token line targets the URL you browsed (https / proxy address), and a URL
without a scheme is rejected outright (`URL must start with http:// or https://`).
**Fix:** edit it to `http://localhost:4589` before running. Multiple servers can be logged in
at once — they are saved as environments and `leap auth select` switches between them, so
moving between a local and a remote install does **not** require re-authenticating. Besides
the UI token line, `leap auth login <url>` also accepts `--api-key` or `--username`/`--password`.

---

## Uninstall & cleanup

### 55. Uninstall / purge fails with `unlinkat ... permission denied`
```
unlinkat <data-dir>/containerd/io.containerd.grpc.v1.cri/containers/<id>/status: permission denied
unlinkat <data-dir>/helm-cache/tensorleap/1.6.39.tgz: permission denied
```
Also surfaces as `Failed purge data: exit status 1`. containerd inside the k3d node wrote
those trees as root; the uninstall runs as the regular user and cannot unlink them. **A retry
trap** — one host retried 3× in two minutes, unchanged. `--purge` does not always succeed.
**Fix:** remove the root-owned cache trees explicitly, then re-run the uninstall:

```bash
sudo rm -rf "<data-dir>/containerd" "<data-dir>/helm-cache"   # cache only — storage/ survives
leap server uninstall            # add --purge only if data loss is intended
```

Only `sudo rm -rf <data-dir>` removes everything (including projects **and**
`install-notes.md`) — confirm explicitly before suggesting it, and **copy `install-notes.md`
out first**: once `config.yaml` and `manifests/params.yaml` are gone it is the only surviving
record of the domain, TLS, proxy, GPU and volume settings needed to rebuild.

Before any of this, if `~/.config/tensorleap` was deleted: `cd /`, restore the `data_dir` line,
and confirm with `leap server info` — see the uninstall reference in the skill. Retrying the
purge from a project directory is how the user's own `storage/` folder gets deleted.

## After the install — expected behaviour, and tuning

### 55b. A long evaluate dies with `socket.gaierror`/`MaxRetryError` reaching minio, mid-run
Not job sizing (#56) — a cluster-wide DNS outage. k3s ships CoreDNS at a tiny 170Mi
limit/Burstable QoS by default; a burst of DNS lookups during heavy engine load (streaming-PCA
against minio) can OOM-kill it, and RabbitMQ/other pods fail the same way at the same moment —
several unrelated-looking failures that are actually one cause.
**Confirm:**
```
kubectl -n kube-system describe pod -l k8s-app=kube-dns | grep -A5 -E "Restart Count|Last State"
```
`Restart Count >= 1` with `Last State: OOMKilled` around the failure time confirms it.
**Fix merged to helm-charts master 2026-08-13 — but not yet in any released installer.**
The installer patches CoreDNS to Guaranteed QoS (requests==limits, 512Mi/250m) on every
install/upgrade, but as of installer pin `v0.10.15` (shipped in the latest CLI, `v0.0.161`)
**no released version carries it** — verified on a live install, which still shows the k3s
default. Do not assume a current install is protected; check:
```
kubectl -n kube-system get deployment coredns -o jsonpath='{.spec.template.spec.containers[0].resources}'
```
`{"limits":{"memory":"170Mi"},"requests":{"cpu":"100m","memory":"70Mi"}}` = unpatched (the
k3s default, Burstable). `512Mi`/`250m` on both requests and limits = patched.
**Apply it yourself today** — same patch the installer will apply, safe to run on a live
cluster (CoreDNS restarts in seconds):
```
kubectl -n kube-system patch deployment coredns -p '{"spec":{"template":{"spec":{"containers":[{"name":"coredns","resources":{"requests":{"cpu":"250m","memory":"512Mi"},"limits":{"cpu":"250m","memory":"512Mi"}}}]}}}}'
```
Re-applied automatically once an installer release containing the fix lands; note a k3s
version change can reset the manifest, so re-check after a major upgrade.

### 56. Jobs OOM, insights stall, "unexplained errors" under load
The install is fine; the platform's job resources are sized for a bigger machine than this
one. This is the **most common post-install complaint** and the fix is in-product, not a
reinstall.
**Fix:** platform **Settings → Kubernetes/job settings**: raise per-job memory (one customer
went to ~9000), give workers ~2 CPUs each, set worker count (~5), raise job TTL. Tensorleap
also runs a "calibration" pass that sets requests/limits per service from the machine's real
resources — offer it when the workload is known. Dashboards crawling under concurrent
evaluate + analysis usually means the machine itself is undersized.

### 57. Expected, not a failure — five things that look broken and aren't
- `leap server tools` → `no kubernetes cluster available` **during** an install: the cluster
  is created after the image phase. Wait.
- Browser **503 Service Temporarily Unavailable** right after install/upgrade: pods still
  starting (Elasticsearch is slowest and the gateway waits for it). Wait, then hard-refresh.
- First `leap push` takes **10–20 min** building the requirements image; later pushes reuse it
  unless requirements change.
- `leap code push` **CLI wait times out** while the build continues server-side — cosmetic;
  follow the job in the UI.
- First job after an upgrade **pends a couple of minutes** while images cache into k8s.
- **The last lines of a successful run can be red `ERROR`s.** Installer telemetry POSTs after
  the work is done, and that call failing (`POST request failed with status code: 500`,
  `res body: 500 Internal Server Error`) is logged as an error at the very end of an otherwise
  clean install/upgrade. The authoritative outcome is the `Successfully completed <command>`
  line just above it. Likewise, counting `error`/`failed` matches in the log is meaningless —
  a healthy run routinely contains hundreds of benign debug lines (e.g. `failed to get IP for
  container /k3d-tensorleap-tools ...`). Read the *first* `Failed` line and the final
  `Successfully completed` line, not the tail and not a match count.

### 58. Moving an install to another machine
PoV box returns to the pool, the server was outgrown, or the VM is ephemeral.
**Fix:** `leap server stop` → copy the whole data dir (and `install-notes.md`) to the new host
→ `leap server install -d <same path>` there. ~10 minutes; projects, users and history come
with it. Inverse symptom: **"my projects are gone"** after a reinstall usually means the CLI is
pointed at a *different* data dir — check `data_dir` in `~/.config/tensorleap/config.yaml`
against what's on disk before concluding data loss.

## Notes

- Arriving at an unfamiliar machine: read `<data-dir>/install-notes.md` first (the skill's
  record of setup, quirks, and past incidents), then `<data-dir>/manifests/params.yaml` (the
  installer's own record of the last flags).
- **Confirm you are on the right machine before diagnosing.** Running `leap server info` on a
  laptop instead of the server has cost real time; on look-alike VMs, identify the host by its
  known data mount. The CLI and the server are frequently on different machines.
- Python: engine base images cover a fixed set of versions — a corporate standard of
  3.11/3.12 is worth confirming against the installed server before the first push, since the
  accepted list comes from the server, not the CLI.
- Installer telemetry (start/failure/success plus the error payload, hostname and flags) is
  sent to Tensorleap unless the install is airgapped or `DISABLE_REPORTING=true`. When working
  with Tensorleap support, give them the exact time of the attempt — they can read the
  structured report instead of asking for terminal screenshots. No telemetry exists for
  license/trial state or for airgap installs.
- The verbose log at `<data-dir>/logs/<command>_<timestamp>.log` has everything the terminal
  didn't show; `VERBOSE=true` streams it live. `watch df -h` plus
  `leap server tools kubectl get pods -n tensorleap -w` in side terminals are the standard
  live-monitoring pair.
- Security questionnaires: nothing leaves the machine except installer telemetry
  (disableable) and image pulls; data and models never leave; SSO via Keycloak, internal pip
  mirrors, and full airgap are the supported answers to "no external services".
