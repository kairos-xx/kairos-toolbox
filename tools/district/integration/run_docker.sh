#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)
docker build \
  --file "$ROOT/tools/district/integration/docker/Dockerfile" \
  --tag district-integration \
  "$ROOT"
docker run --rm district-integration
