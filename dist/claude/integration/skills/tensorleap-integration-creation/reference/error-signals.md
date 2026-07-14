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
| `Some mandatory components have not yet been added … Recommended next interface to add is: …` | Clean exit, but a mandatory interface was never exercised. | Implement/exercise the named next interface. |
| `All mandatory parts have been successfully set …` | Core path exercised. | Stop, or continue to optional interfaces. |
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

## Ground-truth encoder

| Signal | Fix |
|---|---|
| `GT with name … already exists` | GT names must be unique. |
| `The function returned None. If you are working with an unlabeled dataset …` | Return `np.array([], dtype=np.float32)`, not `None`. |
| `Unsupported return type` / `should be … float32` | Return `np.ndarray` cast to `float32`. |
| batch-dimension warning | Remove the leading batch dim from the GT encoder. |

## load_model

| Signal | Fix |
|---|---|
| `prediction_types … but got …` | Pass `List[PredictionTypeHandler]` to the decorator. |
| `prediction_types at position … must be of type PredictionTypeHandler` | Fix that list element. |
| `Supported models are Keras and onnxruntime only …` | Return a Keras model or ONNX `InferenceSession`. |
| `number of declared prediction types(…) != number of model outputs(…)` | Fix declarations or the model (fires on first invocation). |
| `Missing required input(s): […]` | Supply every required ONNX input name. |
| `Unsupported ONNX input type: …` | Encoders return float32 but the ONNX expects int (e.g. int64 `input_ids`). Re-export the model wrapped so its inputs are float32 and cast to int internally (`input_ids.long()`), instead of changing the encoders. |

## Integration test

| Signal | Fix |
|---|---|
| `sample_id type (…) does not match … from the PreprocessResponse` | Fix the call site or preprocess IDs. |
| `indexing is supported only on the model's predictions inside the integration test` | Move indexing into a decorated interface. |
| `Integration test is only allowed to call Tensorleap decorators …` | Move all arithmetic/library/Python logic into decorated functions. |
| `'TempMapping' object is not subscriptable` | A prediction was indexed/sliced more than once in the test body — keep only the single `model.run(None, ...)[i]`; move further slicing into a decorated function. |
| `Successful!` | Real run + mapping rerun + first-sample binder check passed. |

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
