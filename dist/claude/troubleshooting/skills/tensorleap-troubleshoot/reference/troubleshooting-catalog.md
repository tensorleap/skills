# Tensorleap troubleshooting: incident catalog

Incidents that stopped users of installed (standalone) Tensorleap servers, compiled from
Tensorleap's support tickets, customer working sessions, the platform's own logs across
installed servers, and the source code. Each entry: what the user sees, how to confirm it from
the evidence (file names are the collector's; see the skill's Step 2), the cause, the fix, and
when it needs Tensorleap. Ids are stable: quote them to the user and in the support report.

Paths in the UI start at the gear icon, top right. `kubectl` below means
`leap server tools kubectl` (it finds the cluster's kubeconfig itself); run it on the server.
Every command that changes something runs only after the user's yes.

Failures of `leap server install`, `upgrade`, `reinstall` and `uninstall` themselves are not
here: they belong to the `tensorleap-install` skill and its failure catalog.

---

## A. Platform down or unreachable

### A1. The UI doesn't answer at all: connection refused, "site can't be reached"
**Confirm:** `docker.txt` shows `k3d-tensorleap-server-0` `Exited`, or no such container;
`leap server tools kubectl` answers `The connection to the server 127.0.0.1:<port> was refused`
or `: EOF`.
**Cause:** the cluster is stopped: `leap server stop`, Docker was restarted or quit, or the host
is still booting.
**Fix:** after a host reboot or a Docker restart the cluster comes back by itself (its server
node restarts unless stopped): give it 2 to 3 minutes. After an explicit `leap server stop` it
stays down until `leap server run` (about 30 seconds to all pods Running; `start` and `up` are
aliases). It exits again by itself → `docker logs --tail 100 k3d-tensorleap-server-0`: out of
disk → D1, out of memory → A5 or D2. It won't start at all after an upgrade and a reboot →
`leap server upgrade` has revived such clusters, as the `tensorleap-install` skill describes.
After any reboot: runs that were in flight died (J5), and a tunnel from a laptop must be reopened
(A6).

### A2. 503 Service Temporarily Unavailable, or a blank page, right after a start, reboot or upgrade
**Confirm:** pods in `cluster.txt` are young and some are not ready yet; Elasticsearch is the
slowest, and the UI waits for it.
**Cause:** not a failure: the platform is still starting.
**Fix:** wait 5 to 10 minutes, then hard-refresh the browser (Ctrl+Shift+R, Cmd+Shift+R on a
Mac). Still failing after 15 minutes → A3.

### A3. A platform pod is not Running or not ready
**Confirm:** `unhealthy-pods.txt`, then `pods/<pod>.txt`: the describe Events say why it can't
start; the previous container's log says why it died. `health.txt`'s direct probe names a module
`false` when Mongo, Elasticsearch or RabbitMQ is the one down.

| Pod | Usual cause | Fix |
|---|---|---|
| `tl-elasticsearch-es-master-0` Pending, `Insufficient memory` | Docker has under ~12 GB of memory: Elasticsearch alone requests 6 GB | Raise Docker's memory (macOS: Docker Desktop, Settings, Resources), then `leap server run`. Guidance: 32 GB of RAM or more |
| `tl-elasticsearch-es-master-0` restarting, logs mention a disk watermark or `flood stage` | the disk is nearly full | D1 |
| `ingress-nginx-controller-*` crash-looping after a reboot | A4 | A4 |
| `keycloak-0` not ready; the login page says "internal server error" | Keycloak takes over 2 minutes to start, and longer after a configuration change | wait 5 minutes. Still crash-looping: report it (Tensorleap's recovery for this resets Keycloak's own storage, so it is done with them, never alone) |
| `mongodb-*` restarting, or Pending | disk full (D1), or a root-owned `storage/mongodb` | fix the cause; node-server reconnects by itself |
| any pod, `permission denied` on its data folder | storage folders created by a sudo install are root-owned | `tensorleap-install` (its entry on root-owned folders) |
| `tensorleap-node-server-*` not ready | one of its dependencies is down: `health.txt` says which | fix that one first |
| a platform pod, last state `OOMKilled` (a run's own pods are F1, its Redis and streaming handlers F9, CoreDNS F5) | the machine is out of memory | D2; on a Mac also A5 |
| any pod, `ImagePullBackOff` | right after an upgrade: an image of that release is missing or unreachable; on an airgapped server: X1 | `tensorleap-install`, or X1 |

Once the cause is fixed, a pod that stays stuck in a crash-loop can be deleted; its controller
recreates it, and its data lives on its volume, not in the pod:
`kubectl -n tensorleap delete pod <pod>`.

### A4. After the host reboots, the UI stays unreachable: ingress-nginx crash-loops with `permission denied`
**Confirm:** `pods/ingress-nginx-controller-*.txt`:
`unexpected error storing fake SSL Cert: could not create PEM certificate file /etc/ingress-controller/ssl/default-fake-certificate.pem: open /etc/ingress-controller/ssl/default-fake-certificate.pem: permission denied`;
`health.txt`: the probe through the UI's port gets an empty reply (curl exit 52) while the
direct probe to node-server answers. Seen on cloud VMs (EC2) whose Tensorleap data sits on a
separately attached disk.
**Cause:** not established. Two explanations from the field, each with its check in `host.txt`:
- Boot order: Docker and the cluster started before the attached disk was mounted. Unlikely when
  Docker's own data-root is on that disk and its containers came back (`docker.txt` `root=`);
  otherwise compare Docker's `ActiveEnterTimestamp` with the disk's mount (`findmnt`, `who -b`).
- A different Linux user: the machine is used after the reboot by someone other than the user
  who installed Tensorleap. An SSM session starts as `ssm-user`; `install-notes.md` names the
  installing user, `host.txt` shows `id`, and the summary's data dir line says whether this
  user's leap config knows the install.
**Fix (with the user's yes):** with the disk mounted (`host.txt`), restart Docker once:
`sudo systemctl restart docker`. It restarts every container on the machine (`docker.txt` lists
them: name any that aren't Tensorleap's and ask again); the platform needs 5 to 10 minutes;
projects and data are untouched. Deleting the ingress pod alone doesn't help: it was already
recreated after the boot. Work as the installing user (`sudo su - <user>` in an SSM session).
To make Docker wait for the disk on every boot (persistent configuration; if the disk fails to
mount, Docker then stays down instead of starting without it), a systemd drop-in naming the
real mount point:
`sudo mkdir -p /etc/systemd/system/docker.service.d && printf '[Unit]\nRequiresMountsFor=/data1\n' | sudo tee /etc/systemd/system/docker.service.d/10-wait-for-data.conf && sudo systemctl daemon-reload`.
The disk's `/etc/fstab` line should use `UUID=` (NVMe device names can change after a stop and
start); `nofail` alone orders nothing.
After it's back: runs that were in flight at the reboot died (J5), and the tunnel from the
laptop must be reopened (A6). Because the cause isn't established, offer the user a report to
Tensorleap even when the restart works.

### A5. macOS: the whole server disappears under load
**Confirm:** `docker.txt` shows `k3d-tensorleap-server-0` freshly restarted; Docker Desktop
itself restarted; no pod shows `OOMKilled`. In `host.txt`, the Mac's memory (`hw.memsize`)
minus Docker's (`mem=` in `docker.txt`) is under 4 GB.
**Cause:** Docker Desktop's VM holds its whole memory allocation; with less than ~4 GB left for
macOS, the Mac pages the VM out and Docker Desktop restarts it.
**Fix:** Docker Desktop, Settings, Resources: memory at most the Mac's RAM minus 4 GB and at
least 12 GB, then `leap server run`. A Mac under 16 GB can't satisfy both.

### A6. The server is healthy, but the user's browser or CLI can't reach it
**Confirm:** `health.txt` on the server answers HTTP 200 with every module `true`, while the
user's machine gets a network error or a timeout.
**Cause:** the path between them: the SSH tunnel is down, the VPN isn't connected, the remote
machine is off, or the tunnel was opened to `127.0.0.1` instead of `localhost`.
**Fix:** `ssh <server> -L 4589:localhost:4589` and browse `http://localhost:4589` (use
`localhost`); connect the VPN first; on an SSM-only EC2 machine, an SSM port-forwarding session.
A Tensorleap install running on the laptop itself collides with the tunnel's port: stop one of
them. Remapping only the tunnel's local port breaks the login session; don't.

### A7. Windows (WSL2): runs starve for memory, or the PC's drive fills
On Windows the server runs inside WSL2 (`microsoft` in `versions.txt`'s `uname -a`): run the
collector in that WSL shell, not in PowerShell.
**Confirm:** `host.txt` shows far less memory than the PC has: WSL2 gives its virtual machine
half of Windows' memory by default. Runs queue (J1) or die of memory (F1) on a PC that should fit
them.
**Fix:** on Windows, set `memory=<n>GB` under `[wsl2]` in `%UserProfile%\.wslconfig`, then run
`wsl --shutdown` in PowerShell. That stops every WSL distribution, Tensorleap included: running
jobs die, projects survive. Open the WSL shell again and check that the UI comes back (A2). WSL's
disk lives on `C:` whatever the data directory says: `tensorleap-install` (its WSL2 entries).

---

## L. Login, users and license

### L1. A user forgot their password
**Fix:** the login page's **Forgot password?** link emails a 6-digit code through Tensorleap's
mail relay, which needs outbound HTTPS from the server. On an airgapped or proxied server it
answers `EMAIL_RESET_UNAVAILABLE` (503); then, on the server machine,
`leap server reset-password <email>` sets a temporary password and signs the user out
everywhere; they choose a new one at their next browser login. It needs leap CLI v0.0.163 or
newer on the server (`leap cli upgrade -s | bash`). Prevention: two admins; admins can reset
each other.

### L2. A new user can't get in, or waits for approval
**Cause:** the first registered user is the admin and approves every later sign-up.
**Fix:** an admin opens TEAM MANAGEMENT and approves the user (and sets their role).

### L3. Login fails with "HTTPS required"
**Cause:** the server is reached by a real domain over plain HTTP; Keycloak refuses that on any
host but localhost. **Fix:** serve it with TLS, or browse through `localhost`. Changing the
domain or TLS is a reinstall → `tensorleap-install`.

### L4. Signed out every few seconds, sent to a "conflict users" page, or a reload loop
**Cause:** the same user is signed in twice: two browsers, or a local install on port 4589 and
an SSH tunnel to the server on another port (HTTP 409).
**Fix:** sign out of the other session (or stop the local install); give each person their own
user.

### L5. "For your security, you have been signed out due to inactivity" while working; dashboard filters lost afterwards
**Cause:** seen behind VPNs and unstable networks on older servers; older servers also kept
dashboard state in the browser's local storage, which some browser policies clear.
**Fix:** upgrade the server (`tensorleap-install`). Report it with the network path (VPN, SSH
tunnel, SSM) if it continues on a current server.

### L6. "License expired. Please run "leap auth license" to continue."
**Confirm:** pushes and new runs fail with this message (HTTP 403); Population Exploration and
Fetch Similar still work on results already computed. `leap auth whoami` shows the license's
expiry (compare it with today) and the signed-in account.
**Cause:** the license period ended. It counts from the day the token was *issued*, not
applied, so a fresh airgapped install can show only a few days left.
**Fix:** ask Tensorleap for a new token and apply it: MANAGE LICENSE, or
`leap auth license -t <token>` (or `-f <file>`, the usual way to carry it to an airgapped
server). A token issued to an email domain applies only while signed in with an account at that
domain: check the account `whoami` shows first. Then resume what the expiry stopped:
**Continue Evaluate** in the version's menu (not across an upgrade: F11).
A new token applied but the days remaining don't change: a bug of servers before 1.6.100;
upgrade (`tensorleap-install`).

### L7. "You have yet to activate your Tensorleap license…", or an "Activate your license" screen
**Cause:** a Free Tier sign-up whose license isn't activated yet. **Fix:** press **Activate my
license** in the sign-up email; the server unlocks by itself. Only if that button fails, apply
the key from the same email (MANAGE LICENSE or `leap auth license -t <key>`).

### L8. A 403 with `LICENSE_LIMIT`, or a Free Tier "Contact us" dialog
**Cause:** a Free Tier limit: one project, Evaluate on at most 5,000 samples, or a push from a
second git repository into a project already linked to another. **Fix:** stay within the
limits (reuse the project; cap the evaluated samples), or contact Tensorleap for a license.

### L9. The CLI works, but its pushes and runs aren't on the server the user is looking at
**Confirm:** `versions.txt`: `leap auth whoami` shows an API Url other than the server in
trouble; `leap run list` shows only old runs.
**Cause:** the CLI is signed in to another environment (an old local install, another server),
or its token was generated for a different URL.
**Fix:** `leap auth select` to switch environments (they are saved side by side), or sign in to
the right URL. A CLI token copied from the UI targets the URL that was in the browser: on the
server itself, edit it to `http://localhost:4589`. On Windows, a CLI that signs in from
PowerShell but not from the VS Code terminal is a PATH or environment difference between them.

---

## P. Push fails

### P1. The push fails installing requirements from a private index or git repository
**Confirm:** the push's logs show pip failing on a `git+ssh://` or `git+https://` requirement or
an authentication error; or, with a Pip Index URL set (in GENERAL SETTINGS, or at install:
`pipindexurl` in `server-info.txt`),
`Failed to build image after 3 attempts. … NewConnectionError("HTTPConnection(host='<index host>', port=<port>): Failed to establish a new connection: [Errno 111] Connection refused")`
(or `Name or service not known`, a timeout, a 401 from the index).
**Cause:** the dependency build (pippin, in the logs) runs inside the platform: it has none of
the user's SSH keys or credentials, and `localhost` there is the build container, not the host;
on Docker Desktop (macOS, Windows) `172.17.0.1` isn't the host either. A `curl` that works on the
host proves nothing. "Connection refused" means nothing listens at that address for the build:
the index server isn't running (a server started with `&` dies with its terminal) or listens on
the host's loopback only. Read-only checks on the server: `ss -ltn` (Linux) or
`lsof -nP -iTCP -sTCP:LISTEN` (macOS) for the index's port (`127.0.0.1:<port>` is loopback
only, `*:<port>` or `0.0.0.0:<port>` all interfaces); then the build's own view,
`docker exec k3d-tensorleap-server-0 wget -q -T 5 -O /dev/null <index URL>`
(`can't connect to remote host` means the build can't reach it either).
**Fix:** serve the packages from an index the build can reach: the company's PyPI mirror, or a
local one on the server (`pypi-server` on the wheels' folder), listening on all interfaces and
kept running as a service, addressed by the host's real IP. Listening on all interfaces exposes
the packages to the network: the user's IT decides, and a firewall rule can narrow it. Set it as
the Pip Index URL in GENERAL SETTINGS, and replace the `git+…` lines in `requirements.txt` with
plain package names. If it can't be made reachable, clear the Pip Index URL and use public PyPI.

### P2. The dependency build (pippin) is very slow, seems stuck, or times out
**Cause:** the first push builds the requirements image (10 to 20 minutes is normal; later pushes
reuse it until the requirements change). A run that starts before that image exists (a
Visualize, for example) waits for the build too; older servers show it as Initializing all along.
Behind a proxy, downloads inside the build can crawl, or end in `Failed to build image after 3
attempts` with `ReadTimeoutError: HTTPSConnectionPool(host='files.pythonhosted.org', port=443):
Read timed out.`
**Fix:** wait once. After a timeout, push again; if it repeats, the build's way to the internet
is slow or blocked: point the Pip Index URL at the company's PyPI mirror, or check the proxy
(`tensorleap-install`, its proxy entries). The CLI giving up waiting is cosmetic: the build
continues on the server; follow it in the UI or with `leap run info <runId> -o text`.

### P3. "Timed out waiting for the push model import", or the model import takes very long
**Cause:** large models take long to import, H5 far longer than ONNX (a 16 GB H5 took over 25
minutes where a 12 GB ONNX took 1 to 2). **Fix:** follow the import in the UI or with
`leap run info`; don't push again while it runs. Push the ONNX export of the model when one
exists.

### P4. `python version <x> is not available`
**Cause:** the server offers a fixed set of Python versions, older servers fewer.
**Fix:** use one the server lists, or upgrade the server (`tensorleap-install`).

### P5. The code can't find the data on the server
**Confirm:** `code parsing failed`, `list index out of range`, an empty folder, a
`FileNotFoundError`, or the integration's own check printing that the path doesn't exist.
`server-info.txt` lists the dataset volumes (`datasetvolumes`) mounted into the platform.
**Cause:** the data is not under a mounted host path; a symlink inside the mount points outside
it; the path differs between the user's machine and the server; or the code resolves paths
relative to its working folder.
**Fix:** put the real files under a mounted path and reference them by that absolute path; a
split folder the code expects (validation, for example) must exist there too. Adding or changing
a mount is a reinstall (projects survive) → `tensorleap-install`.

### P6. The push waits on "Waiting for version to be ready" forever, with `ERR_DATA_TOO_LARGE`
`Your dataset or metadata is too large to process. Try reducing the size of your inputs or contact Tensorleap support.`
**Cause:** the inputs or metadata are too large for the server to process (seen with
2560 x 2560 inputs). **Fix:** smaller inputs (tile or downscale in the integration); otherwise
report it with the input shapes.

### P7. The push fails at once with an empty error report
`No errors or warnings were found.` followed by `Error: push failed see errors above`, while
`components/node-server.txt` shows `Marked jobs FAILED: absent from the engine active-jobs report`.
**Cause:** under load (other runs queued, a large model upload), the server gave up on the push
before it started. **Fix:** push again once nothing else is queued or starting. Pushes and
insights runs that wait for memory show PENDING, not QUEUED (J1).

### P8. Errors in the integration itself
`validation failed`, `check_dataset` failures, an exception raised in `leap_integration.py`, a
code-loader import error such as `cannot import name 'FoundationInput'` (the requirements pin an
older code-loader than the code needs): hand over to `tensorleap-integration-creation`.

### P9. A poetry project's push fails in the dependency build: `poetry install --no-root`, hash mismatch
**Cause:** on servers up to 1.6.100 the dependency build (pippin) can't install a poetry
project: the lock file stops matching once the build removes code-loader from it.
**Fix:** upgrade (`tensorleap-install`): later releases turn poetry.lock into a requirements list
and install that. On an older server, give the integration a `requirements.txt` instead.

---

## J. A run is stuck: queued, pending, initializing, "Build Model"

### J1. The run stays QUEUED (pushes and insights runs show PENDING)
**Confirm:** `components/engine-orchestrator.txt`:
`Admission | queued engine job (insufficient free memory)` with its queue position.
**Cause:** not a failure: the server starts runs only while the machine has memory for them; this
one waits for a running one to finish.
**Fix:** wait, or stop a run you don't need (`leap run stop <runId>`). A machine that is always
queueing is undersized for its load: D2.
An Evaluate that stays PENDING (not QUEUED) was admitted, but its pod never started: J2, J4 on a
GPU server, X1 when airgapped.
Servers up to 1.6.112 can also queue runs while memory is free: they counted idle warmup pods as
used (fixed after 1.6.112). Runs queueing while `host.txt` and `node.txt` show plenty free on
such a server: upgrade (`tensorleap-install`).

### J2. Stuck in initializing or "Build Model" for hours: a pod can't be scheduled
**Confirm:** `job-<id>/pods.txt` (or `pods/<pod>.txt`): a `generic-process-<id>` (or another of
the run's pods) Pending with `FailedScheduling … Insufficient memory` (or `cpu`), while the main
pod waits; or the main pod's log `Redis pod is Unschedulable -- waiting for cluster resources`.
`components/engine-orchestrator.txt` (and the summary) may show
`Admission | starting oversized job solo (best effort) … footprint_mi=<needed> free_mi=<free>`:
the run alone is bigger than the memory free for runs. The UI keeps showing the run as running:
it waits forever, there is no timeout.
**Cause:** the run's pods together ask for more than the machine has. Typically Auto-Settings
sized the worker from an earlier run's peak (seen: a 39 GB worker next to a 33 GB main pod on a
64 GB machine). Or other runs, pods left over from killed runs, or workloads outside Tensorleap
hold the memory (`host.txt` top processes, `docker.txt` containers): Kubernetes counts the whole
machine as available.
**Fix:**
1. Stop the run: `leap run stop <runId>`. A run that never got past Build Model loses nothing.
2. Pods left over from runs that already ended (any pod whose `jobId` label belongs to a run that
   is not running) still hold memory: delete them, per run:
   `kubectl -n tensorleap delete deployment,pod,service -l jobId=<oldRunId>`. Other workloads on
   the machine: ask the user whether they can pause them.
3. Size the run to fit. The memory free for runs is the orchestrator's `free_mi` (MiB), or the
   node's Allocatable minus the platform's own requests (`node.txt`); keep the main process plus
   the worker at least 5 GB under it (the run's Redis and streaming handler take that). In
   PROJECT SETTINGS, Main → Engine Memory (GB) and Workers → Worker Memory (GB) are each a
   request and a limit; switch that tab's Auto-Settings off (it stays off for the whole project).
   On a 64 GB machine, a main process with a 12 GB request and a 20 GB limit worked; the worker
   gets what is left. A lower Engine Memory also caps later Update Evaluate and visualization
   runs (F1). On a Mac, raise Docker's memory.
4. Start a new Evaluate (Continue Evaluate is for a run that processed samples), and check that
   its worker is Running within a few minutes: `kubectl -n tensorleap get pods -l jobId=<newRunId>`.
If the worker really needs what Auto-Settings gave it (an earlier run of this integration peaked
there), lowering it turns this into an out-of-memory failure (F1): free memory instead (other
workloads, a bigger machine), or make the integration load samples lazily →
`tensorleap-runtime-optimization`.
A reinstall only appeared to fix this in the field, until the same settings came back. The server
starting a run it can't fit is a known product gap (as of 1.6.114): offer the user a short report
to Tensorleap even when this fixes it.

### J3. Two runs on the same version
`An evaluate job is already running for this version. Please wait for it to finish or stop it
before trying again.` (or HTTP 429 `CONCURRENT_EVALUATE_LIMIT`). **Fix:** wait for it, or stop it
with `leap run stop <runId>` and start again.

### J4. GPU runs pending, or running on the CPU on a GPU machine
**Confirm:** Pending with `Insufficient nvidia.com/gpu`; or, on a GPU install, run logs with
`failed call to cuInit` or TensorFlow's `list physical gpus: []` (it silently falls back to the
CPU, many times slower). `host.txt` has `nvidia-smi`; `node.txt` lists `nvidia.com/gpu` under
Allocatable.
**Cause:** Kubernetes gives each run whole GPUs; another run or process holds them, the GPU
setting asks for more than exist, or the GPU isn't visible inside the cluster (driver, host
kernel update).
**Fix:** free the GPU (`nvidia-smi` shows who holds it); PROJECT SETTINGS → Main → GPUs per run
at a whole number the machine has (fractions such as 0.5 fail the run). GPU not visible inside the cluster: check it from a
running run's main pod (`kubectl -n tensorleap exec <pod> -- nvidia-smi`), then
`tensorleap-install` (its GPU entries).

### J5. The UI shows a run as running, but nothing runs
**Confirm:** no pods with the run's `jobId` in `job-<id>/pods.txt`; RabbitMQ or the
orchestrator restarted around the run's start (`cluster.txt` restarts). Or its pods exist, none
of them Pending (that is J2), but their logs stopped long ago.
**Cause:** the run's status update was lost (its pods died before reporting, or RabbitMQ was
restarting), or a step inside it deadlocked.
**Fix:** `leap run stop <runId>`, delete its leftover pods (J2 step 2), then start it again. Hours
without new log lines are not proof of a hang: some steps log nothing for long stretches, then
fail with an out-of-memory error (F1). If runs keep losing status, check `rabbitmq-0` (A3).
**Hang or slow step?** Check twice, a few minutes apart, read-only:
`kubectl -n tensorleap top pods -l jobId=<runId>` (a hung run's pods sit near zero CPU; a slow
step keeps a core busy), and the main pod's latest lines (`job-<id>/pods.txt` names it;
`kubectl -n tensorleap logs <main pod> --since=10m`; in the field, a hung run's
`StreamingHandlerScaler tick` lines showed no documents moving, for good). A hang is a Tensorleap
bug, and the same run usually hangs again: stop it, delete its leftover pods (deleting the main
pod alone leaves its workers, Redis and streaming handlers running), and report it with those
lines and the step it hung in. With the user's yes, on a server with internet access,
`kubectl -n tensorleap exec <main pod> -- sh -c 'pip install -q py-spy && py-spy dump --pid 1'`
shows where it waits: add it to the report.

---

## F. A run failed

### F1. Out of memory
`OOMKilled: Main engine process ran out of memory. Consider enabling auto-settings in the Engine Settings Dialog.`
or `Main engine process ran out of memory. Minimize model/data memory usage or contact Tensorleap for support.`
(the model and the main loop), and the worker variants
`OOMKilled: Engine worker ran out of memory. …` / `Engine worker ran out of memory. Minimize data loader memory usage …`
(the user's integration code: data loading, metrics, visualizers). The run's error report
(`job-<id>/info.txt`) and its details in the UI both show this text.
**Confirm:** the pod's describe output (the summary prints it): its last state `OOMKilled`,
exit code 137, and its memory limit. It is in `job-<id>/pods.txt` while the pod exists, and at
the end of `job-<id>/logs.txt` once the run has ended. A `generic-process-…` pod is a worker,
the run's Redis or a `streaming-handler-…` pod is F9, the run's remaining pod is the main
process.
**Fix:** PROJECT SETTINGS: Main → Engine Memory (GB) for the main process, Workers → Worker
Memory (GB) for workers, or that tab's Auto-Settings on. Population Exploration and other light
runs use Pop. Exp. Settings. Update Evaluate and visualization runs use Main's Engine Memory (GB)
even with Auto-Settings on: raise it by hand when they run out. Size the values to what the
machine has free (J2, step 3), then resume with Continue Evaluate. Large inputs times the batch
size (32 images of 3 × 1024 × 1024, for example) are the usual cause: a smaller batch size lowers
the main process's memory, but needs a fresh Evaluate. A worker that
needs more per sample than expected is the integration's own footprint →
`tensorleap-runtime-optimization`. Label Next and Domain Gap ran out of memory on millions of
samples on older servers: upgrade first.

### F2. "Evaluation failed due to too many discarded samples"
**Cause:** more than 20 % of the samples failed in the user's code; each failure is logged as
`Sample generator failed`. **Fix:** the run's logs hold the exception for the failing samples →
`tensorleap-integration-creation`.

### F3. "Dataset code had crashed"
**Cause:** the user's integration code raised. **Fix:** the traceback in the run's logs →
`tensorleap-integration-creation`.

### F4. "API error (503): This may indicate low available disk space. If you have less than 150 GB of free storage, please consider freeing up disk space and try again."
**Cause:** Elasticsearch stopped accepting writes because its disk crossed the watermark. → D1.

### F5. A long run dies with `socket.gaierror` / `Temporary failure in name resolution` / `MaxRetryError`, several pods failing at once
**Confirm:** the `coredns-*` pod in `kube-system` restarted (`cluster.txt`), last state
`OOMKilled`; `cluster.txt` prints CoreDNS's resources: `{"limits":{"memory":"170Mi"},…}` is the
small default, `512Mi` on both requests and limits is the fixed one.
**Cause:** the cluster's DNS ran out of memory under heavy load, so every service lookup failed
for minutes.
**Fix:** installs and upgrades run with leap CLI v0.0.162 or newer fix it themselves. Older:
upgrade the CLI and the server (`tensorleap-install`), or patch it now (DNS restarts for a few
seconds):
`kubectl -n kube-system patch deployment coredns -p '{"spec":{"template":{"spec":{"containers":[{"name":"coredns","resources":{"requests":{"cpu":"250m","memory":"512Mi"},"limits":{"cpu":"250m","memory":"512Mi"}}}]}}}}'`
(a reinstall with an older CLI resets it). Then start the run fresh (F11).

### F6. Elasticsearch timeouts during a run: `Read timed out` to `tl-elasticsearch-es-master`, or 503 retries
**Cause:** Elasticsearch is overloaded: short of memory, a disk near full, or too many runs
writing at once. **Fix:** D1 for disk; otherwise fewer concurrent runs and more Docker memory
(D2), then resume the run.

### F7. Mongo connection lost during a run (`MongoNetworkError`, `ECONNREFUSED …:27017`)
**Confirm:** `cluster.txt`: the `mongodb-*` pod restarted around the failure; its previous log
says why. **Fix:** that restart's cause, usually disk (D1) or memory (D2). After an upgrade or a
reboot Mongo comes up last; a step that ran before it was ready recovers on the next run.

### F8. The run failed the moment it started, with empty logs and a Kubernetes error in its report
**Confirm:** `job-<id>/info.txt`: `HTTP-Code: 404` (or 403) and a body naming a Kubernetes
object, for example `configmaps "engine-job-config" not found`; `job-<id>/logs.txt` is empty
because no pod ever started.
**Cause:** the server's components were out of step when the run was created: an upgrade still
applying, interrupted, or mixed builds.
**Tell the causes apart, then fix:**
1. `runs.txt` shows a later run of the same kind on the same version FINISHED: it has passed (an
   upgrade or a redeploy was underway). Nothing to fix; start the run again.
2. The object is really missing (`kubectl get configmaps -A --field-selector
   metadata.name=engine-job-config` finds nothing in `tensorleap`): the install is incomplete →
   `leap server upgrade` (or a reinstall) to bring every component to one release →
   `tensorleap-install`.
3. `cluster.txt` shows components on images other than the server's release (a hotfix build
   Tensorleap gave, for example): ask the user before any upgrade, which replaces them.
An upgrade still running: let it finish, then start the run again. (An `AccessDenied` from the
storage, not from Kubernetes, is U3.)

### F9. A run's Redis ran out of memory: "This job was stopped because the Redis service restarted while it was running. Please retry the job."
Often with `Error 111 connecting to redis…:6379. Connection refused.` Servers with one Redis per
run (from mid-2026) say instead `OOMKilled: Process ran out of memory. Try increasing the process
Memory limit by 25% and re-running the job.`, which they also give for a streaming handler (its
fix: Streaming Handler → Streaming Handler Memory (GB)). Newer servers name the pod:
`OOMKilled: The run's Redis ran out of memory. …` or `OOMKilled: The run's streaming handler ran
out of memory. …`.
**Confirm:** `job-<id>/pods.txt`, or the end of `job-<id>/logs.txt` once the run has ended (the
summary prints it): the run's Redis pod last state `OOMKilled` at its memory limit, while the
machine has room (not D2).
**Cause:** the run's own Redis ran out of memory; a retry unchanged fails the same way.
**Fix:** either more memory in PROJECT SETTINGS → Redis → Redis Memory (GB) (default 1 GB:
double what the pod's limit shows), then **Continue Evaluate**; or a smaller batch size (in the
field, 256 failed, 128 failed, 64 worked), which needs a fresh Evaluate, since Continue Evaluate
keeps the batch size.

### F10. "Channel is closed." (with `StreamLostError` / `AMQPConnectionError` in the run's logs)
**Cause:** RabbitMQ restarted under a heavy run; `rabbitmq-0` shows restarts in `cluster.txt`.
**Fix:** read its previous log (`pods/rabbitmq-0.txt`) for the reason (memory, disk); run fewer
things at once; heavy custom metrics that saturate the CPU make it worse
(`tensorleap-runtime-optimization`). Recurring on a machine with room to spare → report it with
RabbitMQ's logs.

### F11. Continue Evaluate after an upgrade: the latent space is missing or wrong, or the run fails at the end
**Confirm:** the version's first Evaluate ran before the server's last upgrade: `runs.txt`
against the upgrade's date in `install-notes.md` History or `installer-last-log.txt`.
**Cause:** an evaluation started on an older server and continued on a newer one can lose its
latent space, or rebuild it inconsistently, when the run's format changed between the releases.
**Fix:** push the version again and run a fresh Evaluate. Continue Evaluate is for resuming on
the same server version; it also keeps the original batch size.

### F12. Evaluation became many times slower after an upgrade, with the GPU mostly idle
**Confirm:** run logs show `calculated max_samples_per_batch: 1`; samples are large (over about
10 MB each); GPU utilization is low.
**Cause:** on some releases the workers fill each inference step with a fixed memory budget, so
large samples run one per step.
**Fix:** upgrade (`tensorleap-install`). On a server that can't be upgraded yet, the workaround
used in the field, repeated after each Evaluate starts:
`kubectl -n tensorleap set env deployment/generic-process-<runId> EVAL_MAX_BATCH_MEMORY_MB=1024`
(it restarts the workers once).

---

## U. Results missing or wrong

### U1. A dashboard panel is empty: which empty?
| The panel says | Meaning | Next |
|---|---|---|
| `Loading...` | still loading | wait |
| `Sorry, there was an error...` | the data store didn't answer | A3 (Elasticsearch) |
| `Training/Evaluation process is required to visualize data` | no metrics were written | did the Evaluate finish? F |
| `Select a version to see data.` | nothing selected | select a version |
| `No results found` | the query returned nothing | the Evaluate failed or isn't finished, or the filters exclude everything: clear them |
| `No data` in one cell | that bucket is empty | expected |

### U2. Dashboards miss samples (a whole split, or some) that Population Exploration shows
**Confirm:** the run's logs (streaming handler) show `failed to index docs to elasticsearch`.
**Cause:** Elasticsearch rejected those samples' records because a metadata value didn't fit its
field: a date field that also receives other strings (missing timestamps written as `'NaT'`, two
date formats), or a string over 32 KB. The run itself continues, so nothing fails visibly.
**Fix:** in the integration, write missing values as `None` and keep one date format; then
evaluate again → `tensorleap-integration-creation`.

### U3. Population Exploration, Evaluate or the code integration panel fail: `AccessDenied` on the `session` bucket
**Confirm:** a run's logs show
`S3 operation failed; code: AccessDenied, message: Access denied, resource: /session/… bucket_name: session`;
or the code integration panel stays empty and the browser's developer tools (Network tab) show
the UI's `/session/…` requests answered 403 `AccessDenied`. Look-alikes: `InvalidAccessKeyId` or
`SignatureDoesNotMatch` mean the storage's credentials were changed; a connection error with no
request id is a network block.
**Cause:** on a standalone install the UI, and on servers up to 1.6.114 the runs' pods too, read
the storage bucket `session` without credentials, relying on the bucket's public policy
(anonymous read, write and delete). Someone removed that policy; in the field, IT hardening the
storage.
**Fix:** every way back restores that public policy, so it is the user's IT's decision first:
with it, anyone who reaches the server's port can read, change and delete project files. If IT
accepts it, limiting who can reach the port (SSH tunnels only, a firewall rule) is the usual
mitigation.
- Releases after 1.6.114 read the bucket with credentials and re-apply the policy at every
  startup (the UI still needs it). Ask Tensorleap which release carries this for the server,
  then upgrade (`tensorleap-install`).
- Before that, report it to Tensorleap with the error line; they give the exact steps to restore
  the policy on the server's version. Don't improvise them (no `mc` commands inside the storage
  pod): they need the storage's admin credentials, which stay out of this conversation.

### U4. The latent-space scatter shows only about 2,000 points
Not missing data: the scatter displays a sample of the population. The dataset's real size is in
the version's details; filters and insights use all samples.

### U5. Insights or Population Exploration missing for a version
**Cause:** insights are computed after the Evaluate, in their own run: check that run's status
(`leap run list`). An Evaluate log that says the `insights`, `subpopulations` and
`prepare display` steps were skipped (too few outliers: a small dataset, or a metric whose
direction is set wrong) leaves no Population Exploration on older servers. An upgrade can delete
and regenerate existing insights: that is expected.
**Fix:** a failed insights run → F (usually memory, F1); skipped steps → evaluate the full
dataset and check each metric's direction (`tensorleap-integration-creation`), or upgrade.

### U6. The screen turns black on a dashboard
Seen on servers before 1.6.66 with duplicated dashboard panels. **Fix:** upgrade
(`tensorleap-install`); on a current server, report it with the browser console's errors.

### U7. Live updates stop, or the UI looks old after an upgrade
Hard-refresh the browser (Ctrl+Shift+R, Cmd+Shift+R on a Mac). Browser-console
`socket io connect_error` lines are the connection dropping and reconnecting.

---

## D. Disk and memory

### D1. The disk is full: pods Evicted, `DiskPressure`, `no space left on device`, or a "Low disk space" warning
`Low disk space on the installation volume: <n> GiB free. The job will run, but …` is the
server's early warning when a run starts.
**Confirm:** `host.txt` (`df -h` of the data directory, `/` and `/tmp`, Docker's disk), the
summary's DISK lines, `node.txt` (`DiskPressure True`), Evicted pods in `cluster.txt`. On Docker
Desktop (macOS, Windows) these are two disks: the host's, which holds the data directory, and
Docker's virtual disk (images and containers, its size set in Docker Desktop), which the node's
`DiskPressure` and evictions measure. `du -sh <data-dir>` (read-only; minutes on a large
install) shows how much of the disk is Tensorleap's; the rest is the user's to clean.
**Cause:** the kubelet evicts pods when free space drops below the larger of 30 GB and roughly
15 to 20 % of the disk; Elasticsearch stops writing earlier still (F4), and dashboards turn slow
or fail. The data directory grows with every evaluated version (metrics, latent spaces,
visualizations); Docker's disk grows with images.
**Fix, safest first, each with the user's yes,** until well over 150 GB is free (the number F4
names; runs start with a warning under 100 GB):
1. Delete versions and projects that are no longer needed, in the UI.
2. Docker's disk (on Docker Desktop this frees Docker's disk, not the host's):
   `docker image prune -a -f` while no install or upgrade is running (the platform keeps its own
   copy of its images).
3. Old installer caches: `tensorleap-install` (its entry on reclaiming disk on a running
   install).
Never delete folders under `<data-dir>/storage` by hand. Once there is room, Kubernetes clears
`DiskPressure` and recreates evicted pods by itself; then re-run what failed.

### D2. The machine is too small for its load
Dashboards crawl, visualizations time out, insights stall, runs queue (J1) or die of memory (F1)
when several run at once. Kubernetes counts the whole machine as available, so a notebook,
training job or other containers on the same server take memory the platform thinks it has
(`host.txt` top processes, `docker.txt` containers). **Fix:** fewer concurrent runs; other
workloads moved or paused; Auto-Settings on in PROJECT SETTINGS; on a Mac, Docker's memory (A5). Guidance for real projects: 32 GB of RAM or more (64 GB
recommended), 16+ CPUs, 150 to 300 GB for the data directory.

---

## X. Airgapped servers

### X1. A run's pod can't pull its image (`ImagePullBackOff`, `ErrImagePull`)
The run's error report says `Failed to pull the required Docker image. Please contact support if
this persists.`
**Cause:** the airgap pack lacks an image the server creates while running (in the field: the
per-run Redis), so the first run that needs it fails. **Fix:** report it with the image name
from the pod's describe and the pack's file name; Tensorleap re-packs it. Upgrading an airgapped
server means installing the newer pack (with its matching CLI) over the existing data, no purge
needed (`tensorleap-install`).

---

## C. The assistant's Tensorleap tools

### C1. The Tensorleap MCP tools are missing in the assistant
**Fix:** `leap mcp` in a terminal shows whether the CLI is signed in, whether the MCP server
answers, and what AI ACCESS withholds. `leap mcp config` sets the assistant up;
`unknown command "mcp"` means the CLI is too old (`leap cli upgrade -s | bash`). VS Code with
Copilot Business or Enterprise: an organization admin must allow MCP servers in Copilot. VS Code
in a WSL window, where `leap mcp config` reports success but no `tl_` tools appear: add the
server to the project's `.vscode/mcp.json`
(`{"servers":{"tensorleap":{"type":"stdio","command":"leap","args":["mcp"]}}}`), then quit VS
Code completely and reopen it.

---

## Not a failure

Healthy servers log these too; don't diagnose from them.

| Line | Where | Why it's harmless |
|---|---|---|
| `Executor start aborting (this does not indicate an error and you can ignore this message)` | run logs | TensorFlow notice |
| `Could not find cuda drivers on your machine, GPU will not be used.`, `Could not find TensorRT`, `This TensorFlow binary is optimized…` | run logs, CPU-only install | no GPU here, as intended (on a GPU install, see J4) |
| `Link requires a different Python`, pip `Downloading …` lines marked as errors | push build logs | pip's resolver chatter |
| `WARNING clustering … please provide at least … training points` | run logs | small-cluster notice |
| `Failed to publish to RabbitMQ, retrying in 5 seconds.` once or twice | run, orchestrator logs | a reconnect around a start; a problem only if it repeats for minutes (F10) |
| `Failed to fetch pod metrics … Is metrics-server installed?` | orchestrator log | optional metrics source |
| `Manual job failed, collecting logs` | node-server log | the log-archiving step after a failure, not a new failure |
| `Failed to create overlay heatmap … showing visualizer without the heatmap` | run logs | a visualizer heatmap whose shape doesn't match its input: cosmetic; the fix is in the integration |
| `socket io connect_error xhr poll error`, `No token provided` | browser console | the connection blinked, or a session expired (sign in again) |
| `No token provided`, `Invalid token` (HTTP 401) | node-server log | a request from a signed-out browser tab or an expired session; a problem only if the user can't sign in (L) |
| Warning `Validation` on `elasticsearch/tl-elasticsearch`: `xpack.security.enabled: Forbidden: Configuration setting is reserved…` | `events.txt` | the platform sets it on purpose; Elasticsearch runs normally with it |
| Elasticsearch health `yellow` | `cluster.txt` | a single node can't place replica copies; `red` is the problem |
| red `POST request failed with status code: 500` after `Successfully completed` | installer log | telemetry after a successful run |
| `no kubernetes cluster available` | `leap server tools` during an install | the cluster doesn't exist yet |
| one old restart of `elastic-operator-0` (or another platform pod) dating from an upgrade or reboot | `cluster.txt` | a dependency wasn't ready yet during the restart; only restarts within the last day, or repeating ones, matter |
