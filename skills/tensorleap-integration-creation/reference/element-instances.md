# Element instances: analyzing per-object rows, not just per-image rows

Reference for **instance mode** — the optional surface that makes each annotated
element of a sample (a GT box, a mask region, an object) a **first-class row**
on the platform, with its own metrics, metadata, and latent-space position.
Use it when the user's question lives at the object level ("which *defects*
cluster together?", "which *instances* does the model miss?") rather than the
image level.

**Version gate:** requires a server release **≥ 1.6.75** and `code-loader`
**≥ 1.0.206** (the `Element instances` preflight line). When DISABLED, skip
everything in this file.

## The row model

The preprocess declares, per sample, how many instances it has and how to mask
each one. The platform then expands the row universe: one row per image **plus
one row per instance**, with flat ids `f"{sample_id}_{instance_id}"`
(0-based). Instance rows are **full rows** — every encoder, metric, metadata
function, visualizer, and built-in latent space runs on them too (fed the
instance-masked input, or pooled from the parent's activations — the engine
decides). Three built-in metadata columns tie the universe together:

| Column | Image row | Instance row |
|---|---|---|
| `builtin_instance_metadata_is_instance` | `"0"` | `"1"` |
| `builtin_instance_metadata_original_sample_id` | own id | **parent** sample id |
| `builtin_instance_metadata_instance_name` | `"none"` | the `ElementInstance.name` |

Join instance rows to their parent via `original_sample_id`, never by parsing
the flat id (a sample id containing `_` breaks the parse).

Incompatible with a **grouped** (nested-`sample_ids`) preprocess — the
decorator raises.

## The decorators

### 1. Preprocess — `@tensorleap_element_instance_preprocess`

Replaces `@tensorleap_preprocess`. Takes the two instance encoders as
**arguments**:

```python
@tensorleap_element_instance_preprocess(instance_length_encoder,
                                        instance_mask_encoder)
def preprocess() -> List[PreprocessResponse]:
    ...   # ordinary preprocess body; ids must be scalars (not grouped)
```

- **Length encoder** `(sample_id, preprocess) -> int` — how many instances the
  sample has. `0` is fine (the sample simply contributes no instance rows).
- **Mask encoder** — see next.
- Optional third argument `instance_metadata_types={...}` — see *Instance
  metadata* below.

### 2. Masks encoder — `@tensorleap_instances_masks_encoder("name")`

```python
@tensorleap_instances_masks_encoder("image")
def instance_mask(sample_id: str, preprocess: PreprocessResponse,
                  instance_id: int) -> Optional[ElementInstance]:
    box = ...                                   # this instance's annotation
    return ElementInstance(
        name=class_name,                        # -> instance_name column
        mask=mask,                              # float32, input-shaped, 1=instance
        instance_metadata={"area": float(a)},   # optional, see below
    )
```

The mask is the instance's spatial footprint on the input; the engine uses it
to build the instance row. Return `None` for an absent instance.

### 3. Instance metrics — `@tensorleap_custom_instances_metric`

Looks like a custom metric (batched `np.ndarray` args + one
`SamplePreprocessResponse` arg) but returns a **dict keyed by instance
position**, each value a `(batch,)` array:

```python
@tensorleap_custom_instances_metric("instance_iou", direction=MetricDirection.Upward)
def instance_iou(y_pred: np.ndarray, preprocess: SamplePreprocessResponse
                 ) -> Dict[int, np.ndarray]:
    ...   # {0: array([...]), 1: array([...]), ...} — one entry per instance
```

Each instance metric lands as one scalar `metrics.<name>` column on instance
rows (no subkeys). Declare `direction` — insights silently default to
`Downward` otherwise. **Call it in `integration_test`** (on the raw prediction
slot plus the `SamplePreprocessResponse`) like any other metric.

### 4. Instance custom latent space — `@tensorleap_instance_custom_latent_space`

The instance-level counterpart of the custom latent space. **Dataset-computed
only** (no model-computed variant), called once per instance:

```python
@tensorleap_instance_custom_latent_space(name="object_features")
def object_features(sample_id: str, preprocess: PreprocessResponse,
                    instance_id: int) -> np.ndarray:
    ...                                        # -> (d,) float32, this instance only
```

- **Sparse:** it holds instance rows only — no image row is fabricated, a
  `k == 0` sample contributes nothing. The sample-level insights pass skipping
  it with "no samples in latent space" is **expected**, not an error.
- Never called with `instance_id=None`; `instance_id` is a plain int.
- Same width caps as the sample-level decorator (≤ 4096 hard, > 1024 warns;
  `ndim > 1` returns are flattened with a warning) and it counts toward the
  ≤ 10 custom latent spaces / unique-names budget.
- No model output, no sibling model file, no `PredictionTypeHandler`, and no
  `integration_test` binding — registration alone wires it. It needs the model's
  view of the instance? Then compute it from what `preprocess` can reach; if the
  signal must come from activations, that is the sample-level
  (`custom-latent-space.md`) decorator's job instead.
- `check_dataset` **fails by name** if it is registered without *both*
  companions (the masks encoder and the element-instance preprocess) — the
  companions are a hard requirement, not a convention.

## Instance metadata (user-defined, per instance)

Not a decorator — it rides on the masks encoder's return value:

1. Put scalar values (`str`/`int`/`bool`/`float`, never arrays) in
   `ElementInstance.instance_metadata`. Keep the **key set identical across
   instances**; types are locked from the first instance probed.
2. If a value can be `None`, its type cannot be inferred — declare it:
   `@tensorleap_element_instance_preprocess(..., instance_metadata_types=
   {"area": DatasetMetadataType.float})`. The declaration wins on type and
   guarantees the column even when the probed instance lacks the key.
   The `instance_metadata_types` argument exists from **code-loader 1.0.206**
   (the version a 1.6.75 server pins); on 1.0.204–1.0.205 the rest of the
   instance surface exists but metadata types are probe-inferred only, so
   every key must have a real (non-`None`) value on the first probed instance.

Each key surfaces as a `builtin_instance_extra_metadata_<key>` column on
instance rows (declared-but-empty on image rows) — filterable and usable for
coloring exactly like sample metadata. The metadata concept cap from
`metadata.md` applies to the combined set.

## How insights use the two populations

One insights run sweeps both: a **sample-level pass** (built-in latent spaces,
image clusters) and an **instance pass** (restricted to the pooled built-in
space plus custom instance latent spaces, instance clusters). Instance rows
carry the sample-level losses too, so both metric families can produce
instance-level insights. Image-level and instance-level insights coexist in
the same run; each is tagged with its latent space and population.

## Guardrails

- Both instance encoders **and** the element-instance preprocess, or none —
  `check_dataset` enforces it; don't wire a partial setup.
- Masks and instance LS functions receive the **parent** `sample_id` plus
  `instance_id: int`; never parse the flat row id.
- Instance metadata: consistent keys, scalar values, declare types for
  `None`-able keys.
- Declare `direction` on every instance metric.
- Instance mode multiplies the row count (one row per GT element): mind the
  sample cap in `project_config.yaml` when datasets are instance-dense.
