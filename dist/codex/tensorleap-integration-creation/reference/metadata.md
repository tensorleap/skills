# Metadata: what to create, where to mine it, how to handle missing values

Reference for the optional metadata surface. Metadata is a per-sample scalar the
user filters, slices, and correlates against loss/metrics on the platform. A field
earns its place only if it answers a question a user would plausibly slice by
("do fish-eye-edge objects fail more?", "does accuracy drop on long inputs?").
Prefer domain-specific fields; a generic statistic such as brightness earns its
place only when it answers a real slicing question (e.g. camera quality), and
identifiers never do. The API shape:

```python
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_metadata
from code_loader.contract.enums import DatasetMetadataType
from code_loader.contract.datasetclasses import PreprocessResponse

@tensorleap_metadata("scene", {"weather": DatasetMetadataType.string,
                               "num_objects": DatasetMetadataType.int,
                               "ego_speed": DatasetMetadataType.float})
def scene_metadata(sample_id: str, preprocess: PreprocessResponse) -> dict:
    rec = preprocess.data[sample_id]      # whatever preprocess stored per sample
    ...
    return {"weather": rec["weather"], "num_objects": len(rec["boxes"]),
            "ego_speed": rec.get("speed")}   # None when absent -> see NaN policy
```

Each dict key surfaces as `<name>_<key>`; a scalar-returning function surfaces as
`<name>`. One function = one **concept** (see the cap below).

## Source taxonomy: sweep all four before writing any field

Do this sweep explicitly and record what each source yielded. A source that
yields nothing for this repo is fine; skipping the sweep is not.

| Source | What to look for | How to detect it in the repo |
|---|---|---|
| **Documents** | Per-sample attributes already recorded: annotation JSON/XML/CSV, sidecar files, dataset index/manifest, split lists, HF dataset columns | `find`/glob for `*.json`, `*.csv`, `*.txt`, `*.xml`, `*.yaml` beside the data; the loader's `open`/`json.load`/`pd.read_csv` calls; dataset-card / README field descriptions |
| **Directory structure** | Facts encoded in paths: class folders, `weather-N/`, `town07/`, camera id, date, session, scanner, speaker | The loader's `os.listdir`/`glob`/`Path(...).parts` usage; a `dataset_index.txt`-style listing; look at 5-10 real paths and read every path segment as a candidate categorical |
| **GT-derived** | Facts computable from the label alone: counts, sizes, class mix, sequence length, label entropy, coverage | Whatever the GT encoder decodes; anything in the repo's own eval/analysis scripts that groups results by a label property |
| **Domain knowledge** | Facts that are not stored anywhere but follow from the task's physics, optics, or semantics; needs first-principles reasoning about *why* this model would fail | Read the README / paper / model card for sensor, geometry, acquisition conditions; ask "what varies between samples that this model is plausibly sensitive to?" |

### Documents

Existing per-sample attributes are the cheapest, highest-value metadata. Load them
once in preprocess (into `PreprocessResponse.data`) and read them in the metadata
function; do not re-open files per call.

```python
@tensorleap_metadata("annot", {"occluded_frac": DatasetMetadataType.float,
                               "source_camera": DatasetMetadataType.string})
def annot_metadata(sample_id, preprocess):
    rec = preprocess.data["records"][sample_id]       # annotation JSON, loaded once in preprocess
    boxes = rec["objects"]
    occluded = sum(b["occlusion"] > 0 for b in boxes) / len(boxes) if boxes else None
    return {"occluded_frac": occluded, "source_camera": rec.get("camera")}
```

### Directory structure

Path segments are categoricals the dataset author already chose to separate by.
Parse them, do not hard-code a mapping you can derive.

```python
@tensorleap_metadata("path", {"weather": DatasetMetadataType.string, "town": DatasetMetadataType.string})
def path_metadata(sample_id, preprocess):
    parts = Path(preprocess.data["paths"][sample_id]).parts
    return {"weather": next((p for p in parts if p.startswith("weather-")), None),
            "town": next((p for p in parts if p.startswith("town")), None)}
```

### GT-derived

Two routes. Prefer plain `@tensorleap_metadata` reading the decoded GT: it is
sample-level, cheap, and slices in the dashboard. Use
`@tensorleap_custom_metric(..., compute_insights=False)` only when the quantity
needs the prediction as well (e.g. per-sample count of predicted vs GT boxes) and
should be shown but not treated as a quality signal in insights.

```python
@tensorleap_metadata("gt", {"num_boxes": DatasetMetadataType.int, "mean_box_area": DatasetMetadataType.float})
def gt_metadata(sample_id, preprocess):
    boxes = decode_gt(preprocess, sample_id)          # reuse the GT encoder's decode path
    if len(boxes) == 0:
        return {"num_boxes": 0, "mean_box_area": None}   # real zero count, undefined mean
    return {"num_boxes": int(len(boxes)), "mean_box_area": float((boxes[:, 2] * boxes[:, 3]).mean())}

# time-series GT: signal-level facts the model may be sensitive to
@tensorleap_metadata("signal", {"duration_s": DatasetMetadataType.float, "n_events": DatasetMetadataType.int})
def signal_metadata(sample_id, preprocess):
    y = load_label_series(preprocess, sample_id)
    return {"duration_s": len(y) / SAMPLE_RATE, "n_events": int(np.count_nonzero(np.diff(y)))}
```

### Domain knowledge

This is the source a checklist cannot give you. Reason from the task: what does
this sensor / modality / setup distort, and which samples sit in the distorted
region? Example: a fish-eye camera warps geometry radially, so an object's
distance from the image center predicts how deformed it is, and a detector's
failures should correlate with it.

```python
@tensorleap_metadata("fisheye", {"max_radial_dist": DatasetMetadataType.float,
                                 "objects_in_edge_ring": DatasetMetadataType.int})
def fisheye_metadata(sample_id, preprocess):
    boxes = decode_gt(preprocess, sample_id)          # relative cx, cy, w, h
    r = np.hypot(boxes[:, 0] - 0.5, boxes[:, 1] - 0.5) / np.hypot(0.5, 0.5)   # 0 = center, 1 = corner
    return {"max_radial_dist": float(r.max()) if len(r) else None, "objects_in_edge_ring": int((r > 0.7).sum())}
```

Other domain-knowledge shapes: acquisition condition (night / rain / sensor gain)
when it must be inferred rather than read; physical state (ego speed, heading
change) for driving; signal-quality proxies (baseline wander, clipping) for ECG /
audio; text register or language when the corpus mixes them.

## Few-shot grounding, not a catalog

These are examples of the *kind* of field each task wants; reason the same way
for any other task rather than looking for a list to tick.

- **Object detection** - `num_boxes` (GT), per-image class occurrence for the
  top-K classes (GT, one concept), `mean_box_area` / `min_box_area` (GT),
  `occluded_frac` or `truncated_frac` when the annotation carries them
  (documents).
- **Segmentation** - `mask_coverage` (fraction of labelled pixels, GT), per-class
  pixel ratio for the top-K classes (GT, one concept), `num_instances` or
  `num_classes_present` (GT), acquisition domain such as `city` / `source_dataset`
  from the path (directory).
- **Text classification / NER** (non-vision) - `num_tokens` and `truncated`
  (GT-derived from the encoded input), `num_entities` / `entity_types_present`
  (GT), `oov_ratio` against the tokenizer vocab (domain knowledge), `source` or
  `language` when the corpus mixes them (documents / directory).

For a multi-input / multi-head model, run the sweep per input and per GT head; a
concept that describes only one head is still one concept.

## Missing values: `None` is the signal, and the decision is per field

The platform handles a missing value natively: code-loader marks it `is_none` and
emits a twin `metadata_is_none.<name>` column, the dashboard shows `null`, and
insights mask those rows out. A fabricated `0` / `-1` / `1` is indistinguishable
from a real value and pollutes every correlation that touches the field.

- **Information absent** (no annotation record, attribute not recorded for this
  sample, computation undefined) -> return `None`.
- **A genuine value that happens to be empty** (`num_boxes = 0` on an image with
  no objects, `n_events = 0`) -> return the real value, never `None`.
- Decide this per field, automatically, from what the field means. When the field
  is derived from the user's own code and it is unclear whether a missing value
  means "absent" or "zero", **ask the user** rather than guess.
- **Always declare `metadata_type` explicitly**, per key for dict-returning
  functions. Type is otherwise inferred from the first sample, and a first-sample
  `None` fails the parse with `… is None and no metadata type is provided`.
- Never let `NaN` or `±inf` leak as "a number": they are treated as missing the
  same way `None` is, so return `None` deliberately instead.

```python
@tensorleap_metadata("car", {"num_cars": DatasetMetadataType.int,
                             "car_size": DatasetMetadataType.float})
def car_metadata(sample_id, preprocess):
    boxes = decode_gt(preprocess, sample_id)
    car_boxes = boxes[boxes[:, 4] == CAR_CLASS_ID]
    if len(car_boxes) == 0:
        # a real count of zero, but "average size of no cars" is undefined
        return {"num_cars": 0, "car_size": None}
    return {"num_cars": int(len(car_boxes)),
            "car_size": float((car_boxes[:, 2] * car_boxes[:, 3]).mean())}
```

### The same policy for `@tensorleap_custom_metric` outputs

A metric that is undefined for a sample (e.g. IoU on an image with no GT and no
predictions, precision with zero predictions) must not emit `0.0` or `1.0` as a
stand-in. Return `np.nan` at that position of the float32 array (or `None` in a
list result); the engine writes it as `null`. Reserve real zeros for real
failures. This keeps a "no GT" sample from reading as either a perfect or a
catastrophic prediction.

## Guardrails

- **Reuse before inventing.** If the repo already computes per-sample attributes
  (analysis scripts, legacy `set_metadata`, dataset stats), lift that logic first
  and only then fill gaps from the taxonomy.
- **Cap on concepts, not keys.** Soft ceiling of roughly 10-15 metadata functions.
  A per-class / per-category expansion counts as one concept. The parser's hard
  limits are 100 functions / 800 keys.
- **Fan-out guard.** When the class count is large (more than roughly 20-30), do
  not emit a per-class key blindly: restrict to the top-K most frequent classes, or
  ask the user which classes matter. Hundreds of mostly-empty columns are noise.
- **Every concept must be sliceable.** Drop identifiers (`filename`,
  `source_index`), duplicates of another field (`label` and `label_index`), and
  generic statistics nobody would filter by. Fewer, domain-relevant fields beat
  many generic ones.
