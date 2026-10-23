#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)
docker build \
  --file "$ROOT/tools/lake_terminal/integration/docker/Dockerfile" \
  --tag lake-terminal-integration \
  "$ROOT"
docker run --rm lake-terminal-integration
