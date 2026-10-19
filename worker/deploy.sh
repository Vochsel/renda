#!/usr/bin/env bash
# Deploy the Renda worker. Pins Modal's image builder so debian_slim is bookworm (current apt indexes, libllvm15).
set -euo pipefail
cd "$(dirname "$0")"
export MODAL_IMAGE_BUILDER_VERSION="${MODAL_IMAGE_BUILDER_VERSION:-2025.06}"
exec uv run modal deploy renda_worker/app.py "$@"
