#!/usr/bin/env python3
"""tl_perf — measurement CLI for the tensorleap-runtime-optimization skill.

Runs inside the integration's own Python environment (it imports code_loader).
Stdlib + numpy + code_loader; psutil is used when present. Python 3.8+.

Subcommands: preflight, fit, floor, profile, score, compare, report.
"""

import argparse
import sys

NOT_IMPLEMENTED = 64

SUBCOMMANDS = {
    "preflight": "check the environment, devices, code-loader version, and that the integration loads",
    "fit": "predict per-sample size and server memory/disk headroom per batch size",
    "floor": "measure the model's standalone inference time (percentiles, batch-size sweep)",
    "profile": "profile every integration component the way Tensorleap runs it",
    "score": "rank optimization candidates from a profile",
    "compare": "check equivalence and the timing/memory delta against the baseline",
    "report": "render report.json into report.md",
}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tl_perf", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    for name, help_text in SUBCOMMANDS.items():
        sub.add_parser(name, help=help_text)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    print("tl_perf %s: not implemented yet" % args.command, file=sys.stderr)
    return NOT_IMPLEMENTED


if __name__ == "__main__":
    sys.exit(main())
