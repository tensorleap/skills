"""Acceptance-mode structured check for a Tensorleap integration.

Wraps ``code_loader.leaploader.LeapLoader.check_dataset()`` and prints a JSON
summary. Use this (not the human-readable exit table) for "is it actually done?"
decisions: ``isValid`` / ``generalError`` / per-handler ``payloads[].passed`` are
authoritative; the exit table can show stale crosses.

Must run inside the project's environment (pyenv + poetry by default, or whatever
the user chose) so ``code_loader`` imports:

    poetry run python scripts/tl_check.py [repo_root] [entry_file]
    # or, with the project's venv active:  python scripts/tl_check.py [repo_root]

    repo_root   defaults to "."
    entry_file  defaults to "leap_integration.py"

Exit code is 0 when the parse reports ``is_valid``, else 1, so it can gate a loop.
"""

import json
import os
import sys


def _as_int_list(values):
    if values is None:
        return None
    return [int(v) for v in values]


def _payload_entry(item):
    entry = {"name": str(item.name), "passed": bool(item.is_passed)}
    if getattr(item, "handler_type", None) is not None:
        entry["handlerType"] = str(item.handler_type)
    if getattr(item, "shape", None) is not None:
        entry["shape"] = _as_int_list(item.shape)
    if getattr(item, "display", None):
        entry["display"] = {str(k): str(v) for k, v in item.display.items()}
    return entry


def _shape_entry(item, include_channel_dim=False):
    entry = {"name": str(item.name)}
    shape = getattr(item, "shape", None)
    if shape is not None:
        entry["shape"] = _as_int_list(shape)
    if include_channel_dim:
        channel_dim = getattr(item, "channel_dim", None)
        if channel_dim is not None:
            entry["channelDim"] = int(channel_dim)
    return entry


def _prediction_type_entry(item):
    entry = {"name": str(item.name)}
    if getattr(item, "labels", None):
        entry["labels"] = [str(v) for v in item.labels]
    channel_dim = getattr(item, "channel_dim", None)
    if channel_dim is not None:
        entry["channelDim"] = int(channel_dim)
    return entry


def main():
    # Absolutize: code_loader's LeapLoader path walker infinite-recurses on a
    # relative root like ".", so always resolve to an absolute path.
    repo_root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    entry_name = sys.argv[2] if len(sys.argv) > 2 else "leap_integration.py"

    try:
        from code_loader.leaploader import LeapLoader
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({
            "available": False,
            "error": "could not import code_loader.leaploader.LeapLoader; "
                     "run inside the project's env (e.g. poetry run python …): %s" % exc,
        }, indent=2))
        return 2

    loader = LeapLoader(repo_root, entry_name)
    result = loader.check_dataset()

    payload = {
        "available": True,
        "entryFile": entry_name,
        "isValid": bool(result.is_valid),
        "hasCustomLayers": bool(getattr(result, "is_valid_for_model", False)),
        "generalError": result.general_error or "",
        "printLog": result.print_log or "",
        "payloads": [_payload_entry(p) for p in (result.payloads or [])],
    }

    setup = getattr(result, "setup", None)
    if setup is not None:
        pp = setup.preprocess
        payload["setup"] = {
            "preprocess": {
                "trainingLength": int(pp.training_length),
                "validationLength": int(pp.validation_length),
                "testLength": int(pp.test_length or 0),
                "unlabeledLength": int(pp.unlabeled_length or 0),
            },
            "inputs": [_shape_entry(i, include_channel_dim=True) for i in (setup.inputs or [])],
            "outputs": [_shape_entry(o) for o in (setup.outputs or [])],
            "predictionTypes": [_prediction_type_entry(p) for p in (setup.prediction_types or [])],
        }

    print(json.dumps(payload, indent=2))

    failed = [p["name"] for p in payload["payloads"] if not p["passed"]]
    if not payload["isValid"] or failed:
        if failed:
            print("FAILED handlers: %s" % ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
