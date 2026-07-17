# Authoring order and `__main__` evolution

Write the minimum next piece that unlocks a more informative validator run, then
run. The `__main__` block evolves stage by stage — it is your run harness, not
production code.

## File split (see skill.md "Project layout")

Author across the fixed file set, each decorator in its component file, all
imported into the entry file `leap_integration.py`:

- `preprocess.py` → `@tensorleap_preprocess`
- `encoders.py` → input + GT encoders
- `metrics.py` / `metadata.py` / `visualizers.py` → the matching optional components
  (`metrics.py` holds the **custom loss** `@tensorleap_custom_loss` alongside custom metrics)
- `project_config.yaml` → constants/config (data root, paths, `sample_limit_per_split`, flags) — no secrets
- `leap_integration.py` → imports the above + `@tensorleap_load_model`,
  `@tensorleap_integration_test`, and the `__main__` harness

The decorator imports below come from `code_loader`; put each where its component
lives. `leap_integration.py` then imports the component symbols:

```python
# leap_integration.py
from preprocess import preprocess
from encoders import image_input, class_gt
# ... metrics / metadata / visualizers as added
```

## Decorator imports (in each component file)

```python
from typing import List

import numpy as np

from code_loader.contract.datasetclasses import PreprocessResponse, PredictionTypeHandler
from code_loader.contract.enums import DataStateType
from code_loader.inner_leap_binder.leapbinder_decorators import (
    tensorleap_preprocess,
    tensorleap_input_encoder,
    tensorleap_gt_encoder,
    tensorleap_load_model,
    tensorleap_custom_loss,
    tensorleap_metadata,
    tensorleap_custom_visualizer,
    tensorleap_custom_metric,
    tensorleap_integration_test,
)
```

## Step 1 — skeleton + tiny `__main__`

Create the file set (`leap_integration.py`, `preprocess.py`, `encoders.py`,
`project_config.yaml`; add `metrics.py`/`metadata.py`/`visualizers.py` when those
components arrive) and a `project_config.yaml` with at least the data root (the
`sample_limit_per_split` cap is optional and unset by default). Then a tiny entry-file `__main__`:

```python
if __name__ == "__main__":
    print("integration module imported")
```

Run it. Confirms imports resolve, the file name is correct, and the exit hook is
attached. The status table will show mostly-missing interfaces — expected.

## Step 2 — preprocess (the root)

Lives in `preprocess.py`. Apply the **configurable, per-split-balanced sample cap** from
`project_config.yaml` (`sample_limit_per_split`; unset/`0` = no cap by default, i.e.
full dataset; the user sets a number only when the user wants faster iterations, see
skill.md Data delivery):

```python
@tensorleap_preprocess()
def preprocess() -> List[PreprocessResponse]:
    limit = CONFIG.get("sample_limit_per_split")   # None/0 = no cap -> full dataset (default)
    train_ids = all_train_ids[:limit] if limit else all_train_ids
    val_ids = all_val_ids[:limit] if limit else all_val_ids   # same cap per split
    train = PreprocessResponse(sample_ids=train_ids, data={...}, state=DataStateType.training)
    val = PreprocessResponse(sample_ids=val_ids, data={...}, state=DataStateType.validation)
    return [train, val]
```

`__main__`:

```python
if __name__ == "__main__":
    subsets = preprocess()
    print([(s.state, s.length) for s in subsets])
```

Proves: no args, returns `list[PreprocessResponse]`, each element valid. Does NOT
yet prove training+validation both exist, positive lengths, or first-sample
fetch — those come after `integration_test` reaches `leap_binder.check()`.

`sample_ids` must be **unique strings**. If the natural ids aren't unique (or
aren't strings), use the row index: `df.index.astype(str).tolist()`. Read the
dataset from a **config-driven data root** (it is a mount point on the platform),
not a hardcoded one.

For a **remote store** (S3, Elasticsearch, …) `preprocess` **lists** the data and
stores a **pointer per sample** — the key/URI, not the bytes — in
`PreprocessResponse.data`; the actual download happens lazily in the encoders (see
Step 4). See skill.md **Data delivery** for the full row-by-row decision (local vs
remote server, copy vs lazy-cache, credentials via `AUTH_SECRET`, and the
data-root switch before push).

## Step 3 — inspect the model contract

Before writing the loader, answer: how many inputs, each name/dtype, each shape
without batch dim, how many outputs, what they mean, what labels /
`PredictionTypeHandler` are required. Do this outside Tensorleap code if easier.

## Step 4 — minimum input encoder set

One encoder per model input; enough for one real inference.

```python
@tensorleap_input_encoder(name="image", channel_dim=-1)
def image_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    row = preprocess.data["records_by_id"][sample_id]
    return load_image(row["image_path"]).astype(np.float32)
```

**Remote store — lazy download + cache in the data volume.** The pointer stored in
`preprocess` resolves to a cache path under the config-driven data root; download
on miss, then load locally. Credentials come from the **`AUTH_SECRET`** env var
(registered via `leap secrets`, auto-injected on the platform, exported yourself
for local runs) — never hardcoded:

```python
@tensorleap_input_encoder(name="image", channel_dim=-1)
def image_input(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    row = preprocess.data["records_by_id"][sample_id]
    local_path = os.path.join(DATA_ROOT, row["key"])   # cache path in the data volume
    if not os.path.exists(local_path):
        download_from_store(row["key"], local_path)     # auth via AUTH_SECRET env var
    return load_image(local_path).astype(np.float32)
```

`__main__`:

```python
if __name__ == "__main__":
    subsets = preprocess()
    train = next(s for s in subsets if s.state == DataStateType.training)
    x = image_input(train.sample_ids[0], train)
    print(x.shape, x.dtype)
```

Return a single sample, NOT a batch. Catches wrong shape/dtype/lookup fastest.

## Step 5 — load_model

```python
prediction_types = [PredictionTypeHandler(name="classes", labels=[...], channel_dim=-1)]

@tensorleap_load_model(prediction_types)
def load_model():
    ...
```

`__main__`:

```python
if __name__ == "__main__":
    model = load_model()
    print(type(model))
```

Validates model type + prediction-type declarations. Does NOT yet validate the
full inference path or that declared types match output count. Return a Keras
model or an ONNX Runtime `InferenceSession`. Note: called outside
`integration_test`, `load_model()` returns a `ModelPlaceholder` — only
`print(type(model))`; calling `.get_inputs()`/`.run()` on it in `__main__` fails.

## Step 6 — minimal integration_test

Add it as soon as preprocess + min encoders + load_model exist.

```python
@tensorleap_integration_test()
def integration_test(sample_id: str, preprocess: PreprocessResponse):
    x = image_input(sample_id, preprocess)
    model = load_model()
    _ = model(...)  # minimal runtime-correct inference only
```

`__main__`:

```python
if __name__ == "__main__":
    subsets = preprocess()
    train = next(s for s in subsets if s.state == DataStateType.training)
    integration_test(train.sample_ids[0], train)
```

First point mapping-mode validation and `leap_binder.check()` run, and the first
point you can see `Successful!`. Keep the body thin (see SKILL.md).

## Step 7 — remaining input encoders

If the model has more inputs than the first pass covered, add the rest, each
called directly then via `integration_test`.

## Step 8 — GT encoder(s)

```python
@tensorleap_gt_encoder(name="classes")
def class_gt(sample_id: str, preprocess: PreprocessResponse) -> np.ndarray:
    row = preprocess.data["records_by_id"][sample_id]
    return row["one_hot_label"].astype(np.float32)
```

Call directly first (is the encoder itself right?), then via `integration_test`
(does the whole path stay mappable + binder-valid?). For an unlabeled path return
`np.array([], dtype=np.float32)`, never `None`.

## Step 9 — expand beyond the first sample, then optional interfaces

`leap_binder.check()` / `check_dataset()` validate only the FIRST sample. Expand:

```python
if __name__ == "__main__":
    subsets = preprocess()
    for subset in subsets:
        if subset.state not in {DataStateType.training, DataStateType.validation}:
            continue
        for sample_id in subset.sample_ids[:3]:
            integration_test(sample_id, subset)
```

Catches sample-specific missing files, shape drift, label edge cases, metadata
drift. Then add custom loss / metadata / visualizers / metrics **one at a time**:
define -> call directly if possible -> call from `integration_test` -> rerun -> fix
before adding the next. The **custom loss is required to push** (the platform build
fails without one) even though `check_dataset()` can report `isValid: True` without
it; metadata / visualizers / metrics are optional.

## leap.yaml

```yaml
entryFile: leap_integration.py
pythonVersion: py310        # match the project's runtime (py310/py311/...), not a fixed value
include:
  - leap.yaml
  - leap_integration.py     # entry file
  - preprocess.py           # component modules imported by the entry file
  - encoders.py
  - metrics.py              # include those that exist
  - metadata.py
  - visualizers.py
  - project_config.yaml     # constants/config the integration reads (NOT secrets)
  - requirements.txt        # deps the platform installs with pip (export from poetry/uv if needed)
  - tokenizer/**            # tokenizer / vocab assets
  - <your_module>/**.py     # any extra helper modules imported by the integration
  # NOT the model — it is uploaded to the platform separately, not bundled
exclude:
  - .git/**
  - .concierge/**
```

Include every **code/asset** the integration reads from disk — the component
modules, `project_config.yaml`, tokenizer, labels, helper modules — and declare
`pythonVersion` to match the project's actual
runtime (`py310`, `py311`, …; whatever `code_loader` and the model/runtime deps
support — it is not fixed to 3.10). A locally-readable but un-included file makes
local validation pass while platform parsing fails.

Dependencies ship as a **`requirements.txt`** in `include`: the platform pip-installs
them additively on top of its Linux/aarch64 base image. Build the list from what the
integration imports at runtime (not the training/dev stack) and let pip resolve the
transitives; do **not** rely on `pyproject.toml`/`poetry.lock` — the platform's poetry
path resolves against the base image's own pyproject, so your deps wouldn't install.
Prefer compatible-release pins (`~=`) over exact patch pins (`==`) so the aarch64 build
finds a wheel, and translate OS-specific deps (`tensorflow-macos` → `tensorflow`, or
`sys_platform` markers).

Do **not** include the **model** (it is uploaded to the platform separately; your
`@tensorleap_load_model` loads it only for local runs / the integration test) or
the **dataset** (it lives on the data volume — either copied there or
runtime-fetched from a remote store — so `preprocess()`/encoders read it from a
config-driven data root rather than a bundled file).

If the integration reads a **remote store**, add its client dep (e.g. `boto3` /
`s3fs`) to `requirements.txt`, and register the store credential as `AUTH_SECRET`
via `leap secrets create` + `leap secrets set` (writes `secretId` into `leap.yaml`
for auto-injection). (`projectId` / `secretId` / `branch` are managed by the
platform.)
