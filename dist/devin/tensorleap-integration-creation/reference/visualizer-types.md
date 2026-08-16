# Visualizer types

Reference for the optional visualizer surface. A visualizer is declared with a
`LeapDataType` and must return the matching `Leap*` class. On the platform a
visualizer receives **unbatched, sample-level** arrays; inside the integration
test the values you pass come back **batched**, so strip the batch axis *inside*
the visualizer (`if x.ndim == 3: x = x[0]`), not in the test body.

```python
from code_loader.inner_leap_binder.leapbinder_decorators import tensorleap_custom_visualizer
from code_loader.contract.enums import LeapDataType
from code_loader.contract.visualizer_classes import LeapImage  # etc.

@tensorleap_custom_visualizer("name", LeapDataType.Image)
def viz(...) -> LeapImage:
    ...
```

| `LeapDataType` | Return class | For | Shape / notes |
|---|---|---|---|
| `Image` | `LeapImage` | images | `(H, W, C)` |
| `Video` | `LeapVideo` | video | `(T, H, W, C)` |
| `Graph` | `LeapGraph` | line charts / time series | `(M, N)` — M points, N traces |
| `Text` | `LeapText` | tokens, optional heatmap | token list (+ optional per-token weights) |
| `HorizontalBar` | `LeapHorizontalBar` | classification scores | `body` `(C,)` + `labels` length C |
| `ImageMask` | `LeapImageMask` | segmentation overlay | image `(H,W,C)` + mask `(H,W)` uint8 + labels |
| `TextMask` | `LeapTextMask` | NER / text highlighting | tokens + per-token mask + labels |
| `ImageWithBBox` | `LeapImageWithBBox` | object detection | image + list of `BoundingBox` (relative x,y,w,h) |
| `ImageWithHeatmap` | `LeapImageWithHeatmap` | attention / saliency | image + heatmap |

Rules that apply across types:

- The return type must match the declared `LeapDataType` exactly, or registration
  fails (`The return type should be ...`).
- **Image data: prefer `uint8` in `[0, 255]`.** For image-bearing types (`Image`,
  `ImageWithBBox`, `ImageWithHeatmap`, `ImageMask`), return the image as uint8 in
  `[0, 255]`. Floating-point images may render poorly on the platform; uint8 is
  unambiguous and renders consistently. If the image comes from a normalized model
  input, convert first (keep it HWC, RGB):
  `disp = (x * 255).clip(0, 255).astype(np.uint8)`.
- Add a return type hint to the function — its absence triggers
  `Tensorleap Warning: no return type hint for function ...`.
- Names must be unique across visualizers.
- Confirm exact class names and constructor arguments against the installed
  `code_loader.contract.visualizer_classes` for your version; this table is the
  map of which type pairs with which class, not a frozen API signature.

## Verifying an image visualizer renders correctly

Prefer returning uint8 `[0, 255]` (see the rule above) — uint8 renders
consistently in `visualize()` and on the platform. To confirm appearance, branch on
the installed code-loader version:

```python
from importlib.metadata import version
_cl = tuple(int(p) for p in version("code-loader").split(".")[:3] if p.isdigit())
```

- **`_cl >= (1, 0, 186)`** — `visualize()` mirrors the platform's rendering; use it
  (or a saved render) to confirm the image looks right, and heed any range warning
  it prints.
- **`_cl < (1, 0, 186)`** — do **not** trust `visualize()` / `matplotlib.imshow`
  float<->uint8 handling to judge appearance. Rely on the uint8 `[0, 255]` rule; if
  you must inspect a float image, cast to uint8 first.

## Reading the original sample (`SamplePreprocessResponse`)

A visualizer (also metrics / custom loss) often needs the *original* sample —
tokens, file paths, ids, anything in `preprocess.data` — not just decoded
tensors. Add an argument **annotated** `SamplePreprocessResponse` and the
framework injects it (matched by annotation **type**, not name):

```python
from code_loader.contract.datasetclasses import SamplePreprocessResponse

@tensorleap_custom_visualizer("with_text", LeapDataType.TextMask)
def viz(prediction: np.ndarray, spr: SamplePreprocessResponse) -> LeapTextMask:
    sid = spr.sample_ids
    if isinstance(sid, np.ndarray):       # platform passes an array; tests a scalar
        sid = sid.reshape(-1)[0]
    sample = spr.preprocess_response.data[sid]
    ...
```

- Annotate it exactly `SamplePreprocessResponse` or it won't be injected.
- Auto-injected on the platform / `check_dataset`, but **not** inside `integration_test` —
  there you must pass it yourself: `viz(pred, SamplePreprocessResponse(sample_id, preprocess))`.
- Lets you avoid bundling/loading heavy assets at runtime (e.g. a tokenizer) just
  to recover strings already computed in preprocess.
- A visualizer whose only argument is a `SamplePreprocessResponse` is valid.
