<!-- BEGIN TENSORLEAP SKILL: tensorleap-integration-creation -->
<!-- Tensorleap skill 'tensorleap-integration-creation' v0.2.0 — generated from skills/tensorleap-integration-creation/skill.md; do not edit here. -->
# Writing a Tensorleap integration

You are authoring `leap_integration.py` (the decorator-based style), not the
legacy `leap_binder.py` registration script. **Put every file you create under a
`tensorleap/` directory at the integration repo root** — never scattered in the
repo root — so the integration is self-contained and doesn't clutter the
customer's project. So the layout is `tensorleap/leap_integration.py` +
`tensorleap/leap.yaml` with `entryFile: leap_integration.py` (paths in `leap.yaml`
are relative to `leap.yaml`, i.e. to `tensorleap/`), run through the project's
Python environment (agreed in Step 0 — pyenv + poetry by default), with a locally
materialized `.onnx` or `.h5` model. Run the run-loop and `leap push` **from
inside `tensorleap/`**. If the integration imports the customer's repo modules,
add the repo root to `sys.path` in the entry file (e.g.
`sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`) so those imports
resolve for local runs.

## What Tensorleap is (and what that implies)

Tensorleap is an **inference-only** analysis platform: it loads a *pre-trained*
model and runs it over your data to visualize predictions and compute metrics.
No training happens here. That shapes the whole integration:

- `@tensorleap_load_model` is **mandatory** in every integration.
- The model returns raw predictions (e.g. logits). Softmax, decoding,
  thresholding, and other postprocessing belong in visualizers/metrics — never
  baked into the model or the integration test.
- **How files reach the platform — anything else is invisible at runtime.** A
  file the integration reads at runtime is available only if it is (a) listed in
  `leap.yaml`'s `include` (bundled with the code), or (b) placed in the Tensorleap
  **data volume**; a path that is neither is not accessible, even though it exists
  locally. The **model is the exception**: it is uploaded to the platform
  *separately* (its own upload), so it goes in **neither** — do **not** bundle it
  in `leap.yaml`. `@tensorleap_load_model` loads the model only for local runs /
  the integration test; on the platform the model comes from the separate upload
  and your loader body is not run (its declared `prediction_types` still are).
  Convention: the **dataset** → data volume (read from a *config-driven* path,
  never hardcoded); the **model** → separate upload; every **other** asset the
  integration reads from disk → `leap.yaml`.
- **Remote data (fetched, not bundled).** If the dataset lives in a store the
  *server* can reach (e.g. S3) and fetching beats bundling it, read it directly
  from `preprocess`/encoders — but **cache each fetched file inside the data
  volume** (config-driven path) and reuse the cached copy when present, so repeated
  runs (and code_loader's in-run re-invocations) don't re-download. If access needs
  a credential, read it from the **`AUTH_SECRET`** env var — register it with `leap
  secrets create` and attach it with `leap secrets set` (writes `secretId` into
  `leap.yaml`); it is auto-injected into platform jobs, so **export it yourself for
  local runs**. Prefer sourcing the value from a **local credentials file** the
  user points you at rather than pasting it into the session (see **Credentials**
  under Data delivery). Gate only the download, not cached reads. Full row-by-row
  detail (local vs remote server, copy vs lazy-cache) is in **Data delivery** below.

## Operating principle: run autonomously; ask only when blocked

Drive the work forward on your own as far as you can. **Infer configuration from
the repo and the environment before asking** (the data store and load code,
dependencies, label semantics, a *local* data volume via `leap server info`,
etc.), and **use anything the user already supplied proactively** — in the
prompt, a config file, or the repo — without re-asking for it. Some settings
genuinely **cannot** be obtained autonomously — most notably the **remote data
volume when running against a remote server** (a local `leap server info` can't
reach it) — and must come from the user.

**Ask the user for an important config setting (remote data volume, credentials,
bucket URI, topology, …) only when you are actually *blocked* on it and could not
obtain it yourself and it wasn't provided.** Don't front-load a questionnaire at
the start: defer each ask to the exact step that needs the value (e.g. the remote
volume at the data-root switch before push; store credentials when a fetch first
needs them). When you must ask, ask for the specific missing piece, then continue
autonomously. This keeps the run hands-off unless a genuine blocker requires the
user.

## Starting point: usually an existing repo

The classic activation is **inside the customer's existing repo** (or one the user
points you to) whose task is to *become* a Tensorleap integration. **Infer from
that repo before asking** — it typically already contains what the integration
needs:

- the **data store** (local paths vs a remote bucket/index) and the code that
  lists/loads data → informs the Data delivery row;
- the **preprocessing / transforms**, **label handling**, and any existing
  **metrics / metadata / loss** logic → reuse these in the decorated components
  rather than reinventing them;
- the **dependencies** (`requirements.txt` / `pyproject.toml` / imports) → the
  starting point for the integration's environment.

Only when the skill is activated **without** such a repo, or a specific detail
genuinely can't be determined from it, **ask the user** for that piece — data
location and credentials, the model file, preprocessing/label semantics, etc. Do
not silently guess a detail the repo doesn't establish.

## Project layout

Split the integration into these files, **all under the `tensorleap/` directory**
(the Python components are all imported into `leap_integration.py`, the entry
file) rather than one monolith — it keeps each component reviewable, the entry
file thin, and the whole integration confined to `tensorleap/` instead of the
customer's repo root:

- **`leap_integration.py`** — entry file; imports the component modules below and
  holds `@tensorleap_load_model` + `@tensorleap_integration_test` and the
  `__main__` run harness.
- **`leap.yaml`** — the **platform manifest** (a *different* file from
  `project_config.yaml`): `entryFile: leap_integration.py`, `pythonVersion`, the
  `include` list, `projectId`/`secretId`/`branch`. Required to push the
  integration to the platform; it is config *for the platform*, not read by your
  Python code.
- **`project_config.yaml`** — every constant/config **your integration code**
  reads: data root, directory names and other paths, the **sample limit**, and any
  flags or constants used by project logic/computations. **No secrets** — those
  live in the `leap secrets` / `AUTH_SECRET` flow (see "Remote data" above). Loaded
  once and shared by the component modules.
- **`preprocess.py`** — `@tensorleap_preprocess`.
- **`encoders.py`** — all input and GT encoders.
- **`metrics.py`**, **`metadata.py`**, **`visualizers.py`** — the matching
  optional components, one concern per file. **`metrics.py` holds the custom
  loss** (`@tensorleap_custom_loss`) as well as custom metrics — loss and metrics
  are the same shape of per-sample function, so they live together.

All component files must be listed in `leap.yaml`'s `include` (along with
`project_config.yaml`). Validation still fires from running the entry file, so the
run loop below is unchanged — it exercises the imported decorators.

## The one rule that drives everything

**Validation fires when a decorated function is *called*, not when it is
defined.** A decorator can exist in the file and still report as "missing"
because nothing exercised it. Therefore the integration must be **run
constantly**, and `leap_integration.py` is not just a file name — it is a
*progressive validator* for your own work.

This makes the run loop below non-optional. Do not batch up edits and run once at
the end; you lose the ability to tell which change caused which failure.

## The run loop (do this after every meaningful edit)

```
1. RUN     .tensorleap/scripts/run_integration.sh tensorleap
           (runs `tensorleap/leap_integration.py` through the project env —
            `poetry run` by default, or set `TL_PY` — from the `tensorleap/` dir;
            the exit-status table only prints when the entry file is named
            exactly leap_integration.py)

2. READ    the output in this priority order; STOP at the first problem:
             a. uncaught exception / "Script crashed ... crashed at function '…'"
                -> fix THAT function before trusting any later row
             b. "Warnings (Default use. It is recommended to set values explicitly)"
                -> make the warned value explicit (state=, channel_dim=,
                   prediction_types=, direction=)
             c. exit status table
                -> find the EARLIEST unexercised mandatory row
             d. "Successful!"
                -> the current stage's real run + mapping rerun + first-sample
                   binder check all passed

3. FIX     address only the earliest failure. Re-running surfaces the next one.

4. REPEAT  run again before doing anything else.

GATE       Do NOT author the next interface until the current stage's row is
           exercised and no mandatory errors remain. One interface at a time.
```

> The bundled scripts (`.tensorleap/scripts/run_integration.sh`, `.tensorleap/scripts/tl_check.py`) live
> in this skill's own directory and take the path to run in as their first
> argument (default: current directory). Point them at the **`tensorleap/`
> directory** (e.g. `.tensorleap/scripts/run_integration.sh tensorleap`), or run them
> from inside it.

**Keep an `integration-report.md` (in `tensorleap/`).** As you author,
**log every issue you hit** — the failing signal, what caused it, and how you
resolved it (or that it's still open, e.g. data that can't be verified on a remote
volume, a missing dependency, an ambiguous model contract). Append as you go
rather than reconstructing at the end. This is the artifact the user can hand to
the Tensorleap team when something needs their help, so make each entry
self-contained: the exact error text and the file/interface it came from.

For "is it actually done?" decisions, use **both** checks — they cover different
scopes:

- **`check_dataset()` (structured) — dataset side only:** preprocess, input/GT
  encoders, metadata. Trust it over the exit table *for these* — the table can show
  stale crosses when a later dataset interface is what's actually broken.
- **Exit table (from `integration_test`) — the dataset<->model connections:**
  `load_model`, `custom_loss`, and any metric/visualizer that consumes model
  predictions. `check_dataset()` never loads the model, so it is **silent** on
  these — a `❌` here is real even when `check_dataset()` reports `isValid: True`.

## Preflight gate (run before authoring)

Before writing anything, run the bundled check-only gate from the integration
repo root:

```
.tensorleap/scripts/preflight.sh        # CLI defaults to `leap`; set TL_CLI=leapdev to override
```

It verifies the platform prerequisites that need **no Python environment** (so it
runs before the project env exists): the CLI is on PATH, which **server topology**
the CLI points at (local vs remote — see the decision below), and — for a
**local** server — that it is running, a data volume is configured, and you are
authenticated. It **only checks** — it never installs, authenticates, starts a
server, or configures anything. React to its exit status:

**Deciding server topology.** Resolve local vs remote in this order:
1. **The user explicitly stated their topology in the prompt** → that wins in
   **every** case, regardless of the URL. Said **remote** → run the gate with
   `TL_TOPOLOGY=remote` (they may reach it through a port-forward that looks
   local) and follow the remote flow. Said **local** → run with
   `TL_TOPOLOGY=local`; the URL-based guesses (rules 2–3) and the remote question
   are skipped, and a missing local server is a plain blocker (install/start it).
2. **The `API Url` host in `leap auth whoami` is anything other than `localhost`**
   (`127.0.0.1` / `::1` / `0.0.0.0` count as local) → **remote**.
3. **The host is `localhost` (any port)** → the gate probes `leap server info`,
   because a localhost URL alone can't prove there's a *local* server (a
   port-forward to a remote one looks identical):
   - **No server answers** (any port) → **ambiguous (exit 6)**: could be a remote
     server reached via a port-forward — which can bind **any** local port — or no
     server installed here. Ask whether it's installed remotely; route to remote
     or tell the user to install it.
   - **A server answers on a non-`4589` port** → **ambiguous (exit 5)**: local
     server on a custom port, or a live remote port-forward. Ask, then re-run with
     `TL_TOPOLOGY=local`/`remote`.
   - **A server answers on `4589`** (or unspecified) → **local**.

- **Blocker (exit 2)** — either the CLI is missing (checked before topology is
  known), or — **only when `whoami` points at a local server** — that local server
  responds but **has no data volume** configured. (A local server that does *not*
  respond is treated as ambiguous — exit 6 below — not a hard blocker.) These
  checks never fire for a remote server (that path exits 4 first). **Relay the
  printed guidance to the user and STOP.** Do **not** install the CLI, start the
  server, or configure the volume yourself.
- **Setup: not authenticated (exit 3)** — guide the user through
  `leap auth login`, then re-run preflight.
- **Remote server (exit 4)** — *not* an error. The CLI is pointed at a remote
  server. **Ask the user whether they want to work remotely** (they may have both
  a local and a remote server; the CLI is currently pointed at the remote one).
    - **No** → tell them to re-point the CLI at the local server (reconfigure
      `apiUrl` / re-auth), then re-run preflight.
    - **Yes** → follow the **remote flow**, staying autonomous (see *Operating
      principle*): **use any remote volume path / creds the user already gave**.
      The remote data volume **cannot be inferred locally** — a local
      `leap server info` can't reach the remote server — so the **user must supply
      it** (they can obtain it by running `leap server info` **on the remote host**
      and pasting its `datasetvolumes`). **Defer each ask to the step that needs
      it** (the remote volume at the **data-root switch before push**; store
      credentials when a fetch first needs them, via `leap secrets` /
      `AUTH_SECRET`), and don't re-ask for anything already provided. Then pick the
      data-delivery row (see **Data delivery** below) and remember the **local data
      subset** the local test needs.
- **Ambiguous topology (exit 5)** — a server **answers** at `localhost` on a
  non-`4589` port, which could be a local server on a custom port *or* a live
  remote server reached via port-forwarding. **Ask the user which it is**, then
  re-run the gate with the answer:
    - **Local** → `TL_TOPOLOGY=local .tensorleap/scripts/preflight.sh` (runs the
      local server/volume checks).
    - **Remote** → `TL_TOPOLOGY=remote .tensorleap/scripts/preflight.sh`, then follow
      the **remote flow** above.
- **No local server (exit 6)** — the URL is local (`localhost`, **any port**),
  topology was **derived** (the user did *not* explicitly say local/remote), but
  `leap server info` reports nothing running/installed. Ambiguous: it may be a
  **remote server reached via a port-forward** (which can bind **any** local port,
  not just 4589), or simply **no server installed here**. **Ask the user whether
  the Tensorleap server is installed remotely**:
    - **Yes (remote)** → ensure the CLI points at a **reachable** remote endpoint
      (the remote URL, or a live port-forward on whatever port — re-point/re-auth
      if the current URL is dead), then re-run
      `TL_TOPOLOGY=remote .tensorleap/scripts/preflight.sh` and follow the **remote
      flow** above.
    - **No** → the local server must be installed/started (`leap server run`).
      **Relay that guidance and STOP** — do not install or start it yourself.
  - **Exception:** if the user **explicitly said local** (`TL_TOPOLOGY=local`),
    the explicit choice wins — a missing server is a plain **blocker (exit 2)**,
    "install/start your local server," with **no** remote question asked.
- **OK (exit 0)** — the local platform is ready. Continue to the Python
  environment and `code_loader` setup in Step 0 below; those need the env to
  exist, so preflight deliberately leaves them to the skill.

## Data delivery (how the dataset reaches the code)

The dataset must end up readable from a **config-driven data root** (never
hardcoded) — but *how* it gets there depends on the **server topology** (local vs
remote, from the Preflight gate) and **where the data currently lives**. There are
two delivery strategies:

- **Volume** — the data sits as files on the data volume; encoders read a
  filesystem path under the data root.
- **Lazy-cache** — for a **remote store** (S3, Elasticsearch, …): `preprocess`
  lists the data and stores a **pointer per sample** (not bytes); each encoder
  resolves the pointer to a cache path under the data root and **downloads-on-miss
  into the volume** (see "Remote data"), so steady state is local reads. *Eager
  one-shot* is the small/convenient variant — pull everything up front when the
  dataset is small or has a convenient one-shot API where lazy-caching is
  non-trivial (e.g. Ultralytics, MNIST).

Pick the row, then act:

| # | Server | Data is… | Data delivery | Skill action | Verify present? |
|---|--------|----------|---------------|--------------|-----------------|
| 1 | Local  | Local path | Volume | detect existing project folder → if none, ask whether data is a local path (this row), a remote store (row 3), or on a remote volume (row 4) → for a local path, copy in | ✅ emptiness gate |
| 2 | Local  | Already on volume | Volume | detect & **reuse**, point config, don't copy | ✅ emptiness gate |
| 3 | Local  | Remote store (S3/ES/…) | **Lazy-cache** into local volume (eager if small/convenient) | infer fetch logic from the repo; `preprocess` lists→pointers; encoder downloads-on-miss; config root = local volume; creds only when a fetch needs auth (defer, don't front-load) | ✅ list the store |
| 4 | Remote | Pre-staged on volume | Volume | remote volume path **can't be inferred locally** — user supplies it (via `leap server info` **on the remote host**) unless already given; point **platform** config at it; **never copy**; ask user to **manually download a few images** to a local dir for the local test (1/split *not* enforced here) | ❌ can't verify |
| 5 | Remote | Remote store (S3/ES/…) | **Lazy-cache** into volume | infer fetch logic from the repo; creds via a **creds file path** (→ `AUTH_SECRET`) **only when a fetch needs auth** and not already supplied; local test downloads **1 sample/split** to a local dir; **before push, switch config root → remote volume** | ✅ list the store (client-side) |
| 6 | Remote | Local path only | **Unsupported** | detect & **stop**: no client→remote-volume copy path; user must stage to a store or pre-stage on the remote volume | — |

### Cross-cutting rules

- **Local integration test uses a small local subset, never the full dataset.**
  For a **remote server**: row 5 downloads **one sample per split** (training +
  validation) to a **local directory** for the test; row 4 (data opaque on the
  remote volume) instead asks the user to **manually place a few images** in a
  local dir — **1/split is not enforced** there. For a **local server** the test
  runs against the volume directly — real files (rows 1–2) or lazy-cached
  on-demand into the local volume (row 3), no separate local dir needed.
- **Configurable sample limit (balanced per split).** `preprocess` must support a
  configurable cap on the number of samples, applied **balanced across splits**
  (same count per split), read from `project_config.yaml` (e.g.
  `sample_limit_per_split`). **Default to NO limit — the push evaluates the full
  dataset.** Do **not** set an initial cap on your own; only apply one when the
  **user explicitly asks** for it (e.g. a small, fast initial evaluation). When
  they do, tell the user the cap lives at that key in `project_config.yaml` and
  how to change or remove it. This is separate from the local-test subset above:
  the limit caps what `preprocess` returns; the local test iterates a few of
  those.
- **Credentials — don't make the user paste secrets into the session.** Only set
  this up **when a remote-store fetch actually needs auth** (per *Operating
  principle*, defer it to that point) and the user hasn't already pointed you at a
  creds file / registered the secret. The common case is the user does **not**
  want to hand raw credentials to the Claude session, so when you do need them:
  **ask for a path to a local credentials file** (preferably JSON or a similar
  `{key: value}` format) and **register the secret from that file** with the
  `leap` CLI — the raw value never enters the conversation. `leap secrets create`
  takes the file path as its second positional argument (`secretKeyPath`):
  ```bash
  leap secrets create <name> <path-to-creds-file>   # reads the content FROM the file
  leap secrets set --secret-id <secretId>           # attaches it (writes secretId into leap.yaml)
  ```
  (`-k/--secret-key-content "<value>"` is the inline alternative — use it only if
  the user **volunteers** the value.) The integration reads the credential at
  runtime from the **`AUTH_SECRET`** env var (the secret is auto-injected into
  platform jobs). For **local runs**, export it from the same file rather than
  typing it (e.g. `export AUTH_SECRET="$(cat <path-to-creds-file>)"`), so the value
  stays out of the transcript. Never hardcode credentials; when the creds file (or
  non-trivial fetch logic — custom client, endpoint, query, pagination) can't be
  inferred and wasn't supplied, **ask for that specific piece**, then continue.
  - **Default when a creds file WAS supplied: register it as the `AUTH_SECRET`
    secret and move on — do NOT ask.** In particular, **do not assume the remote
    host has an IAM role / instance profile** and do not offer the platform's
    default credential chain as an option. Only skip the secret and rely on that
    chain if the user **explicitly states** the remote server already has S3
    access.
- **Data-root switch (any remote server — rows 4 & 5).** There are two roots: a
  **local directory** for the local integration test, and the **remote data
  volume** for the platform run. **Before pushing, edit the `data_root` in
  `project_config.yaml` to the remote-volume path**, and leave the local path as a
  **commented-out line with a verbal note** to switch back before re-running the
  local test. e.g. in `project_config.yaml`:
  ```yaml
  # data_root: "/path/to/tl-local-subset"         # LOCAL TEST: switch to this to re-run the local integration test
  data_root: "/remote/tensorleap/data/my-project" # PLATFORM: active for push
  ```
- **Emptiness gate.** Before authoring, verify the data is present — rows 1–2 by
  the data-root path being non-empty, rows 3 & 5 by the store prefix listing ≥1
  object (client-side; the local volume cache legitimately starts empty). Row 4
  **cannot be verified** from the client; say so explicitly rather than skipping
  silently.
- **Dependencies.** A remote store adds a client dep (e.g. `boto3` / `s3fs` for
  S3) — put it in `requirements.txt` and `leap.yaml`'s `include` so the platform
  build installs it (see Guardrails).

## Authoring order

Write the minimum next piece that unlocks a more informative run. Full detail and
the `__main__` evolution snippets are in `.tensorleap/reference/authoring-order.md`. The
order:

0. Setup (after the Preflight gate passes):
   - **Agree on the Python environment and provision it.** Match the repo's own
     tooling — `poetry` if it ships `pyproject.toml` + `poetry.lock`, `uv` if
     `uv.lock`, `conda` if `environment.yml`, otherwise a plain `venv` + `pip` on its
     `requirements.txt`. Confirm with the user; do **not** silently assume poetry.
     Use the agreed environment for every command below; the bundled scripts default
     to `poetry run …`, so set `TL_PY=<path-to-python>` to point them at a different
     interpreter.
   - **Validate `code_loader`.** If it isn't installed, install the latest release.
     If it is, require `code-loader >= 1.0.142`; if older, stop and report it as a
     blocker.
   - **Make the dataset reachable — pick the Data delivery row.** A file is
     readable on the platform only if it's in `leap.yaml`, the data volume, or
     runtime-fetched into the volume (see **Data delivery** above). For the common
     **local server + local data** case: find the volume with
     `leap server info` → `datasetVolumes`, **detect an existing per-project
     folder and reuse it**. If no local data is found, **do not assume a local
     path — ask the user whether (a) the data is at a local path to copy in
     (row 1), or (b) it lives on a remote store like S3/ES (row 3), or (c) it is
     pre-staged on a remote volume (row 4)**, and route to that row. Only after
     the user picks a local path do you create the project folder and copy the
     data in. Then point the integration's config-driven root at it. For a
     **remote store** set up the lazy-cache pattern and `AUTH_SECRET` credential;
     for a **remote server** follow the remote-flow steps from the Preflight gate.
     Then run the **emptiness gate** before authoring (skipped, with a note, only
     for row 4).
1. Create the file set (`leap_integration.py`, `preprocess.py`, `encoders.py`,
   `project_config.yaml`, `leap.yaml`; add `metrics.py`/`metadata.py`/
   `visualizers.py` when those components arrive — see **Project layout**); run a
   one-line entry-file `__main__` to confirm imports resolve and the exit hook
   attaches.
2. `@tensorleap_preprocess` (in `preprocess.py`) — return `list[PreprocessResponse]`
   with explicit `state=` and real `sample_ids` (unique strings; use the row index
   as the id if natural ids aren't unique), applying the config-driven
   per-split-balanced `sample_limit_per_split`. Call it directly from `__main__`
   and run.
3. Inspect the model I/O contract (input names, dtypes, shapes without batch
   dim, output count/meaning, required labels) before writing the loader.
4. The minimum input encoder set for **one real inference** — one encoder per
   model input, `channel_dim` explicit, returns a single unbatched
   `np.float32` array. Call each directly and run. Encoders must return
   `float32`; if the model needs integer inputs (e.g. `input_ids`), export it to
   accept `float32` and cast internally (see `.tensorleap/reference/error-signals.md`,
   load_model).
5. `@tensorleap_load_model` with explicit `prediction_types`. Call it directly
   and run.
6. A minimal `@tensorleap_integration_test` as soon as preprocess + min encoders
   + load_model exist. Switch `__main__` to call `integration_test(...)`. This is
   the first point you can see `Successful!`.
7. Remaining input encoders if the model has more inputs.
8. GT encoder(s) — call directly, then via `integration_test`.
9. Expand from one sample to several in training AND validation; then add
   optional metadata / visualizers / metrics / custom loss **one at a time**,
   running between each.

`load_model()` alone validates only model type and declared outputs. Useful
validation starts when a real encoded sample flows into the model.

## Keep `integration_test` thin (the #1 failure class)

When you call `integration_test(sample_id, preprocess_response)`, it runs your
body once, then **reruns it in mapping mode** where decorated functions return
placeholders. Plain Python logic cannot be traced in mapping mode and fails.

Inside the integration-test body, do ONLY:
- call decorated encoders / GT / loss / metric / metadata / visualizer functions
- call the decorated model loader
- the minimal runtime-correct inference for the returned model object
  (e.g. for an ONNX `InferenceSession`: `model.run(None, {model.get_inputs()[0].name: x})[i]`
  — pass `None` for the output names to return all outputs, then index the one you
  need; do not select outputs by name)

Do NOT, inside the body:
- `argmax` / `softmax` / `squeeze` / decode / threshold / clip / reshape
- arithmetic on arrays, pandas logic
- read `sample_id` or `preprocess.data` directly
- index anything except the model's predictions — and even those only **once**:
  the single output-select `model.run(None, ...)[i]` is fine, but a second index/slice
  on it (e.g. `[0]` to drop the batch) raises
  `'TempMapping' object is not subscriptable`
- manually add a batch dimension (Tensorleap batches encoder/GT outputs here)

Move all of that into decorated interfaces. Inside the test, decorated calls
return **batched** arrays (leading axis = 1) and the model is fed/returns batched
data — so design loss/metrics for batched input, and strip the batch axis
*inside* a visualizer (`if x.ndim == 3: x = x[0]`), never in the test body. When
the mapping rerun fails, code_loader can mask the real exception (and
mis-attribute "crashed at function 'X'") — see `.tensorleap/reference/error-signals.md`
(Integration test) to surface it.

## Reading feedback -> fix

The full catalog of known signals (preprocess, encoder, GT, load_model,
integration-test, loss, metadata, visualizer, metric, legacy-binder) and the
exact fix for each is in `.tensorleap/reference/error-signals.md`. Consult it when a signal
isn't obvious. The highest-frequency ones:

- `Integration test is only allowed to call Tensorleap decorators …` — plain
  Python leaked into the test body. Move it into a decorated function.
- `Tensorleap will add a batch dimension at axis 0 …` — an encoder returned a
  batched shape. Return a single unbatched sample.
- `The return type should be a numpy array of type float32` — cast with
  `.astype(np.float32)`.
- `The function returned None` — missing `return`, or a branch returns nothing.
  For unlabeled GT, return `np.array([], dtype=np.float32)`, never `None`.
- `number of declared prediction types(…) != number of model outputs(…)` — fix
  the `PredictionTypeHandler` list or the model.

## Guardrails

- Keep edits minimal, local, reviewable. Prioritize the integration files; do
  not refactor or modify unrelated training/business logic.
- **Install packages as needed** — when a runtime import is missing, install it
  **autonomously** into the agreed project environment from Step 0, using that
  env's tool (`poetry add` / `uv add` / `pip install` / `conda install`). **Mirror
  every runtime dependency you add into `requirements.txt`** (see the deps
  guardrail below) so the platform build installs it too. Keep it scoped to what
  the integration needs — don't gratuitously upgrade unrelated packages or
  restructure the project's stack.
- **Never** run `git commit` / `push` / `rebase` / `reset`. Leave change control
  to the human / orchestrator.
- **Never hardcode data-store credentials** in the integration. Read them from the
  **`AUTH_SECRET`** env var (registered via `leap secrets create` + `leap secrets
  set`, auto-injected on the platform; exported yourself for local runs). Prefer
  asking the user for a **local credentials-file path** and setting the secret from
  that file so the value stays out of the session (see **Credentials**); ask for
  the value inline only if the user offers it — never invent it.
- Run Python through the project's agreed environment from Step 0 (pyenv + poetry
  by default: `poetry run python …`; otherwise the venv/tool the user chose), not
  a different or global interpreter. Do not probe global site-packages to
  compensate for an interpreter mismatch.
- In `leap.yaml`, `include` every **code/asset** file the integration reads at
  runtime (the component modules `preprocess.py`/`encoders.py`/`metrics.py`/
  `metadata.py`/`visualizers.py`, `project_config.yaml`, tokenizer, labels, helper
  modules) — but **not the model**, which is uploaded to the platform separately,
  nor the **dataset**, which is read from the data volume via a config-driven
  path. A component module left out of `include` fails platform parsing even
  though the local run imported it fine. Set `pythonVersion` to match the
  project's actual runtime — `py310`, `py311`, etc. It is **not** fixed to 3.10;
  pick whatever `code_loader` and the model/runtime dependencies support (and
  confirm your pinned deps publish a wheel for that interpreter — e.g. recent
  `onnxruntime` releases dropped the py310 wheel). If a needed code/asset file is
  excluded, local validation can pass while platform parsing fails.
- Ship dependencies as a **`requirements.txt`** listed in `leap.yaml`'s `include` —
  the platform pip-installs them on top of its Linux (aarch64) base image. Do **not**
  ship `pyproject.toml`/`poetry.lock` (the platform resolves those against its own base
  image, so your deps never install). **Build the list additively — never seed it from
  the repo's `requirements.txt`/`pyproject.toml`.** Start empty and add one line per
  package the integration **actually imports at runtime**, as you add each import (the
  same deps you install per the *install packages as needed* rule). Because the list
  only ever grows from real runtime imports, the repo's training/dev stack never
  enters — so it stays lean with no pruning step. Two adjustments when you add a line,
  since the build runs on **Linux/aarch64**: translate OS-specific packages to their
  cross-platform form (e.g. `tensorflow-macos` → `tensorflow`, or `sys_platform`
  markers), and pin only as tightly as needed — prefer compatible-release pins
  (`tensorflow~=2.11.0`) over exact patch pins (`==2.11.1`) so the aarch64 build can
  resolve an available wheel. A package's import name can differ from its pip name
  (`code_loader.helpers` → `code-loader-helpers`).
- Keep prints minimal inside `@tensorleap_load_model` and
  `@tensorleap_integration_test` — the platform invokes these and heavy stdout
  can interfere. Put diagnostic prints in the `__main__` block, and never let
  parser success depend on a specific printed message. Import the model runtime
  **inside** the loader body (`def load_model(): import onnxruntime as ort` /
  `import keras`), not at module top — keeps the module import light and off the
  platform parse path.
- Do **not** add `from __future__ import annotations` to `leap_integration.py`.
  code_loader reads `function.__annotations__` for the real visualizer return
  *class* (e.g. `LeapHorizontalBar`); stringized annotations fail registration
  with a misleading "return type is invalid … should be one of [a list that
  already contains it]" error.
- Set `state=`, `channel_dim=`, and prediction-type semantics explicitly — never
  rely on the defaults the warnings flag.

## Optional surfaces (after the core path is green)

Add these one at a time, running after each:

- **Visualizers** — pick a `LeapDataType` and return its matching `Leap*` class.
  See `.tensorleap/reference/visualizer-types.md` for the catalog (type -> return class +
  shape rules) and how to read the original sample (tokens, paths, ids) via a
  `SamplePreprocessResponse` argument.
- **Metadata** — `@tensorleap_metadata("name", DatasetMetadataType.<string|float|int|boolean>)`,
  returning a scalar, `None`, or a flat dict of scalars (never arrays/nested).
  One function can emit several typed fields: pass a
  `Dict[str, DatasetMetadataType]` and return a matching dict (each surfaces as
  `<name>_<key>`).
- **Metrics / custom loss** — return a **batch-aligned 1D array (one value per
  sample)**, not a single scalar. Give a metric its `direction`
  (`MetricDirection.Upward`/`Downward`). A metric/loss must **discriminate
  better predictions from worse ones** — "it runs and returns varying numbers"
  is **not** sufficient. Stop and repair if either holds:
    1. **Placeholder** — it ignores its prediction/GT arguments, or returns
       zeros/constants/echoes an input. A no-op must not satisfy the wiring.
    2. **Doesn't track quality** — it returns a real, varying value, but that
       value doesn't reflect how good the prediction is. Counterfactual test:
       if the prediction got better or worse, would the value move in a
       meaningful direction? If not, it's a descriptor, not a metric — replace
       it. (A prediction-only value is acceptable *only* as a genuine
       unsupervised quality signal, e.g. calibration or uncertainty.)

## Acceptance (staged definition of done)

Use the validator as a staged signal, not a binary oracle.

- **Early:** preprocess, an input encoder, and load_model each run directly
  without error.
- **Middle:** a minimal `integration_test(...)` runs, the mapping rerun does not
  fail, and `Successful!` prints.
- **Core:** the exit table shows preprocess, integration test, input encoder, GT
  encoder, and load_model all exercised, with no unresolved mandatory rows.
- **Real:** several training AND validation samples pass through
  `integration_test(...)`, no default-use warnings remain, the structured parse is
  clean, **and a custom loss is registered** (required to push — the platform build
  fails without one).

For the Core/Real decision, account for **both** signals — they cover different
scopes (see above):

```
poetry run python .tensorleap/scripts/tl_check.py "$(pwd)/tensorleap"   # project env (poetry by default); pass the ABSOLUTE path to tensorleap/
```

It prints JSON from `LeapLoader.check_dataset()`, which validates the **dataset
side only** (preprocess, input/GT encoders, metadata) — it never loads the model.
For those, trust `isValid`, `generalError`, and per-handler `payloads[].passed`
over the human-readable exit table (`print_log` carries the captured stdout). For
the **dataset<->model rows** — `load_model`, `custom_loss`, and any
metric/visualizer that consumes predictions — `check_dataset()` is silent; rely on
the exit table there (a `❌` is real even when `isValid` is `True`). "Clean" =
`isValid: True` **and** no unresolved model-connection crosses in the table.

## Deploy: push the finished integration

Once the structured parse is clean, ship it to the platform **from inside
`tensorleap/`** (where `leap.yaml` lives).
**Push by default — don't ask.** As soon as the integration validates, proceed
through the steps below automatically (switch `data_root` to the remote volume,
ensure a project, upload the model, run `leap push … --eval`). Only **hold and
leave the code unpushed** if the user **explicitly asked** not to push yet (or to
review first). If a project name is genuinely required and can't be inferred,
create a sensible one or ask for just that — don't turn the whole push into a
yes/no question.

1. **Auth (re-check)** — auth was verified in the Preflight gate; quickly
   re-confirm with `leap auth whoami` in case the token expired mid-session. If
   it no longer returns your user/team, re-run `leap auth login`.
2. **Project** — the workspace must point at a project (a `projectId` in
   `leap.yaml`). If `leap projects info` says "No project configured," run
   `leap projects create <name>` and set its id in `leap.yaml`.
3. **Remote-server prep (rows 4 & 5 only)** — before pushing, **switch the
   `data_root` in `project_config.yaml` from the local test path to the remote
   data-volume path** (leave the local path as a commented line with a note — see
   Data delivery). If the integration reads a remote store, ensure its credential
   is registered as `AUTH_SECRET` (`leap secrets create` + `leap secrets set`,
   which writes `secretId` into `leap.yaml` for auto-injection).
4. **Push + evaluate** — from inside `tensorleap/`:
   `leap push -m <model> -n <version> -b <batch> --eval`. `-m` uploads the model
   separately (it is not bundled); the code bundle comes from `leap.yaml`'s
   `include`. (`leap push -h` for `-o/--overwrite`, `--branch`.) **Never use
   `--no-wait`** — it returns before the push completes, so the `--eval` step never
   runs and no Evaluate job is created. By default the push evaluates the **full
   dataset** (no `sample_limit_per_split`); apply a cap only if the **user
   explicitly asked** for a smaller initial evaluation, in which case that key in
   `project_config.yaml` holds it.
5. **Run the push and get the Evaluate started.** Capture output to a file:
   ```
   leap push -m <model> -n <version> -b <batch> --eval < /dev/null > push.log 2>&1
   ```
   `--eval` starts an Evaluate job **after the push completes**; the command
   returns once that job has *started* (not finished). `< /dev/null` and the
   `push.log` redirect keep the push non-interactive and keep the loader's output
   out of your context.
   **Failure behavior you must handle (`leap` ≤ 0.0.155):** on a **build failure**
   these versions do **not** exit — on a non-interactive shell they stall at the
   prompt `View errors in interactive mode? (Y/n):`, waiting for input that never
   comes, so the command **hangs**. (Dev/newer CLIs exit non-zero instead.) So **if
   `push.log` contains `View errors in interactive mode`, the push FAILED** — stop
   waiting for it and kill it; never treat a non-returning push as "still working".
   The real error is **not** in `push.log` — read it from the failed Push run's
   server-side log: `leap run list -t Push` (newest) → `leap run logs <run-id>`.
   Fix the error, then re-push (`-o/--overwrite`).
6. **Confirm the Evaluate started — then it runs asynchronously.** The `--eval`
   runs as an `Evaluate` job on the platform; a clean push does **not** mean a
   clean evaluation, and on the **full dataset the job can take hours to a day**.
   ```
   leap run list -t Evaluate      # find the run you just created; note its id (EVAL_ID)
   ```
   - **No `Evaluate` job exists at all** → the eval was **not triggered**. Re-push
     `leap push -o <version> --eval` and re-check.
   - Once an `Evaluate` job exists and is running (`PENDING` / `INITIALIZING` /
     `STARTED`), the integration is wired up and the evaluation is underway.
   **Don't poll it with your own turns** — a turn per check burns tokens, for up to
   a day. Instead launch a **background shell** that watches *this* run and exits
   when it reaches a terminal state; the loop is token-free (not your turns) and
   re-invokes you once on completion:
   ```
   # background; poll every 5 min; exit when THIS run (EVAL_ID) is terminal
   while :; do
     s=$(leap run list -t Evaluate | grep "$EVAL_ID")   # match the exact run, not the newest
     case "$s" in *FINISHED*|*FAILED*|*STOPPED*|*TERMINATED*) echo "$s"; break;; esac
     sleep 300                                            # empty/failed list => keep waiting, not terminal
   done
   ```
   When it exits: **`FINISHED`** → the integration is done. **`FAILED` / `STOPPED` /
   `TERMINATED`** → pull `leap run logs <run-id>`, read the earliest real error, fix
   it in the integration, re-push, and track the new eval the same way. (Errors that
   surface only here are typically platform-only conditions the local test can't
   see — the data-root/volume switch, `AUTH_SECRET` not injected, a missing
   `include`, or a dependency absent from `requirements.txt`.)
<!-- END TENSORLEAP SKILL: tensorleap-integration-creation -->
