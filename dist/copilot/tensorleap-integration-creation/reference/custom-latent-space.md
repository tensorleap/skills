# Custom latent space: one task-informed representation on top of the platform's

Reference for the optional custom-latent-space surface. A latent space is the
representation the platform clusters samples in; every insight records which
latent space it was found in, and the platform's built-in spaces
(`classification-semantic`, `image-non-semantic`, `foreground`, `balanced`, …)
are extracted by **model-agnostic heuristics** — the same layer rules for every
network. The custom latent space is your addition: **exactly one** extra
representation, chosen because it is the most informative for *this* task on
*this* architecture. It does not replace the built-in ones.

Precedent that shipped: a YOLO integration exposing the stride-8 neck map and
pooling it at the GT boxes, so samples cluster by *what the objects look like to
the detector* rather than by whole-image appearance.

## API shape

```python
import numpy as np
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_latent_space
from code_loader.contract.enums import LatentSpaceReduction

@tensorleap_custom_latent_space(name="object_features", use_ls_for_analysis=True)
def object_features(p3: np.ndarray, gt_boxes: np.ndarray) -> np.ndarray:
    ...                                   # -> (batch, d) float32
```

Two signatures exist; the decorator tells them apart by the **type annotation of
the second parameter**:

- **Model-computed** (prefer this): `(x: np.ndarray, ...) -> (batch, d, ...)`.
  Every parameter is a tensor the platform already has — a **model output**, an
  **input encoder** output, or a **GT encoder** output — so the platform runs the
  model once and hands you the activations. The binding comes from what you pass
  in `integration_test` (see *Wiring*). An extra parameter annotated
  `SamplePreprocessResponse` is auto-injected (original sample access) and takes
  no binding.
- **Dataset-computed**: `(sample_id, preprocess: PreprocessResponse) -> (d, ...)`.
  Runs per sample and has to run the model itself — a second inference pass. Use
  it only when the model-computed contract cannot express the space (e.g. a
  grouped preprocess response, which model-computed rejects).

Contract, enforced by code-loader:

- Return `np.ndarray`; model-computed needs `ndim >= 2` with the batch axis first
  and matching the batch size. `float32` by convention.
- **Width per sample ≤ 4096** (hard error), **> 1024 warns**. Wide spaces are slow
  to store and cluster. Two stateless reductions ship with the decorator as a
  convenience — `reduce=LatentSpaceReduction.RANDOM_PROJECTION` (`n_components`,
  default 512) and `reduce=LatentSpaceReduction.MEAN_POOL` (`channel_axis`,
  averages the non-channel axes of the raw map you return) — but any **stateless
  per-sample** reduction is equally fine in your own code when it suits the task
  better: max or strided pooling, attention pooling, a fixed-seed projection.
  What must not happen inside the function is a *fitted* reduction (PCA and the
  like): it sees one batch at a time, so a per-batch fit changes basis between
  batches and the space is meaningless.
- **≤ 10 custom latent spaces per project**, unique names. This skill adds one.
- `use_ls_for_analysis=True` on **at most one** latent space in the project: the
  Out-Of-Distribution, Domain-Gap and mislabeling insights run in that space.
  Set it on the space you add — that is what makes its insights attributable —
  **unless the repo already has a custom latent space carrying it**: that flag
  is the user's choice, so leave theirs in place and add yours without it
  (code-loader rejects a second one).

## Selecting the layer: two jobs, both before any inference

The choice is a **pre-inference decision**: it fixes which tensor the model
exposes, so nothing computed during the evaluate (metrics, losses) can inform
it. What can: the dataset's already-materialized metadata and the model graph.

**Job 1 — profile the task from materialized metadata.** Reuse what the metadata
sweep (`metadata.md`) already surfaced: object-size and class distributions from
the annotation files, sequence/duration stats, class balance, acquisition
domains. Read the annotation JSON/CSV/manifest and the repo's own dataset
statistics. **Budget guardrail:** materialized metadata first. A pass over the
data is fine when it is cheap and the statistic is worth it — a few minutes on a
random subsample of some hundreds of samples — but the layer choice must not
cost a decode of the whole dataset (a million HD images to pick one tensor).
If you scan, cap it, and record the sample count and wall-clock in
`integration-report.md`.

**Job 2 — map task profile + architecture to a layer.** Inspect the graph:
`onnx.load` + `onnx.shape_inference.infer_shapes` and walk `graph.node` /
`value_info` for ONNX; `model.summary()` / `model.layers` for Keras. Then reason
from the task about *where the signal the user cares about lives*:

| Task | Candidate tensor | Pool to a vector by |
|---|---|---|
| Detection | The neck/FPN level whose stride matches the **dominant object scale** in the profile (stride 8 for small objects, 32 for large); not P3 by reflex | Crop the map at each GT box, mean-pool each crop, average over boxes; whole-map mean when a sample has no GT |
| Classification | The last conv block **before** global pooling (texture / part level) when the built-in semantic space already covers the logits; or an earlier block when the profile says appearance drives failures | Spatial mean (`reduce=MEAN_POOL` does it for you) |
| Segmentation | The decoder feature map **before** the per-pixel classifier | Masked mean over the GT foreground, or per-class masked means concatenated for the top-K classes |
| ViT / DINO-style | Last block's CLS token, or the patch tokens | CLS as-is; patch tokens mean- or attention-pooled |
| Time series / regression | The encoder bottleneck (autoencoder latent) or the last recurrent / conv state before the head | Usually already a vector; mean over time otherwise |

Refute the default before taking it: "P3 for YOLO" is right for a small-object
dataset and wrong for one dominated by large objects (P5 carries the semantics
there). The layer follows the profile, or the choice is just another fixed
heuristic — which the platform already has.

Record the decision in `integration-report.md`: the profile evidence, the
tensor chosen (name, shape, stride), the pooling, and the width.

## Exposing the layer as a model output

A model-computed latent space reads a **real model output**, so the chosen
tensor must become one. Write a sibling model file — **never overwrite the
user's original** — and point `MODEL_PATH` (in `project_config.yaml`) at it.
That sibling is what `leap push -m` uploads.

ONNX:

```python
import onnx
from onnx import shape_inference, helper, TensorProto

m = shape_inference.infer_shapes(onnx.load(MODEL_PATH))
vi = next((v for v in m.graph.value_info if v.name == LAYER_TENSOR), None) \
     or helper.make_tensor_value_info(LAYER_TENSOR, TensorProto.FLOAT, None)
m.graph.output.append(vi)
onnx.save(m, LS_MODEL_PATH)              # e.g. <name>_ls.onnx next to the original
```

Keras:

```python
base = tf.keras.models.load_model(MODEL_PATH, compile=False)
ls_model = tf.keras.Model(base.inputs, base.outputs + [base.get_layer(LAYER_NAME).output])
ls_model.save(LS_MODEL_PATH)             # e.g. <name>_ls.h5
```

**Every model output needs a `PredictionTypeHandler`** — there is no
"latent-only" output flag, and code-loader rejects a count mismatch. Add one for
the new output (`labels` = channel names if meaningful, else `[f"c{i}" ...]`;
`channel_dim` per the tensor layout). Keep the new output **last** so the
existing prediction indices in `integration_test`, metrics and visualizers do
not shift.

For a **single layer**, pooling can be baked into the graph (a `ReduceMean`
over the spatial axes) or done in NumPy inside the latent-space function. NumPy
is fine here and is the only option for GT-guided pooling (the graph never sees
the GT); bake it into the graph when the raw map alone would exceed the width
cap in transit. For **several layers**, see the next section: pool and
concatenate in the graph so the model still gains exactly one output.

## Combining layers and pooling

The platform's own multi-layer spaces are built per layer as *gather channels →
flatten spatial → pool to `(batch, channels)`*, then **concatenated along the
feature axis**, then optionally reduced. Mirror that shape when you combine
layers: pool each to a vector first (that is what makes layers of different
spatial size concatenable — no resizing), then concatenate. Where the custom
space deliberately departs from the platform: **pooling is chosen per layer**
(mean, max, GT-box crop-and-pool), and **cropping before pooling is allowed**.
The concatenated width still has to respect the caps.

**Guideline: do the per-layer pooling and the concat inside the graph, so the
model gains one new output, not one per layer.** Several appended outputs each
need a `PredictionTypeHandler`, each ride through the platform as a full
prediction tensor, and the decorator function ends up re-implementing the
engine's concat. One in-graph vector avoids all three. Not a hard block — when a
layer needs GT-guided pooling, that one stays a raw map and is pooled in the
function — but the default is a single combined output.

ONNX (`ReduceMean` takes `axes` as an attribute up to opset 12 and as a second
input from opset 13 — check `m.opset_import`):

```python
pooled = []
for t in LAYER_TENSORS:                                   # each (N, C_i, H_i, W_i)
    m.graph.node.append(helper.make_node("ReduceMean", [t], [f"{t}_pooled"], axes=[2, 3], keepdims=0))
    pooled.append(f"{t}_pooled")
m.graph.node.append(helper.make_node("Concat", pooled, ["custom_ls"], axis=1))
m.graph.output.append(helper.make_tensor_value_info("custom_ls", TensorProto.FLOAT, ["N", TOTAL_CHANNELS]))
onnx.save(m, LS_MODEL_PATH)
```

Keras:

```python
pooled = [tf.keras.layers.GlobalAveragePooling2D()(base.get_layer(n).output) for n in LAYER_NAMES]
ls_out = tf.keras.layers.Concatenate(name="custom_ls")(pooled)
ls_model = tf.keras.Model(base.inputs, base.outputs + [ls_out])
```

Swap `ReduceMean` / `GlobalAveragePooling2D` for the max variants per layer as
the profile dictates; the concat is the same.

Post-processing here means *semantic* pooling — box-guided, mask-guided,
attention-weighted, per-class means — plus whatever stateless reduction brings
the width under the cap. `reduce=` is the zero-code version of the two commonest
ones, not a requirement.

## Wiring it in `integration_test`

The binding is positional: the argument you pass from a model output becomes
`Prediction{k}` (`k` = that output's index) and the platform resolves it to the
layer at import; an argument you pass from an encoder binds to that encoder by
name. So, inside the test body, call the function on the **raw output slot** and
the **encoder's return value**:

```python
outputs = model.run(None, {model.get_inputs()[0].name: x})   # ONNX; Keras: model(x)
detections = outputs[0]
...
object_features(outputs[4], gt)      # outputs[4] = the appended output; gt = the GT encoder's return
```

Same rules as every other decorated call in the test body: index the model
output **once**, no reshaping or slicing in the body, pass encoder outputs
straight through. **Nothing flags a model-computed latent space the test never
calls** — it simply never binds and never appears on the platform — so the call
in the test body is the wiring, not an optional check.

## Worked pooling functions

The detection and segmentation rows of the selection table, as code — the two
cases where the pooling needs the GT and so cannot live in the graph. The other
rows reduce to `return z.astype(np.float32)` or a `reduce=` argument.

Detection, stride-8 neck map exposed as the 5th output, channel-first ONNX
layout, pooled at the GT boxes (normalized `cx, cy, w, h, class`, NaN-padded):

```python
@tensorleap_custom_latent_space(name="object_features", use_ls_for_analysis=True)
def object_features(p3: np.ndarray, gt_boxes: np.ndarray) -> np.ndarray:
    B, C, H, W = p3.shape                                 # layout read from the graph
    out = np.empty((B, C), dtype=np.float32)
    for i, (feat, boxes) in enumerate(zip(p3, gt_boxes)):
        boxes = boxes[~np.isnan(boxes).any(axis=1)]
        if len(boxes) == 0:
            out[i] = feat.mean(axis=(1, 2))               # no GT: whole-map mean
            continue
        vecs = []
        for cx, cy, w, h, _ in boxes:
            x1, y1 = max(0, int((cx - w / 2) * W)), max(0, int((cy - h / 2) * H))
            x2, y2 = min(W, max(x1 + 1, int(np.ceil((cx + w / 2) * W)))), min(H, max(y1 + 1, int(np.ceil((cy + h / 2) * H))))
            vecs.append(feat[:, y1:y2, x1:x2].mean(axis=(1, 2)))
        out[i] = np.mean(vecs, axis=0)
    return out
```

Segmentation, decoder map before the classifier exposed as the 2nd output,
masked-mean over the GT foreground (one-hot GT at input resolution, class 0 =
background):

```python
@tensorleap_custom_latent_space(name="foreground_features", use_ls_for_analysis=True)
def foreground_features(decoder: np.ndarray, gt_mask: np.ndarray) -> np.ndarray:
    B, h, w, C = decoder.shape
    H, W = gt_mask.shape[1:3]
    fg = gt_mask[..., 1:].any(axis=-1)                    # (B, H, W)
    fg = fg[:, (np.arange(h) * H) // h][:, :, (np.arange(w) * W) // w]   # nearest resize to (B, h, w)
    weight = fg[..., None].astype(np.float32)
    pooled = (decoder * weight).sum(axis=(1, 2)) / np.maximum(weight.sum(axis=(1, 2)), 1.0)
    return pooled.astype(np.float32)
```

## Guardrails

- **Exactly one** custom latent space from this skill, added after the core path
  is green; the built-in spaces stay.
- **Choose the layer from materialized metadata and the model graph**, before the
  model runs. A capped, minutes-long scan of a subsample is allowed when it earns
  its cost; a full-dataset decode to pick a tensor is not.
- **Never overwrite the user's model file.** Write a sibling with the extra
  output and point the config at it.
- **One `PredictionTypeHandler` per model output**, the new one appended last.
- `use_ls_for_analysis=True` on the space you add — unless a user-defined custom
  latent space already carries it; then leave theirs and add yours without it.
- Respect the width caps with stateless reductions — in your code or via
  `reduce=` — never a fitted one; keep post-processing semantic (box-, mask-,
  attention-guided pooling).
- The call in `integration_test` **is** the wiring — an uncalled model-computed
  latent space silently does not exist on the platform.
- Write the *why* in `integration-report.md`: profile evidence → tensor → pooling
  → width.
