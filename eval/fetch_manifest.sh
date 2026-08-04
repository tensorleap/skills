#!/usr/bin/env bash
# Fetch the fixture manifest from the private S3 bucket. The manifest names
# customer repos and files, so it is never committed to this public repo.
# An existing local copy wins (delete it to re-fetch); edit locally, then
# upload with: aws s3 cp manifest.json s3://integration-skill-bucket/manifest.json
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${SCRIPT_DIR}/manifest.json"
S3_URI="s3://integration-skill-bucket/manifest.json"

[[ -f "${MANIFEST}" ]] && exit 0

echo "Fetching fixture manifest from ${S3_URI}" >&2
aws s3 cp "${S3_URI}" "${MANIFEST}" >&2
