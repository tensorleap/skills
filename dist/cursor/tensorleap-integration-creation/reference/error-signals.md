# Signal -> meaning -> fix

Condensed from the code-loader feedback surfaces. Match the message you see in a
run (or in `tl_check.py` output) to its row, apply the fix, then re-run.

## Exit-table / process-level

| Signal | Meaning | Fix |
|---|---|---|
| `Warnings (Default use. It is recommended to set values explicitly)` | You relied on a default. | Make the warned value explicit. |
| `Parameter 'PreprocessResponse.state' defaults to specific order` | `state=` not set on a subset. | Set `state=DataStateType.training` / `validation` explicitly. |
| `Parameter 'channel_dim' defaults to -1` | Input encoder omitted `channel_dim`. | Set the channel axis of the *batched* tensor: NCHW→`1`, NHWC→`-1` (`0` is invalid). |
| `Parameter 'prediction_types' defaults to []` | `load_model()` had no prediction types. | Add `PredictionTypeHandler` entries once outputs are known. |
| `Parameter 'prediction_types[i].channel_dim' defaults to -1` | Prediction type omitted channel axis. | Set it explicitly. |
| `Parameter 'direction' defaults to Downward` | Custom metric omitted optimization direction. | Add `direction=` explicitly. |
| `This integration is not ready to push: \`X\` is not used in the integration test yet. Add it next.` (exit 3) | Clean run, but mandatory part X was never exercised. Older code-loader: `Some mandatory components have not yet been added … Recommended next interface to add is: X` (exit 0). | Add X to the integration test. Do not push. |
| `Ready to push: all mandatory parts are in the integration test.` (exit 0) | Every mandatory part ran. Older code-loader: `All mandatory parts have been successfully set …`. | Push, or continue with `Optional next: …`. |
| `Script crashed before completing all steps. crashed at function '…'` | Exception escaped early. | Fix the named function before trusting later rows. |
| `Tensorleap_integration_test code flow failed, check raised exception.` | Mapping-mode rerun failed. | Remove plain Python from the test body. |

## Generic decorator feedback

| Signal | Fix |
|---|---|
| `validation failed: Missing required argument` | Match the call site to the decorator contract (usually `(sample_id, preprocess_response)`). |
| `validation failed: Expected exactly … arguments` | Wrong arg count — legacy helper signature leaked in. |
| `validation failed: Argument 'idx' expected type …` | Wrong arg type at the call. |
| `validation failed: The function returned None` | Missing `return`, or a branch returns nothing. |
| `validation failed: The function returned multiple outputs` | Return one object; move extras into separate decorated interfaces. |
| `warning: Tensorleap will add a batch dimension at axis 0 …` | Encoder/GT returned a batched shape; remove the leading batch dim. |

## Preprocess

| Signal | Fix |
|---|---|
| `preprocess() … should not take any arguments` | Make preprocess argument-free; move config to module scope. |
| `expected return type list[PreprocessResponse]` | Return a list even for one subset. |
| `expected to return a single list … returned … objects instead` | Wrap subsets in one list (accidental tuple). |
| `Element #… should be a PreprocessResponse` | Construct proper `PreprocessResponse` instances. |
| `should not contain duplicate PreprocessResponse objects` | Build distinct subset objects. |
| `length is deprecated, please use sample_ids instead.` | Don't set `length`; return real `sample_ids`. |
| `Sample id should be of type str. Got: …` | Make `sample_ids` homogeneous strings. |
| `PreprocessResponse.state must be of type DataStateType` | Use the `DataStateType` enum, not a string. |
| `Duplicate state … in preprocess results` | Each state may appear once. |
| `Training data is required` / `Validation data is required` | Add the missing subset. |
| `Invalid dataset length` | Subset length must be > 0 during validation. |
| `Sample id are too long. Max allowed length is 256 …` | Use shorter stable IDs. |

## Input encoder

| Signal | Fix |
|---|---|
| `Input with name … already exists` | Input names must be unique. |
| `Channel dim for input … is expected to be either -1 or positive` | Use `-1` or a positive axis index. |
| `Argument sample_id should be as the same type as defined in the preprocess response` | Keep `sample_ids` homogeneous; pass unchanged. |
| `Unsupported return type. Should be a numpy array` | Convert list/PIL/tensor to `np.ndarray`. |
| `The return type should be a numpy array of type float32` | `.astype(np.float32)`. |
| `The channel_dim (…) should be <= to the rank …` | Fix the axis or the returned shape. |
| `FoundationInput() validation failed: rgb_range should be an ImageRange preset, one (min, max) pair, or three (min, max) pairs in R, G, B order, each with min < max. Got …` / `… rgb_channels should be three non-negative channel indices (R, G, B) along the channel axis. Got ….` | Raised at import. Use an `ImageRange` preset, one `(min, max)` pair, or exactly three pairs (R, G, B) — not two or four; each pair two numbers with min < max. Three channel indices. |
| ``FoundationInput() validation failed: masks should be the function returned by a @tensorleap_instances_masks_encoder decorator -- pass the decorated function object, not the encoder name. Got ….`` | `masks=foundation_mask`, not `masks="foundation_mask"` (`element-instances.md`, *Foundation input on an instance project*). |
| ``Invalid frame_dim for input '…': frame_dim=… is not a frame axis of a 4-D sample: it counts axes of the batched tensor (batch = axis 0), so use 1..4 or -4..-1.`` / ``… frame_dim should be an int (the frame axis in the batched tensor) or None, got ….`` | Raised at import. `frame_dim` counts axes of the batched tensor like `channel_dim`: `1` for `[T, H, W, C]`, `2` for `[C, T, H, W]`. |
| ``Invalid frame_dim for input '…': frame_dim=… and channel_dim=… are the same axis of a 4-D sample (axis … of the sample); the frame axis must differ from the channel axis.`` | Raised at import. Point `frame_dim` at the frame axis and `channel_dim` at the channel axis. |
| ``…() validation failed: frame_dim=… (axis … of the sample) is set but the sample has … axes (shape […]); frame_dim is the frame axis of a 4-D (multi-frame) sample -- remove frame_dim, or return a 4-D sample.`` | `frame_dim` is only for a 4-D sample: remove it from a 2-D or 3-D input. |
| ``Input `…`: sample shape … has … axes; a foundation input is a 3-D image (H, W and the channel axis on channel_dim), a 2-D H x W grayscale image, or a 4-D multi-frame sample (frames on frame_dim).`` | Return a 2-D, 3-D or 4-D sample (no batch axis). A 1-D or 5-D+ tensor cannot carry `foundation=`: put it on an image input or add a dedicated foundation input. |
| ``Input `…`: 4-D sample …; the foundation encoders read its middle frame, index … of … on frame_dim=… (axis … of the sample).`` (warning) / ``… -- frame_dim is not set, so the frame axis was inferred as frame_dim=… (axis … of the sample), the axis that is neither channel_dim=… (axis … of the sample) nor one of the last two (spatial) axes; set frame_dim= on the encoder to state it.`` (warning) | Expected on a multi-frame input. If the inferred axis is wrong, or to state it, set `frame_dim=` on the encoder (`authoring-order.md` Step 4, *Multi-frame inputs*). |
| ``Input `…` (…): rgb_range=… converts … random samples back to pixel values …–…, outside 0–255: the tensor's values are wider than the declared range, which a correct declaration cannot produce (e.g. byte values declared UNIT, or [-1, 1] values declared UNIT). rgb_range is the convention's theoretical [min, max], not the samples' min/max. Fix rgb_range=.`` | Declare the convention's range, never the observed min/max (`authoring-order.md` Step 4, *Foundation input*). Fix the declaration, not the encoder. In `tl_check.py` this is payload `foundation_input`, which also prints, per split, ``foundation input `…`: pixel values …–… ✗``. |
| ``Input `…` (…): rgb_range=… converts … random samples back to pixel values …–…, a span of … of 256 levels: the tensor is nearly constant under the declared range, narrower than any real image (e.g. [0, 1] values declared BYTE -- declare UNIT). Fix rgb_range=.`` | Same fix: the range is too wide for the tensor (a `/255` tensor declared `BYTE` → `UNIT`). |
| ``Input `…` (…): rgb_range=… converts … random samples back to pixel values …–…, a span of … of 256 levels: narrower than typical images. Fine for dark or low-contrast data; otherwise the declared range may be wider than the tensor (e.g. a /255 tensor declared BYTE -> UNIT, or [0, 1] values declared SIGNED -> UNIT).`` (warning) | Dark, foggy or low-contrast data: leave it. Otherwise the range is too wide (a `/255` tensor declared `BYTE` → `UNIT`; `[0, 1]` values declared `SIGNED` → `UNIT`). |
| ``Input `…`: the selected red channel averages … against … for blue on the sampled images -- natural images rarely do; if the tensor is BGR, set rgb_channels=(2, 1, 0) (channel order cannot be verified from statistics).`` (warning) | If the preprocessing is OpenCV/BGR, `rgb_channels=(2, 1, 0)`; if the images really are blue-heavy, leave it. If the channel order is right, check that a per-channel `Normalize(mean, std)` is declared per channel (`IMAGENET`, `CLIP` or three pairs), not as one pair. |
| ``Input `…` carries foundation=, but `…` already does -- exactly one input per project is the foundation image; keep foundation= on one of them.`` | Raised at import (decoration), naming both inputs — the error users normally see. Keep `foundation=` on one input: the model's image input *or* a dedicated foundation input, not both. |
| ``More than one input encoder carries foundation=: `…`, `…`. Exactly one input per project is the foundation image -- keep foundation= on one of them.`` | `check_dataset` safety net; users normally see the decoration error above first. Exactly one `foundation=` per project — on the model's image input *or* on a dedicated foundation input, not both. |
| ``Input `…` is passed to the model in the integration test, so it is the native image and the native instance masks apply to it; masks= is only for a dedicated foundation input that is not passed to the model. Remove masks=….`` | The encoder is wired to the model, so it is the native input: drop `masks=`. |
| ``Input `…` is passed to the model in the integration test, so it must return float32 (got …); uint8 is allowed only for a dedicated foundation input that is not passed to the model.`` | Return `float32`. Only a dedicated foundation input (never passed to the model in `integration_test`) may return `uint8`. |
| ``Input `…`: a uint8 foundation input must declare rgb_range=ImageRange.BYTE (got rgb_range=…). uint8 holds 0–255 pixels, which the platform reads as they are; UNIT, SIGNED, IMAGENET, CLIP or a custom range never fit it.`` | Declare `rgb_range=ImageRange.BYTE` on the `uint8` input. |
| ``Input `…`: the probed samples differ in shape (…). A foundation input must have one fixed size, the same for every sample: the platform batches its samples, which needs equal shapes. Resize, crop or pad to one size.`` | Resize (or crop / pad) every sample to one fixed target size in the encoder (`authoring-order.md` Step 4, *Dedicated foundation input*). |
| ``Input `…`: rgb_channels=[…] index […] out of range -- the input has … channels on channel_dim=… (axis … of the sample; sample shape […]).`` / ``Input `…`: sample shape … has no channel axis at channel_dim=… (counted in the batched tensor), so rgb_channels=[…] cannot be applied.`` | Fix `channel_dim` (batched axis: `1` for NCHW, `-1` for NHWC) or `rgb_channels`; a single-channel image takes `(0, 0, 0)`. |
| ``foundation= on input `…` is not supported in an autoregressive integration (tensorleap_autoregressive_step) in this version. Remove foundation=.`` / ``foundation= on input `…` is not supported together with simulations (…) in this version. Remove foundation= or the simulations.`` | Not supported in this release: remove `foundation=` (or the simulations). |
| ``Input `…` names no masks=; the masks of encoder `…` are used for it, assuming this image shows the same view as the model's input (same crop and padding).`` (warning) / ``Input `…` (…): the native mask is …x… and the foundation image …x…; the mask will be resized to the foundation image's size, assuming it shows the same view. If the view differs (crop, letterbox, tile), add masks= with a masks encoder drawing in the foundation image's coordinates.`` (warning) | Fine when the foundation image differs from the model's input by size alone; for a different crop, letterbox or tiling add a second masks encoder and pass it as `masks=`. |
| ``Input `…`: masks=… is not a registered masks encoder; pass the function returned by a @tensorleap_instances_masks_encoder decorator.`` / ``Input `…`: masks=… is the only registered masks encoder; an instance project needs the native one too (the encoder bound through tensorleap_element_instance_preprocess), which defines the instances.`` | Pass the function object of a second `@tensorleap_instances_masks_encoder`; keep the native one bound to the element-instance preprocess. |
| ``Input `…`: masks encoder `…` returned None for instance 0 of sample … (…) where the native encoder `…` returned a mask -- every instance the native encoder creates needs a foundation mask, otherwise that instance gets no foundation embedding.`` / ``Input `…`: the mask from `…` is …x… for instance 0 of sample … (…) but the foundation image is …x…; the mask must be the image's H x W or an integer divisor of it.`` / ``Input `…`: masks encoder `…` returned a mask of shape … for instance 0 of sample … (…); a foundation mask is a 2-D H x W array.`` | Build both masks encoders from one helper (same instances, same order) and draw at the foundation image's H×W. |
| ``Input `…`: masks encoder `…` names instance 0 of sample … (…) … while the native encoder `…` names it …; only the geometry is taken from `…`, the name comes from the native encoder.`` / ``Input `…`: masks encoder `…` returns instance_metadata for instance 0 of sample … (…) that differs from the native encoder `…`'s; only the geometry is taken from `…`, the metadata comes from the native encoder.`` (warnings) | Only the mask is used from the foundation masks encoder: return the bare H×W mask (or `None`), not an `ElementInstance`. |
| ``Input `…`: masks=… is ignored -- this project has no element instances (no tensorleap_element_instance_preprocess), so there are no instances to mask.`` (warning) | Drop `masks=` on a classification project. |

## Ground-truth encoder

| Signal | Fix |
|---|---|
| `GT with name … already exists` | GT names must be unique. |
| `The function returned None. If you are working with an unlabeled dataset …` | Return `np.array([], dtype=np.float32)`, not `None`. |
| `Unsupported return type` / `should be … float32` | Return `np.ndarray` cast to `float32`. |
| batch-dimension warning | Remove the leading batch dim from the GT encoder. |
| Evaluate warns `'…' has a different shape from sample to sample` / fails with `all the input array dimensions for the concatenation axis must match exactly` | The GT shape varies per sample (one row per box). Pad it to a fixed `MAX_*` with NaN rows; drop them in loss, metrics, visualizers and GT-derived metadata. |

## load_model

| Signal | Fix |
|---|---|
| `prediction_types … but got …` | Pass `List[PredictionTypeHandler]` to the decorator. |
| `prediction_types at position … must be of type PredictionTypeHandler` | Fix that list element. |
| `Supported models are Keras and onnxruntime only …` | Return a Keras model or ONNX `InferenceSession`. |
| `number of declared prediction types(…) != number of model outputs(…)` | Fix declarations or the model (fires on first invocation). |
| `Missing required input(s): […]` | Supply every required ONNX input name. |
| Batch-truth check fails (a batch of 2 raises, e.g. `Cannot split using values in 'split' attribute`, or differs from each sample run alone) | The export froze the batch at trace time — often a Python loop over the batch unrolled into `x[0]`. Fix the export so the batch flows through tensor ops, or re-export with a static batch of 1 and push with `-b 1`. See `authoring-order.md` Step 3. |
| `Unsupported ONNX input type: …` | Encoders return float32 but the ONNX expects int (e.g. int64 `input_ids`). Re-export the model wrapped so its inputs are float32 and cast to int internally (`input_ids.long()`), instead of changing the encoders. |

## Integration test

| Signal | Fix |
|---|---|
| `sample_id type (…) does not match … from the PreprocessResponse` | Fix the call site or preprocess IDs. |
| `indexing is supported only on the model's predictions inside the integration test` | Move indexing into a decorated interface. |
| `Integration test is only allowed to call Tensorleap decorators …` | Move all arithmetic/library/Python logic into decorated functions. |
| `'TempMapping' object is not subscriptable` | A prediction was indexed/sliced more than once in the test body — keep only the single `model.run(None, ...)[i]`; move further slicing into a decorated function. |
| `Successful!` (older code-loader only) | First-sample check passed. Not a verdict: read the readiness line instead. |

When the mapping rerun fails, code_loader can mask the real exception (it raises
a string, surfacing as `TypeError: exceptions must derive from BaseException`)
and mis-attribute "crashed at function 'X'". To see the true error + line, replay
the rerun directly:

```python
import os
from code_loader.inner_leap_binder.leapbinder_decorators import (
    mapping_runtime_mode_env_var_mame as MM, leap_binder)
from code_loader.contract.datasetclasses import PreprocessResponse
from code_loader.contract.enums import DataStateType
import leap_integration            # registers handlers; __main__ does not run on import
os.environ[MM] = 'True'
try:
    leap_binder.integration_test_func(
        None, PreprocessResponse(state=DataStateType.training, length=0))
except Exception:
    import traceback; traceback.print_exc()
finally:
    os.environ.pop(MM, None)
```

## Custom loss

| Signal | Fix |
|---|---|
| `Custom loss with name … already exists` | Loss names must be unique. |
| `Expected at least one positional|key-word argument …` | Loss operates on already-batched arrays. |
| `Argument #… should be a numpy array` | Pass predictions/GT arrays or the placeholder type. |
| `The return type should be a numpy array` / `… 1Dim …` | Return a batch-aligned 1D `np.ndarray`; reduce feature axes, not the batch axis. |
| Platform push build fails at **"Testing Loss"** (other steps pass); a non-interactive push then exits 1 on the `View errors? (Y/n)` prompt | No custom loss registered. A custom loss is **required to push** — `check_dataset()` / `isValid` can be `True` without one. Add a per-sample 1D loss. |

## Metadata

| Signal | Fix |
|---|---|
| `Metadata with name … already exists` | Names must be unique. |
| `Unsupported return type …` | Return a scalar, `None`, or a flat dict of scalars — no arrays/lists/nested. |
| `Keys … should be of type str` / `Values … should be of type …` | Dict keys are strings; values are supported scalars. |
| `… is None and no metadata type is provided` | Declare `metadata_type` if metadata may be missing. |
| `More than 100 metadata function …` / `More than 800 metadata keys …` | Parser hard limits. |

## Visualizer

| Signal | Fix |
|---|---|
| `Visualizer with name … already exists` | Names must be unique. |
| `visualizer_type should be of type LeapDataType` | Pass a `LeapDataType` enum. |
| `Argument #… should be without batch dimension` | Visualizers take unbatched sample-level arrays. |
| `The return type should be …` | Return the declared type (e.g. `LeapImage` for `LeapDataType.Image`). |
| `The return type of function … is invalid. current return type: Leap…, should be one of [list that already contains it]` | The file has `from __future__ import annotations`; remove it so code_loader sees the real return class, not a string. |
| `Tensorleap Warning: no return type hint for function …` | Add the return type annotation. |
| Platform shows dark / near-black images (bounding boxes placed correctly) — no local message | Image visualizer returned a float image; the platform renders images on a `0-255` scale, so return **uint8 `[0, 255]`**. Not flagged locally: `LeapImage` validates dtype/shape, not value range. |

## Metric

| Signal | Fix |
|---|---|
| `Metric with name … already exists` | Names must be unique. |
| `direction must be a MetricDirection or a Dict[…]` | Fix the direction declaration. |
| `Argument #… first dim should be as the batch size` | Metrics operate on batched arrays. |
| `has returned unsupported type` / `The return shape should be 1D` / `The return len … should be as the batch size` | Return a batch-aligned 1D result, one value per sample. |

## Legacy-binder compatibility

| Signal | Fix |
|---|---|
| `Please remove the metadata_type on leap_binder.set_metadata …` | Stop using `leap_binder.set_*` as the authoring API; use decorators. |
