#!/bin/sh
set -eu
if ! command -v sbx >/dev/null 2>&1; then
  echo "Docker Sandboxes CLI (sbx) is not installed." >&2
  exit 127
fi
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
for provider in claude codex cursor devin; do
  echo "Creating/running $provider sandbox qualification target..."
  sbx create "$provider" "$ROOT" --name "lake-$provider" || true
  echo "Use: sbx run --name lake-$provider"
done
