---
name: tensorleap-install
description: >
  Use when installing, upgrading, or reinstalling a Tensorleap server on a
  machine — "install tensorleap", "leap server install / upgrade / reinstall",
  "set up tensorleap locally", "my tensorleap install failed / is stuck",
  "airgap install", "install behind a proxy", "tensorleap on WSL2 / EC2 /
  SageMaker / Azure / with GPU" — runs preflight checks (Docker RAM, disk and
  data-root, GPU, WSL2, network), composes the right command (domain/TLS,
  data-dir, dataset volumes, proxy, airgap), monitors the run, verifies first
  login, diagnoses failures from a field-sourced failure catalog, and records
  each action's setup in the data dir's install-notes.md so later upgrades and
  reinstalls reuse that context.
group: tensorleap
---

# Install Tensorleap

`leap server install` creates a single-node k3d Kubernetes cluster inside Docker, then installs
two Helm charts: `tensorleap-infra` (ECK operator + CRDs, zot registry, nvidia device plugin)
followed by `tensorleap` (the app). End state: the platform serving at `http://localhost:4589`
(or the user's domain), with all state under a data directory (default
`/var/lib/tensorleap/standalone`). Everything persists on disk — clusters and containers can be
destroyed and reinstalled in ~10 minutes without losing projects, as long as the data dir and
Docker's image cache survive.

This skill also maintains **`<data-dir>/install-notes.md`** — a record of the machine's setup
(proxy, volumes, domain/TLS, quirks, past issues) written after every action and read before
the next one, so an upgrade or reinstall months later starts with full context. Always run
Step 0 first.

Hard rules:

- **Strongly prefer not running the install with `sudo`.** Run as a regular user in the
  `docker` group; the installer prompts for sudo itself where it needs it. Sudo installs
  create root-owned storage folders that make pods (Keycloak, Elasticsearch) crash with
  permission-denied, and later break other users' upgrades on root-owned cache files.
  *Exception seen in the field:* on a shared machine where IT refuses docker-group membership,
  a sudo install does work on a current CLI (its check-permissions step repairs storage
  ownership) — accept it knowingly, and expect the chart-cache trap (#7b) for other users.
- **Never run `uninstall`, `reinstall`, or `upgrade` without explicit user confirmation.**
  Reinstall/upgrade stop all running jobs; `uninstall --purge`/`--clear-data` delete user data.
- **Never blind-retry a failed install** before matching the error in
  [install-troubleshooting.md](reference/install-troubleshooting.md). Some errors are retry-safe (image-pull hiccups —
  re-running pulls only the diff), but three return the identical failure every time: the
  resource preflight gate, the WSL2 read-only data dir, and the Helm release lock.
- Run **`leap server install` itself** from a clean shell: `conda deactivate` / leave any
  virtualenv first — active Python environments have broken the installer. (This applies to
  the installer only — the CLI installs fine inside a virtualenv, and conda is a perfectly
  good home for the user's *integration* code environment.)
- `leap server check` is a no-op stub. Never suggest it as a diagnostic.

The failure catalog ships alongside this skill as `reference/install-troubleshooting.md`;
a read-only preflight script lives at `scripts/install_preflight.sh`.

## Step 0 — Recall prior state

Before asking the user anything, check whether this machine already has an install and what
this skill (or the installer) recorded about it:

```bash
DATA_DIR=$(sed -n 's/^data_dir:[[:space:]]*//p' ~/.config/tensorleap/config.yaml 2>/dev/null)
DATA_DIR=${DATA_DIR:-/var/lib/tensorleap/standalone}
cat "$DATA_DIR/install-notes.md" 2>/dev/null      # this skill's record — richest context
cat "$DATA_DIR/manifests/params.yaml" 2>/dev/null # installer's own record of the last flags
leap server info 2>/dev/null
leap server --info 2>/dev/null   # "Installer Version:" — the embedded helm-charts pin,
                                  # NOT the same number as `leap --version` (the CLI binary)
```

A stale-looking fix or a flag that shouldn't exist yet can mean `leap --version` is current
but the embedded installer pin (`leap server --info`'s "Installer Version" line) is older —
the CLI vendors a specific helm-charts release, and the two version numbers are independent.
`leap cli upgrade -s | bash` refreshes both together.

(Don't `cat` the whole `config.yaml` — it contains API keys; the `sed` line extracts only
`data_dir`. Keep this line `sed`-based and free of awk positional fields: a dollar-sign
followed by a digit anywhere in this file is rewritten by slash-command argument
substitution whenever the skill is invoked with arguments, which silently empties
`DATA_DIR` and sends Step 0 to the default path on machines that use a custom
`--data-dir`.)

- **Notes found** → this is an upgrade, reinstall, or repair. Don't re-run triage from
  scratch: present the recorded setup back to the user ("last installed 2026-06 with
  `-d /data1/tensorleap`, GPUs 0,1, proxy X, volumes Y — still accurate?") and only ask about
  what changed. Jump to [Upgrading](#upgrading-leap-server-upgrade) or
  [Reinstalling](#reinstalling-leap-server-reinstall) as appropriate.
- **Install exists but no notes** (installed before this skill existed) → reconstruct from
  `params.yaml`, `leap server info`, `docker info`, and env (`env | grep -i proxy`), confirm
  with the user, and write the notes file at the end of this action.
- **Nothing found** → fresh install; continue with Step 1.

## Step 1 — Triage the environment

**First, confirm the deployment shape.** This skill covers the **standalone** install only
(k3d Kubernetes inside Docker, one machine, ~1 hour). Route away if the answer is:
*existing Kubernetes / OpenShift / HPC* → a helm install with StorageClass, ingress, namespace,
certificates and SSO design decisions — a scoping conversation, not this skill; *Tensorleap
SaaS* → nothing to install. Segmented orgs (e.g. sites that can't route to each other) get
**one standalone install per segment**, with no shared state between them — say so early.

Then ask everything in one message (skip what's already known from Step 0 or context):

> 1. **OS / platform** — macOS, Linux, Windows (→ WSL2)? Bare machine, shared multi-user
>    server, or a cloud VM (EC2 / SageMaker / Azure ML / Azure VM)?
> 2. **Network** — open internet, behind a corporate proxy, or fully air-gapped?
> 3. **GPU** — should training use NVIDIA GPUs? (Linux only; never on macOS) Shared with other
>    users/workloads?
> 4. **Access** — will users browse from this machine, or remotely (SSH tunnel / VPN / domain)?
>    If a real domain: is there a TLS cert + key?
> 5. **Disks** — where is the big disk? Check both the OS root and data mounts. Tensorleap
>    wants ~65GB free for Docker **plus** 150–300GB on the data disk for a real POC.
> 6. **Data** — where do the datasets live (local disk, NAS, S3)? Local paths need to be given
>    as `--dataset-volume` at install time.

**If the install is a scheduled session with a customer, send this ahead.** The most common
reason an install call is wasted is that IT hasn't prepared the machine. Hand over verbatim:

> **Before our session, on the target machine:**
> 1. Docker installed and running; the account we'll use is in the `docker` group
>    (`docker ps` works without sudo). Confirm whether that account has `sudo` — the installer
>    needs it to create its data directory.
> 2. GPU (if any): NVIDIA driver + **nvidia-container-toolkit**, verified with
>    `nvidia-smi` and `docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi`
>    — both must print without error.
> 3. Disk: ≥65 GB free where Docker stores images **and** 150–300 GB where Tensorleap will
>    store data; tell us the mount points (`df -h`, `docker info | grep "Root Dir"`).
> 4. RAM ≥32 GB (64 GB+ recommended), 16+ CPUs preferred.
> 5. Network: either open egress, or ask your proxy/firewall team to allow the host list we
>    sent; internal PyPI mirror URL if you have one.
> 6. Access: how we reach the machine (SSH/SSM/Bastion/VPN) and how users will reach the UI.
> 7. Datasets: which host directories must be visible to the platform.

## Step 2 — Preflight

Quick path: `bash <this skill's dir>/scripts/install_preflight.sh` (use the skill's
absolute path — your shell's cwd is the user's project, not the skill) runs every read-only check in this
step and prints a report; the sections below explain how to read it and what to fix.

Fix failures before installing — the installer's own checks happen late and some failures roll
back a 20-minute install.

```bash
docker ps
```
Fails → Docker not installed, not running, or the user isn't in the `docker` group — the
installer reports all three identically as "docker is not running". Fix group membership with
`sudo usermod -aG docker $USER` then `newgrp docker` (stale SSH/tmux sessions won't pick it up —
open a fresh shell).

**Resources.** The installer's hard gate (measured on the *Docker* filesystem via
`docker run --rm alpine:3.18.3 df -P /`): ≥ 6 GB Docker memory, ≥ 65 GB free Docker storage.
**Passing the gate is not enough** — Elasticsearch alone requests *and* limits 6Gi, and
minio/rabbitmq/orchestrator/node-server add ~2Gi, so under roughly **12 GB of Docker memory
ES never schedules**: preflight passes, the ES pod sits Pending on `Insufficient memory`, helm
burns its 4-hour timeout and the user sees `context deadline exceeded` (catalog #11).
Real-world guidance the team gives customers: **32 GB RAM minimum, 64 GB+ recommended**
(parallelism is memory-bound), 16+ CPUs preferred, **150–300 GB storage** for a month-long POC.

```bash
docker info -f 'mem={{.MemTotal}} root={{.DockerRootDir}}'
docker run --rm alpine:3.18.3 df -P /            # Docker's disk — same probe the installer uses
d=/var/lib/tensorleap/standalone; while [ ! -d "$d" ]; do d=$(dirname "$d"); done; df -h "$d"
```

(On Linux `DockerRootDir` is a host path you can `df` directly; on macOS it lives inside the
Docker Desktop VM — only the alpine probe measures it.)

**The two-disk model — the most common field problem.** Docker's data-root holds all pulled
images and the k3d node; the Tensorleap data dir holds MongoDB, Elasticsearch (60Gi),
Keycloak, MinIO, the registry blobs, and (Linux) the containerd cache. Cloud VMs and many
servers have a tiny OS root with the real storage on a separate mount — then **both** must be
moved before installing:

1. Docker: **merge** (never clobber — the file often holds the nvidia runtime config) a
   `data-root` key into `/etc/docker/daemon.json`, restart, verify:

   ```bash
   sudo test -s /etc/docker/daemon.json || echo '{}' | sudo tee /etc/docker/daemon.json
   sudo jq '. + {"data-root":"/bigdisk/docker"}' /etc/docker/daemon.json | sudo tee /tmp/dj && sudo mv /tmp/dj /etc/docker/daemon.json
   sudo systemctl restart docker && docker info -f '{{.DockerRootDir}}'
   ```

   Then **validate before installing** — a bad data-root has broken Docker host-wide in the
   field (random `exit status 125`/`127` on pulls while a manual pull works):

   ```bash
   jq . /etc/docker/daemon.json && docker info -f '{{.DockerRootDir}}'
   sleep 5 && docker run --rm alpine:3.18.3 true && echo "docker healthy"
   ```

   Re-run the GPU pre-check too on a GPU machine. **If pulls start failing after the move,
   restore the previous `daemon.json`, restart docker, and install to the default location
   instead** — the relocation is an optimization, not a requirement. Note Docker Desktop and
   some cloud images have no `daemon.json` at all (create it), and Docker Desktop's disk is
   moved in its Settings UI, not here.
2. Tensorleap: install with `--data-dir /bigdisk/tensorleap` (separate folder from docker's).

**Fixed on current CLIs** (the storage probe reads the live daemon's actual data-root, not a
cached path) — a low-storage report right after a data-root move is real on today's CLI, not
a stale reading. If you still suspect a false negative, run `leap cli upgrade -s | bash`
first; only fall back to `DISABLE_DOCKER_CHECKS=true` (skips the check only, no side
effects) once you've confirmed the CLI is current and the number is still wrong.

**Eviction floor — bigger than one number.** The installer sets
`eviction-hard=nodefs.available<30G,imagefs.available<30G`, but eviction also fires on
*percentage*: in the field pods started dying around **15–20% free**, and the team quotes
"keep ~50GB free" on big machines. Treat the floor as **whichever is larger — 30–50 GB
absolute or ~15–20% of the disk**; on a 2 TB disk, 30 GB free is already too late. Growth to
plan for: units-of-GB to tens of GB per team per month; a 300M-sample stress test peaked ~1 TB
before compacting to 50–150 GB.

**Per-OS / platform:**

- **macOS** — resources are Docker Desktop's allocation (Settings → Resources), not the
  machine's. If the alpine probe's *total* is under ~80GB, no amount of pruning reaches 65GB
  free — the virtual disk limit itself must be raised. Suggested values: memory ≈ half the
  host RAM (≥8GB), virtual disk ≥150GB. No GPU support.
- **Linux + GPU** — the canonical pre-check (the single most commonly forgotten prerequisite is
  nvidia-container-toolkit):

  ```bash
  nvidia-smi && docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi
  ```

  If the docker half fails: install nvidia-container-toolkit from NVIDIA's official apt repo,
  run `sudo nvidia-ctk runtime configure --runtime=docker`, then `sudo systemctl restart docker`
  and re-check. See troubleshooting for stale-repo, Secure Boot (MOK), apparmor, and
  `nvidia-persistenced` variants. CPU-first install + `leap server reinstall` after fixing GPU
  is a legitimate unblocking pattern.
- **WSL2** — works, two traps: (a) **always pass `--data-dir` to a path inside the WSL
  filesystem** (default `/var/lib/tensorleap` is read-only to Docker Desktop's mount
  namespace); (b) the WSL virtual disk physically lives on `C:` regardless of `--data-dir` —
  if `C:` is small, move the distro (`wsl --export/--unregister/--import` onto the big drive,
  catalog #5) before installing. Docker Desktop's own disk image *also* lives on `C:` — move
  it via Docker Desktop Settings → Resources → Advanced → Disk image location (the
  `daemon.json` data-root trick does not apply under Docker Desktop). Don't install to a
  Windows drive mount (`/mnt/e/...`) — permission errors. Expect slow I/O
  (Windows→WSL→docker→k3d stacking) — that's normal.
- **Cloud VMs** — see the platform notes in
  [install-troubleshooting.md](reference/install-troubleshooting.md#cloud-vms-ec2--sagemaker--azure): EC2 (tiny root
  volume, put data-root + data-dir on the NVMe/EBS data disk, SSM/SSH port-forward), SageMaker
  (bootstrap re-run after every stop/start, UI via `/proxy/4589`), Azure ML (`/mnt` wiped on
  stop/start — prefer a plain Azure VM), ECS/Fargate (impossible — needs privileged
  docker-in-docker).

**sudo** — "never install *with* sudo" does not mean sudo is unneeded: the installer shells out
to `sudo mkdir`/`sudo chmod` for the data dir and dataset-volume paths and will prompt for a
password (warn the user, or they'll think it hung). Check availability **once** with `sudo -v`
(on a locked-down box each failure prints "This incident will be reported" and mails IT — one
probe is preflight, repeats are noise that lands on the admin you're about to ask for a
favour) —
if the user is in `docker` but not in `sudoers`, the run hard-fails at the dataset-volume
prompt (catalog #7c); pick a data dir and volumes they already own and pass them explicitly,
or have an admin pre-create and `chown` them.

**Ports** — `4589` (HTTP), `5699` (registry), `443` (only with TLS) must be free. The
installer does **not** check; a conflict surfaces as a late cluster-creation failure with full
rollback. `lsof -i :4589 -i :5699` first — but read the output: if the listener is
`com.docker`/k3d (lsof shows it truncated as `com.docke`), that IS an existing Tensorleap install → go back to Step 0 (upgrade or
reinstall), don't treat it as a conflict. A foreign listener that must keep running → install
with `--port <free>` (and/or `--registry-port`) after the user approves — the port becomes
their permanent URL and changing it later forces a reinstall; substitute it everywhere 4589
appears below, including the `leap auth` URL. A leftover local install also collides with an
SSH tunnel to a remote one — stop one or shift the tunnel's local port.

**Network (online installs)** — the machine must reach: `api.github.com`, `github.com`,
`objects.githubusercontent.com`, `helm.tensorleap.ai`, `public.ecr.aws`,
`registry-1.docker.io` + `auth.docker.io` + `production.cloudflare.docker.com`,
`registry.k8s.io`, `quay.io`, `ghcr.io`, `docker.elastic.co`, `nvcr.io`, `gcr.io`, and PyPI.
Give this list to the proxy/firewall admin. Corporate PyPI mirror? Pass
`--pip-index-url`/`--pip-extra-index-url` (also configurable later in platform Settings). If a
registry can't be opened, use the airgap path.

Behind a proxy, **verify the proxy actually permits the hosts before composing any fix** —
exporting proxy vars against a closed allowlist just changes which error you get:

```bash
curl -sS -x http://proxy.corp:3128 -o /dev/null -w '%{http_code}\n' https://api.github.com
docker pull public.ecr.aws/tensorleap/engine-generic:master-ee2324f1-py310  # registry reachable?
```

Tell the user plainly: **proxy env vars vanish under `sudo` and in every new terminal** — put
them in the shell rc (and the root environment if any step needs sudo), or the next session
fails identically.

## Step 3 — Install the leap CLI

```bash
curl -s https://raw.githubusercontent.com/tensorleap/leap-cli/master/install.sh | bash
```

- No sudo available: `curl -s ... | BIN_DIR=~/bin USE_SUDO=false bash -s -- --no-sudo` — or
  download the binary directly from leap-cli GitHub releases, `chmod +x`, put it on `PATH`.
- Pin a version: prefix with `TAG=v<x.y.z>`.
- Server installs run on Linux/macOS (Windows → WSL2). On a shared server, each user installs
  the CLI **on their own workstation** and talks to the server over HTTP — only the server
  install itself runs on the server.
- Weird CLI failures right after install → the fix is often just re-running the curl line to
  get the latest CLI (several install bugs were fixed CLI-side). A same-day release can itself
  be broken — pinning the previous `TAG` is the workaround.
- The one-liner erroring on its very first run has been fixed by simply **closing every
  terminal open against that machine and reopening one** — stale SSH sessions.
- A `pip`-installable leap CLI exists but lags the curl script; prefer the script.
- Windows *client* CLI (for users who only push code): the installer puts it on the **user**
  PATH, so `leap` may not resolve in a fresh CMD/PowerShell — copy the entry into the system
  PATH. The *server* install still requires WSL2.

**No sudo at all on this machine?** Then: the CLI can be installed entirely user-local (above),
but **`leap server install` will still demand sudo** even with a user-writable `--data-dir` —
so book the install with whoever holds sudo (IT). Don't burn time crafting a sudoers rule for
`leap cli upgrade`: it writes the new binary to a *randomized* `/tmp` path, so it cannot be
safely whitelisted. Good news for afterwards — **`leap server upgrade` does not need sudo**, so
the day-to-day user can self-serve upgrades once IT has done the initial install.

## Step 4 — Compose the install command

| Scenario | Command additions |
|---|---|
| Default (localhost, online, CPU) | `leap server install` |
| Custom data location (big disk / WSL2 / cloud) | `--data-dir /bigdisk/tensorleap` |
| GPU (Linux) | `--gpus <n>` or `--gpu-devices 0,1` — **prompt defaults, not settings** (see below) |
| Force CPU-only on a **GPU** host | `--cpu` (do **not** combine with `--gpus`/`--gpu-devices` — `--cpu` does not override them and you get a GPU cluster) |
| Limit cluster CPUs | `--cpu-limit <int>` (integer only — a non-integer is fatal; silently clamped to the host's CPU count) |
| Engine job memory budget | `--cluster-memory-gb <n>` (0 = auto-detect from Docker) |
| Free disk after install | `--clear-images` (sticky — reused on later runs) |
| Image cache location | `--image-caching docker-volume\|local-volume` (`local-volume` is Linux-only) |
| Real domain + TLS | `--domain tl.corp.com --cert cert.pem --key key.pem [--chain chain.pem]` (`--tls-port` defaults to 443, which drops the port from the URL) |
| Behind a reverse proxy / SageMaker | `--proxy-url https://public.url/path` |
| Air-gapped | `--airgap /path/to/tl-<version>-linux-amd64.tar.gz` |
| Pin version | `--tag <chart-version>` |
| Default ports busy | `--port <n>` / `--registry-port <n>` (sticky — changing later = reinstall) |
| Mount local datasets | `-v /host/data:/host/data` (repeatable) |
| Corporate PyPI mirror | `--pip-index-url <url>` |
| Non-interactive | `--yes` (see gotchas) |
| No auth (demo only) | `--disable-auth` |
| No in-cluster metrics agent | `--disable-metrics` |

**On a host with no NVIDIA hardware, pass no GPU flags at all — not even `--cpu`.** With no
`nvidia-smi` the installer detects no GPU, asks nothing, and installs CPU-only; `--cpu` exists
only to suppress the GPU prompt on a machine that *has* GPUs. Adding it on a CPU-only box is
noise that later readers of `install-notes.md` will misread as "GPUs were deliberately
disabled here".

**GPU flags are prompt defaults, not declarative settings.** On any host where `nvidia-smi`
lists GPUs the installer *always* asks `Select GPU option:` with `Use all` preselected —
`--gpus 2` only pre-fills the count and `--gpu-devices 0,1` only pre-ticks boxes. Pressing
Enter takes **every** GPU regardless of the flags, so on a shared or display-driving machine
you must actively pick "Select specific". Conversely, if GPU detection *fails* the installer
asks `Do you want to continue without GPU?` with the default **No** — accepting the default
aborts the whole run with `GPU setup aborted`; answer **yes** to get a CPU-only install now
and add GPUs later with `leap server reinstall`.

Consequence worth stating to anyone who asks for an unattended install on a GPU box: the only
two unattended outcomes are **all GPUs** (`--yes`) or **no GPUs** (`--cpu`). Selecting specific
GPUs *requires* someone at the keyboard. The prompts all come up front, before the long pull
phase, so "answer three prompts, then walk away" is the practical compromise — run it in
`tmux`/`screen` so the session survives.

Never pass the hidden `--dataset-dir`: it is deprecated and kills *every* `leap server`
subcommand at startup with a fatal error. Use `-v` instead.

**Domain + TLS — validate before installing.** A bad cert or wrong DNS only surfaces after the
install completes, and fixing either forces a full reinstall:

```bash
getent hosts tl.corp.com                                   # must resolve to THIS machine's IP
openssl x509 -in cert.pem -noout -dates -ext subjectAltName # not expired; SAN covers the domain
diff <(openssl x509 -in cert.pem -noout -pubkey) <(openssl pkey -in key.pem -pubout)  # cert↔key match
openssl verify -CAfile chain.pem cert.pem                   # chain validates (if a chain was given)
```

**Dataset volumes — get this right the first time:**

- The data must **physically live** under the mounted host path. Symlinks inside the mount do
  NOT resolve in the container (docker bind-mount semantics) — the folder shows up empty.
  NAS/network data: either mount the network path itself as the volume or copy data in.
- Keep the container path **identical** to the host path (`-v /data:/data`) so code paths work
  both inside and outside the platform.
- **Never invent or assume a host path.** Confirm each one exists (`ls -d <path>`) before it
  goes on the command line: a mount pointing at a non-existent or misspelled path is the
  empty-folder trap, and it is sticky (changing it later needs a reinstall).
- **De-duplicate the list** and don't re-add a path that's already mounted — a repeated path
  fails the install at cluster creation with `Duplicate mount point`, after the entire pull
  phase (catalog #43b).
- Default to the **exact paths the user named** (`-v /data/images:/data/images`). Offer a
  stable parent folder (`-v /data:/data`) as an opt-in — it avoids a reinstall when future
  datasets land beside it, but costs privacy (unrelated data in the tree) *and* time: the
  bigger the mounted tree, the longer upgrades/reinstalls spend scanning it (one customer's
  upgrade "was taking forever" for exactly this reason). Never accept the `$HOME` default the
  prompt suggests on a server with a data disk.
- The mount is **read-write** by default — it is a plain docker bind mount, so a job can write
  into the user's data tree. To protect a precious source tree, append `:ro` yourself
  (`-v /data/sets:/data/sets:ro`). Caveat: if the installer corrects the path's capitalization
  it re-suggests the mount **without** your `:ro` — re-check the value it proposes.
- Data in S3/cloud storage: point the volume at the **local download/cache directory** the
  code writes to, so it doubles as a persistent cache across runs.
- **No spaces in folder names**, and loose files need a labeled subfolder — the preprocess
  directory walk breaks on both.
- Adding/changing mounts later = `leap server reinstall` (re-prompts everything, ~5 min thanks
  to caching, no data loss). `leap server info` shows the current mapping. **Say this to the
  user in the proposal**, before they approve the mount list — it's the difference between
  one install and two.

Gotchas that regularly burn users:

- **`--proxy-url` is NOT an egress proxy.** It's the public reverse-proxy URL Tensorleap is
  served behind. An egress (corporate) proxy is configured with plain env vars —
  `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` (http:// scheme for both) — which the installer
  propagates into the k3d node, engine jobs, and the metrics agent. Put them in the shell rc
  or they'll be missing in the next terminal.
- **No custom-CA support exists anywhere.** A TLS-intercepting corporate proxy needs its CA in
  the OS trust store for the installer and Docker; containerd inside the node cannot be given
  one — if in-cluster pulls fail on cert errors behind such a proxy, switch to airgap.
- **A real `--domain` without `--cert`/`--key` breaks login**: Keycloak refuses plaintext auth
  on non-loopback hosts ("HTTPS required"). Localhost is fine without TLS.
- **Changing `--port`, TLS settings, or `--dataset-volume` on an existing install forces a
  full reinstall** (running jobs are lost, data survives). Warn before agreeing.
- **`--yes` is more dangerous than it looks.** It answers *every* prompt with the default, so
  on top of continuing past the resource gate it will: **auto-confirm a destructive reinstall**
  (tears down the cluster, kills running jobs) on an existing install, **take all GPUs**, and
  **mount `$HOME/tensorleap/data`** as the dataset volume. It also *keeps* the current version
  on an existing install — moving forward requires `leap server upgrade`. (`install`/`reinstall`
  without `--tag` may separately ask "Do you want to use latest version (latest: X, current:
  Y)?" if a previous install is found — pin explicitly with `--tag <version>` for any scripted
  run that must not silently change versions; `upgrade` never asks this, it always takes
  latest.) Use `--yes` only on a fresh machine with every other flag stated explicitly.
- **Changing `--data-dir` on an existing install triggers a migration prompt that is
  destructive either way**, and `--yes` does **not** bypass it: one answer runs an uninstall
  first and moves the storage; the other **deletes whatever already exists at the new path**.
  Read the prompt to the user before answering.
- Shared/CI egress IPs hit GitHub API rate limits — export `GITHUB_TOKEN` to fix.

**Airgap flow**: on a connected machine, download the pack from
`https://helm.tensorleap.ai/latest_airgap_versions.html` (or build one:
`leap server pack-installation -o pack.tar --tag <version>`). Transfer only the `leap` binary
(matching the pack's version) + the tar. Install with `--airgap <tar>`; telemetry is disabled
automatically and all images load from the tar into the local registry. **Switching an
existing online install to airgap**: purge state first (`leap server uninstall --purge`) or
the leftover online-mode state corrupts the install.

Useful env vars: `VERBOSE=true` (debug to stdout), `DISABLE_REPORTING=true` (no telemetry —
`--disable-metrics` does *not* cover this), `DISABLE_DOCKER_CHECKS=true` (skip the storage
preflight — for the false-negative after a data-root move),
`TL_DISABLE_KUBE_STORAGE_TAINT=true` (sets kubelet `eviction-hard` to `0%` instead of `30G` —
the escape hatch when a kubelet inside Docker misreports 0 available storage and taints the
node with false DiskPressure), `GITHUB_TOKEN`, `PIP_INDEX_URL`/`PIP_EXTRA_INDEX_URL`.

**Avoid `TL_DATA_DIR`** — prefer `--data-dir`. It is read by *every* `leap server` subcommand
(`run`, `stop`, `install`…), so if it disagrees with the recorded `data_dir` the CLI thinks
the data dir changed and fires the destructive migration prompt below.

**Show the user the final command with a one-line justification per flag, and wait for
explicit confirmation before running it.**

## Step 5 — Run and monitor

**Before running, size-check out loud.** If the machine clears the gates but sits under the
team's POC guidance — host RAM at or under 32 GB, or under ~150 GB for the data disk (every
laptop, and most Macs on Docker Desktop) — say so explicitly rather than silently proceeding:
it is fine for evaluation and demos, the POC guidance is 32 GB+ (64 GB recommended) and
150–300 GB, and real workloads will likely need per-job memory and worker counts raised in
the platform's Settings (see the catalog's post-install tuning entry). Passing the gate is
not the same as being sized for the work.

Run the composed command. Expectations to set:

- Online install: typically 10–40 min, dominated by image pulls (~15GB; office networks and
  HDDs stretch this a lot). Airgap: dominated by pushing images into the local registry.
- Sudo prompt near the start (data-dir creation) is expected — that's the installer, not a hang.
- The Helm phase waits up to **4 hours** with little output — a quiet terminal is not a hang.
  Elasticsearch is the slowest pod; the UI waits for it. "Disk pressure detected" warnings are
  the signal that it *is* stuck on disk.
- Transient image-pull errors during startup are normal — pods retry until Running.
- Full logs: `<data-dir>/logs/<command>_<timestamp>.log` — the file is named after the command,
  so an upgrade writes `upgrade_*.log`, a reinstall `reinstall_*.log`, and `run`/`stop`/
  `uninstall` log too.

Watch progress from a second shell (the CLI bundles kubectl):

```bash
leap server tools kubectl get pods -n tensorleap -w
```

Until the image phase finishes there is no cluster yet, so this correctly answers
`no kubernetes cluster available` — that is *not* a broken install, just "too early".

## Step 6 — Verify and first login

1. All pods `Running`/`Completed`; the installer printed `You can now access Tensorleap at <url>`.
2. Reach the UI: locally `http://localhost:4589`; remotely
   `ssh <server> -L 4589:localhost:4589` then browse `http://localhost:4589` (use `localhost`,
   not `127.0.0.1` — tunnels have failed on the raw IP), or `http://<server-ip>:4589` inside a
   VPN/LAN.
3. **First registered user becomes admin and approves every later signup. There is no
   password reset on-prem — make the user store the admin password now** (a forgotten admin
   password has forced a full wipe in the field). Mitigation to set up immediately: **have the
   first admin promote a second admin** — admins can reset each other, which turns a lost
   password from a reinstall into a two-minute fix. No spaces in usernames.
4. Apply the license/trial-extension token: Settings (gear) → Manage License → insert token →
   Apply. Fresh installs start with a 1-week trial. It can also be applied without the UI —
   `leap auth license -t <token>` (or `-f <file>`), and `leap server install` itself accepts
   `--license-token`/`--license-file`, which is the better path for scripted installs.
5. CLI auth: the "generate CLI token" line copied from the UI is only one of three login
   paths — `leap auth login <url>` also accepts `--api-key` or `--username`/`--password`, and
   the URL **must** include the scheme (`http://…`). On a local install edit the copied line to
   `http://localhost:4589`. Multiple servers coexist as saved environments; `leap auth select`
   switches between them (no need to re-login when moving between a local and a remote install).
   If the CLI reports x509/certificate-verify errors talking to a self-signed or private-CA
   server, set `LEAP_SKIP_SSL_VERIFY=true` in the CLI's own environment — there is no way to
   add a custom CA to the CLI's trust store otherwise.
6. Confirm the per-job memory budget actually deployed (silent-fail-open, no other
   visible symptom otherwise): grep the install log for
   `Memory budget deployed: total_memory_bytes=`; a warning that it's empty instead means
   job-memory admission is disabled — re-run with an explicit `--cluster-memory-gb <n>` or
   confirm Docker's memory is being read correctly.
7. `df -h` both disks again — confirm >30G free remains, or the first training job will
   trigger eviction.
8. **Prove it end to end** (the acceptance check the team actually uses): `git lfs install &&
   git lfs pull` in the example repo — **without git-lfs the model file is a 134-byte pointer
   and the push fails** — then `leap project push` the MNIST example, run validate + evaluate,
   and watch `nvidia-smi`/`nvtop` during evaluate to confirm the GPU is really in play.
9. Record the setup in `install-notes.md` — see
   [After every action](#after-every-action--record-the-state).

## Step 7 — When it fails

Open [install-troubleshooting.md](reference/install-troubleshooting.md) and match the error text — it catalogs every
failure mode observed in the field (installer telemetry and customer installation sessions),
ranked by frequency, with root cause and the fix the team actually used. Triage order when
the error is generic (`context deadline exceeded`, `connection refused`): **disk first
(both disks), memory second, then the specific error**. Grep the install log for the first
`Failed` line, not the last.

## Upgrading (`leap server upgrade`)

Moves an existing install to the latest version, **reusing all previous parameters**. It
cannot change ports, GPU selection, domain, TLS, data-dir, or dataset volumes — any of those
means [reinstall](#reinstalling-leap-server-reinstall) instead. A specific version is
`install --tag <v>`, not upgrade.

1. Run Step 0 — recall the recorded setup.
2. Re-export the machine's proxy env vars in this shell first if the notes say the machine
   needs them (they don't survive new terminals or sudo).
3. **Warn about all four side effects** and get confirmation — projects and data survive, but:
   running jobs are killed (long evaluates are better restarted clean); **existing insights can
   be deleted and regenerated** — say this out loud, it is user-visible data loss; a customer
   deliberately pinned to a **special tag** is silently moved off it (use `install --tag`
   instead); and on older installs the first push afterwards rebuilds the dependency image
   (12–20 min).
4. Run **on the server itself** (not from a client machine):
   `leap cli upgrade && leap server upgrade` — the team's rule is always both together.
5. Expect the "reinstall is required" prompt — it is the **norm, not the exception**. It fires
   on any of: a stuck/failed helm release, an app-version change, a k3s image change, any
   infra-value change (GPU selection, airgap sync registries), any cluster-param change (ports,
   TLS port, volumes, cpu-limit, image-caching), or an airgap↔online flip. Confirm with the
   user (jobs lost, data survives) and let it proceed.
   **Precondition to check:** if `<data-dir>/manifests/params.yaml` is missing (a previous
   `uninstall --clear-data` deletes it), upgrade cannot recover the old settings and silently
   falls back to defaults — port 4589/5699, domain localhost, **no TLS, no proxy** — and goes
   straight to a reinstall. Restore those values from `install-notes.md` and pass them to an
   `install` instead.
6. Verify: pods settle (give them a few minutes), hard-refresh the browser
   (Ctrl/Cmd+Shift+R), re-run `leap auth login` if the CLI complains. If it fails with
   `pre-upgrade hooks failed: timed out waiting for the condition` — the most common
   upgrade-phase failure — triage Mongo before retrying (catalog #18b).
7. **Confirm what's actually running** (users can't tell after a self-serve update):
   `leap server info`, and check the UI's version indicator.
8. Update `install-notes.md` (below).

`leap server upgrade` is also a legitimate **recovery** move — it has revived a cluster that
`stop`/`run` reported as down, and it regenerates a per-user kube context (without needing
sudo, unlike install). Airgap installs upgrade by installing with a **newer airgap tar** (and
matching CLI) — plain `upgrade` has nothing to download.

## Reinstalling (`leap server reinstall`)

Tears down the cluster and rebuilds it — **data survives, running jobs die, and it re-prompts
every install question**. Fast (~5 min) because images are cached. Use it to: change ports /
TLS / domain, add or change dataset volumes, change GPU selection, recover from broken cluster
state, or (with a purge first — catalog #39) switch online↔airgap. Moving the data dir is
`install -d <new>` instead — the installer offers a migration.

1. Run Step 0. The recorded state is what makes reinstall safe: answer the re-prompts from it
   so nothing silently changes (same volumes plus the new one, same GPU choice, same domain).
2. Confirm with the user: jobs will be killed; state/projects survive.
3. Export proxy env vars if the machine needs them; run as the regular (non-sudo) user.
4. After: verify pods, re-apply nothing (users/projects survive), re-run `leap auth login`,
   and update `install-notes.md` with what changed and why.

## Start / stop / reboot

- **After a host reboot or a Docker restart the cluster comes back on its own** (its
  containers carry `restart: unless-stopped`) — typically ready a couple of minutes after
  boot. No wake-up command is needed on current versions; don't tell users to re-install.
- After an explicit `leap server stop` it stays down until `leap server run`.
- Both commands print `Cluster 'tensorleap' not found` and exit 0 when there is no cluster —
  that is "nothing to do", not a failure.
- `leap server tools` wraps both `kubectl` and `k3d`, resolving its kubeconfig as
  `--kubeconfig` → `$KUBECONFIG` → `<data-dir>/manifests/kubeconfig.yaml` → `~/.kube/config`.
  A stale `~/.kube/config` from an older install is the classic "kubectl talks to nothing";
  prefer `leap server tools kubectl …` over a system kubectl.

## After every action — record the state

After every successful install / upgrade / reinstall (and after a failed attempt whose
diagnosis is worth remembering), write or update `<data-dir>/install-notes.md`. It sits at the
data-dir **root**, so it survives `uninstall` and even `--purge` (only a manual
`rm -rf <data-dir>` removes it). Keep the "Current setup" section current and **append** to
History — never rewrite it. Copy the template below **in full**, including the version,
data-dir and docker data-root lines: those are exactly what a later upgrade needs, and they
are the first thing people drop when abbreviating.

```markdown
# Tensorleap install notes — maintained by the tensorleap-install skill
Last updated: <YYYY-MM-DD> · Last action: install|upgrade|reinstall · Ran as: <os user>, sudo: <yes/no>

## Current setup
- Versions: CLI <leap --version>, chart/server <version>
- Command: <the exact leap server ... command used>
- Data dir: <path> · Docker data-root: <path> (relocated: yes/no)
- Platform: <Ubuntu 22.04 / macOS / WSL2 on Win11 / EC2 g6.xlarge / SageMaker / Azure VM>
- Domain/TLS: <localhost | domain + cert/key/chain paths> · Ports: <4589/5699[/443]>
- GPU: <devices selected, driver version, host kernel `uname -r` | CPU-only>
- Network: <open | proxy: HTTP_PROXY/NO_PROXY values + where persisted (.bashrc etc.) | airgap: pack version>
- Dataset volumes: <host:container, one per line>
- Pip mirror: <url | default> · Access: <ssh -L cmd | VPN ip:4589 | domain URL>
- Admin user: <name/email> (password held by customer) · License: <trial/extended until date>
- Machine quirks: <shared server users, IT contact, eviction-prone disk, slow HDD, ...>

## History
- <YYYY-MM-DD> install: <one line — what was done; issues hit → fixes (catalog entry #s)>
```

**Never store secrets**: no passwords, license tokens, API keys; strip `user:pass@` from proxy
URLs. The data dir is world-writable — this file is machine-local context, not a vault.

## Uninstall / cleanup reference

| Command | Effect |
|---|---|
| `leap server uninstall` | Deletes the cluster only — data survives, reinstall restores |
| `leap server uninstall --cleanup` | Also clears caches (helm-cache, containerd, registry) |
| `leap server uninstall --clear-data` | **Deletes user data** (storage + manifests) |
| `leap server uninstall --purge` | **Deletes everything** including the image-cache volume |
| `leap server uninstall --custom` | Interactive picker (ten targets incl. "All application data"); cannot be combined with the flags above; selecting nothing = cluster-only uninstall |

Confirm destructive flags explicitly, every time. Field caveats:

- `--purge` often **fails** with `unlinkat ... permission denied` on the root-owned
  containerd/helm-cache trees (catalog #55 — a retry trap).
- **Never run `uninstall` with an empty/deleted `~/.config/tensorleap` config.** Unlike the
  other commands, uninstall has no default-path fallback: with no recorded `data_dir` it
  resolves the data dir to the **current working directory** and `sudo rm -rf`s
  `./storage ./registry ./containerd ./manifests ./helm-cache`. (Clearing that config to
  "start fresh" is only safe *before an install*, never before an uninstall.) Two guards,
  every time — `cd /` first so no project directory is ever the cwd, and make the CLI resolve
  a real path before anything destructive is typed:

  ```bash
  cd /
  printf 'data_dir: <the real data dir>\n' > ~/.config/tensorleap/config.yaml   # if it was deleted
  leap server info      # must print that path — if it doesn't, STOP
  ```

  If the config was already deleted and uninstall was already run from a directory containing
  a `storage/` folder, **check that folder's contents before doing anything else** — it may
  already have been removed, and that outranks finishing the uninstall.
- `install-notes.md` survives both `uninstall` and `--purge`; only `rm -rf <data-dir>`
  destroys it.

