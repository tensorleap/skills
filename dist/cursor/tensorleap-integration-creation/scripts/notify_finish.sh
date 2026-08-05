#!/usr/bin/env bash
#
# Send the finish report to Tensorleap's Slack (via a public relay endpoint;
# the Slack webhook itself never leaves Tensorleap's AWS account).
# Consent-gated: call ONLY after the user explicitly agreed to share the
# report (see skill.md "Finish report"). Best-effort — never fails the run.
#
# Usage: scripts/notify_finish.sh "<customer>" "<use-case>" "<problems>" "<user-name>"

CUSTOMER="${1:-}" USE_CASE="${2:-}" PROBLEMS="${3:-}" USER_NAME="${4:-}" \
python3 - <<'EOF' || true
import json, os, urllib.request

body = {"customer": os.environ["CUSTOMER"],
        "use_case": os.environ["USE_CASE"],
        "problems": os.environ["PROBLEMS"],
        "user": os.environ["USER_NAME"]}
req = urllib.request.Request(
    "https://hjjtb3yv7l.execute-api.us-east-1.amazonaws.com",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=10)
    print("finish report sent")
except Exception as exc:
    print(f"finish report not sent ({exc}) — non-fatal, continuing")
EOF
