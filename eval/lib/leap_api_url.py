#!/usr/bin/env python3
"""Print the api_url the Tensorleap CLI currently targets, or exit non-zero.

Reads ~/.config/tensorleap/config.yaml (override with TENSORLEAP_CONFIG). The
top-level `auth:` block mirrors whichever environment `leap auth select` last
picked, so this is the server a push would actually reach — the command's NAME
proves nothing. Deliberately parsed without PyYAML: it is absent from most
python3 installs, and a safety check that fails on a missing dependency just
invites the override flag.

Shared by run.sh (hard gate) and check.sh (preflight table).
"""
import os
import sys


def api_url():
    cfg = os.environ.get("TENSORLEAP_CONFIG") or os.path.expanduser(
        "~/.config/tensorleap/config.yaml")
    try:
        lines = open(cfg).read().splitlines()
    except Exception as exc:
        sys.exit(f"cannot read {cfg}: {exc}")
    url, top = None, None
    for line in lines:
        if line[:1] not in (" ", "\t", "", "#"):
            top = line.split(":", 1)[0].strip()
        elif top == "auth":
            key, _, val = line.strip().partition(":")
            if key == "api_url" and val.strip():
                url = val.strip()
                break
    if not url:
        sys.exit(f"no auth.api_url in {cfg} — is the CLI logged in? (leap auth login)")
    return url


if __name__ == "__main__":
    print(api_url())
