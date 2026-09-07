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

## Heatmap alignment (`heatmap_function`)

The platform computes a gradient/saliency heatmap over the **raw model input
tensor** and overlays it on whatever the visualizer returned. If the visualizer
changed the data's shape or orientation — unbatching, flipping, resizing,
cropping, transposing, picking a channel — the overlay lands on the wrong pixels
unless you tell the platform how to transform the heatmap the same way. That is
what `heatmap_function` is for:

```python
@tensorleap_custom_visualizer("input_image", LeapDataType.Image,
                              heatmap_function=heatmap_image)
def visualize_input(features: np.ndarray) -> LeapImage:
    ...
```

Decide per visualizer:

- **Returns the input essentially unchanged** (same H, W, orientation; only a
  dtype/scale change like `(x * 255).astype(np.uint8)`): skip it.
- **Changes spatial layout, or takes more than one input**: write one.

### The two rules code-loader enforces

1. **Argument names must match the visualizer's exactly.** The heatmap function
   is called with the same argument names as the visualizer, so mismatched names
   raise `The argument names of the heatmap visualizer callback must match the
   visualizer callback [...]` at registration. Exactly one *extra* argument is
   allowed, and only if it is annotated `RawInputsForHeatmap` (a dataclass
   holding `raw_input_by_vizualizer_arg_name`) — use it when the transform
   depends on the original input values, not just their shape.
2. **A multi-input visualizer needs one.** Without a `heatmap_function`,
   code-loader's heatmap path asserts there is exactly one input tensor, so a
   visualizer taking image + boxes (or image + logits) fails there. The heatmap
   function is where you say which input the heatmap belongs to.

Note what the arguments carry: the values passed in are the **heatmaps** for
those inputs, not the raw inputs. A heatmap function is a shape transform on
heatmap data; arguments it does not need are simply ignored.

### Example

An object-detection visualizer set where every image goes through the same
unbatch + vertical-flip transform. One shared helper, one thin wrapper per
visualizer so the argument names line up:

```python
def to_heatmap(array: np.ndarray) -> np.ndarray:
    if array.ndim == 4:
        array = array[0]
    if array.ndim == 3:
        array = array[0]
    if FLIP_VERTICAL:
        array = np.flipud(array)
    return np.ascontiguousarray(array.astype(np.float32))


def heatmap_image(features: np.ndarray) -> np.ndarray:
    return to_heatmap(features)


# names match visualize_predictions' arguments; only `features` is used
def heatmap_predictions(features: np.ndarray, pred_logits: np.ndarray,
                        pred_boxes: np.ndarray) -> np.ndarray:
    return to_heatmap(features)


@tensorleap_custom_visualizer("input_image", LeapDataType.Image,
                              heatmap_function=heatmap_image)
def visualize_input(features: np.ndarray) -> LeapImage:
    return LeapImage(to_image(features))


@tensorleap_custom_visualizer("predicted_boxes", LeapDataType.ImageWithBBox,
                              heatmap_function=heatmap_predictions)
def visualize_predictions(features: np.ndarray, pred_logits: np.ndarray,
                          pred_boxes: np.ndarray) -> LeapImageWithBBox:
    ...
```
